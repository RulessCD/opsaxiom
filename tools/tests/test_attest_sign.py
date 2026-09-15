"""U-4 attestation Ed25519 签名/验签测试。"""
import os
import pathlib
from importlib.machinery import SourceFileLoader

ROOT = pathlib.Path(__file__).resolve().parents[2]
# opsaxiom-attest 无 .py 扩展，用 SourceFileLoader 从文件路径加载
attest = SourceFileLoader("attest", str(ROOT / "tools" / "bin" / "opsaxiom-attest")).load_module()


def _load(tmp_home):
    os.environ["OPSAXIOM_HOME"] = str(tmp_home)


def _att():
    return {"skill": "host.x", "skill_version": "0.1.0", "outcome": "resolved",
            "mode": "navigator", "env_fingerprint": {"os": {"family": "rhel", "version_bucket": "8.x"},
            "arch": "x86_64"}, "rollback_exercised": True, "attestor": "gh:t"}


def test_sign_verify_roundtrip(tmp_path):
    _load(tmp_path)
    att = _att()
    att["signature"] = attest.sign_att(att)
    assert att["signature"].startswith("ed25519:")
    ok, note = attest.verify_att(att)
    assert ok, note


def test_tamper_detected(tmp_path):
    _load(tmp_path)
    att = _att()
    att["signature"] = attest.sign_att(att)
    att["outcome"] = "failed"          # 篡改被签名内容
    ok, _ = attest.verify_att(att)
    assert not ok


def test_unsigned_rejected(tmp_path):
    _load(tmp_path)
    att = _att()
    att["signature"] = "UNSIGNED-TODO"
    ok, _ = attest.verify_att(att)
    assert not ok


def test_keyring_trust(tmp_path):
    _load(tmp_path)
    att = _att()
    att["signature"] = attest.sign_att(att)
    _, note = attest.verify_att(att)
    assert "TOFU" in note              # 未加入 keyring → TOFU
    kr = tmp_path / "keys" / "trusted"
    kr.mkdir(parents=True)
    (kr / "self.pub").write_text((tmp_path / "keys" / "attest_ed25519.pub").read_text())
    ok, note = attest.verify_att(att)
    assert ok and "可信" in note
