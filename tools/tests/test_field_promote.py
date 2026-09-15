"""X-4 field_verified 晋级：≥3 份独立且验签有效的 attestation。"""
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sim"))
import promote  # noqa: E402

ATTEST = ROOT / "tools" / "bin" / "opsaxiom-attest"


def _mk_attestation(home, skill_dir, attestor, os_family, os_ver, arch="x86_64"):
    env = {"OPSAXIOM_HOME": str(home), "PATH": __import__("os").environ["PATH"]}
    # attest 写进真实 skill_dir/attestations——用临时 skill 目录避免污染仓库
    subprocess.run([sys.executable, str(ATTEST), "--skill", "middleware.redis.hotkey",
                    "--skill-version", "0.1.0", "--outcome", "resolved", "--mode", "navigator",
                    "--os-family", os_family, "--os-version", os_ver,
                    "--attestor", attestor],
                   env=env, capture_output=True, cwd=str(skill_dir.parents[3]))


def _temp_skill(tmp_path):
    """把 redis-hotkey 复制到临时仓库结构，返回 skill.yaml 路径。"""
    src = ROOT / "skills" / "middleware" / "redis-hotkey"
    dst_root = tmp_path / "skills" / "middleware" / "redis-hotkey"
    shutil.copytree(src, dst_root)
    shutil.rmtree(dst_root / "attestations", ignore_errors=True)
    shutil.rmtree(dst_root / ".maturity", ignore_errors=True)
    return dst_root / "skill.yaml"


def test_field_needs_three_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path / "home"))
    subprocess.run([sys.executable, str(ATTEST), "--keygen"],
                   env={"OPSAXIOM_HOME": str(tmp_path / "home"),
                        "PATH": __import__("os").environ["PATH"]}, capture_output=True)
    sf = _temp_skill(tmp_path)
    adir = sf.parent / "attestations"
    adir.mkdir()

    import importlib.util
    from importlib.machinery import SourceFileLoader
    at = SourceFileLoader("at", str(ATTEST)).load_module()

    def add(attestor, fam, ver, arch, date):
        att = {"skill": "middleware.redis.hotkey", "skill_version": "0.1.0",
               "outcome": "resolved", "mode": "navigator",
               "env_fingerprint": {"os": {"family": fam, "version_bucket": at.bucket_version(ver)},
                                   "arch": arch},
               "rollback_exercised": False, "attestor": attestor}
        att["signature"] = at.sign_att(att)
        (adir / f"{date}.yaml").write_text(__import__("yaml").safe_dump(att, allow_unicode=True))

    # 2 份独立 → 不够
    add("gh:alice", "rhel", "8", "x86_64", "2026-07-01-a")
    add("gh:bob", "ubuntu", "22", "aarch64", "2026-07-02-b")
    assert promote.promote_field(sf) == 1

    # 第 3 份独立 → 够了
    add("gh:carol", "debian", "12", "x86_64", "2026-07-03-c")
    assert promote.promote_field(sf) == 0
    assert "field_verified" in sf.read_text()


def test_same_attestor_not_independent(tmp_path):
    from importlib.machinery import SourceFileLoader
    at = SourceFileLoader("at2", str(ATTEST)).load_module()
    import os
    os.environ["OPSAXIOM_HOME"] = str(tmp_path / "home")
    subprocess.run([sys.executable, str(ATTEST), "--keygen"],
                   env={"OPSAXIOM_HOME": str(tmp_path / "home"), "PATH": os.environ["PATH"]},
                   capture_output=True)
    sf = _temp_skill(tmp_path)
    adir = sf.parent / "attestations"; adir.mkdir()

    def add(attestor, fam, arch, date):
        att = {"skill": "x", "skill_version": "0.1.0", "outcome": "resolved", "mode": "navigator",
               "env_fingerprint": {"os": {"family": fam, "version_bucket": "8.x"},
                                   "arch": arch},
               "rollback_exercised": False, "attestor": attestor}
        att["signature"] = at.sign_att(att)
        (adir / f"{date}.yaml").write_text(__import__("yaml").safe_dump(att, allow_unicode=True))

    # 3 份但同一 attestor → 独立数应为 1
    add("gh:alice", "rhel", "x86_64", "d1")
    add("gh:alice", "ubuntu", "aarch64", "d2")
    add("gh:alice", "debian", "armv7l", "d3")
    n, _ = promote._independent_valid_attestations(sf.parent)
    assert n == 1
