"""
Incident 会话与诊断卷宗（Z-4，docs/09 §1.3–1.5）——交互 v2 的产品核心。

顶层对象从"裸 skill session"变成 incident（一次故障）：症状 + 多假设 + 事实 + 时间线。
流程：陈述 → 批量取证（Z-2/Z-3）→ 各假设在事实上【干跑】→ 诊断卷宗 → 处置 → 复盘。

干跑（dry-run）是关键：树还是那棵树、判读还是 exprlang 判（法律不变，一行判读逻辑
都不进 LLM/启发式），只是不再拿人当 I/O——事实够就自动步进，事实缺就诚实标"证据不足"。

卷宗三栏（docs/09 §1.3）：
- 已证实：干跑走到 done（有结论）或 action（收敛到处置）
- 已排除：干跑在真实事实上走到 escalate（树跑完未命中该假设）
- 证据不足：缺某条命令的事实，或需要人做选择（ask）——附还差什么

处置(action)/verify/attest 环节不在此实现——原样复用 runtime.Session（v1 做对的部分）。
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "sim"))
import yaml            # noqa: E402
import exprlang        # noqa: E402
import runtime         # noqa: E402
import evidence        # noqa: E402
import sweep           # noqa: E402
import linkbook        # noqa: E402
import overlay         # noqa: E402
from facts import FactStore, LOCAL  # noqa: E402

_BADGE = {"draft": "⚪草稿", "sim_verified": "🔵已验证",
          "field_verified": "🟢实地", "certified": "🟡认证"}

CONFIRMED, REFUTED, INSUFFICIENT, PENDING = \
    "confirmed", "refuted", "insufficient", "pending"


def load_skill_by_id(skill_id):
    import diagnose
    for sp in (diagnose._SKILLS_CACHE, diagnose._SKILLS_LOCAL):
        if sp.is_dir():
            for p in sp.rglob("skill.yaml"):
                s = yaml.safe_load(p.read_text(encoding="utf-8"))
                if s.get("metadata", {}).get("id") == skill_id:
                    return p, s
    return None, None


class Hypothesis:
    """一个候选假设 = 一个 Skill 在本次 incident 事实上的干跑状态。"""
    def __init__(self, skill, params):
        self.skill = skill
        self.meta = skill["metadata"]
        self.params = dict(params)
        self.status = PENDING
        self.path = []
        self.terminal = None          # done:<id> / escalate:<id> / action:<id> / ask:<id> / missing
        self.conclusion = ""
        self.used_cmds = []           # 干跑消费过的命令（供证据引用回溯）
        self.missing = None           # 证据不足时：还差哪条命令
        self.pending = None           # 诊断确立后的待办：{"kind":"ask|action","node","prompt"}
        self.overlay = None           # 个人叠加层（docs/13）；None=无

    @property
    def name(self):
        return self.meta["name"]

    @property
    def badge(self):
        return _BADGE.get(self.meta.get("maturity"), self.meta.get("maturity", "?"))


class Incident:
    def __init__(self, symptom, params=None, target=LOCAL, store=None):
        self.symptom = symptom
        self.params = dict(params or {})
        self.target = target
        self.store = store or FactStore()
        self.hyps = []
        self.timeline = []

    def _t(self, event, **kw):
        self.timeline.append({"event": event, **kw})

    # ---- 陈述 → 候选假设 ----
    def add_hypotheses(self, skill_dicts):
        for s in skill_dicts:
            h = Hypothesis(s, self.params)
            # 叠加个人 overlay：本地参数并入（填 source:local 占位符），
            # 节点注记留待卷宗展示。overlay 不碰树，只补值/贴注记（docs/13）。
            try:
                ov = overlay.load(s["metadata"]["id"])
            except overlay.OverlayError:
                ov = None                      # overlay 违规不阻塞排查，静默跳过（doctor 里报）
            if ov:
                h.overlay = ov
                for k, v in overlay.local_params(ov).items():
                    h.params.setdefault(k, v)
            self.hyps.append(h)
        self._t("hypotheses", ids=[h.meta["id"] for h in self.hyps])
        return self

    @classmethod
    def from_diagnose(cls, symptom, idx, params=None, top=3, target=LOCAL):
        import diagnose
        inc = cls(symptom, params=params, target=target)
        hits = diagnose.match(symptom, idx=idx, top=top)
        skills = []
        for _, entry in hits:
            _, s = load_skill_by_id(entry["id"])
            if s:
                skills.append(s)
        inc.add_hypotheses(skills)
        return inc

    # ---- 取证 ----
    def plan(self):
        return evidence.build_plan([(h.skill, h.params) for h in self.hyps],
                                   target=self.target)

    def auto_sweep(self, runner=None, now=None):
        """本机协驾：自动执行 auto 探针入事实库（需 target=local 且已授权，调用方把关）。"""
        rep = sweep.execute_auto(self.plan(), self.params, self.store,
                                 now=now, runner=runner)
        self._t("auto_sweep", executed=sum(1 for r in rep if r["status"] == "executed"))
        return rep

    def mixed_sweep(self, now=None, remote_runner=None, authorized=None):
        """混合取证（I-7）：本机 + 已授权远程目标自动执行；未授权/不可达目标留作粘贴。
        返回 sweep.execute_mixed 的 {executed, manual}。"""
        plan = evidence.build_plan([(h.skill, h.params) for h in self.hyps],
                                   target=self.target)
        res = sweep.execute_mixed(plan, self.params, self.store, now=now,
                                  remote_runner=remote_runner, authorized=authorized)
        n = sum(1 for r in res["executed"] if r["status"] == "executed")
        self._t("mixed_sweep", executed=n, manual_targets=sorted(res["manual"]))
        return res

    def paste_block(self, nonce, only_manual=True):
        return sweep.render_paste_block(self.plan(), nonce, only_manual=only_manual)

    def ingest(self, text, nonce, now=None):
        res = sweep.ingest(text, self.plan(), nonce, self.store,
                           params=self.params, now=now)
        self._t("ingest", n=len(res["ingested"]), forged=res["ignored_forged"])
        return res

    def seed_fact(self, cmd, parsed, now=None):
        """测试/演示：直接把一次命令的解析产物置入事实库（等价于取证已完成）。"""
        self.store.put_parsed(cmd, parsed, target=self.target, now=now)

    # ---- 干跑：在事实上推进每个假设 ----
    def _render(self, text):
        ctx = dict(self.params)
        ctx.setdefault("sid", "inc")
        return runtime.render(text, ctx)

    @staticmethod
    def _merge_ctx(acc, parsed):
        """把一次 check 的解析产物并入累积 ctx（镜像 runtime._parse_into_ctx：
        标量并进 output.* 与裸命名空间，供后续节点与终点 summary 引用）。"""
        scalar_ns = {k: v for k, v in parsed.items() if k not in ("rows", "lines")}
        acc.update(parsed)
        acc["output"] = {**acc.get("output", {}), **scalar_ns,
                         **(parsed.get("output") if isinstance(parsed.get("output"), dict) else {})}
        return acc

    def _dry_run_one(self, h, now=None):
        nodes = {n["id"]: n for n in h.skill["tree"]["nodes"]}
        node = h.skill["tree"]["entry"]
        h.path = [node]
        acc = dict(self.params)              # 累积 ctx：随干跑积累各 check 的输出
        acc.setdefault("sid", "inc")
        guard = 0
        while guard < 80:
            guard += 1
            n = nodes.get(node)
            if n is None:                      # 裸 goto token
                if node == "done":
                    h.status, h.terminal = CONFIRMED, "done"
                elif node == "escalate":
                    h.status, h.terminal = REFUTED, "escalate"
                else:
                    h.status, h.terminal = INSUFFICIENT, "unknown:" + str(node)
                return
            t = n["type"]
            if t == "done":
                h.status, h.terminal = CONFIRMED, "done:" + node
                h.conclusion = runtime.render(n.get("summary", n.get("title", "")), acc)
                return
            if t == "escalate":
                h.status, h.terminal = REFUTED, "escalate:" + node
                h.conclusion = runtime.render(n.get("summary", ""), acc)
                return
            # 走通全部诊断 check 到达 ask/action = 诊断已确立（ask/action 属处置步骤，
            # 非诊断缺口）；处置本身交 runtime.Session 接管（v1 做对的部分）。
            if t == "action":
                h.status, h.terminal = CONFIRMED, "action:" + node
                h.conclusion = runtime.render(n.get("title", "建议处置"), acc)
                h.pending = {"kind": "action", "node": node,
                             "prompt": runtime.render(n.get("title", ""), acc)}
                return
            if t == "ask":
                h.status, h.terminal = CONFIRMED, "ask:" + node
                h.conclusion = runtime.render(n.get("title", "诊断确立，待选择处置"), acc)
                h.pending = {"kind": "ask", "node": node,
                             "prompt": runtime.render(n.get("question", ""), acc)}
                return
            # check：查事实 → 判读 → 步进
            _, ctmpl = evidence._cmd_of(n.get("run"))
            rc = runtime.render(ctmpl or "", acc)
            parsed = self.store.get_parsed(rc, target=self.target, now=now)
            if parsed is None:
                h.status, h.terminal, h.missing = INSUFFICIENT, "missing:" + node, rc
                return
            h.used_cmds.append(rc)
            self._merge_ctx(acc, parsed)
            nxt = None
            for br in n.get("branch", []) or []:
                try:
                    if exprlang._truthy(exprlang.evaluate(br["when"], acc)):
                        nxt = br["goto"]; break
                except exprlang.EvalError:
                    pass
            node = nxt or n.get("otherwise", "escalate")
            h.path.append(node)
        h.status, h.terminal = INSUFFICIENT, "loop-guard"

    def dry_run(self, now=None):
        for h in self.hyps:
            self._dry_run_one(h, now=now)
        self._t("dry_run", confirmed=sum(h.status == CONFIRMED for h in self.hyps),
                refuted=sum(h.status == REFUTED for h in self.hyps),
                insufficient=sum(h.status == INSUFFICIENT for h in self.hyps))
        return self

    # ---- 卷宗 ----
    def _evidence_for(self, h, now=None):
        """该假设干跑消费过的命令对应的标量事实（证据引用）。"""
        cmds = set(h.used_cmds)
        return [e for e in self.store.evidence(now=now) if e["source_cmd"] in cmds]

    def dossier(self, now=None):
        buckets = {CONFIRMED: [], REFUTED: [], INSUFFICIENT: []}
        for h in self.hyps:
            buckets.setdefault(h.status, []).append({
                "name": h.name, "badge": h.badge, "id": h.meta["id"],
                "status": h.status, "conclusion": h.conclusion,
                "terminal": h.terminal, "missing": h.missing,
                "pending": h.pending,
                "evidence": self._evidence_for(h, now=now),
                "overlay_note": self._overlay_note(h)})
        return buckets

    @staticmethod
    def _overlay_note(h):
        """假设终点节点上的个人注记（📌 展示行），无则空串。"""
        if not h.overlay or not h.terminal:
            return ""
        node_id = h.terminal.split(":", 1)[-1] if ":" in h.terminal else h.terminal
        return overlay.render_note(overlay.note_for(h.overlay, node_id))

    def render_dossier(self, now=None):
        d = self.dossier(now=now)
        out = ["── 诊断卷宗 ──────────────────────────────"]

        def _ev(items):
            for it in items:
                line = f"  {mark} {label}  {it['name']} [{it['badge']}]"
                out.append(line)
                if it["conclusion"]:
                    out.append(f"       {it['conclusion']}")
                for e in it["evidence"][:4]:
                    out.append(f"       证据: {e['source_cmd']} → {e['field']} = {e['value']}")
                if it.get("pending"):
                    out.append(f"       → 待处置: {it['pending']['prompt']}")
                if it["missing"]:
                    out.append(f"       还差: {it['missing']}")
                if it.get("overlay_note"):
                    for ln in it["overlay_note"].splitlines():
                        out.append(f"       {ln}")
        mark, label = "✔", "已证实"
        _ev(d[CONFIRMED])
        mark, label = "✘", "已排除"
        _ev(d[REFUTED])
        mark, label = "?", "证据不足"
        _ev(d[INSUFFICIENT])
        out.append("──────────────────────────────────────────")
        if not d[CONFIRMED]:
            out.append("  未证实任何假设。见下方移交卷宗，可转人工/强模型接手。")
        line = linkbook.render_line(self._taxonomies())
        if line:
            out.append(line)
        return "\n".join(out)

    def _taxonomies(self):
        """本次 incident 涉及的所有 taxonomy（供 linkbook 聚合，个人展示层）。"""
        return [h.meta.get("taxonomy", "") for h in self.hyps if h.meta.get("taxonomy")]

    def next_action(self):
        """若有假设收敛到 action，返回 (skill_path, node_id) 供 runtime 接管处置。"""
        for h in self.hyps:
            if h.status == CONFIRMED and (h.terminal or "").startswith("action:"):
                p, _ = load_skill_by_id(h.meta["id"])
                return p, h.terminal.split(":", 1)[1]
        return None, None

    # ---- 移交 / 复盘 ----
    def handover(self, now=None):
        """全排除或证据不足时的移交卷宗：已收集事实 + 已排除假设 + 时间线。"""
        return {"symptom": self.symptom, "target": self.target,
                "facts": self.store.evidence(now=now),
                "refuted": [h.meta["id"] for h in self.hyps if h.status == REFUTED],
                "insufficient": [{"id": h.meta["id"], "missing": h.missing}
                                 for h in self.hyps if h.status == INSUFFICIENT],
                "timeline": self.timeline}

    def export_report(self, now=None, share=False):
        """故障报告 markdown（docs/09 §1.5）。share=True 剥离个人层（📌/内网URL）后导出。"""
        d = self.dossier(now=now)
        lines = [f"# 故障报告：{self.symptom}", "",
                 f"- 目标：{self.target}", ""]
        if d[CONFIRMED]:
            lines.append("## 结论（已证实）")
            for it in d[CONFIRMED]:
                lines.append(f"- **{it['name']}** [{it['badge']}]：{it['conclusion']}")
                for e in it["evidence"][:6]:
                    lines.append(f"  - 证据：`{e['source_cmd']}` → {e['field']} = {e['value']}")
            lines.append("")
        if d[REFUTED]:
            lines.append("## 已排除")
            for it in d[REFUTED]:
                lines.append(f"- {it['name']}：{it['conclusion'] or '树内判据未命中'}")
            lines.append("")
        if d[INSUFFICIENT]:
            lines.append("## 证据不足 / 待人工")
            for it in d[INSUFFICIENT]:
                lines.append(f"- {it['name']}：还差 {it['missing'] or '（人工选择）'}")
            lines.append("")
        text = "\n".join(lines)
        if share:
            import share as _share
            text = _share.strip_personal(text)
        return text
