"""
Terminal REPL —— OpsAxiom 默认交互入口（W-1，docs/08 §4.2a）。

心智模型：像跟老师傅说话，不像查手册。裸敲 `opsaxiom` 就进来，敲字（说人话）就有反应。
- 非命令输入 = 症状 → diagnose top-3（自然语言是一等公民）
- 纯数字 = 选上次候选 → 原地进导航档（复用 runtime.Session，同进程）
- 少量内置词（可选，不学也能用）：help/list/info/run/doctor/hub/record/resume/quit
- Ctrl-C 中断当前 Skill 回提示符；空闲再 Ctrl-C/quit 退出。无 TTY 不进 REPL。

REPL 不复制任何业务逻辑：diagnose/run/attest 全走既有模块。
"""
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "sim"))
import yaml            # noqa: E402
import diagnose        # noqa: E402
import runtime         # noqa: E402
import incident as I   # noqa: E402  交互 v2：取证式诊断
import sweep           # noqa: E402
import access          # noqa: E402  远程目标清单加载
import gate            # noqa: E402  远程执行门
import llm             # noqa: E402  可选 LLM 适配层（无模型时全走降级）
import run_sim         # noqa: E402  复用 _is_readonly

_BADGE = {"draft": "⚪草稿", "sim_verified": "🔵已验证",
          "field_verified": "🟢实地", "certified": "🟡认证"}
_BUILTINS = {"help", "?", "list", "info", "run", "doctor", "hub", "record",
             "skill", "resume", "quit", "exit", "q", "sweep", "report", "model",
             "sug", "auth", "overlay", "new", "edit",
             "search", "sync", "push", "fork", "promote"}


def _find_skill(skill_id):
    import diagnose
    idx = diagnose._SKILLS_CACHE
    if idx.is_dir():
        for p in idx.rglob("skill.yaml"):
            s = yaml.safe_load(p.read_text(encoding="utf-8"))
            if s.get("metadata", {}).get("id") == skill_id:
                return p, s
    # fallback: 个人 fork
    local = diagnose._SKILLS_LOCAL
    if local.is_dir():
        for p in local.rglob("skill.yaml"):
            s = yaml.safe_load(p.read_text(encoding="utf-8"))
            if s.get("metadata", {}).get("id") == skill_id:
                return p, s
    return None, None


def _home():
    return pathlib.Path(os.environ.get("OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom"))


