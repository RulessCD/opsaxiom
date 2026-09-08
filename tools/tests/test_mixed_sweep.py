"""I-7 混合取证端到端：本机 + 已授权远程自动执行，未授权/不可达降级粘贴。"""
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sim"))
import evidence  # noqa: E402
import incident as I  # noqa: E402
import sweep  # noqa: E402
from facts import LOCAL, FactStore  # noqa: E402


def _skill(taxonomy="host/cpu/x"):
    return {
        "metadata": {"id": "test.host.x", "name": "t", "taxonomy": taxonomy,
                     "maturity": "sim_verified"},
        "tree": {"entry": "c1", "nodes": [
            {"id": "c1", "type": "check", "title": "查负载",
             "run": {"linux": "cat /proc/loadavg"},
             "branch": [{"when": "output.value > 0", "goto": "done"}],
             "otherwise": "done"},
            {"id": "done", "type": "done", "summary": "完"},
        ]},
    }


def _plan_for(target):
    return evidence.build_plan([(_skill(), {})], target=target)


def test_mixed_local_and_authorized_remote(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    # 组合两个 plan：本机 + 已授权远程 web-01
    store = FactStore()
    plan_local = _plan_for(LOCAL)
    plan_remote = _plan_for("web-01")
    plan = {"target": "mixed", "waves": plan_local["waves"] + plan_remote["waves"]}
    authorized = lambda n: n == "web-01"
    remote_runner = lambda tn, cmd, pr: f"remote:{tn}:{cmd}"
    local_runner = lambda cmd: f"local:{cmd}"
    res = sweep.execute_mixed(plan, {}, store, now="T",
                              local_runner=local_runner,
                              remote_runner=remote_runner,
                              authorized=authorized)
    targets = {r.get("target") for r in res["executed"]}
    assert LOCAL in targets and "web-01" in targets       # 两者都自动执行
    assert res["manual"] == {}                            # 无降级


def test_unauthorized_remote_degrades_to_manual(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    store = FactStore()
    plan = _plan_for("web-02")
    authorized = lambda n: False                            # web-02 未授权
    res = sweep.execute_mixed(plan, {}, store, now="T",
                              local_runner=lambda c: "x",
                              remote_runner=lambda tn, c, p: "y",
                              authorized=authorized)
    assert res["executed"] == []                            # 没自动执行
    assert "web-02" in res["manual"]                        # 降级为粘贴目标


def test_classify_target():
    from facts import LOCAL as L
    assert sweep.classify_target(L)[0] == "local"
    assert sweep.classify_target("t", authorized=lambda n: False)[0] == "paste"
    assert sweep.classify_target("t", authorized=lambda n: True)[0] == "auto"
    assert sweep.classify_target("t", authorized=lambda n: True,
                                 reachable=lambda n: False)[0] == "paste"   # 拨不通


# ---------- B 轮：白名单即路由（sudo_whitelist 目标名单外降级贴回） ----------

def _wl_targets_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {
        "ro-01": {"connector": "ssh", "host": "10.0.0.9", "user": "opsaxiom-ro",
                  "auth": "agent", "sudo_whitelist": True},
        "root-01": {"connector": "ssh", "host": "10.0.0.10", "user": "root",
                    "auth": "agent"},
    }}, allow_unicode=True), encoding="utf-8")


def test_whitelist_tier_non_member_degrades_to_manual(tmp_path, monkeypatch):
    """白名单档（未授权的 sudo_whitelist 目标）+ 命令不在名单 → 降级贴回。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    import access
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {
        "ro-01": {"connector": "ssh", "host": "h", "user": "opsaxiom-ro",
                  "auth": "agent", "sudo_whitelist": True}}}, allow_unicode=True),
        encoding="utf-8")
    store = FactStore()
    plan = _plan_for("ro-01")
    # monkeypatch gate.sudo_routed → False（模拟名单不含该命令）
    import gate
    monkeypatch.setattr(gate, "sudo_routed", lambda tn, cmd: False)
    res = sweep.execute_mixed(plan, {}, store, now="T",
                              local_runner=lambda c: "x",
                              remote_runner=lambda tn, c, p: pytest.fail("名单外不应自动执行"),
                              authorized=lambda n: False)   # 未授权 → 白名单档
    assert res["executed"] == [] and "ro-01" in res["manual"]


def test_whitelist_tier_member_auto_runs(tmp_path, monkeypatch):
    """白名单档：名单内命令（sudo_routed True）→ 自动执行（远端 sudo 物理闸）。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {
        "ro-01": {"connector": "ssh", "host": "h", "user": "opsaxiom-ro",
                  "auth": "agent", "sudo_whitelist": True}}}, allow_unicode=True),
        encoding="utf-8")
    store = FactStore()
    plan = _plan_for("ro-01")
    import gate
    monkeypatch.setattr(gate, "sudo_routed", lambda tn, cmd: True)   # 名单内
    res = sweep.execute_mixed(plan, {}, store, now="T",
                              local_runner=lambda c: "x",
                              remote_runner=lambda tn, c, p: "y",
                              authorized=lambda n: False)
    assert len(res["executed"]) == 1 and res["manual"] == {}


