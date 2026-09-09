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
    monkeypatch.setattr(r, "_run", lambda sid, resume=False: picked.setdefault("id", sid))
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
    assert "诊断运维问题" in out and "决策树" in out and "没有这个 Skill" in out


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
    """双目标对抗（Fable P2 + 裁定 3）：
    web-01 全部探针 connect 级失败 → 死目标：一次性指引，不转贴回；
    web-02 全部探针 rc 级失败但 err 文本含"连接" → 活目标：必须转贴回
    （按 err_kind 判，不得因文本误判成连接级而短路丢证据）。"""
    import incident as I
    inc = _mk_inc(monkeypatch, None)
    # 两目标各一条探针：手动打 build_plan 不便，直接喂 mixed_sweep 的返回形状——
    # 用 monkeypatch 替换 incident.mixed_sweep，模拟 execute_mixed 的产物
    fake = {
        "executed": [
            {"node": "c", "cmd": "cat /proc/loadavg", "status": "error",
             "target": "web-01", "err": "SSHConnectError", "err_kind": "connect"},
            {"node": "c", "cmd": "cat /proc/loadavg", "status": "error",
             "target": "web-02", "err": "远端返回码 2：无法连接数据库", "err_kind": "exec"},
        ],
        "manual": {},
    }
    monkeypatch.setattr(I.Incident, "mixed_sweep",
                        lambda self, **kw: fake)
    monkeypatch.setattr(I.Incident, "plan",
                        lambda self: {"waves": [{"probes": [
                            {"node": "c", "cmd": "cat /proc/loadavg",
                             "target": "web-01", "auto": False, "index": 0},
                            {"node": "c", "cmd": "cat /proc/loadavg",
                             "target": "web-02", "auto": False, "index": 1},
                        ]}]})
    monkeypatch.setattr("builtins.input", lambda *a: "")       # 参数收集等不阻塞
    # 手动贴回读到 END 即止——喂一条输出 + END
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("load: 0.5\nEND\n"))
    r = repl.Repl()
    r._sweep_remote(inc)
    out = capsys.readouterr().out
    assert "web-01 连不上" in out                    # 死目标一次性指引
    assert "需手动执行" in out and "web-02" in out    # 活目标照常转贴回
    assert inc.store.has("cat /proc/loadavg", target="web-02") is not None  # 贴回入了库


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
