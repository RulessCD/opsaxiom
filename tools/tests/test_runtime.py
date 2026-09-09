"""T-1 运行时导航档会话测试（脚本驱动，非交互）。"""
import os
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sim"))
import runtime  # noqa: E402
import parsers  # noqa: E402


def _run_demo(demo_file, skill_id, tmp_home):
    a = yaml.safe_load((ROOT / "demos" / demo_file).read_text())
    p = next(x for x in (ROOT / "skills").rglob("skill.yaml")
             if yaml.safe_load(x.read_text())["metadata"]["id"] == skill_id)
    os.environ["OPSAXIOM_HOME"] = str(tmp_home)
    io = runtime.IO(answers=a.get("answers"), echo=False)
    sess = runtime.Session(p, params=a.get("params"), mode=a.get("mode", "guided"), io=io, sid="t")
    return sess.run()


def test_disk_full_guided_reaches_done(tmp_path):
    r = _run_demo("disk-full-guided.answers.yaml", "host.storage.capacity.disk-full", tmp_path)
    assert r["outcome"] == "done"
    assert r["path"][-1] == "done_ok"
    # 审计落盘且含 action 审批记录（U-1：decision + verify 结果 + 输出摘要）
    audit = pathlib.Path(r["audit_file"]).read_text().splitlines()
    assert any('"type": "action"' in ln and '"decision": "proceed"' in ln for ln in audit)
    assert any('"verify_passed": true' in ln for ln in audit)
    assert any('"output":' in ln for ln in audit)


def test_action_skip_and_quit(tmp_path):
    """U-1：action 三选项——skip 走 goto 继续，quit 中断并存续跑状态。"""
    os.environ["OPSAXIOM_HOME"] = str(tmp_path)
    a = yaml.safe_load((ROOT / "demos" / "disk-full-guided.answers.yaml").read_text())
    p = next(x for x in (ROOT / "skills").rglob("skill.yaml")
             if yaml.safe_load(x.read_text())["metadata"]["id"] == "host.storage.capacity.disk-full")
    # quit：在 action 节点退出，state 应指向该 action 节点
    a["answers"]["compress_old_logs"] = "quit"
    io = runtime.IO(answers=a["answers"], echo=False)
    sess = runtime.Session(p, params=a["params"], mode="guided", io=io, sid="q")
    r = sess.run()
    assert r["outcome"] == "quit"
    state = tmp_path / "sessions" / "q.state.json"
    assert state.exists()
    import json
    assert json.loads(state.read_text())["node"] == "compress_old_logs"
    # resume：换成 proceed，从 action 续跑到 done
    a["answers"]["compress_old_logs"] = "y"
    io2 = runtime.IO(answers=a["answers"], echo=False)
    sess2 = runtime.Session(p, params=a["params"], mode="guided", io=io2, sid="q")
    start = sess2.load_state()
    assert start == "compress_old_logs"
    r2 = sess2.run(start=start)
    assert r2["outcome"] == "done"
    assert not state.exists()          # 完成后清理续跑状态


def test_mysql_slow_query_guided_reaches_lock_exit(tmp_path):
    r = _run_demo("mysql-slow-query-guided.answers.yaml", "middleware.mysql.slow-query", tmp_path)
    assert r["outcome"] == "done"
    assert r["path"][-1] == "lock_exit"


def test_render_template():
    from runtime import render
    ctx = {"mount": "/data", "sid": "s1", "rows": [{"pcent": 80, "comm": "java"}], "output": {"v": 3}}
    assert render("使用率 {{rows[0].pcent}}% 于 {{mount}}", ctx) == "使用率 80% 于 /data"
    assert render("会话 {{sid}}", ctx) == "会话 s1"
    assert render("进程 {{rows[0].comm}}", ctx) == "进程 java"
    # 求值失败（语法不可解析）保留原样，不崩
    assert render("坏 {{1 + }}", ctx) == "坏 {{1 + }}"
    # U-1：字段缺失但求值成功→None→⟨?⟩ 占位，不留语义黑洞
    assert render("共 {{gone}} 个", ctx) == "共 ⟨?⟩ 个"
    assert render("坏 {{nonexistent[9].x}}", ctx) == "坏 ⟨?⟩"


