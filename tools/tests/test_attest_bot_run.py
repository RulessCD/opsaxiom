"""attest_bot_run 批次驱动器单测——mock gh CLI，本地树全流程（不出网）。"""
import json
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import attest_bot_run as BR  # noqa: E402


def _mk_registry(tmp_path):
    """最小 registry 树：skills/host.x/0.1.0/skill.yaml + 空 index。"""
    reg = tmp_path / "reg"
    sd = reg / "skills" / "host.x" / "0.1.0"
    sd.mkdir(parents=True)
    (sd / "skill.yaml").write_text(
        "apiVersion: skill/v0.1\nkind: Check\n"
        "metadata:\n  id: host.x\n  name: x\n  taxonomy: host/x\n"
        "  version: 0.1.0\n  maturity: sim_verified\n")
    return reg


def _mk_issue(num, author, att_yaml, created="2026-09-15T11:30:49Z"):
    return {"number": num, "title": "attest: host.x resolved",
            "body": f"automatic attestation.\n\n```yaml\n{att_yaml}\n```\n",
            "author": {"login": author}, "createdAt": created}


def _signed_att(attestor="alice", skill="host.x", monkey=None):
    from importlib.machinery import SourceFileLoader
    import os
    m = SourceFileLoader("attest_br", str(ROOT / "tools" / "bin" / "opsaxiom-attest")).load_module()
    os.environ.setdefault("OPSAXIOM_HOME", "/tmp/abr-home")
    att = {"skill": skill, "skill_version": "0.1.0", "outcome": "resolved",
           "mode": "navigator",
           "env_fingerprint": {"os": {"family": "rhel", "version_bucket": "8.x"},
                               "arch": "x86_64"},
           "deviations": [], "rollback_exercised": False, "attestor": attestor}
    att["signature"] = m.sign_att(att)
    return yaml.safe_dump(att, allow_unicode=True, sort_keys=False)


@pytest.fixture
def gh_env(tmp_path, monkeypatch):
    """mock gh()：记录调用；issue list 返回注入的批。
    布局与 workflow checkout 一致：cwd 即 registry 根（skills/ 平铺其上），
    opsaxiom-src/ → 真仓库 tools（bot 校验器代码单源）。"""
    reg = tmp_path
    sd = reg / "skills" / "host.x" / "0.1.0"
    sd.mkdir(parents=True)
    (sd / "skill.yaml").write_text(
        "apiVersion: skill/v0.1\nkind: Check\n"
        "metadata:\n  id: host.x\n  name: x\n  taxonomy: host/x\n"
        "  version: 0.1.0\n  maturity: sim_verified\n")
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "opsaxiom-src"
    src.mkdir()
    (src / "tools").symlink_to(ROOT / "tools")
    calls = []
    state = {"issues": []}

    def fake_gh(*args, **kw):
        calls.append(args)
        if args[0] == "issue" and args[1] == "list":
            return _Completed(json.dumps(state["issues"]))
        return _Completed("")
    monkeypatch.setattr(BR, "gh", fake_gh)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("MODE", "direct")
    return reg, calls, state


class _Completed:
    def __init__(self, stdout):
        self.stdout = stdout


def test_full_batch_accept_close_rebuild(gh_env):
    reg, calls, state = gh_env
    state["issues"] = [_mk_issue(1, "alice", _signed_att())]
    monkey = None
    BR.main()
    # 落树：文件名日期取自 issue createdAt（凭据本体无日期字段）
    files = list((reg / "skills" / "host.x" / "0.1.0" / "attestations").glob("*.yaml"))
    assert len(files) == 1
    assert files[0].name.startswith("2026-09-15-")
    att = yaml.safe_load(files[0].read_text())
    assert att["attestor"] == "alice"
    # index 重建
    idx = json.loads((reg / "index.json").read_text())
    assert idx[0]["id"] == "host.x" and idx[0]["attestations"] == 1
    # 回执：评论 + 打 accepted + 关闭
    flat = [a for c in calls for a in c]
    assert "attest:accepted" in flat and "close" in flat


def test_reject_author_mismatch(gh_env):
    reg, calls, state = gh_env
    state["issues"] = [_mk_issue(2, "mallory", _signed_att(attestor="ops-lead"))]
    BR.main()
    flat = [a for c in calls for a in c]
    assert "attest:rejected" in flat
    assert not list((reg / "skills" / "host.x" / "0.1.0" / "attestations").glob("*.yaml"))


def test_idempotent_rerun(gh_env):
    reg, calls, state = gh_env
    state["issues"] = [_mk_issue(3, "alice", _signed_att())]
    BR.main()
    # 同一 issue 重跑一遍（模拟重复触发）：幂等 skip，不重复落文件
    BR.main()
    files = list((reg / "skills" / "host.x" / "0.1.0" / "attestations").glob("*.yaml"))
    assert len(files) == 1


def test_no_yaml_block_rejected(gh_env):
    reg, calls, state = gh_env
    state["issues"] = [{"number": 4, "title": "t", "body": "没有代码块",
                        "author": {"login": "alice"}}]
    BR.main()
    flat = [a for c in calls for a in c]
    assert "attest:rejected" in flat


def test_issue_date_fallback_when_missing(gh_env):
    """issue 无 createdAt（异常数据）→ 落树文件名回退 0000-00-00，不炸。"""
    reg, calls, state = gh_env
    issue = _mk_issue(5, "alice", _signed_att())
    del issue["createdAt"]
    state["issues"] = [issue]
    BR.main()
    files = list((reg / "skills" / "host.x" / "0.1.0" / "attestations").glob("*.yaml"))
    assert len(files) == 1
    assert files[0].name.startswith("0000-00-00-")
