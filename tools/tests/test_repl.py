"""W-1 Terminal REPL 分发逻辑测试。"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sim"))
import repl  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_opsaxiom_home(monkeypatch, tmp_path):
    """与用户 ~/.opsaxiom 隔离：测试不读真实 model.yaml（否则会真跑本地推理，
    慢且不确定——M-1 内置模型启用后踩到）。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))


"""W-1 Terminal REPL 分发逻辑测试。"""
import io as _io
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sim"))
import repl  # noqa: E402
import sweep  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_opsaxiom_home(monkeypatch, tmp_path):
    """与用户 ~/.opsaxiom 隔离：测试不读真实 model.yaml（否则会真跑本地推理，
    慢且不确定——M-1 内置模型启用后踩到）。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))


def test_symptom_sets_hits(monkeypatch):
    # _intake 现在会先问诊断目标（目标维度），喂一个"回车保持本机"；
    # disk-full 是 Linux 专属 skill，把本机假装成 Linux 让 os 过滤放行
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    monkeypatch.setattr("platform.system", lambda: "Linux")
    r = repl.Repl()
    r._handle("磁盘满了但 df 还有空间")
    assert r.last_hits
    assert r.last_hits[0][1]["id"] == "host.storage.capacity.disk-full"


def test_numeric_selection_runs_that_skill(monkeypatch):
    # _intake 会先问诊断目标，喂"回车保持本机"
    monkeypatch.setattr("builtins.input", lambda *a: "")
    r = repl.Repl()
    r._handle("kafka 积压")
    picked = {}
    monkeypatch.setattr(r, "_run", lambda sid, resume=False, **kw: picked.setdefault("id", sid))
    r._handle("1")
    assert picked["id"] == r.last_hits[0][1]["id"]


def test_numeric_without_hits_is_safe(capsys):
    r = repl.Repl()
    r._handle("2")            # 无候选
    assert "先描述问题" in capsys.readouterr().out


def test_quit_stops_loop():
    r = repl.Repl()
    r._handle("quit")
    assert r.running is False


def test_builtins_dont_crash(capsys):
    r = repl.Repl()
    r._handle("help")
    r._handle("list host")
    r._handle("info host.storage.capacity.disk-full")
    r._handle("info nonexistent.skill")
    out = capsys.readouterr().out
    # 帮助文案随版本演进，这里只断言关键信息仍在
    assert "问题诊断" in out and "决策树" in out and "没有这个 Skill" in out


def test_no_tty_refuses(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    rc = repl.start()
    assert rc == 2
    assert "需要终端" in capsys.readouterr().err


# ---------- 十七轮裁定 3：远程取证 fail-fast 按 err_kind 聚合，不猜错误文本 ----------

def _mk_inc(monkeypatch, targets):
    """构造多目标事件：skill 探针渲染 {{target}} 占位由 skill 本身定型，
    这里用 incident API 直挂 hypothesis，target 交给 plan 阶段。"""
    import incident as I
    skill = {
        "metadata": {"id": "t.x", "name": "t", "maturity": "sim_verified",
                     "taxonomy": "host/x"},
        "tree": {"entry": "c", "nodes": [
            {"id": "c", "type": "check", "run": {"linux": "cat /proc/loadavg"}},
        ]},
    }
    monkeypatch.setattr("platform.system", lambda: "Linux")
    inc = I.Incident("卡")
    inc.add_hypotheses([skill])
    return inc


def test_failfast_dead_target_short_circuits_alive_target_pasteback(monkeypatch, capsys):
    """双目标对抗（F-21 修复验证）：
    web-01 全部探针 connect 级失败 → 死目标：一次性指引，不盘问、不转贴回；
    web-02 rc 级失败（err 文本含"连接"，err_kind=exec）→ 活目标：照常贴回。
    断言三件套（has() 返 bool，F-20 同款空洞不可再用）：①贴回问答条数=1
    （死目标不被盘问）②入库 target=web-02 ③web-01 名下无证据。"""
    import incident as I
    inc = _mk_inc(monkeypatch, None)
    fake = {
        "executed": [
            {"node": "c", "cmd": "cat /proc/loadavg", "status": "error",
             "target": "web-01", "err": "SSHConnectError", "err_kind": "connect"},
            {"node": "c", "cmd": "cat /proc/loadavg", "status": "error",
             "target": "web-02", "err": "远端返回码 2：无法连接数据库", "err_kind": "exec"},
        ],
        "manual": {},
    }
    monkeypatch.setattr(I.Incident, "mixed_sweep", lambda self, **kw: fake)
    monkeypatch.setattr(I.Incident, "plan",
                        lambda self: {"waves": [{"probes": [
                            {"node": "c", "cmd": "cat /proc/loadavg",
                             "target": "web-01", "auto": False, "index": 0},
                            {"node": "c", "cmd": "cat /proc/loadavg",
                             "target": "web-02", "auto": False, "index": 1},
                        ]}]})
    monkeypatch.setattr("builtins.input", lambda *a: "")
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("load: 0.5\nEND\n"))
    paste_calls = []
    monkeypatch.setattr(sweep, "_store_result",
                        lambda store, p, out, now=None: paste_calls.append((p["target"], p["cmd"])))
    r = repl.Repl()
    r._sweep_remote(inc)
    out = capsys.readouterr().out
    assert "web-01 连不上" in out                    # 死目标一次性指引
    # ① 死目标不被盘问：贴回 prompt 只出现一次（唯一目标 web-02）
    assert out.count("请在目标上执行并粘贴输出") == 1, f"盘问次数异常:\n{out}"
    # ②③ 贴回证据落在活目标名下，死目标零入库
    assert paste_calls == [("web-02", "cat /proc/loadavg")], paste_calls


def test_failfast_rc_level_connect_word_not_short_circuited(monkeypatch, capsys):
    """单目标 rc 级失败、err 文本含"连接" → err_kind=exec → 不短路人贴回
    （裁定 3 反例：文本匹配会把活着的目标整轮静默跳过，丢证据）。"""
    import incident as I
    inc = _mk_inc(monkeypatch, None)
    fake = {
        "executed": [
            {"node": "c", "cmd": "cat /proc/loadavg", "status": "error",
             "target": "web-01", "err": "远端返回码 2：无法连接数据库", "err_kind": "exec"},
        ],
        "manual": {},
    }
    monkeypatch.setattr(I.Incident, "mixed_sweep", lambda self, **kw: fake)
    monkeypatch.setattr(I.Incident, "plan",
                        lambda self: {"waves": [{"probes": [
                            {"node": "c", "cmd": "cat /proc/loadavg",
                             "target": "web-01", "auto": False, "index": 0},
                        ]}]})
    monkeypatch.setattr("builtins.input", lambda *a: "")
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("load: 0.5\nEND\n"))
    r = repl.Repl()
    r._sweep_remote(inc)
    out = capsys.readouterr().out
    assert "连不上" not in out                        # 没有被误判成死目标
    assert "需手动执行" in out                        # 转贴回（不丢证据）


# ---------- 回流点②：_offer_treatment 全量列出（发起人口径 2026-09-09）----------
import yaml  # noqa: E402

def _mk_pending_inc(monkeypatch, n_pending):
    """构造 n 个 CONFIRMED+pending 假设的 incident（绕过干跑直接置状态）。"""
    import incident as I
    monkeypatch.setattr("platform.system", lambda: "Linux")
    inc = I.Incident("卡")
    for k in range(n_pending):
        skill = {
            "metadata": {"id": f"t.p{k}", "name": f"处置{k}", "maturity": "sim_verified",
                         "taxonomy": "host/x"},
            "tree": {"entry": "c", "nodes": [
                {"id": "c", "type": "check", "run": {"linux": "cat /proc/loadavg"}},
            ]},
        }
        inc.add_hypotheses([skill])
        h = inc.hyps[-1]
        h.status, h.terminal = I.CONFIRMED, f"action:c"
        h.pending = {"kind": "action", "node": "c",
                     "prompt": f"建议执行处置{k}", "risk": "中"}
    return inc


def test_offer_treatment_lists_all_pending(monkeypatch, capsys):
    """多条可处置假设必须全部列出（修复原 return-早退只提第一条的静默缺陷）；
    回车默认执行第 1 项。选择的假设进 _run_treatment。"""
    monkeypatch.setattr("builtins.input", lambda *a: "")
    r = repl.Repl()
    r.last_hits = []
    r.model_cfg = None
    inc = _mk_pending_inc(monkeypatch, 3)
    picked = {}
    monkeypatch.setattr(r, "_run_treatment",
                        lambda h, i: picked.setdefault("id", h.meta["id"]))
    r._offer_treatment(inc)
    out = capsys.readouterr().out
    for k in range(3):
        assert f"处置{k}" in out, f"第 {k} 项未列出:\n{out}"
    assert picked["id"] == "t.p0"                    # 回车=默认第 1 项


def test_offer_treatment_digit_selects_that_item(monkeypatch, capsys):
    """输入序号 2 → 执行第二项。"""
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    r = repl.Repl()
    r.last_hits = []
    r.model_cfg = None
    inc = _mk_pending_inc(monkeypatch, 3)
    picked = {}
    monkeypatch.setattr(r, "_run_treatment",
                        lambda h, i: picked.setdefault("id", h.meta["id"]))
    r._offer_treatment(inc)
    capsys.readouterr()
    assert picked["id"] == "t.p1"


def test_offer_treatment_q_skips(monkeypatch, capsys):
    """q 跳过不处置，提示可手动 run。"""
    monkeypatch.setattr("builtins.input", lambda *a: "q")
    r = repl.Repl()
    r.last_hits = []
    r.model_cfg = None
    inc = _mk_pending_inc(monkeypatch, 1)
    picked = {}
    monkeypatch.setattr(r, "_run_treatment",
                        lambda h, i: picked.setdefault("id", h.meta["id"]))
    r._offer_treatment(inc)
    out = capsys.readouterr().out
    assert "已跳过" in out
    assert picked == {}


def test_treatment_session_carries_facts(monkeypatch, capsys, tmp_path):
    """_run_treatment 构造的 Session 必须持有 inc.store + inc.target
    （回流点②透传），否则 TTL 内证据会在 v1 重跑重采。"""
    import runtime
    captured = {}

    import incident as I
    monkeypatch.setattr("platform.system", lambda: "Linux")
    inc = I.Incident("卡")
    skill = {
        "metadata": {"id": "t.p9", "name": "处置", "maturity": "sim_verified",
                     "taxonomy": "host/x"},
        "tree": {"entry": "c", "nodes": [
            {"id": "c", "type": "action", "title": "t", "cmd": {"linux": "echo hi"},
             "risk": "低"},
        ]},
    }
    inc.add_hypotheses([skill])
    h = inc.hyps[0]
    h.status, h.terminal = I.CONFIRMED, "action:c"
    h.pending = {"kind": "action", "node": "c", "prompt": "建议执行", "risk": "低"}

    orig_init = runtime.Session.__init__

    class _Stop(Exception):
        pass

    def spy_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        captured["facts"] = self.facts
        captured["target"] = self.facts_target
        raise _Stop()                      # 短路 run，只验构造

    monkeypatch.setattr(runtime.Session, "__init__", spy_init)
    r = repl.Repl()
    r.target_mode = "local"
    # _find_skill 走 diagnose._SKILLS_CACHE（import 期按真实 HOME 定格，env 隔离
    # 改不动它）——直接把模块属性指到 tmp，写一个临时 skill 让查找命中
    sk_dir = tmp_path / "hub" / "registry" / "skills" / "t" / "p9"
    sk_dir.mkdir(parents=True)
    (sk_dir / "skill.yaml").write_text(yaml.safe_dump(skill), encoding="utf-8")
    monkeypatch.setattr(repl.diagnose, "_SKILLS_CACHE", tmp_path / "hub" / "registry" / "skills")
    with pytest.raises(_Stop):
        r._run_treatment(h, inc)
    assert captured["facts"] is inc.store
    assert captured["target"] == inc.target


# ---------- #35 执行行五要素：排查列/目标列动态去重 + 结果预览 ----------

def _feed(inc, executed):
    """_sweep_remote 的 mixed_sweep 假桩：直接回喂 executed/manual。"""
    import incident as I
    return {"executed": executed, "manual": {}}


def test_probe_label_ctx_single_skill_no_rank_column(monkeypatch):
    """单假说：排查列永不展示（show_rank=False）。"""
    import incident as I
    skill = {"metadata": {"id": "t.a", "name": "甲", "taxonomy": "host/x"},
             "tree": {"entry": "c", "nodes": []}}
    monkeypatch.setattr("platform.system", lambda: "Linux")
    inc = I.Incident("卡")
    inc.add_hypotheses([skill])
    r = repl.Repl()
    cfg = r._probe_label_ctx(inc)
    assert cfg["show_rank"] is False
    assert cfg["show_target"] is False              # 单目标不展示目标列


def test_probe_label_ctx_multi_skill_uniform_for_skills_no_rank(monkeypatch):
    """多假说但所有指令归属同一集合（全 1+2）→ 列无信息量，不打。"""
    import incident as I
    a = {"metadata": {"id": "t.a", "name": "甲", "taxonomy": "host/x"},
         "tree": {"entry": "c", "nodes": []}}
    b = {"metadata": {"id": "t.b", "name": "乙", "taxonomy": "host/x"},
         "tree": {"entry": "c", "nodes": []}}
    monkeypatch.setattr("platform.system", lambda: "Linux")
    inc = I.Incident("卡")
    inc.add_hypotheses([a, b])
    monkeypatch.setattr(I.Incident, "plan", lambda self: {"waves": [{"probes": [
        {"node": "c", "cmd": "df", "auto": False, "index": 0,
         "for_skills": ["t.a", "t.b"]},
        {"node": "d", "cmd": "free", "auto": False, "index": 1,
         "for_skills": ["t.b", "t.a"]},
    ]}]})
    r = repl.Repl()
    cfg = r._probe_label_ctx(inc)
    assert cfg["show_rank"] is False                # 集合相同（顺序无关）→ 不打


def test_probe_label_ctx_multi_skill_mixed_for_skills_shows_rank(monkeypatch):
    """多假说且归属有差异（有独占也有共用）→ 整轮打排查标签；id→序号映射
    与候选菜单序号一致（顺序沿 hits→hyps 传递不洗牌）。"""
    import incident as I
    a = {"metadata": {"id": "t.a", "name": "甲", "taxonomy": "host/x"},
         "tree": {"entry": "c", "nodes": []}}
    b = {"metadata": {"id": "t.b", "name": "乙", "taxonomy": "host/x"},
         "tree": {"entry": "c", "nodes": []}}
    monkeypatch.setattr("platform.system", lambda: "Linux")
    inc = I.Incident("卡")
    inc.add_hypotheses([a, b])
    monkeypatch.setattr(I.Incident, "plan", lambda self: {"waves": [{"probes": [
        {"node": "c", "cmd": "df", "auto": False, "index": 0,
         "for_skills": ["t.a"]},
        {"node": "d", "cmd": "free", "auto": False, "index": 1,
         "for_skills": ["t.b", "t.a"]},
    ]}]})
    r = repl.Repl()
    cfg = r._probe_label_ctx(inc)
    assert cfg["show_rank"] is True and cfg["id2n"] == {"t.a": 1, "t.b": 2}
    # for_skills 序与菜单序一致：t.a=排查1、t.b=排查2
    assert r._probe_tag({"for_skills": ["t.a", "t.b"]}, cfg["id2n"], True) == "排查1+排查2"
    assert r._probe_tag({"for_skills": ["t.b"]}, cfg["id2n"], True) == "排查2"


def test_probe_row_scalar_value_and_rank_tag(capsys):
    """✅ 行五要素：状态+指令+标量值预览+排查标签；单目标无目标列。"""
    r = repl.Repl()
    r._probe_cfg = {"id2n": {"t.a": 1, "t.b": 2}, "show_target": False,
                    "show_rank": True}
    r._show_probe_result({"node": "c", "cmd": "df -i -P /var",
                          "status": "executed",
                          "for_skills": ["t.a", "t.b"],
                          "target": "web-01", "out": "25", "out_nlines": 1,
                          "fields": ["output"]})
    out = capsys.readouterr().out
    assert "✅" in out and "df -i -P /var" in out
    assert "25" in out                              # 标量值直接可见
    assert "排查1+排查2" in out
    assert "web-01" not in out                      # 单目标不打目标列


def test_probe_row_multiline_output_truncated(capsys):
    """多行输出：引导词独占一行（保 df 自带列对齐），明细真换行逐行 7 格
    缩进，超 3 行给 …（共 N 行）。"""
    r = repl.Repl()
    r._probe_cfg = {"id2n": {}, "show_target": False, "show_rank": False}
    r._show_probe_result({"node": "n", "cmd": "du", "status": "executed",
                          "target": "t", "for_skills": [],
                          "out": "l1\nl2\nl3", "out_nlines": 20})
    out = capsys.readouterr().out
    assert "       返回结果：\n" in out              # 引导词独占一行（保表格对齐）
    assert "\n       l1\n" in out and "\n       l2\n" in out and "\n       l3\n" in out
    assert "共 20 行" in out


def test_probe_row_scalar_two_line_layout_and_brackets(capsys):
    """二版结构：第一行 = ✅ 指令 ⟦排查N⟧（⟦⟧ 包裹元信息），第二行 =
    "返回结果：值"；箭头衔接退役（不含卷宗行）。"""
    r = repl.Repl()
    r._probe_cfg = {"id2n": {"t.a": 1}, "show_target": False, "show_rank": True}
    r._show_probe_result({"node": "c", "cmd": "df", "status": "executed",
                          "for_skills": ["t.a"], "target": "t",
                          "out": "25", "out_nlines": 1})
    out = capsys.readouterr().out
    assert "⟦排查1⟧" in out                        # 括号符包裹
    assert "返回结果：25" in out
    lines = out.rstrip("\n").splitlines()
    assert len(lines) == 2 and lines[1].lstrip().startswith("返回结果：")
    assert " → " not in out                         # 箭头退役


def test_probe_row_empty_output_prints_null(capsys):
    """空输出：打"返回结果：空"（不打空洞行）。"""
    r = repl.Repl()
    r._probe_cfg = {"id2n": {}, "show_target": False, "show_rank": False}
    r._show_probe_result({"node": "c", "cmd": "cmd", "status": "executed",
                          "target": "t", "for_skills": [],
                          "out": "", "out_nlines": 0})
    out = capsys.readouterr().out
    assert "返回结果：空" in out


def test_probe_row_error_keeps_reason_and_tag(capsys):
    """❌ 行：指令+原因（即其结果）+排查标签；样例纠正——门拒绝的指令是 ❌ 非 ✅。"""
    r = repl.Repl()
    r._probe_cfg = {"id2n": {"t.a": 1}, "show_target": False, "show_rank": True}
    r._show_probe_result({"node": "n", "cmd": "smartctl --scan",
                          "status": "error", "for_skills": ["t.a"],
                          "target": "t", "err": "写/非只读命令被执行门拒绝"})
    out = capsys.readouterr().out
    assert "❌ smartctl --scan" in out
    assert "原因：写/非只读命令被执行门拒绝" in out
    assert "排查1" in out


# 注：execute_mixed 的 rec 字段契约（for_skills / out 预截 / out_nlines）
# 属 sweep 层，测试落在 test_mixed_sweep.py（那边的 _plan_for/FactStore 基建同源）。


# ---------- #39 report 导出重构：交互脱敏 / 空壳守门 / LLM 结论行 ----------

def _swept_inc(monkeypatch):
    """一个已干跑完的 incident（disk-full 树 + 96% 事实 → 已排查实例）。"""
    import incident as I
    inc = I.Incident("/var 满了", params={"mount": "/data"}, target="web-01")
    inc.add_hypotheses([I.load_skill_by_id("host.storage.capacity.disk-full")[1]])
    inc.seed_fact("df -B1 --output=target,size,used,avail,pcent /data",
                  {"rows": [{"target": "/data", "pcent": 96}]}, now=1_000_000.0)
    inc.seed_fact("df -i -P /data", {"rows": [{"ipcent": 99}]}, now=1_000_000.0)
    inc.seed_fact(
        "find /data -xdev -type d -exec sh -c 'echo \"$(ls -a \"$1\" | wc -l) $1\"' _ {} \\; 2>/dev/null | sort -rn | head -10",
        {"rows": [{"path": "/data/sess", "n": 500000}]}, now=1_000_000.0)
    inc.dry_run(now=1_000_000.0)
    return inc


def test_report_no_incident_prints_hint(capsys):
    """没陈述过症状 → report 无从导出。"""
    r = repl.Repl()
    r._report()
    assert "当前无可导出的卷宗" in capsys.readouterr().out


def test_report_unswept_gated_not_empty_shell(monkeypatch, capsys):
    """陈过述没取证 → 不打空骨架，守门提示"当前无可导出的卷宗"。"""
    import incident as I
    r = repl.Repl()
    r.last_incident = I.Incident("卡")
    r.last_incident.add_hypotheses([])
    monkeypatch.setattr("builtins.input", lambda *a: "")
    r._report()
    out = capsys.readouterr().out
    assert "当前无可导出的卷宗" in out
    assert "# 故障报告" not in out


def test_report_interactive_share_prompt(monkeypatch, capsys):
    """输入 report → 先问脱敏（回车=否）；y 走 share 剥离。
    问询文案是 input() 的 prompt 参数（不进 stdout），断言捕获参数本身。"""
    r = repl.Repl()
    r.last_incident = _swept_inc(monkeypatch)
    r.model_cfg = None
    seen = []
    monkeypatch.setattr("builtins.input", lambda *a: (seen.append(a[0] if a else ""), "")[1])
    r._report()
    out = capsys.readouterr().out
    assert seen and "是否脱敏导出" in seen[0]
    assert "# 故障报告" in out
    # y → share 版（本例无个人层内容可剥，只验 share True 出报告且不炸）
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    r._report()
    assert "# 故障报告" in capsys.readouterr().out


def test_report_without_model_omits_conclusion_line(monkeypatch, capsys):
    """未接模型 → 头部无"- 结论"行（其余节照出）。"""
    r = repl.Repl()
    r.last_incident = _swept_inc(monkeypatch)
    r.model_cfg = None
    monkeypatch.setattr("builtins.input", lambda *a: "")
    r._report()
    out = capsys.readouterr().out
    assert "- 结论：" not in out
    assert "- 症状：" in out and "## 排查结果" in out


def test_report_with_model_calls_and_redacts(monkeypatch, capsys):
    """接模型 → 现场调 backend_call，返回值清洗后进"- 结论"；模型异常 → 整行不出现。"""
    r = repl.Repl()
    r.last_incident = _swept_inc(monkeypatch)
    r.model_cfg = {"backend": "builtin"}
    monkeypatch.setattr("builtins.input", lambda *a: "")
    monkeypatch.setattr(repl.llm, "backend_call",
                        lambda cfg, p, s: "  结论一句话。\n第二行不要 ")
    r._report()
    out = capsys.readouterr().out
    assert "- 结论：结论一句话。 第二行不要" in out       # 换行折空格、去首尾
    # 模型炸 → 整行不出现，不炸命令
    monkeypatch.setattr(repl.llm, "backend_call",
                        lambda cfg, p, s: (_ for _ in ()).throw(TimeoutError()))
    r._report()
    out2 = capsys.readouterr().out
    assert "- 结论：" not in out2 and "# 故障报告" in out2


def test_report_conclusion_line_absent_when_model_returns_empty(monkeypatch, capsys):
    """模型返回空/纯空白 → 降级整行不出现（不留"- 结论："空壳）。"""
    r = repl.Repl()
    r.last_incident = _swept_inc(monkeypatch)
    r.model_cfg = {"backend": "builtin"}
    monkeypatch.setattr("builtins.input", lambda *a: "")
    monkeypatch.setattr(repl.llm, "backend_call", lambda cfg, p, s: "  \n  ")
    r._report()
    out = capsys.readouterr().out
    assert "- 结论：" not in out