class Repl:
    def __init__(self):
        self.idx = diagnose.load_index()
        self.last_hits = []          # 上次 diagnose 的候选（供数字选择兜底）
        self.last_incident = None    # 上次陈述建立的 incident（供 sweep/report）
        self.last_incident_swept = False   # 当前 incident 是否已取证
        self.last_unmatched = ""     # 上次未命中的原始输入（供 sug 预填）
        self.target_mode = None      # 当前诊断目标：local/manual/remote
        self.remote_target_name = None   # remote 模式下所选的目标名（targets.yaml key）
        self.remote_target_os = None     # remote 模式下所选目标的 os 字段
        self.running = True
        try:
            self.model_cfg = llm.load_config()   # None = 无模型，全走降级
        except Exception:
            self.model_cfg = None

    # ---------- 展示 ----------
    def _maybe_sync(self):
        """距离上次 hub sync 超过 24h → 静默同步。失败不阻塞。"""
        marker = _home() / ".last_sync"
        try:
            if marker.exists():
                ago = time.time() - marker.stat().st_mtime
                if ago < 86400:          # 24h
                    return
            import subprocess
            venv_py = sys.executable
            bin_dir = pathlib.Path(venv_py).parent
            opsaxiom_bin = str(ROOT / "tools" / "bin" / "opsaxiom")
            subprocess.run(
                [str(bin_dir / "python3"), opsaxiom_bin, "hub", "sync"],
                capture_output=True, timeout=30)
            marker.write_text("")
        except Exception:
            pass

    def _welcome(self):
        verified = sum(1 for s in self.idx if s["maturity"] != "draft")
        print(f"OpsAxiom v0.1 · {len(self.idx)} 个 Skill（{verified} 已验证）")
        print("欢迎使用 OpsAxiom，您可以通过我：")
        print("1. 诊断运维问题：")
        print("     <直接描述症状>  匹配 skills 并执行")
        print("     run <id>         执行指定 skill")
        print("2. 浏览 skills 资产：")
        print("     list [域]        查看全部 skills，可后缀指定域")
        print("     search <关键词>   搜索 skills")
        print("     info <id>        查看指定 skill 详情")
        print("     sync             手动同步社区 Skill（系统 24h 自动）")
        print("3. 配置参数信息：")
        print("     doctor           环境自检")
        print("     overlay <id>     为指定 skill 生成个人叠加层")
        print("     model            配置大模型")
        print("     target           接入设备管理")
        print("     cred             本地凭证管理")
        print("     auth             GitHub 个人 token（社区贡献用）")
        print("4. 贡献社区资产：")
        print("     sug              异常提报（向社区提 issue）")
        print("     new              从零创建 skill 草稿")
        print("     fork             从已有 skill 派生修改")
        print("     edit             编辑草稿")
        print("     promote          本地仿真验证")
        print("     push             推送 PR 至社区")
        if not self.idx:
            print("  🟡 尚未同步 Skill 库，请先执行 hub sync 获取可用 Skill。")

    def _show_hits(self, hits):
        if not hits:
            # registry 缓存为空（可能没同步过）→ 先指路 sync，再谈匹配
            if not diagnose._SKILLS_CACHE.is_dir() or not any(
                    diagnose._SKILLS_CACHE.rglob("skill.yaml")):
                print("  ⚠ 本机 Skill 库为空，请先执行 hub sync 拉取社区 Skill。")
                return
            print("  ⚠ 库内未找到相关 Skill。您可以：")
            print("    · 重新描述报错/现象，尝试再次匹配")
            print("    · 上报技能缺失，输入：sug \"您遇到的问题\"")
            print("    · 查看并手动检索全部 Skills，输入：list")
            return
        print(f"  找到 {len(hits)} 个匹配：")
        for i, (_, sk) in enumerate(hits, 1):
            print(f"  {i}) [{_BADGE.get(sk['maturity'], sk['maturity'])}] {sk['name']}"
                  f"       {sk['id']}")
            if sk.get("symptom"):
                print(f"       {sk['symptom']}")
        print("  → 输入序号进入排查，或继续描述别的问题。")

    # ---------- 内置词 ----------
    def _help(self):
        self._welcome()

    def _list(self, dom=None):
        rows = [s for s in self.idx if not dom or s["l1"] == dom]
        rows.sort(key=lambda s: s["id"])
        cur = None
        for s in rows:
            if s["l1"] != cur:
                cur = s["l1"]
                print(f"\n  {cur}")
            print(f"    [{_BADGE.get(s['maturity'], s['maturity'])}] {s['id']}  —— {s['name']}")
        print()

    def _info(self, sid):
        p, s = _find_skill(sid)
        if not p:
            print(f"  没有这个 Skill：{sid}")
            return
        m = s["metadata"]
        nodes = s.get("tree", {}).get("nodes", [])
        adir = p.parent / "attestations"
        n_att = len(list(adir.glob("*.yaml"))) if adir.is_dir() else 0
        print(f"  {m['name']}  [{_BADGE.get(m['maturity'], m['maturity'])}]")
        print(f"    id: {m['id']} · 分类: {m['taxonomy']} · v{m['version']}")
        print(f"    决策树: {len(nodes)} 个节点 · 实地验证记录: {n_att}")
        cauts = [c for n in nodes for c in (n.get("cautions") or [])][:3]
        for c in cauts:
            print(f"    ⚠ {c[:88]}")
        # fork 差异
        derived = m.get("derived_from", "")
        if derived:
            base_id = derived.split("@")[0]
            bp, base = _find_skill(base_id)
            if bp and base:
                base_nodes = {n["id"]: n for n in base.get("tree", {}).get("nodes", [])}
                cur_nodes = {n["id"]: n for n in nodes}
                added = set(cur_nodes) - set(base_nodes)
                same = [nid for nid in cur_nodes if nid in base_nodes
                        and cur_nodes[nid] != base_nodes[nid]]
                if added or same:
                    print(f"    相对 {base_id} 的差异：")
                    for nid in sorted(added):
                        print(f"      + 新增节点：{nid}")
                    for nid in sorted(same)[:5]:
                        print(f"      ~ 修改节点：{nid}")
        # overlay 失配
        ov_path = _home() / "overlays" / (m["id"] + ".yaml")
        if ov_path.exists():
            try:
                ov = __import__("yaml").safe_load(ov_path.read_text(encoding="utf-8"))
                ov_notes = ov.get("notes", {})
                unmatched = [nid for nid in ov_notes if nid != "_global"
                             and nid not in {n["id"] for n in nodes}]
                if unmatched:
                    print(f"    overlay 失效注记：{unmatched}")
            except Exception:
                pass
        print(f"  → run {m['id']} 开始排查")

    def _run(self, skill_id, resume=False, session_id=None, params=None):
        p, s = _find_skill(skill_id)
        if not p:
            print(f"  没有这个 Skill：{skill_id}")
            return
        if self.target_mode == "local" and not self._os_ok_for_local(s):
            import platform
            local_os = {"Darwin": "macOS", "Linux": "Linux"}.get(
                platform.system(), platform.system())
            declared = [p.get("os") for p in s.get("metadata", {}).get("platforms", []) or []
                        if p.get("os")]
            declared_str = "/".join(declared) if declared else "特定平台"
            print(f"  ⚠ 该 Skill 声明适用于 {declared_str}，本机是 {local_os}，无法在本机诊断。")
            print("  如需诊断其他环境的机器，请切换目标为非本机（手动/远程）。")
            return
        io = runtime.IO(answers=None, echo=True)
        # F-14：resume 必须用状态文件的真实 sid（可能来自子命令/自定义 --sid），
        # 不能由 skill_id 重新派生——否则刚列出的会话选中后找不到状态
        session_id = session_id or skill_id.replace(".", "_") + "-repl"
        params = self._collect_params([s], params)   # 补必填参数

        # 远程模式：走 gate 远程执行（协驾档）
        if self.target_mode == "remote":
            tname = self.remote_target_name
            if not tname:
                print("  没有选中远程目标。"); return
            # 授权检查
            if not sweep.is_trusted(tname):
                print(f"\n  {tname} 上可自动执行只读取证命令（绝不含写操作）。")
                try:
                    ans = input("  授权在目标上自动取证？一次性，30 天后到期 [y/N]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    ans = ""
                if ans.lower() in ("y", "yes", "是"):
                    sweep.grant_trust(tname, ttl_days=30, scope="readonly")
                else:
                    print("  未授权，无法自动执行。请使用 target grant 指令授权后重试。"); return
            remote_runner = lambda cmd, pr=None: gate.run_remote(tname, cmd, params=pr)
            mode_label = "协驾档（远程自动执行）"
        elif self.target_mode == "manual":
            remote_runner = None
            mode_label = "导航档（你敲命令，Agent 只出方案与判读）"
        else:
            remote_runner = None
            mode_label = "导航档（你敲命令，Agent 只出方案与判读）"

        sess = runtime.Session(p, params=params, mode="guided", io=io, sid=session_id,
                               remote_runner=remote_runner)
        start = None
        if resume:
            start = sess.load_state()
            if not start:
                print("  没有可续跑的进度。")
                return
        print(f"\n进入：{s['metadata']['name']}（{mode_label}）")
        try:
            res = sess.run(start=start)
        except KeyboardInterrupt:
            print("\n  ⏸ 已中断本次排查（进度已存）。输入 resume 可续跑，或继续描述别的问题。")
            return
        if res["outcome"] == "quit":
            print("  已退出本次排查（进度已存，输入 resume 续跑）。")

    def _resume_pick(self):
        sd = _home() / "sessions"
        states = sorted(sd.glob("*.state.json")) if sd.is_dir() else []
        if not states:
            print("  没有可续跑的排查。")
            return
        import json
        print("  可续跑的排查：")
        metas = []
        for i, st in enumerate(states, 1):
            d = json.loads(st.read_text(encoding="utf-8"))
            d["_sid"] = st.name[: -len(".state.json")]   # 状态文件名 = 真实 sid
            metas.append(d)
            print(f"  {i}) {d.get('skill_id')}  停在节点 {d.get('node')}")
        sel = input("  选择序号续跑（回车取消）: ").strip()
        if sel.isdigit() and 1 <= int(sel) <= len(metas):
            m = metas[int(sel) - 1]
            self._run(m["skill_id"], resume=True, session_id=m["_sid"])

    # ---------- 交互 v2：陈述 → 取证 → 卷宗 ----------
    @staticmethod
    def _parse_symptom(line):
        """从陈述里剥出结尾的 k=v 参数（如 `磁盘满 mount=/data`）。
        Z-5 的 LLM intake 会把这一步自动化（从自然语言抽实体）；无模型时靠这个显式兜底。"""
        toks = line.split()
        params, rest = {}, []
        for t in toks:
            if "=" in t and t.split("=")[0].isidentifier():
                k, v = t.split("=", 1)
                params[k] = v
            else:
                rest.append(t)
        return " ".join(rest), params

    def _auth(self):
        """TODO Batch 5：引导用户配置 GitHub token。"""
        token_file = _home() / "gh_token"
        if token_file.exists() and token_file.read_text(encoding="utf-8").strip():
            print("  ✅ GitHub token 已配置。")
        else:
            print("  尚未配置 GitHub token。")
            print("  前往 https://github.com/settings/tokens/new")
            print("  生成 Classic token，勾选 public_repo 权限（仅公开仓库读写），")
            print("  将 token 粘贴至下方：")
            try:
                tok = input("token> ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if tok:
                token_file.write_text(tok, encoding="utf-8")
                token_file.chmod(0o600)
                print("  ✅ token 已保存（0600 权限）。后续社区反馈将自动同步。")

    def _sug(self, body):
        """上报未覆盖场景：有 token → 静默提 issue；无 token → 浏览器预填。"""
        body = body.strip() or self.last_unmatched or ""
        if body:
            print(f"  （已预填您的输入：{body[:60]}…）")
            ans = input("  您也可以自定义引号中的内容进行上报，直接回车确认: ").strip()
            if ans:
                body = ans
        else:
            body = input("  请输入遇到的问题描述: ").strip()
        if not body:
            print("  已取消。")
            return
        title = "技能缺失：" + body[:80]
        import platform
        body_text = "自动采集自用户反馈。\n\n---\n环境：" + platform.system()
        # 有 token → 静默用 GitHub API 提 issue
        token = self._read_token()
        if token:
            try:
                ok = self._github_create_issue(title, body_text, ["report:missing"],
                                               token=token)
                if ok:
                    print("  ✅ 感谢您的反馈，社区将查收并完善 skills 资产。")
                    return
            except Exception:
                pass
            # API 失败降级为浏览器
        import urllib.parse
        q = urllib.parse.quote
        url = ("https://github.com/hf4Academic/opsaxiom-registry/issues/new"
               "?title=" + q(title) + "&labels=" + q("report:missing")
               + "&body=" + q(body_text))
        import webbrowser
        webbrowser.open(url)
        print("  ✅ 已打开反馈页面，请确认后点击 Submit。感谢您的反馈，社区将查收并完善 skills 资产。")

    def _overlay_new(self, skill_id):
        """交互填空生成个人叠加层。"""
        import yaml as _yaml
        # 确认 Skill 存在
        sp, s = _find_skill(skill_id)
        if not sp:
            print(f"  没有这个 Skill：{skill_id}")
            return
        # 已有 overlay → 确认覆盖
        ov_path = _home() / "overlays" / (skill_id + ".yaml")
        if ov_path.exists():
            ans = input("  已存在 overlay，覆盖？[y/N]: ").strip()
            if ans.lower() not in ("y", "yes", "是"):
                print("  已取消。")
                return
        ov = {"overlay": "skill-overlay/v0.1",
              "base": skill_id,
              "base_version": s["metadata"].get("version", "0.1.0")}
        # 参数
        print("  填本地参数（key=value，多个逗号分隔，回车跳过）")
        p = input("> ").strip()
        if p:
            params = {}
            for kv in p.split(","):
                kv = kv.strip()
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    params[k.strip()] = v.strip()
            if params:
                ov["params"] = params
        # 链接
        print("  贴内部链接（名称 URL，一行一条，END 结束）")
        links_input = self._read_until_end()
        if links_input.strip():
            ov["notes"] = {"_global": {"links": []}}
            for line in links_input.strip().split("\n"):
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    ov["notes"]["_global"]["links"].append(
                        {"name": parts[0], "url": parts[1]})
        # 注记
        print("  追加提醒（节点ID 内容，如 check_inode: 我家log-es-3盘最小，回车跳过）")
        caution = input("> ").strip()
        if caution and ":" in caution:
            nid, text = caution.split(":", 1)
            ov.setdefault("notes", {})
            ov["notes"].setdefault(nid.strip(), {})
            ov["notes"][nid.strip()]["caution"] = text.strip()
        ov_path.parent.mkdir(parents=True, exist_ok=True)
        ov_path.write_text(_yaml.safe_dump(ov, allow_unicode=True, sort_keys=False),
                           encoding="utf-8")
        print(f"  ✅ overlay 已生成：{ov_path}")

    @staticmethod
    def _overlay_show(skill_id):
        ov_path = _home() / "overlays" / (skill_id + ".yaml")
        if not ov_path.exists():
            print(f"  没有该 Skill 的 overlay：{skill_id}")
            return
        print(ov_path.read_text(encoding="utf-8"))

    @staticmethod
    def _read_token():
        p = _home() / "gh_token"
        if p.exists():
            tok = p.read_text(encoding="utf-8").strip()
            if tok:
                return tok
        return ""

    @staticmethod
    def _github_create_issue(title, body, labels, token):
        import json as _j
        import subprocess as _sp
        data = _j.dumps({"title": title, "body": body, "labels": labels})
        try:
            r = _sp.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "-X", "POST",
                 "-H", "Authorization: Bearer " + token,
                 "-H", "Accept: application/vnd.github+json",
                 "-H", "User-Agent: OpsAxiom",
                 "-d", data,
                 "https://api.github.com/repos/hf4Academic/opsaxiom-registry/issues"],
                capture_output=True, text=True, timeout=15)
            return r.stdout.strip() == "201"
        except Exception:
            return False

    def _skill_new(self):
        """从零创建 Skill：5 问 → LLM 生成。需先配置模型。"""
        if self.model_cfg is None:
            self._model_required(); return
        import yaml as _yaml
        print("  故障现象 [一行描述]:")
        symptom = input("> ").strip()
        if not symptom:
            print("  已取消。"); return
        print("  你排查时敲的检查命令（多条，END 结束）:")
        cmds = self._read_until_end().strip()
        print("  判断标准（什么值说明确实有问题）:")
        criterion = input("> ").strip()
        print("  排查出的根因:")
        root_cause = input("> ").strip()
        print('  建议的修复方式（若不需修复输"无"）:')
        fix = input("> ").strip()
        print("  踩过的坑（选填，回车跳过）:")
        caution = input("> ").strip()

        draft_yaml = self._llm_gen_skill(symptom, cmds, criterion,
                                         root_cause, fix, caution)
        if draft_yaml is None:
            print("  ❌ LLM 生成失败，请检查模型配置后重试。"); return

        name_default = symptom[:40]
        name = input(f"  Skill 名称 [{name_default}]: ").strip() or name_default
        att = input("  你的标识 [anonymous]: ").strip() or "anonymous"
        self._write_draft(draft_yaml, name, att)

    def _skill_edit(self, skill_id):
        """LLM 辅助编辑已有 Skill。需先配置模型。"""
        if self.model_cfg is None:
            self._model_required(); return
        skill_id = skill_id.strip()
        if not skill_id:
            print("  用法：edit <skill-id>"); return
        sp, s = _find_skill(skill_id)
        if not sp:
            print(f"  没有这个 Skill：{skill_id}"); return
        import yaml as _yaml
        cur = _yaml.safe_load(sp.read_text(encoding="utf-8"))
        print(f"  当前 Skill：{cur['metadata'].get('name','')}")
        print("  你想怎么改？（改阈值 / 加分支 / 补场景 / ...）:")
        desc = input("> ").strip()
        if not desc:
            print("  已取消。"); return
        draft_yaml = self._llm_edit_skill(cur, desc)
        if draft_yaml is None:
            print("  ❌ LLM 生成失败，请检查模型配置后重试。"); return
        self._write_draft(draft_yaml, cur["metadata"]["name"],
                          cur["metadata"].get("authors", ["anonymous"])[0])
    def _promote(self, skill_id):
        if not skill_id:
            print("  用法：promote <skill-id>"); return
        sp, s = _find_skill(skill_id)
        if not sp:
            print(f"  没有这个 Skill：{skill_id}"); return
        import promote as _p
        _p.promote(str(sp))

    @staticmethod
    def _model_required():
        print("  ⚠ 需要先配置大模型才能生成 Skill。\n")
        print("  配置方法：")
        print("  · model use ollama    （接入本地 Ollama）")
        print("  · model use remote    （接入 OpenAI 兼容远程 API）")
        print("  · model use pi        （pi 多 provider 网关）")
        print("  · model show          （查看当前配置与可用后端）")
        print("\n  配置完成后重试 new。")
    
    @staticmethod
    def _write_draft(draft_yaml, name, attestor):
        import yaml as _yaml
        try:
            s = _yaml.safe_load(draft_yaml)
        except Exception:
            print("  ❌ 无法解析生成的 YAML，请检查后重试。")
            return
        meta = s.get("metadata", {})
        tax = meta.get("taxonomy", "host/other")
        slug = tax.replace("/", ".")
        draft_dir = ROOT / "skills-drafts" / slug
        draft_dir.mkdir(parents=True, exist_ok=True)
        # 拼 ID：tax + attestor + 版本
        vid = 1
        while (draft_dir / f"{slug}-{attestor}-{vid}").exists() or \
              (draft_dir / f"{slug}-{attestor}-{vid}.yaml").exists():
            vid += 1
        meta["name"] = name
        meta["id"] = f"{slug}-{attestor}-{vid}"
        meta["maturity"] = "draft"
        meta.setdefault("authors", []).append(attestor)
        s["metadata"] = meta
        path = draft_dir / "skill.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_yaml.safe_dump(s, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
        print(f"  ✅ 草稿已生成：{path}")

    def _llm_gen_skill(self, symptom, cmds, criterion, root_cause, fix, caution):
        """LLM 生成 Skill YAML。失败返回 None。"""
        prompt = self._build_skill_prompt(symptom, cmds, criterion,
                                          root_cause, fix, caution)
        try:
            resp = llm.backend_call(self.model_cfg, prompt, "")
            # 提取 YAML 块
            if "```yaml" in resp:
                resp = resp.split("```yaml")[1].split("```")[0]
            elif "```" in resp:
                resp = resp.split("```")[1].split("```")[0]
            return resp.strip()
        except Exception:
            return None

    def _llm_edit_skill(self, cur_skill, description):
        """LLM 编辑 Skill，返回新 YAML 或 None。"""
        import yaml as _yaml
        cur_yaml = _yaml.safe_dump(cur_skill, allow_unicode=True, sort_keys=False)
        prompt = ("根据以下描述修改 Skill YAML。只输出修改后的完整 YAML，不要解释。\n\n"
                  "当前 Skill YAML：\n```yaml\n" + cur_yaml + "\n```\n\n"
                  "修改要求：" + description + "\n\n"
                  "规则：改写后的 Skill 必须仍符合 schema；"
                  "branch 中的 when 表达式合法；"
                  "每个 action 必须有 rollback；"
                  "参数用 {{param}} 模板；"
                  "cautions 写具体可操作的提醒。")
        try:
            resp = llm.backend_call(self.model_cfg, prompt, "")
            if "```yaml" in resp:
                resp = resp.split("```yaml")[1].split("```")[0]
            elif "```" in resp:
                resp = resp.split("```")[1].split("```")[0]
            return resp.strip()
        except Exception:
            return None

    def _prompt_paste_skill(self, symptom, cmds, criterion, root_cause, fix, caution):
        prompt = self._build_skill_prompt(symptom, cmds, criterion,
                                          root_cause, fix, caution)
        print("\n  未配置模型。请复制以下 prompt 到任意大模型对话中，将生成的 YAML 贴回（END 结束）：")
        print("  ┌─────────────────────────────")
        for line in prompt.split("\n")[:8]:
            print(f"  │ {line}")
        print("  │ ...")
        print("  └─────────────────────────────")
        print("  将生成的 YAML 贴回，单独一行 END 结束：")
        lines = self._read_until_end()
        if lines.strip():
            # 可能包含 ```yaml 标记
            text = lines.strip()
            if "```yaml" in text:
                text = text.split("```yaml")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            return text.strip()
        return None


    def _build_skill_prompt(self, symptom, cmds, criterion, root_cause, fix, caution):
        has_fix = fix and fix.strip() and fix.strip() != "无"
        kind = "Hybrid" if has_fix else "Diagnostic"
        # 金标准参考（精简版，展示 schema 全要素）
        reference = (
            'apiVersion: skill/v0.1\nkind: Hybrid\nmetadata:\n'
            '  id: host.storage.capacity.disk-full\n'
            '  name: 磁盘空间耗尽排查与处置\n'
            '  taxonomy: host/storage/capacity/disk-full\n'
            '  version: 0.1.0\n  maturity: draft\n'
            '  platforms: [{os: linux}]\n'
            '  params: [{name: mount, source: alert, desc: 告警指向的挂载点}]\n'
            'requirements: {capability_level: high_risk_write, connectors: [ssh]}\n'
            'tree:\n  entry: locate_mount\n  nodes:\n'
            '  - id: locate_mount\n    type: check\n'
            '    run: {linux: "df -B1 --output=pcent {{mount}}"}\n'
            '    parser: table/df-v1\n'
            '    branch: [{when: "rows[0].pcent >= 90", goto: check_inode},\n'
            '             {when: "rows[0].pcent < 90", goto: false_alarm}]\n'
            '    otherwise: escalate\n'
            '    cautions: ["先确认监控与 df 一致，告警可能来自采集延迟"]\n'
            '  - id: check_inode\n    type: check\n'
            '    run: {linux: "df -i --output=ipcent {{mount}}"}\n'
            '    branch: [{when: "rows[0].ipcent >= 95", goto: inode_exhaustion}]\n'
            '    otherwise: escalate\n'
            '  - id: false_alarm\n    type: done\n'
            '    summary: "当前使用率已低于告警阈值，建议观察"\n'
            '  - id: inode_exhaustion\n    type: check ...\n'
            '  - id: escalate\n    type: escalate\n'
            '    summary: "已采集的事实与走过的路径将打包升级"\n'
            'feedback: {ask: "磁盘问题解决了吗？"}\n'
        )
        prompt = (
            "生成一份符合 OpsAxiom Skill Schema (apiVersion: skill/v0.1) 的 YAML。"
            "只输出 YAML，不要解释。\n\n"
            "参考示例（金标准 Skill 的格式与要素）：\n```yaml\n"
            + reference + "\n```\n\n"
            "故障现象：" + symptom + "\n"
            "排查命令：" + cmds + "\n"
            "判断标准：" + criterion + "\n"
            "根因：" + root_cause + "\n"
            "修复方式：" + fix + "\n"
            "踩过的坑：" + caution + "\n\n"
            "要求：\n"
            f"- kind: {kind}\n"
            "- 按示例格式：metadata.name/taxonomy 从现象推断，maturity:draft\n"
            "- 每个 check 节点：run(linux命令)、parser(如有)、"
            "branch(when条件+goto)、otherwise:escalate、至少一条具体cautions\n"
            "- when 用 rows[0].field>=value / count(rows)>0 等合法表达式\n"
            "- 如有修复(" + ("是" if has_fix else "否") + ")：加action节点(risk/preflight/"
            "run/rollback/verify/on_fail)，rollback用inverse或service_restore\n"
            "- 参数用{{param}}模板\n"
            "- feedback.ask 写一句问用户问题是否解决\n"
        )
        return prompt

    def _show_hypotheses(self, inc):
        print(f"  假设 {len(inc.hyps)} 个（按相关度）：")
        for i, h in enumerate(inc.hyps, 1):
            print(f"  {i}) [{h.badge}] {h.name}       {h.meta['id']}")
        print("  → 输入序号进入对应 Skill 逐步排查；回车则批量取证。")

    def _llm_prefill(self, symptom, params):
        """有模型则从自然语言预填 params（显式 k=v 优先）；无模型原样返回。R11/T-3 由 llm 层保证。"""
        if self.model_cfg is None:
            return params
        r = llm.intake(symptom, config=self.model_cfg)
        prefilled = {k: v for k, v in r.get("params", {}).items() if k not in params}
        if prefilled:
            shown = ", ".join(f"{k}={v}" for k, v in prefilled.items())
            print(f"  （从你的描述预填：{shown}——回车确认，或输 k=v 覆盖）")
        return {**prefilled, **params}

    def _intake(self, line):
        """陈述入口：建 incident、列假设。交互态自动接一键取证；非 TTY 只列假设（不阻塞）。"""
        self.target_mode = self._confirm_target()
        # remote 模式：选具体设备
        if self.target_mode == "remote":
            tname = self._pick_remote_target()
            if tname is None:             # targets.yaml 为空，退回目标确认
                self.target_mode = None; return
            self.remote_target_name = tname
            targets = access.load_targets()
            self.remote_target_os = targets[tname].get("os") if tname in targets else None
        symptom, params = self._parse_symptom(line)
        params = self._llm_prefill(symptom, params)
        self.last_hits = diagnose.match(symptom, idx=self.idx, top=3)
        if not self.last_hits:
            self.last_unmatched = line
            self._show_hits(self.last_hits)
            return
        skills = []
        filtered_hits = []
        for sc, e in self.last_hits:
            _, s = I.load_skill_by_id(e["id"])
            if s and self._os_ok_for_target(s):
                skills.append(s)
                filtered_hits.append((sc, e))
        self.last_hits = filtered_hits
        if not skills:
            self.last_unmatched = line
            if self.target_mode == "local":
                print("  ⚠ 匹配到的 Skill 都是 Linux 专属，本机无法诊断。")
                print("  如需诊断 Linux 服务器，请重新输入并切换目标为非本机（2手动 / 3远程）。")
            elif self.target_mode == "remote":
                t_os = self.remote_target_os or "未声明"
                print(f"  ⚠ 匹配到的 Skill 都不适用目标 {self.remote_target_name}（os:{t_os}）。")
                print("  如需诊断其他平台，请重新输入并切换目标，或在 targets.yaml 中修正 os 字段。")
            else:
                self._show_hits([])
            return
        # 确定 incident 的 target
        if self.target_mode == "local":
            target = I.LOCAL
        elif self.target_mode == "remote":
            target = self.remote_target_name
        else:
            target = "manual-" + secrets.token_hex(4)  # 手动模式：每次唯一标记
        inc = I.Incident(symptom, params=params, target=target)
        inc.add_hypotheses(skills)
        self.last_incident = inc
        self.last_incident_swept = False
        self._show_hypotheses(inc)

    def _read_until_end(self):
        lines = []
        for line in sys.stdin:
            if line.strip() == "END":
                break
            lines.append(line.rstrip("\n"))
        return "\n".join(lines)

    def _confirm_target(self):
        """确认诊断目标：读记忆默认，回车确认，输数字切换。返回 mode（local/manual/remote）。"""
        import platform
        mode_file = _home() / "target_mode"
        default = "local"
        if mode_file.exists():
            v = mode_file.read_text(encoding="utf-8").strip()
            if v in ("local", "manual", "remote"):
                default = v
        os_name = {"Darwin": "macOS", "Linux": "Linux"}.get(
            platform.system(), platform.system())
        labels = {"local": f"本机（{os_name}）",
                  "manual": "非本机-手动",
                  "remote": "非本机-远程"}
        print(f"  请确认诊断目标（当前：{labels[default]}，回车保持）：")
        print(f"  1 {labels['local']}")
        print(f"  2 {labels['manual']}")
        print(f"  3 {labels['remote']}")
        try:
            ans = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans in ("1", "2", "3"):
            mode = {"1": "local", "2": "manual", "3": "remote"}[ans]
            mode_file.parent.mkdir(parents=True, exist_ok=True)
            mode_file.write_text(mode, encoding="utf-8")
            return mode
        return default

    def _pick_remote_target(self):
        """remote 模式下：列出 targets.yaml 设备让用户挑一台。
        返回目标名（str），targets.yaml 为空时返回 None。"""
        try:
            targets = access.load_targets()
        except access.AccessError as e:
            print(f"  ✘ targets.yaml 加载失败：{e}")
            return None
        if not targets:
            print("  尚无远程设备。请使用 target add 指令添加一台。")
            return None
        print("  选择诊断目标（回车确认当前，输序号切换）：")
        names = sorted(targets.keys())
        for i, name in enumerate(names, 1):
            t = targets[name]
            os_hint = t.get("os", "未声明")
            print(f"  {i} {name}  ({t.get('connector','?')} | os:{os_hint} | {t.get('host','?')})")
        try:
            ans = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans.isdigit():
            idx = int(ans)
            if 1 <= idx <= len(names):
                return names[idx - 1]
        # 回车 / 无效 → 取第一个
        return names[0]

    def _os_ok_for_target(self, skill):
        """按当前目标过滤 skill 的平台声明。
        local：本机 os 必须在声明的 os 集合里。
        remote：目标 os 必须在声明的 os 集合里。
        manual / 未声明 os：不过滤。"""
        if self.target_mode == "manual":
            return True
        import platform
        plats = skill.get("metadata", {}).get("platforms", []) or []
        declared = {p.get("os") for p in plats if p.get("os")}
        if not declared:
            return True                       # 未声明 os = 与目标 os 无关
        if self.target_mode == "local":
            target_os = platform.system().lower()   # linux / darwin / ...
        elif self.target_mode == "remote":
            target_os = self.remote_target_os       # 从 targets.yaml 条目取的 os 字段
        else:
            return True
        if not target_os:
            return True                       # 目标未声明 os → 不过滤
        # macos / darwin 双向映射（Python platform.system() 返回 darwin，用户习惯说 macos）
        _ALIAS = {"macos": "darwin", "darwin": "macos"}
        check = declared | {_ALIAS.get(o, o) for o in declared}
        target = _ALIAS.get(target_os, target_os)
        return target in check

    @staticmethod
    def _collect_params(skills, existing):
        """扫必填参数（source in alert/user）且没值的，逐个问用户补。返回补齐后的完整 dict。"""
        params = dict(existing or {})
        for s in skills:
            for prm in s.get("metadata", {}).get("params", []) or []:
                name = prm.get("name")
                if prm.get("source") not in ("alert", "user"):
                    continue
                if name in params and params[name]:
                    continue
                desc = prm.get("desc", "")
                if desc:
                    prompt = f"  需提供 {name}（{desc}）："
                else:
                    prompt = f"  {name}："
                try:
                    v = input(prompt).strip()
                except (EOFError, KeyboardInterrupt):
                    v = ""
                if v:
                    params[name] = v
        return params

    def _sweep_incident(self):
        """批量取证：远程模式走 execute_mixed + gate，本机/手动保留现有逐条逻辑。"""
        inc = self.last_incident
        if not inc:
            print("  先描述一个问题，我才好取证。")
            return

        # --- 远程模式：走 execute_mixed ---
        if self.target_mode == "remote":
            self._sweep_remote(inc)
            return

        # ===== 以下本机/手动模式：现有逻辑不动 =====
        # 补参数：所有假设的必填参数（批量取证前问用户）
        skills = [h.skill for h in inc.hyps]
        params = self._collect_params(skills, inc.params)
        inc.params.update(params)
        for h in inc.hyps:
            h.params.update(params)
        plan = inc.plan()
        probes = sweep.flatten(plan)

        # 分类
        autoable = [p for p in probes if p["auto"] and runtime.MISSING not in p["cmd"]]
        need_param = [p for p in probes if runtime.MISSING in p["cmd"]]
        need_manual = [p for p in probes if not p["auto"] and runtime.MISSING not in p["cmd"]]

        # 1. 自动执行能自动的（白名单内只读 + 参数齐全）
        if autoable:
            if not sweep.is_trusted(I.LOCAL):
                print(f"  本机可自动执行 {len(autoable)} 条只读取证命令（绝不含写操作）。")
                try:
                    ans = input("  授权本机自动取证？一次性，记 trust.yaml [y/N]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    ans = ""
                if ans.lower() in ("y", "yes", "是"):
                    sweep.grant_trust(I.LOCAL)
            if sweep.is_trusted(I.LOCAL):
                print(f"  ▶ 自动执行 {len(autoable)} 条只读命令…")
                filtered = {"target": plan["target"],
                            "waves": [{"wave": 0, "probes": autoable}]}
                sweep.execute_auto(filtered, inc.params, inc.store)

        # 2. 列出全部指令 + 状态
        print("\n  ── 取证指令 ──")
        for p in autoable:
            print(f"  ✅ 已自动执行    {p['cmd'][:68]}")
        for p in need_manual:
            print(f"  ⏳ 需手动（指令不在白名单）  {p['cmd'][:68]}")
        for p in need_param:
            print(f"  ❓ 需补充用户参数  {p['cmd'][:68]}")

        # 3. 回车确认，逐条处理不能自动的
        if need_manual or need_param:
            try:
                input("\n  回车确认，逐条处理未自动执行的指令…")
            except (EOFError, KeyboardInterrupt):
                pass

        # 4. 补参数（补齐后重判：能自动就自动，不能就手动）
        for p in need_param:
            ctx = dict(inc.params)
            ctx.setdefault("sid", "sess")
            for m in re.findall(r"\{\{\s*([^}]+?)\s*\}\}", p.get("cmd_template", "")):
                name = m.strip()
                if name in ctx and ctx[name]:
                    continue
                v = input(f"  请提供参数 {name} 的值: ").strip()
                if v:
                    inc.params[name] = v
                    ctx[name] = v
            new_cmd = runtime.render(p["cmd_template"], ctx)
            p["cmd"] = new_cmd
            if run_sim._is_readonly(new_cmd) and inc.target == I.LOCAL:
                print(f"  ▶ 参数已补，自动执行：{new_cmd}")
                try:
                    stdout = subprocess.run(["bash", "-c", new_cmd],
                                            capture_output=True, text=True, timeout=15).stdout
                    sweep._store_result(inc.store, p, stdout)
                except Exception:
                    pass
            else:
                print(f"  请执行并粘贴输出（END 结束）：\n  $ {new_cmd}")
                stdout = self._read_until_end()
                if stdout.strip():
                    sweep._store_result(inc.store, p, stdout)

        # 5. 手动贴回（不在白名单）
        for p in need_manual:
            print(f"\n  请执行并粘贴输出（END 结束）：\n  $ {p['cmd']}")
            stdout = self._read_until_end()
            if stdout.strip():
                sweep._store_result(inc.store, p, stdout)

        # 6. 干跑 + 卷宗
        inc.dry_run()
        print(inc.render_dossier())
        self._offer_treatment(inc)
        self.last_incident_swept = True

    def _offer_treatment(self, inc):
        for h in inc.hyps:
            if h.status == I.CONFIRMED and h.pending:
                print(f"  → 处置：run {h.meta['id']}"
                      f"（进入导航档执行变更，变更简报/审批门/verify 不变）")
                return
        if all(h.status != I.CONFIRMED for h in inc.hyps):
            print("  未证实任何假设。输入 report 导出移交卷宗，转人工/强模型接手。")
            if self.model_cfg is not None:           # escalate 助理：只荐库内 id（R8/R10）
                sid = llm.suggest_skill(inc.handover(), self.idx, config=self.model_cfg)
                if sid:
                    print(f"  → 模型建议再看：run {sid}（库内 Skill，徽章以库为准）")

    # ---------- 远程取证 ----------
    def _ensure_authorized(self, target_name, inc):
        """交互授权回调：已授权→True；未授权→问用户一次。用户同意则记 TTL 30 天。"""
        if sweep.is_trusted(target_name):
            return True
        plan = inc.plan()
        auto_count = sum(
            1 for p in sweep.flatten(plan)
            if p["target"] == target_name and p["auto"] and runtime.MISSING not in p["cmd"]
        )
        if auto_count == 0:
            return False               # 全是手动指令，不需要授权
        print(f"\n  {target_name} 上可自动执行 {auto_count} 条只读取证命令（绝不含写操作）。")
        try:
            ans = input("  授权在目标上自动取证？一次性，30 天后到期 [y/N]: ").strip()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans.lower() in ("y", "yes", "是"):
            sweep.grant_trust(target_name, ttl_days=30, scope="readonly")
            return True
        return False

    def _sweep_remote(self, inc):
        """远程取证流程：
        1. 调 mixed_sweep（本机探针走 bash，远程已授权走 gate，未授权进 manual）
        2. 展示已自动执行的结果（逐条 ✅）
        3. 对 manual 桶里的探针逐条交互（保持与现有手动模式一致的体验）
        4. 干跑 + 卷宗
        """
        # 补参数
        skills = [h.skill for h in inc.hyps]
        params = self._collect_params(skills, inc.params)
        inc.params.update(params)
        for h in inc.hyps:
            h.params.update(params)

        # 混合取证：authorized 回调带交互
        res = inc.mixed_sweep(
            remote_runner=lambda tn, cmd, pr: gate.run_remote(tn, cmd, params=pr),
            authorized=lambda name: self._ensure_authorized(name, inc)
        )

        executed = res["executed"]
        manual = res["manual"]

        # 展示：已自动执行的
        if executed:
            print("\n  ── 自动取证结果 ──")
            for r in executed:
                if r["status"] == "executed":
                    fields_hint = ", ".join(r.get("fields", [])[:4])
                    print(f"  ✅ {r.get('target',''):<12} {r['cmd'][:60]}"
                          f"{' → ' + fields_hint if fields_hint else ''}")
                else:
                    print(f"  ❌ {r.get('target',''):<12} {r['cmd']}\n"
                          f"     原因：{str(r.get('err', r['status']))[:200]}")

        # 手动桶：逐条交互
        manual_items = []
        for tname, probes in manual.items():
            for p in probes:
                manual_items.append((tname, p))

        if manual_items:
            print(f"\n  ── 需手动执行 {len(manual_items)} 条 ──")
            for tname, p in manual_items:
                label = f" [{tname}]" if tname != I.LOCAL else ""
                print(f"\n  请在目标上执行并粘贴输出（END 结束）{label}：\n  $ {p['cmd']}")
                stdout = self._read_until_end()
                if stdout.strip():
                    sweep._store_result(inc.store, p, stdout)

        # 干跑 + 卷宗
        inc.dry_run()
        print(inc.render_dossier())
        self._offer_treatment(inc)
        self.last_incident_swept = True

    def _report(self, share=False):
        if not self.last_incident:
            print("  还没有可导出的排查。先描述问题并 sweep。")
            return
        if share:
            print("  （--share：已剥离个人 overlay 注记与内网地址，可放心贴工单/发社区）")
        print(self.last_incident.export_report(share=share))

    def _delegate(self, parts):
        """hub/record/skill/doctor 交给既有 CLI 模块处理（复用，不复制）。"""
        import argparse
        ap = argparse.ArgumentParser(prog="opsaxiom", add_help=False)
        sub = ap.add_subparsers(dest="cmd")
        for mod, fn in (("doctor", "add_doctor"), ("capture_cli", "add_capture"),
                        ("hub_cli", "add_hub"), ("model_cli", "add_model"),
                        ("target_cli", "add_target")):
            try:
                m = __import__(mod)
                getattr(m, fn)(sub)
            except Exception:
                pass
        try:
            args = ap.parse_args(parts)
        except SystemExit:
            return
        if hasattr(args, "fn"):
            try:
                args.fn(args)
            except Exception as e:
                print(f"  出错：{e}")

    # ---------- 主循环 ----------
    def _handle(self, line):
        line = line.strip()
        if not line:
            # 回车：刚匹配完还没取证 → 批量取证
            if self.last_incident is not None and not self.last_incident_swept:
                self._sweep_incident()
            return
        parts = line.split()
        head = parts[0].lower()
        if head in ("quit", "exit", "q"):
            self.running = False
            return
        if head == "sug":
            body = " ".join(parts[1:]) if len(parts) > 1 else self.last_unmatched
            self._sug(body); return
        if head == "auth":
            self._auth(); return
        if head == "overlay":
            sid = parts[1] if len(parts) > 1 else ""
            if sid == "--show":
                self._overlay_show(parts[2] if len(parts) > 2 else ""); return
            elif sid:
                self._overlay_new(sid); return
            else:
                print("  用法：overlay <skill-id>  或  overlay --show <skill-id>"); return
        if head == "new":
            self._skill_new(); return
        if head == "edit":
            sid = " ".join(parts[1:]) if len(parts) > 1 else ""
            self._skill_edit(sid); return
        if head == "search":
            kw = " ".join(parts[1:]) if len(parts) > 1 else ""
            self._delegate(["hub", "search", kw]); return
        if head == "sync":
            self._delegate(["hub", "sync"]); return
        if head == "push":
            self._delegate(["hub", "push"] + (parts[1:] if len(parts) > 1 else ["--help"])); return
        if head == "fork":
            self._delegate(["skill", "fork"] + (parts[1:] if len(parts) > 1 else ["--help"])); return
        if head == "promote":
            sid = parts[1] if len(parts) > 1 else ""
            self._promote(sid); return
        if head in ("help", "?"):
            self._help(); return
        if head == "list":
            self._list(parts[1] if len(parts) > 1 else None); return
        if head == "info" and len(parts) > 1:
            self._info(parts[1]); return
        if head == "run" and len(parts) > 1:
            self.target_mode = self._confirm_target()
            self._run(parts[1]); return
        if head == "resume":
            self._resume_pick(); return
        if head == "sweep":
            self._sweep_incident(); return
        if head == "report":
            self._report(); return
        if head in ("doctor", "hub", "record", "skill", "model", "target"):
            self._delegate(parts)
            if head == "model":                     # 配置可能变了，热重载
                try:
                    self.model_cfg = llm.load_config()
                except Exception:
                    self.model_cfg = None
            return
        if line.isdigit():
            i = int(line)
            if 1 <= i <= len(self.last_hits):
                self.last_incident_swept = True   # 用户选 v1，标记已处理，避免回车误触发批量
                self._run(self.last_hits[i - 1][1]["id"])
            else:
                print("  没有这个序号。先描述问题看到候选，再输序号。")
            return
        # 输入是 Skill ID → 直接进 v1 导航档
        p, s = _find_skill(line)
        if p:
            self.target_mode = self._confirm_target()
            self._run(s["metadata"]["id"]); return
        # 默认：当症状 → 陈述入口（交互 v2：建 incident、列假设、交互态自动取证）
        self._intake(line)

    def loop(self):
        try:
            import readline  # noqa: F401  上下键历史
            hist = _home() / "history"
            _home().mkdir(parents=True, exist_ok=True)
            try:
                readline.read_history_file(str(hist))
            except Exception:
                pass
        except Exception:
            hist = None
        self._maybe_sync()
        self.idx = diagnose.load_index()     # 同步后重新加载
        self._welcome()
        # M-2 首次向导：model.yaml 不存在且在真终端 → 问一次（任何选择都落盘不再问）
        if not llm.config_path().exists() and sys.stdin.isatty():
            try:
                import model_cli
                model_cli.first_run_wizard()
                self.model_cfg = llm.load_config()
            except Exception:
                pass
        idle_interrupt = False
        while self.running:
            try:
                line = input("axiom> ")
                idle_interrupt = False
                self._handle(line)
            except KeyboardInterrupt:
                if idle_interrupt:
                    print("\n再见。")
                    break
                print("\n（再按一次 Ctrl-C 退出，或 quit）")
                idle_interrupt = True
            except EOFError:
                print("\n再见。")
                break
        if hist:
            try:
                import readline
                readline.write_history_file(str(hist))
            except Exception:
                pass


def start():
    if not sys.stdin.isatty():
        print("OpsAxiom 交互态需要终端。脚本/自动化请用子命令："
              "opsaxiom diagnose \"<症状>\" 或 opsaxiom run <id>。", file=sys.stderr)
        return 2
    Repl().loop()
    return 0
