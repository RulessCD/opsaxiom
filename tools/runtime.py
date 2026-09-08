"""
运行时引擎——导航档（Navigator）会话（T-1，落实 docs/01 §2 第一档 + R3/R5/R6）。

导航档语义：Agent 只出方案、变更简报、判读，**人执行一切命令**（写操作 Agent 绝不代执行）。
每一步"执行→采集输出→机器判读→符合预期才放行下一步"（R5）。风险操作前渲染变更简报（R6）。

输入抽象 IO：交互式读 stdin，或脚本驱动（答案文件，供测试/演示非交互运行）。
审计：每步落 ~/.opsaxiom/sessions/<sid>.jsonl（R11 脱敏；这里只记节点/决策/输入摘要）。

模板渲染（§7.4/§7.6c，落实 R-7）：`{{expr}}` 用受限表达式求值器对当前上下文求值，
支持 {{mount}}(param) / {{rows[0].comm}} / {{output.pcent}}(节点标量) / {{sid}}。
"""
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "sim"))
import yaml            # noqa: E402
import exprlang        # noqa: E402
import parsers         # noqa: E402
import run_sim         # noqa: E402  复用 _is_readonly / _default_parse

_TEMPLATE = re.compile(r"\{\{\s*(.+?)\s*\}\}")
MISSING = "⟨?⟩"          # 字段缺失占位（U-1：别再渲染成空串留下语义黑洞）


class RemoteNotAllowed(Exception):
    """远程命令不可自动执行（如 ro 目标白名单外）——上层转为人工贴回。
    由 remote_runner 闭包抛出（target 信息在 repl 构造处，Session 不持有）。"""
    pass


def render(text, ctx):
    """把 {{expr}} 用受限求值器渲染。

    - 求值成功但为 None（字段在 ctx 里缺失）→ 显示 MISSING 占位，
      而非空串（四轮评审：空串会让"共 X 个"渲染成"共 　个"这类语义黑洞）。
    - 求值抛异常（语法/求值器问题）→ 保留原样 {{...}}，不崩。
    """
    if not isinstance(text, str):
        return text

    def repl(m):
        try:
            v = exprlang.evaluate(m.group(1), ctx)
            return MISSING if v is None else str(v)
        except Exception:
            return m.group(0)
    return _TEMPLATE.sub(repl, text)


class IO:
    """交互 or 脚本。脚本模式：answers[node_id] 提供该节点的人类输入。"""
    def __init__(self, answers=None, echo=True):
        self.answers = answers          # dict|None
        self.echo = echo
        self.transcript = []

    def _p(self, s=""):
        if self.echo:
            print(s)

    def paste(self, node, prompt):
        """check：粘贴命令输出（脚本模式取 answers[node]，交互模式读到 END）。"""
        self._p(prompt)
        if self.answers is not None:
            return self.answers.get(node, "")
        lines = []
        for line in sys.stdin:
            if line.strip() == "END":
                break
            lines.append(line.rstrip("\n"))
        return "\n".join(lines)

    def choose(self, node, prompt, options):
        self._p(prompt)
        for i, o in enumerate(options, 1):
            self._p(f"  {i}) {o['label']}")
        if self.answers is not None:
            a = str(self.answers.get(node, "1")).strip()
            idx = int(a) if a.isdigit() else next((i for i, o in enumerate(options, 1) if o["label"] == a), 1)
        else:
            idx = int(input("选择 [1-%d]: " % len(options)) or "1")
        idx = max(1, min(idx, len(options)))
        return idx - 1

    def confirm(self, node, prompt):
        self._p(prompt)
        if self.answers is not None:
            return str(self.answers.get(node, "n")).strip().lower() in ("y", "yes", "是")
        return input("[y/N]: ").strip().lower() in ("y", "yes")

    def attest_intake(self, node, prompt):
        """V-3 一键认证：返回预填字段的补充 dict，或 None 表示跳过。

        脚本模式：answers[node+':attest'] 为 dict（补 os_family/scale/attestor）或缺省=跳过。
        交互模式：先 y/N，y 则只问 os-family 与规模两个分桶。
        """
        if self.answers is not None:
            info = self.answers.get(node + ":attest")
            return info if isinstance(info, dict) else None
        self._p(f"\n{prompt} [y/N]")
        if input("> ").strip().lower() not in ("y", "yes", "是"):
            return None
        return {"os_family": input("  os 家族 [linux]: ").strip() or "linux",
                "scale": input("  规模(主机数) [1]: ").strip() or "1",
                "attestor": input("  你的标识 (gh:user) [anonymous]: ").strip() or "anonymous"}

    def action_decision(self, node, prompt):
        """变更节点决策（U-1：不再是 y/n 二选一）。返回 proceed|skip|escalate|quit。

        脚本兼容：答案 y/yes/是→proceed，n/no→escalate（沿用旧 demo），
        另接受显式 proceed/skip/escalate/quit 或序号 1-4。
        """
        self._p(prompt)
        opts = ["确认，我将亲自执行此变更", "跳过此步（不执行，继续后续排查）",
                "升级人工处理", "退出会话"]
        keys = ["proceed", "skip", "escalate", "quit"]
        if self.answers is not None:
            a = str(self.answers.get(node, "n")).strip().lower()
            if a in ("y", "yes", "是"):
                return "proceed"
            if a in ("n", "no", "否"):
                return "escalate"
            if a in keys:
                return a
            if a.isdigit() and 1 <= int(a) <= 4:
                return keys[int(a) - 1]
            return "escalate"
        for i, o in enumerate(opts, 1):
            self._p(f"  {i}) {o}")
        raw = input("选择 [1-4，默认 3 升级]: ").strip() or "3"
        idx = int(raw) if raw.isdigit() and 1 <= int(raw) <= 4 else 3
        return keys[idx - 1]


