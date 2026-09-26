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


def _signed_att(attestor="alice", skill="host.x", monkey=None,
                family="rhel", bucket="8.x", arch="x86_64",
                outcome="resolved", seed=None):
    """签名凭据 YAML 文本。env 三元组可调（#41 晋级判定按 (family,bucket,arch)
    去重，测试各自保证独立）；seed 仅扰动同 env 的内容哈希避免幂等撞名。"""
    from importlib.machinery import SourceFileLoader
    import os
    m = SourceFileLoader("attest_br", str(ROOT / "tools" / "bin" / "opsaxiom-attest")).load_module()
    os.environ.setdefault("OPSAXIOM_HOME", "/tmp/abr-home")
    att = {"skill": skill, "skill_version": "0.1.0", "outcome": outcome,
           "mode": "navigator",
           "env_fingerprint": {"os": {"family": family, "version_bucket": bucket},
                               "arch": arch},
           "deviations": [], "rollback_exercised": False, "attestor": attestor}
    if seed is not None:
        att["deviations"] = [str(seed)]
    att["signature"] = m.sign_att(att)
    return yaml.safe_dump(att, allow_unicode=True, sort_keys=False)


@pytest.fixture
def gh_env(tmp_path, monkeypatch):
    """mock gh()：记录调用；issue list 返回注入的批。
    布局与 workflow checkout 一致：cwd 即 registry 根（skills/ 平铺其上），
    opsaxiom-src/ → 真仓库 tools（bot 校验器代码单源）。
    skill.yaml 用 minimal 合法版（#41 起晋级环节会真跑 validate）。"""
    import copy
    import test_validate as TV
    reg = tmp_path
    skill = copy.deepcopy(TV._minimal())
    skill["metadata"]["id"] = "host.x"          # 目录与凭据 skill= 对齐
    skill["metadata"]["maturity"] = "sim_verified"
    skill["tests"] = [{"scenario": "tests/ok.yaml", "expect_path": ["done1"]}]
    sd = reg / "skills" / "host.x" / "0.1.0"
    sd.mkdir(parents=True)
    (sd / "skill.yaml").write_text(yaml.safe_dump(skill, allow_unicode=True, sort_keys=False))
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


# ---------- #41：🟢 自动晋级（bot 落树后查独立 resolved 凭据） ----------

def test_third_independent_attestation_promotes_field(gh_env):
    """第 3 个独立 attestor 的 resolved 凭据入库 → 同批自动升 field_verified。"""
    import os
    from importlib.machinery import SourceFileLoader
    m = SourceFileLoader("attest_br3", str(ROOT / "tools" / "bin" / "opsaxiom-attest")).load_module()
    os.environ.setdefault("OPSAXIOM_HOME", "/tmp/abr3-home")

    reg, calls, state = gh_env
    sdir = reg / "skills" / "host.x" / "0.1.0"
    adir = sdir / "attestations"
    adir.mkdir()
    # 两份既有独立凭据（不同 attestor+env，真签名）
    for i, (who, fam) in enumerate((("alice", "rhel"), ("bob", "debian"))):
        att = {"skill": "host.x", "skill_version": "0.1.0", "outcome": "resolved",
               "mode": "navigator",
               "env_fingerprint": {"os": {"family": fam, "version_bucket": "8.x"},
                                   "arch": "x86_64"},
               "deviations": [], "rollback_exercised": False, "attestor": who}
        att["signature"] = m.sign_att(att)
        (adir / f"seed-{i}.yaml").write_text(yaml.safe_dump(att, sort_keys=False))
    # 批次：第 3 票（carol，env 三元组全新：centos/9.x/arm64）
    state["issues"] = [_mk_issue(10, "carol", _signed_att(
        attestor="carol", family="centos", bucket="9.x", arch="arm64"))]
    BR.main()
    raw = (sdir / "skill.yaml").read_text()
    assert "maturity: field_verified" in raw
    assert (sdir / ".maturity" / "field.json").exists()
    # index 反映新徽章
    idx = json.loads((reg / "index.json").read_text())
    assert idx[0]["maturity"] == "field_verified"


def test_negative_outcome_never_promotes(gh_env):
    """凭据 outcome≠resolved 不计独立数（含既往负票）：3 份 partial→不晋级。"""
    import os
    from importlib.machinery import SourceFileLoader
    m = SourceFileLoader("attest_br4", str(ROOT / "tools" / "bin" / "opsaxiom-attest")).load_module()
    os.environ.setdefault("OPSAXIOM_HOME", "/tmp/abr4-home")

    reg, calls, state = gh_env
    sdir = reg / "skills" / "host.x" / "0.1.0"
    adir = sdir / "attestations"
    adir.mkdir()
    for i, (who, fam, oc) in enumerate((("alice", "rhel", "partial"),
                                        ("bob", "debian", "failed"))):
        att = {"skill": "host.x", "skill_version": "0.1.0", "outcome": oc,
               "mode": "navigator",
               "env_fingerprint": {"os": {"family": fam, "version_bucket": "8.x"},
                                   "arch": "x86_64"},
               "deviations": [], "rollback_exercised": False, "attestor": who}
        att["signature"] = m.sign_att(att)
        (adir / f"seed-{i}.yaml").write_text(yaml.safe_dump(att, sort_keys=False))
    state["issues"] = [_mk_issue(11, "carol", _signed_att(attestor="carol"))]
    BR.main()
    raw = (sdir / "skill.yaml").read_text()
    assert "maturity: sim_verified" in raw          # 维持原徽章
    assert not (sdir / ".maturity" / "field.json").exists()