def test_granted_whitelist_target_skips_member_check(tmp_path, monkeypatch):
    """root 档（已 grant 的白名单目标）不查名单——名单外命令也自动执行（管理员直登）。
    注意"root 档"需要物理通道：admin_user 或登录用户非 ro。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {
        "ro-01": {"connector": "ssh", "host": "h", "user": "opsaxiom-ro",
                  "auth": "agent", "sudo_whitelist": True,
                  "admin_user": "root"}}}, allow_unicode=True),
        encoding="utf-8")
    store = FactStore()
    plan = _plan_for("ro-01")
    import gate
    # sudo_routed 是纯静态谓词（名单内才 True）；root 档的豁免在 execute_mixed
    # 的授权分支——名单外命令也不该被它拦
    monkeypatch.setattr(gate, "sudo_routed", lambda tn, cmd: False)
    res = sweep.execute_mixed(plan, {}, store, now="T",
                              local_runner=lambda c: "x",
                              remote_runner=lambda tn, c, p: "y",
                              authorized=lambda n: True)     # 已 grant + 有通道 → root 档
    assert len(res["executed"]) == 1 and res["manual"] == {}


def test_granted_ro_without_admin_stays_whitelist_tier(tmp_path, monkeypatch):
    """旧流程开通（ro 账号、无 admin_user）已 grant → 仍白名单档
    （"没通道不给假 root"），名单外照旧贴回。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {
        "ro-01": {"connector": "ssh", "host": "h", "user": "opsaxiom-ro",
                  "auth": "agent", "sudo_whitelist": True}}}, allow_unicode=True),
        encoding="utf-8")
    store = FactStore()
    plan = _plan_for("ro-01")
    import gate
    monkeypatch.setattr(gate, "sudo_routed", lambda tn, cmd: False)
    res = sweep.execute_mixed(plan, {}, store, now="T",
                              local_runner=lambda c: "x",
                              remote_runner=lambda tn, c, p: pytest.fail("名单外不应自动执行"),
                              authorized=lambda n: True)
    assert res["executed"] == [] and "ro-01" in res["manual"]


def test_non_whitelist_target_not_blocked_by_route(tmp_path, monkeypatch):
    """root 直登目标（无 sudo_whitelist 标记）不受名单降级影响——现状行为保留。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {
        "web-01": {"connector": "ssh", "host": "h", "user": "root", "auth": "agent"}}},
        allow_unicode=True), encoding="utf-8")
    store = FactStore()
    plan = _plan_for("web-01")
    import gate
    monkeypatch.setattr(gate, "sudo_routed", lambda tn, cmd: False)  # 谓词恒 False
    res = sweep.execute_mixed(plan, {}, store, now="T",
                              local_runner=lambda c: "x",
                              remote_runner=lambda tn, c, p: "y",
                              authorized=lambda n: True)
    assert len(res["executed"]) == 1 and res["manual"] == {}   # 照常自动执行


def test_incident_mixed_sweep_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    inc = I.Incident("机器卡")
    inc.add_hypotheses([_skill()])
    # incident target 是 LOCAL，远程混合由调用方拼 plan；此处验证端点存在且跑通
    res = inc.mixed_sweep(now="T", authorized=lambda n: False)
    assert "executed" in res and "manual" in res


# ---------- B 轮实机验证暴露：远程执行异常的诚实呈现 ----------

def test_remote_runner_exception_records_nonempty_error(tmp_path, monkeypatch):
    """remote_runner 抛异常（如 ssh 执行超时）→ executed 里 status=error 且
    err 非空（真机教训：socket.timeout str 为空，显示成"原因："空串）；
    且该探针【不】默默消失——上层靠 cmd 找回转贴回。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    store = FactStore()
    plan = _plan_for("web-01")

    def boom(tn, cmd, pr):
        raise TimeoutError()          # socket.timeout 同款：str 为空

    res = sweep.execute_mixed(plan, {}, store, now="T",
                              local_runner=lambda c: "x",
                              remote_runner=boom,
                              authorized=lambda n: True)
    assert len(res["executed"]) == 1
    r = res["executed"][0]
    assert r["status"] == "error"
    assert r["cmd"]                   # cmd 在，repl 可按它把探针捞进贴回桶
    assert "err" in r and str(r["err"]) != ""   # 异常 str 为空时也不能是空串


def test_gate_audits_connector_exception(tmp_path, monkeypatch, tmp_path_factory):
    """连接器异常（如执行超时）必须审计（decision=error）——命令已打到远端，
    无痕即盲区（真机：find 全盘超时在 remote.jsonl 无任何记录）。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    import gate
    import access as _access
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {
        "web-01": {"connector": "ssh", "host": "h", "user": "root", "auth": "agent"}}},
        allow_unicode=True), encoding="utf-8")
    sweep.grant_trust("web-01", ttl_days=30)

    def boom(t, cred, cmd):
        raise TimeoutError()          # socket.timeout 模拟：str 为空

    try:
        gate.run_remote("web-01", "df -B1 /", connector_fn=boom)
        raise AssertionError("应抛出异常")
    except (gate.GateError, TimeoutError):
        pass
    recs = [json.loads(l) for l in
            (tmp_path / "audit" / "remote.jsonl").read_text().splitlines()]
    errs = [r for r in recs if r["decision"] == "error" and "df -B1 /" in r["cmd"]]
    assert errs and errs[0]["cmd"] and errs[0]["exec_as"] == "root"