class Session:
    def __init__(self, skill_path, params=None, mode="guided", io=None, sid="sess",
                 remote_runner=None):
        self.skill = yaml.safe_load(pathlib.Path(skill_path).read_text(encoding="utf-8"))
        self.nodes = {n["id"]: n for n in self.skill["tree"]["nodes"]}
        self.entry = self.skill["tree"]["entry"]
        self.mode = mode              # guided | real
        self.io = io or IO()
        self.sid = sid
        self.remote_runner = remote_runner  # 远程 gate 执行器（remote 模式时传入）
        self.ctx = dict(params or {})
        self.ctx["sid"] = sid
        self.path = []
        self.audit = []
        self.outcome = None

    # ---- 审计 ----
    @staticmethod
    def _summ(text, limit=200):
        """粘贴输出摘要：压平空白、截断（审计留证据但不灌爆）。"""
        s = " ".join((text or "").split())
        return s if len(s) <= limit else s[:limit] + "…"

    def _log(self, node, ntype, **kw):
        self.audit.append({"node": node, "type": ntype, **kw})

    def _sess_dir(self):
        d = pathlib.Path(os.environ.get("OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom")) / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_audit(self):
        f = self._sess_dir() / f"{self.sid}.jsonl"
        with f.open("w", encoding="utf-8") as fh:
            for rec in self.audit:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return f

    def _write_state(self, node):
        """断点续跑状态（U-1）：每步落 ctx + 下一节点 + 已走路径。

        只对真实节点存档；控制/终止 token（__quit__/escalate/done/…）不覆盖，
        这样 quit 后 state 仍指向那个待执行的真实节点，resume 从它重跑。
        """
        if node not in self.nodes:
            return
        f = self._sess_dir() / f"{self.sid}.state.json"
        try:
            f.write_text(json.dumps(
                {"skill_id": self.skill["metadata"]["id"], "node": node,
                 "ctx": self.ctx, "path": self.path, "mode": self.mode,
                 "audit": self.audit}, ensure_ascii=False), encoding="utf-8")
        except TypeError:
            pass          # ctx 含不可序列化对象时跳过（不影响主流程）

    def load_state(self):
        """从 state.json 恢复；返回续跑起点节点，无则 None。"""
        f = self._sess_dir() / f"{self.sid}.state.json"
        if not f.exists():
            return None
        st = json.loads(f.read_text(encoding="utf-8"))
        self.ctx = st.get("ctx", self.ctx)
        self.path = st.get("path", [])
        self.mode = st.get("mode", self.mode)
        self.audit = st.get("audit", [])
        return st.get("node")

    # ---- 渲染 ----
    def r(self, text):
        return render(text, self.ctx)

    def _cmd_for(self, run):
        """取 linux/kubectl/... 任一平台命令，渲染模板。"""
        if not isinstance(run, dict):
            return ""
        for _, c in run.items():
            return self.r(c)
        return ""

    def _cautions(self, n):
        for c in n.get("cautions", []) or []:
            self.io._p(f"  ⚠ {self.r(c)}")

    def _parse_into_ctx(self, node, stdout):
        pfn = parsers.get_parser(node["parser"]) if node.get("parser") else None
        out = pfn(stdout) if pfn else run_sim._default_parse(stdout)
        if not isinstance(out, dict):
            out = {"rows": out}
        # 节点标量并入 output.*（§7.6c）与裸命名空间
        scalar_ns = {k: v for k, v in out.items() if k not in ("rows", "lines")}
        self.ctx.update(out)
        self.ctx["output"] = {**self.ctx.get("output", {}), **scalar_ns,
                              **(out.get("output") if isinstance(out.get("output"), dict) else {})}
        self.ctx.setdefault("lines", stdout.splitlines())

    def _eval_branch(self, n):
        for br in n.get("branch", []):
            try:
                if exprlang._truthy(exprlang.evaluate(br["when"], self.ctx)):
                    return br["goto"]
            except exprlang.EvalError:
                pass
        return n.get("otherwise", "escalate")

    # ---- 节点处理 ----
    def _do_check(self, n):
        self.io._p(f"\n━━ [排查] {self.r(n.get('title',''))} ━━")
        self._cautions(n)
        cmd = self._cmd_for(n.get("run"))
        if self.remote_runner:
            # 远程模式：走 gate 自动执行（ro 目标白名单路由在 gate 侧；
            # 名单外命令会收到带降级提示的 GateError，这里转贴回）
            self.io._p(f"▶ 远程执行（{self.sid}）：{cmd}")
            try:
                stdout = self.remote_runner(cmd, self.ctx)
            except RemoteNotAllowed as e:
                stdout = self.io.paste(n["id"], f"▶ {e}\n  请人工执行并粘贴输出（END 结束）：\n  $ {cmd}")
            except Exception as e:
                stdout = ""
                self.io._p(f"  远程命令异常：{e}")
        elif self.mode == "real" and run_sim._is_readonly(cmd):
            import subprocess
            self.io._p(f"▶ 自动执行(只读)：{cmd}")
            try:
                stdout = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=15).stdout
            except Exception as e:
                stdout = ""
                self.io._p(f"  命令异常：{e}")
        else:
            stdout = self.io.paste(n["id"], f"▶ 请执行并粘贴输出（END 结束）：\n  $ {cmd}")
        self._parse_into_ctx(n, stdout)
        nxt = self._eval_branch(n)
        self.io._p(f"→ 判读结果：转 {nxt}")
        self._log(n["id"], "check", cmd=cmd, next=nxt, output=self._summ(stdout))
        return nxt

    def _do_ask(self, n):
        self.io._p(f"\n━━ [选择] {self.r(n.get('title',''))} ━━")
        self.io._p(self.r(n["question"]))
        idx = self.io.choose(n["id"], "", n["options"])
        opt = n["options"][idx]
        if n.get("binds"):
            self.ctx[n["binds"]] = opt["label"]
        self._log(n["id"], "ask", chose=opt["label"], next=opt["goto"])
        return opt["goto"]

    def _do_action(self, n):
        risk = n.get("risk", "?")
        self.io._p(f"\n━━ [变更] {self.r(n.get('title',''))} ━━  风险: {risk}")
        pf = n.get("preflight")
        if pf:
            self.io._p("📋 变更简报（Pre-flight Brief）")
            self.io._p(f"  影响面: {self.r(pf.get('blast_radius',''))}")
            if pf.get("est_downtime"):
                self.io._p(f"  预估停机: {self.r(pf['est_downtime'])}")
            self.io._p("  执行中盯这些指标:")
            for w in pf.get("watch", []):
                self.io._p(f"    · {self._cmd_for(w.get('run'))}  → 期望: {self.r(w.get('expect',''))}")
            self.io._p("  什么情况立即中止:")
            for a in pf.get("abort_if", []):
                self.io._p(f"    · {self.r(a)}")
        rb = n.get("rollback", {})
        self.io._p("  ── 将要执行的命令（导航档：请你亲自执行，Agent 不代执行）──")
        self.io._p(f"    $ {self._cmd_for(n.get('run'))}")
        if rb.get("advisory"):
            self.io._p(f"  ── 回滚（人工指引，{rb.get('type')}）──  非可执行命令，见 cautions")
        else:
            self.io._p(f"  ── 回滚方案（{rb.get('type')}）──")
            if rb.get("type") == "snapshot" and rb.get("snapshot"):
                self.io._p(f"    先快照: $ {self._cmd_for(rb['snapshot'].get('run'))}")
            self.io._p(f"    $ {self._cmd_for(rb.get('run'))}")
        self._cautions(n)
        if n.get("human_only"):
            self.io._p("  ⛔ human_only：此步骤 Agent 任何档位都不执行，仅出指导。")
        decision = self.io.action_decision(n["id"], "需要审批。你的决定？")
        if decision != "proceed":
            self._log(n["id"], "action", risk=risk, decision=decision)
            if decision == "skip":
                nxt = n.get("goto", "escalate")
                self.io._p(f"→ 跳过此变更（未执行），继续：转 {nxt}。")
                return nxt
            if decision == "quit":
                self.io._p("→ 退出会话（进度已保存，可 --resume 续跑）。")
                return "__quit__"
            self.io._p("→ 升级人工。")
            return "escalate"
        # verify 指导
        v = n.get("verify", {})
        vcmd = self._cmd_for(v.get("run"))
        stdout = self.io.paste(n["id"] + ":verify", f"执行完成后，粘贴 verify 输出判定结果（END 结束）：\n  $ {vcmd}")
        if v.get("parser"):
            self._parse_into_ctx({"parser": v["parser"]}, stdout)
        passed = False
        try:
            passed = exprlang._truthy(exprlang.evaluate(v.get("assert", "false"), self.ctx))
        except exprlang.EvalError:
            passed = False
        self._log(n["id"], "action", risk=risk, decision="proceed",
                  verify_passed=passed, verify_output=self._summ(stdout))
        if passed:
            self.io._p("→ verify 通过。")
            return n.get("goto", "escalate")
        self.io._p(f"→ verify 未过，on_fail={v.get('on_fail')}。")
        return "rollback_guide" if v.get("on_fail") == "rollback" else "escalate"

    def _do_terminal(self, n):
        kind = n["type"]
        icon = "✅" if kind == "done" else "⏫"
        self.io._p(f"\n━━ {icon} {'结论' if kind=='done' else '升级人工'} ━━")
        self.io._p(self.r(n.get("summary", "")))
        self.outcome = kind
        # 统一反馈：done/escalate 都问"有帮助吗"
        self.io._p("\n对这次诊断有帮助吗？ 👍y / 👎n")
        ans = ""
        if self.io.answers is not None:
            ans = self.io.paste(n["id"] + ":fb", "").strip()
        else:
            try:
                ans = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                ans = ""
        self._log(n["id"], "feedback", answer=ans)
        # y → 静默签名；n → 统一上报社区（done/escalate 一致）
        if ans.lower() in ("y", "yes", "是"):
            self._offer_attest_silent(n)
        else:
            self._report_issue(n)

    def _has_gh_token(self):
        p = pathlib.Path(os.environ.get("OPSAXIOM_HOME",
                     pathlib.Path.home() / ".opsaxiom")) / "gh_token"
        return p.exists() and p.read_text(encoding="utf-8").strip() != ""

    def _push_attest_silent(self, n):
        """提 attestation issue 到 registry（签名已落地，issue 仅告知社区）。"""
        import json as _j
        import subprocess as _sp
        home = pathlib.Path(os.environ.get("OPSAXIOM_HOME",
                         pathlib.Path.home() / ".opsaxiom"))
        token = (home / "gh_token").read_text(encoding="utf-8").strip()
        sid = self.skill["metadata"]["id"]
        title = "attest: " + sid + " resolved"
        body = ("automatic attestation.\n\n"
                "skill: " + sid + "\n"
                "version: " + str(self.skill["metadata"].get("version", "0.1.0")) + "\n"
                "outcome: resolved\n"
                "mode: navigator")
        data = _j.dumps({"title": title, "body": body,
                         "labels": ["attestation"]})
        try:
            _sp.run(
                ["curl", "-s", "-o", "/dev/null",
                 "-X", "POST",
                 "-H", "Authorization: Bearer " + token,
                 "-H", "Accept: application/vnd.github+json",
                 "-H", "User-Agent: OpsAxiom",
                 "-d", data,
                 "https://api.github.com/repos/hf4Academic/opsaxiom-registry/issues"],
                capture_output=True, text=True, timeout=15)
        except Exception:
            pass

    def _offer_attest_silent(self, n):
        """y 反馈后：静默生成签名 → 有 token 就推送 → 无 token 提醒 auth。"""
        mode = {"guided": "navigator", "real": "copilot"}.get(self.mode, "navigator")
        attest_bin = str(HERE / "bin" / "opsaxiom-attest")
        try:
            import subprocess as _sub
            r = _sub.run(
                [sys.executable, attest_bin,
                 "--skill", self.skill["metadata"]["id"],
                 "--skill-version", str(self.skill["metadata"].get("version", "0.1.0")),
                 "--outcome", "resolved", "--mode", mode,
                 "--os-family", "linux", "--scale", "1",
                 "--attestor", "anonymous"],
                capture_output=True, text=True, timeout=10)
            self._log(n["id"], "attest", ok=(r.returncode == 0))
        except Exception:
            pass

        # token 检查
        first_run_marker = pathlib.Path(os.environ.get("OPSAXIOM_HOME",
                         pathlib.Path.home() / ".opsaxiom")) / ".attest_asked"
        if self._has_gh_token():
            self._push_attest_silent(n)
            self.io._p("  ✅ 感谢您的使用")
        elif not first_run_marker.exists():
            self.io._p("  同步至社区可帮更多人完善排查经验，只需一次配置（约 1 分钟），"
                       "是否开始？ [y/n]")
            try:
                a = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                a = "n"
            if a in ("y", "yes", "是"):
                # TODO Batch 5：引导 auth 配置流程
                self.io._p("  请执行 auth 完成配置。")
            else:
                first_run_marker.write_text("")
                self.io._p("  ✅ 感谢您的使用"
                           "（签名未同步社区，可通过 auth 指令完成配置后自动同步）")
        else:
            self.io._p("  ✅ 感谢您的使用"
                       "（签名未同步社区，可通过 auth 指令完成配置后自动同步）")

    def _report_issue(self, n):
        """n 反馈后：统一上报社区（有 token 静默提，无 token 浏览器预填）。"""
        import json as _j
        import subprocess as _sp
        home = pathlib.Path(os.environ.get("OPSAXIOM_HOME",
                         pathlib.Path.home() / ".opsaxiom"))
        sid = self.skill["metadata"]["id"]
        title = "诊断无帮助：" + sid
        body = ('用户在诊断结束时反馈"无帮助"。\n\n'
                "skill: " + sid + "\n"
                "version: " + str(self.skill["metadata"].get("version", "0.1.0")) + "\n"
                "\n---\n请补充具体原因（结论错误 / 覆盖不足 / 其他）：")
        token = ""
        tp = home / "gh_token"
        if tp.exists():
            token = tp.read_text(encoding="utf-8").strip()
        if token:
            data = _j.dumps({"title": title, "body": body,
                             "labels": ["report:bug"]})
            try:
                _sp.run(
                    ["curl", "-s", "-o", "/dev/null",
                     "-X", "POST",
                     "-H", "Authorization: Bearer " + token,
                     "-H", "Accept: application/vnd.github+json",
                     "-H", "User-Agent: OpsAxiom",
                     "-d", data,
                     "https://api.github.com/repos/hf4Academic/opsaxiom-registry/issues"],
                    capture_output=True, text=True, timeout=15)
                self.io._p("  ✅ 感谢您的反馈，社区将查收并完善 skills 资产。")
                return
            except Exception:
                pass
        # 无 token 或失败 → 浏览器预填
        import urllib.parse
        import webbrowser
        import platform
        q = urllib.parse.quote
        url = ("https://github.com/hf4Academic/opsaxiom-registry/issues/new"
               "?title=" + q(title) + "&labels=" + q("report:bug")
               + "&body=" + q(body))
        webbrowser.open(url)
        self.io._p("  ✅ 已打开反馈页面，请确认后点击 Submit。感谢您的反馈。")

    def run(self, start=None):
        node, guard = (start or self.entry), 0
        if not self.path:
            self.path = [node]
        while guard < 80:
            guard += 1
            self._write_state(node)          # 每步落状态，供 --resume
            n = self.nodes.get(node)
            if n is None:
                if node == "__quit__":
                    self.outcome = "quit"
                    break
                if node == "rollback_guide":
                    self.io._p("请按上方回滚方案执行回滚，然后重新评估。")
                    self.outcome = "rolled_back"
                    break
                if node in ("escalate", "done"):
                    self.io._p(f"\n━━ {node} ━━")
                    self.outcome = node
                    break
                self.io._p(f"[异常] 未知节点 {node}")
                break
            t = n["type"]
            if t == "check":
                node = self._do_check(n)
            elif t == "ask":
                node = self._do_ask(n)
            elif t == "action":
                node = self._do_action(n)
            elif t in ("done", "escalate"):
                self._do_terminal(n)
                break
            else:
                break
            self.path.append(node)
        # 结束态：清理续跑状态；中途退出则保留
        f = self._write_audit()
        sf = self._sess_dir() / f"{self.sid}.state.json"
        if self.outcome in ("done", "escalate", "rolled_back") and sf.exists():
            sf.unlink()
        # meta.json 留存供 attest --from-session 预填（不随 state 清理）
        (self._sess_dir() / f"{self.sid}.meta.json").write_text(json.dumps(
            {"skill_id": self.skill["metadata"]["id"], "mode": self.mode,
             "path": self.path, "outcome": self.outcome}, ensure_ascii=False), encoding="utf-8")
        return {"path": self.path, "outcome": self.outcome, "audit_file": str(f)}
