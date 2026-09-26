"""P-4/P-5 maturity 流水线测试（非破坏性：只断言 reject 路径与场景查找，不改动仓库 Skill）。

promote 的成功路径会写文件，故不在单测里跑（已在 P-5 批量晋级中实测）；
这里只测"会在写盘前返回"的拒绝分支与场景查找。
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
import promote  # noqa: E402


def test_promote_rejects_when_no_scenario():
    # 造一个 ROOT 内的临时合法 Skill（无 sim 场景）→ 写盘前因"无场景"拒绝(rc=1)、不改动文件。
    # （H-1 后库内 draft 大多已补场景，故用临时 Skill 而非依赖某库内 Skill 的缺场景状态。
    #  _scenarios_for 用 relative_to(ROOT)，必须落在 ROOT 内。）
    import shutil

    import yaml
    src = ROOT / "skills/host/raid-degraded/skill.yaml"
    skill = yaml.safe_load(src.read_text())
    skill["metadata"]["id"] = "host.storage.__promote_notest__"
    d = ROOT / "skills" / "host" / "__promote_notest__"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "skill.yaml"
    path.write_text(yaml.safe_dump(skill, allow_unicode=True))
    try:
        before = path.read_text()
        rc = promote.promote(path)          # 无场景指向它 → 拒绝
        assert rc == 1
        assert path.read_text() == before   # 未被改动
    finally:
        shutil.rmtree(d)


def test_scenarios_lookup():
    # disk-full：3 个 context_walk + 1 个 real（F-9 修复后恢复）
    assert len(promote._scenarios_for(ROOT / "skills/host/disk-full/skill.yaml")) == 4
    # 无场景指向的 ROOT 内虚构路径 → 空（文件不存在也能 resolve + relative_to）
    assert promote._scenarios_for(ROOT / "skills/host/__no_such_skill__/skill.yaml") == []
    assert len(promote._scenarios_for(ROOT / "skills/host/agent-deploy/skill.yaml")) == 1
    # load-high 现有 context + real 两个场景
    assert len(promote._scenarios_for(ROOT / "skills/host/load-high/skill.yaml")) == 2


# ---------- #41：outcom 过滤 + maybe_promote_field（bot 编程入口） ----------

def _tmp_skill_dir(tmp_path, maturity="sim_verified", skill_id="host.promoteme"):
    """最小合法 skill 目录（过 validate 零 ERROR）：skill.yaml + 空 attestations/。
    不落 ROOT，免 _scenarios_for 误伤。"""
    import yaml
    d = tmp_path / skill_id.replace(".", "_")
    d.mkdir()
    (d / "skill.yaml").write_text(yaml.safe_dump({
        "apiVersion": "skill/v0.1",
        "kind": "Diagnostic",
        "metadata": {"id": skill_id, "name": "p", "taxonomy": "host/p",
                     "version": "0.1.0", "maturity": maturity,
                     "platforms": [{"os": "linux"}],
                     "provenance": {"generated_by": "test"}},
        "requirements": {"capability_level": "read", "connectors": ["ssh"]},
        "tree": {"entry": "done1",
                 "nodes": [{"id": "done1", "type": "done", "summary": "ok"}]},
        "tests": [{"scenario": "tests/ok.yaml", "expect_path": ["done1"]}],
    }, allow_unicode=True, sort_keys=False))
    (d / "attestations").mkdir()
    return d


def _write_att(d, name, attestor, outcome, family="rhel", bucket="8.x", arch="x86_64",
               sign=True):
    """写一份凭据（sign=True 时真签名，验签）；返回文件路径。"""
    import os
    import yaml as _yaml
    from importlib.machinery import SourceFileLoader
    m = SourceFileLoader("attest_tp", str(ROOT / "tools" / "bin" / "opsaxiom-attest")).load_module()
    os.environ.setdefault("OPSAXIOM_HOME", "/tmp/tp-home")
    att = {"skill": "host.promoteme", "skill_version": "0.1.0", "outcome": outcome,
           "mode": "navigator",
           "env_fingerprint": {"os": {"family": family, "version_bucket": bucket},
                               "arch": arch},
           "deviations": [], "rollback_exercised": False, "attestor": attestor}
    if sign:
        att["signature"] = m.sign_att(att)
    p = d / "attestations" / name
    p.write_text(_yaml.safe_dump(att, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def test_independent_counts_only_resolved(tmp_path):
    """outcome 过滤（#41 发起人裁定）：partial/failed/made_worse 不计入独立数。"""
    d = _tmp_skill_dir(tmp_path)
    _write_att(d, "a1.yaml", "u1", "resolved")
    _write_att(d, "a2.yaml", "u2", "partial")
    _write_att(d, "a3.yaml", "u3", "failed")
    n, kept = promote._independent_valid_attestations(d)
    assert n == 1 and kept == ["a1.yaml"]


def test_independent_dedup_unchanged_for_resolved(tmp_path):
    """resolved 票仍按 attestor+env 去重（原口径不回退）。"""
    d = _tmp_skill_dir(tmp_path)
    _write_att(d, "a1.yaml", "u1", "resolved")
    _write_att(d, "a2.yaml", "u1", "resolved")                       # 同人 → 不独立
    _write_att(d, "a3.yaml", "u2", "resolved", family="debian")      # 独立
    n, kept = promote._independent_valid_attestations(d)
    assert n == 2 and kept == ["a1.yaml", "a3.yaml"]


def test_maybe_promote_field_promotes_at_three(tmp_path):
    """3 份独立 resolved 真签名 → 改 maturity 行 + field.json 落证。"""
    d = _tmp_skill_dir(tmp_path)
    _write_att(d, "a1.yaml", "u1", "resolved")
    _write_att(d, "a2.yaml", "u2", "resolved", family="debian", bucket="12.x")
    _write_att(d, "a3.yaml", "u3", "resolved", arch="aarch64")
    ok, note = promote.maybe_promote_field(d)
    assert ok, note
    raw = (d / "skill.yaml").read_text()
    assert "maturity: field_verified" in raw
    evfile = d / ".maturity" / "field.json"
    assert '"independent_attestations": 3' in evfile.read_text()


def test_maybe_promote_field_blocked_under_three(tmp_path):
    """2 份独立 → 不动文件，返回解释性 note。"""
    d = _tmp_skill_dir(tmp_path)
    _write_att(d, "a1.yaml", "u1", "resolved")
    _write_att(d, "a2.yaml", "u2", "resolved", family="debian")
    before = (d / "skill.yaml").read_text()
    ok, note = promote.maybe_promote_field(d)
    assert not ok and "2/3" in note
    assert (d / "skill.yaml").read_text() == before


def test_maybe_promote_field_ignores_non_sim(tmp_path):
    """非 sim_verified（已被人工升过/降过）→ 不动，note 说明现状。"""
    d = _tmp_skill_dir(tmp_path, maturity="field_verified")
    ok, note = promote.maybe_promote_field(d)
    assert not ok and "非 sim_verified" in note
