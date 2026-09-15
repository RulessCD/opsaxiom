"""attest_batch 批量校验器——五道门逐一击破 + Sybil 场景。"""
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import attest_batch as AB  # noqa: E402

ATTEST_BIN = str(ROOT / "tools" / "bin" / "opsaxiom-attest")

def _sign(att, dev_date=None):
    """用真签名工具给 att 盖 Ed25519 签名（缓存 keypair，同进程幂等）。"""
    from importlib.machinery import SourceFileLoader
    import os
    m = SourceFileLoader("attest_t", ATTEST_BIN).load_module()
    os.environ.setdefault("OPSAXIOM_HOME", "/tmp/ab-test-home")
    att.pop("signature", None)
    att["signature"] = m.sign_att(att)
    return att


def _valid_att(attestor="alice", os_family="linux", os_ver="8.5.2", arch="x86_64"):
    att = {
        "skill": "host.storage.capacity.disk-full",
        "skill_version": "0.1.0",
        "outcome": "resolved",
        "mode": "navigator",
        "env_fingerprint": {
            "os": {"family": os_family, "version_bucket": "8.x"},
            "arch": arch,
        },
        "deviations": [],
        "rollback_exercised": False,
        "attestor": attestor,
    }
    return _sign(att)


@pytest.fixture
def skill_dir(tmp_path):
    """建一个带 skill.yaml 的树节点 + attestations 目录。"""
    sd = tmp_path / "skills" / "host.storage.capacity.disk-full" / "0.1.0"
    (sd / "attestations").mkdir(parents=True)
    (sd / "skill.yaml").write_text("metadata:\n  id: host.storage.capacity.disk-full\n")
    return sd / "attestations"


# ---------- 五道门 ----------

def test_accept_clean(skill_dir):
    st, fname = AB.check_one(_valid_att(), issue_author="alice", dest_dir=skill_dir)
    assert st == "accept" and fname.endswith(".yaml")


def test_reject_schema_bad_outcome(skill_dir):
    att = _valid_att()
    att["outcome"] = "banana"     # 篡改 outcome → schema + 验签双不过（schema 先拦）
    st, why = AB.check_one(att, issue_author="alice", dest_dir=skill_dir)
    assert st == "reject" and "schema" in why


def test_reject_bad_signature(skill_dir):
    att = _valid_att()
    att["rollback_exercised"] = True    # 签后篡改 → 验签必挂
    st, why = AB.check_one(att, issue_author="not-alice", dest_dir=skill_dir)
    assert st == "reject" and "验签未过" in why


def test_skip_when_already_imported(skill_dir):
    att = _valid_att()
    AB.check_one(att, issue_author="alice", dest_dir=skill_dir)   # 第一遍 accept
    # 手动落树模拟已导入
    st, fname = AB.check_one(att, issue_author="alice", dest_dir=skill_dir)
    if st == "accept":
        (skill_dir / fname).write_text("x")
        st, fname = AB.check_one(att, issue_author="alice", dest_dir=skill_dir)
    assert st == "skip"


def test_reject_skill_missing(tmp_path):
    """④ 给树里不存在的 Skill 签名 → 拒。"""
    empty = tmp_path / "skills" / "host.cpu.nonexistent" / "0.9.9" / "attestations"
    empty.mkdir(parents=True)
    att = _valid_att()
    att["skill"] = "host.cpu.nonexistent"
    _sign(att)
    st, why = AB.check_one(att, issue_author="alice", dest_dir=empty)
    assert st == "reject" and "skill 不存在" in why


def test_reject_author_mismatch(skill_dir):
    """⑤ attestor ≠ issue 作者 → 恒拒（无人工审核）。"""
    st, why = AB.check_one(_valid_att(attestor="ops-lead"),
                           issue_author="alice", dest_dir=skill_dir)
    assert st == "reject"


def test_reject_anonymous(skill_dir):
    """anonymous 不入树（发件端约定的服务端兜底）。"""
    st, why = AB.check_one(_valid_att(attestor="anonymous"),
                           issue_author="someone", dest_dir=skill_dir)
    assert st == "reject" and "anonymous" in why


# ---------- Sybil 场景 ----------

def test_sybil_same_content_same_hash(tmp_path, skill_dir):
    """同内容重复灌 100 遍 → 哈希同 → 首个 accept 之后全 skip。"""
    att = _valid_att()
    seen = set()
    for _ in range(3):
        st, fname = AB.check_one(att, issue_author="alice", dest_dir=skill_dir)
        if st == "accept":
            (skill_dir / fname).write_text("x")
        seen.add(st)
    (skill_dir / sorted(p.name for p in skill_dir.glob("*.yaml"))[0]).exists()
    assert seen <= {"accept", "skip"}


def test_cross_check_author_helper():
    att = {"attestor": "alice"}
    assert AB.cross_check_author(att, "alice") is True
    assert AB.cross_check_author(att, "bob") is False
    assert AB.cross_check_author({"attestor": ""}, "bob") is False
