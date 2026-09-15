"""ghutil token 门面——四分支探活 + 缓存 + login 派生。mock HTTP 层，不出网。"""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import ghutil as G  # noqa: E402


@pytest.fixture(autouse=True)
def _iso_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path / "home"))
    G.invalidate_cache()


def _api(monkeypatch, code=None, body='{"login":"alice"}', exc=None):
    """替 curl 桩：code=None 模拟涉网异常。"""
    def fake_run(cmd, **kw):
        if exc:
            raise exc(cmd=cmd[0], timeout=kw.get("timeout", 0))
        out = (body + "\n" + code) if code else ""
        return subprocess.CompletedProcess([], 0, out, "")
    monkeypatch.setattr(G.subprocess, "run", fake_run)


def _write_token(tmp_path, tok="t0k"):
    p = pathlib.Path(tmp_path) / "home" / "gh_token"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(tok)
    return p


# ---------- 四分支 ----------

def test_missing(tmp_path):
    assert G.check_token() == ("missing", None, G.check_token()[2])
    assert G.check_token()[0] == "missing"


def test_valid(tmp_path, monkeypatch):
    _write_token(tmp_path)
    _api(monkeypatch, "200")
    assert G.check_token() == ("valid", "alice", "")
    assert G.login() == "alice"


def test_invalid_401(tmp_path, monkeypatch):
    _write_token(tmp_path)
    _api(monkeypatch, "401", body='{"message":"Bad credentials"}')
    st, who, why = G.check_token()
    assert st == "invalid" and who is None and "401" in why


def test_offline_exception(tmp_path, monkeypatch):
    _write_token(tmp_path)
    _api(monkeypatch, exc=subprocess.TimeoutExpired)
    st, who, why = G.check_token()
    assert st == "offline" and "不可达" in why


def test_offline_bad_http(tmp_path, monkeypatch):
    """5xx/限流：token 生死未卜，按 offline 不判死刑。"""
    _write_token(tmp_path)
    _api(monkeypatch, "503", body="{}")
    assert G.check_token()[0] == "offline"


# ---------- 缓存行为 ----------

def test_cache_hit_no_second_call(tmp_path, monkeypatch):
    _write_token(tmp_path)
    calls = []
    def fake_run(cmd, **kw):
        calls.append(1)
        return subprocess.CompletedProcess([], 0, '{"login":"a"}\n200')
    monkeypatch.setattr(G.subprocess, "run", fake_run)
    G.check_token()
    G.check_token()
    assert len(calls) == 1          # TTL 内第二次不再打 API


def test_offline_not_cached(tmp_path, monkeypatch):
    """offline 结果不缓存——恢复网络下一次调用立即真探。"""
    _write_token(tmp_path)
    state = {"n": 0}
    def fake_run(cmd, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="curl", timeout=8)
        return subprocess.CompletedProcess([], 0, '{"login":"a"}\n200')
    monkeypatch.setattr(G.subprocess, "run", fake_run)
    assert G.check_token()[0] == "offline"
    assert G.check_token() == ("valid", "a", "")


def test_invalidate_cache(tmp_path, monkeypatch):
    _write_token(tmp_path)
    calls = []
    def fake_run(cmd, **kw):
        calls.append(1)
        return subprocess.CompletedProcess([], 0, '{"login":"a"}\n200')
    monkeypatch.setattr(G.subprocess, "run", fake_run)
    G.check_token()
    G.invalidate_cache()
    G.check_token()
    assert len(calls) == 2


def test_read_token_empty_and_missing(tmp_path):
    assert G.read_token() == ""
    (pathlib.Path(tmp_path) / "home").mkdir(parents=True)
    (pathlib.Path(tmp_path) / "home" / "gh_token").write_text("  \n")
    assert G.read_token() == ""     # 空白 token 视同 missing
