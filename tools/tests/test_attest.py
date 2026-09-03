"""Q-4 opsaxiom-attest + attestation schema 测试。"""
import pathlib, subprocess, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ABIN = ROOT / "tools" / "bin" / "opsaxiom-attest"
sys.path.insert(0, str(ROOT / "tools"))


def _run(*args):
    return subprocess.run([sys.executable, str(ABIN), *args], capture_output=True, text=True)


def test_attest_generates_valid_desensitized(tmp_path, monkeypatch):
    """attest 落盘"registry 为准"：写入 registry 缓存条目旁的 attestations/；
    仓库 skills/ 是历史存档，不参与（需求0723 #7）。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    # 模拟已 hub sync：registry 缓存里放一个最小 load-high 条目（仓库 skills/ 没有）
    cdir = tmp_path / "hub" / "registry" / "skills" / "host.cpu.load-high" / "0.1.0"
    cdir.mkdir(parents=True)
    (cdir / "skill.yaml").write_text(
        "apiVersion: skill/v0.1\nkind: Check\nmetadata:\n  id: host.cpu.load-high\n"
        "  name: 负载高\n  taxonomy: host/cpu/load-high\n  version: 0.1.0\n"
        "  maturity: sim_verified\n  platforms:\n    - {os: linux}\n"
        "tree:\n  entry: t\n  nodes: [{id: done_ok, type: done, summary: ok}]\n",
        encoding="utf-8")
    r = _run("--skill", "host.cpu.load-high", "--skill-version", "0.1.0",
             "--outcome", "resolved", "--mode", "navigator",
             "--os-family", "rhel", "--os-version", "8.5.2", "--scale", "47",
             "--rollback-exercised", "--attestor", "gh:t", "--date", "2026-01-01")
    assert r.returncode == 0, r.stderr
    adir = cdir / "attestations"
    files = list(adir.glob("*.yaml"))
    assert len(files) == 1
    import yaml
    att = yaml.safe_load(files[0].read_text())
    # 脱敏：精确版本被抹成分桶，精确规模被抹成区间
    assert att["env_fingerprint"]["os"]["version_bucket"] == "8.x"
    assert att["env_fingerprint"]["scale_bucket"] == "10-100 hosts"
    assert "8.5.2" not in files[0].read_text()   # 精确版本不得残留
    # 本次运行不得在仓库 skills/ 新增任何签名字段为 anonymous/gh:t 的文件（历史存档只读）
    leaked = [p for p in (ROOT / "skills").rglob("attestations/*.yaml")
              if "gh:t" in p.read_text(encoding="utf-8")]
    assert not leaked, f"attestation 泄漏进仓库：{leaked}"


def test_attest_schema_rejects_pii_version(tmp_path):
    import json
    from jsonschema import Draft202012Validator
    schema = json.loads((ROOT / "schema/attestation.schema.json").read_text())
    v = Draft202012Validator(schema)
    bad = {"skill": "a.b.c", "skill_version": "0.1.0", "outcome": "resolved",
           "mode": "navigator",
           "env_fingerprint": {"os": {"family": "rhel", "version_bucket": "8.5.2"}},  # 精确版本=PII
           "rollback_exercised": False, "attestor": "x", "signature": "y"}
    assert list(v.iter_errors(bad)), "精确版本应被 schema 拒绝（只允许 N.x）"