# ---------- 回流点②：v1 处置消费 FactStore（发起人口径 2026-09-09）----------
import facts as F  # noqa: E402

def _mk_session(skill_id, answers, tmp_path, facts=None, facts_target=None, sid="t2"):
    """带 facts 槽的会话构造（复用仓库存档 skill）；facts_target 对应入键 target。"""
    p = next(x for x in (ROOT / "skills").rglob("skill.yaml")
             if yaml.safe_load(x.read_text())["metadata"]["id"] == skill_id)
    os.environ["OPSAXIOM_HOME"] = str(tmp_path)
    io = runtime.IO(answers=answers, echo=False)
    return runtime.Session(p, params={"mount": "/", "svc": "nginx"},
                           mode="guided", io=io, sid="fx",
                           facts=facts, facts_target=facts_target)


def test_facts_hit_skips_paste(tmp_path):
    """回流点②核心：batch 先采的证据（TTL 内）在续接的 v1 check 命中——
    复用解析产物、不盘问粘贴（paste 被调用即失败：交互模式会阻塞读 stdin）。"""
    import time
    store = F.FactStore()
    parsed = parsers.get_parser("table/df-v1")(
        "target,size,used,avail,pcent\n/ 107374182400 102005473280 5368709120 95%")
    store.put_parsed("df -B1 --output=target,size,used,avail,pcent /", parsed, target="dev-01")
    os.environ["OPSAXIOM_HOME"] = str(tmp_path)
    # monkeypatch IO.paste 计数——若被调用说明缓存未命中（纪律失败）
    calls = []
    orig = runtime.IO.paste
    runtime.IO.paste = lambda self, node, prompt: (calls.append(node), "")[1]
    try:
        sess = runtime.Session(
            next(x for x in (ROOT / "skills").rglob("skill.yaml")
                 if yaml.safe_load(x.read_text())["metadata"]["id"]
                 == "host.storage.capacity.disk-full"),
            params={"mount": "/"}, mode="guided", io=runtime.IO(echo=False), sid="hit",
            facts=store, facts_target="dev-01")
        nxt = sess._do_check(sess.nodes["locate_mount"])
        assert nxt == "check_inode"          # pcent>=90 分支在缓存产物上正常求值
        assert sess.ctx["rows"][0]["pcent"] == 95   # 缓存值已灌进 ctx
        assert any(r.get("reused") for r in sess.audit)   # 复用标记入审计
        assert calls == []                    # 未盘问粘贴（命中即跳过）
    finally:
        runtime.IO.paste = orig


def test_facts_expired_recollects(tmp_path, monkeypatch):
    """过期事实不复用（诚实：宁可重采不给旧值）：ts 超 TTL → 缓存不命中，
    走正常采集路径（answers 提供新输出）。"""
    import time
    parsed = parsers.get_parser("table/df-v1")(
        "target,size,used,avail,pcent\n/ 107374182400 102005473280 5368709120 95%")
    store = F.FactStore()
    store._facts[F.make_key("dev-01",
                            F.normalize_cmd("df -B1 --output=target,size,used,avail,pcent /"),
                            F.BUNDLE)] = {
        "key": "x", "value": parsed, "source_cmd": "df", "target": "dev-01",
        "ts": time.time() - 999, "ttl": 300, "parser": "table/df-v1", "field": "*"}
    os.environ["OPSAXIOM_HOME"] = str(tmp_path)
    answers = {"locate_mount":
               "target,size,used,avail,pcent\n/ 107374182400 102005473280 5368709120 10%"}
    io = runtime.IO(answers=answers, echo=False)
    sess = runtime.Session(
        next(x for x in (ROOT / "skills").rglob("skill.yaml")
             if yaml.safe_load(x.read_text())["metadata"]["id"]
             == "host.storage.capacity.disk-full"),
        params={"mount": "/"}, mode="guided", io=io, sid="exp",
        facts=store, facts_target="dev-01")
    nxt = sess._do_check(sess.nodes["locate_mount"])
    assert nxt == "false_alarm"              # 用的是新采值（pcent 10<90），非缓存旧值 95
    assert not any(r.get("reused") for r in sess.audit)
