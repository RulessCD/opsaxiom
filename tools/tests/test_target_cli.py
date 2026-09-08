"""I-3 target CLI 纯逻辑测试：ssh_config 解析 / 条目组装 / reach 分组诊断。"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import target_cli as T  # noqa: E402

SSH_CONFIG = """
Host bastion
    HostName jump.corp.example.com
    User ops

Host prod-web-01
    HostName 10.0.1.11
    User opsaxiom-ro
    Port 2222
    ProxyJump bastion

Host prod-web-*
    User opsaxiom-ro
"""


def test_parse_ssh_config_skips_wildcards():
    got = {t["name"]: t for t in T.parse_ssh_config(SSH_CONFIG)}
    assert set(got) == {"bastion", "prod-web-01"}          # 通配符 prod-web-* 跳过
    assert got["prod-web-01"]["host"] == "10.0.1.11"
    assert got["prod-web-01"]["port"] == 2222
    assert got["prod-web-01"]["user"] == "opsaxiom-ro"
    assert got["prod-web-01"]["auth"] == "ssh_config"
    assert got["prod-web-01"]["reach"] == "jump:bastion"   # ProxyJump → reach 标签


def test_build_target_entry_defaults():
    e = T.build_target_entry("ssh", host="1.2.3.4", user="ro")
    assert e == {"connector": "ssh", "auth": "ssh_config", "host": "1.2.3.4", "user": "ro"}
    k = T.build_target_entry("kubectl", context="prod")
    assert k == {"connector": "kubectl", "auth": "kubeconfig", "context": "prod"}


def test_detect_default_auth():
    assert T.detect_default_auth("prod-web-01", SSH_CONFIG) == "ssh_config"
    # local_key=False 显式声明无私钥，排除本机 ~/.ssh 干扰
    assert T.detect_default_auth("unknown-host", "", agent_has_keys=True,
                                 local_key=False) == "agent"
    assert T.detect_default_auth("unknown-host", "", agent_has_keys=False,
                                 local_key=False) == "ssh_config"


def test_detect_default_auth_prefers_local_key():
    """本机有私钥 → file: 引用优先于 agent（避开"agent 但无钥匙"坑）。"""
    assert T.detect_default_auth("unknown-host", "", agent_has_keys=True,
                                 local_key="/Users/x/.ssh/id_ed25519") == \
        "file:/Users/x/.ssh/id_ed25519"


def test_diagnose_reach_all_down_is_grouped():
    rows = [
        {"target": "a", "reach": "vpn:office", "reachable": False},
        {"target": "b", "reach": "vpn:office", "reachable": False},
        {"target": "c", "reach": None, "reachable": True},
    ]
    hints = T.diagnose_reach(rows)
    assert len(hints) == 1
    assert "vpn:office" in hints[0] and "VPN" in hints[0] and "office" in hints[0]


def test_diagnose_reach_partial_up_no_hint():
    rows = [
        {"target": "a", "reach": "vpn:office", "reachable": True},   # 有一个通
        {"target": "b", "reach": "vpn:office", "reachable": False},
    ]
    assert T.diagnose_reach(rows) == []                    # 不是"全不通"→不误报 VPN


def test_doctor_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    import yaml
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {
        "a": {"connector": "ssh", "host": "10.0.0.1", "auth": "agent", "reach": "vpn:x"},
        "b": {"connector": "ssh", "host": "10.0.0.2", "auth": "agent", "reach": "vpn:x"},
    }}, allow_unicode=True), encoding="utf-8")

    def probe(name, t):
        return {"reachable": False, "cred_ok": True, "cred_msg": ""}
    T._cmd_doctor(object(), probe=probe)
    out = capsys.readouterr().out
    assert "🔴" in out and "vpn:x" in out and "VPN" in out   # 全不通→分组诊断
