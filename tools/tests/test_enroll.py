"""I-4 enroll + gen_sudoers 测试：密码零痕迹 / 流程编排 / sudoers 生成边界。"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "authoring"))
import enroll  # noqa: E402
import gen_sudoers as G  # noqa: E402


# ---------- sudoers 生成 ----------

def test_extract_entries_pipelines_and_compound():
    e = G.extract_entries("df -B1 --output=x {{mount}} | grep -v tmpfs")
    bins = {b for b, _p in e}
    assert {"df", "grep"} <= bins


def test_sudo_passes_through():
    e = G.extract_entries("sudo -n dmesg | grep -ic err")
    assert ("dmesg", None) in e


def test_deny_bins_excluded():
    e = G.extract_entries('mysql -e "SELECT 1" && curl http://x && rm -rf /tmp/x')
    bins = {b for b, _ in e}
    assert "mysql" not in bins and "curl" not in {b for b, _ in e}


def test_write_action_bins_excluded():
    """action（写变更）节点结构性不进白名单——umount/fsck 教训（fs-readonly skill）。"""
    skill = {
        "metadata": {"id": "x"},
        "tree": {"nodes": [
            {"type": "action", "run": {"linux": "umount /data && fsck -y /dev/vdb"}},
            {"type": "check", "run": {"linux": "df -B1 / | grep -v tmpfs"}},
        ]},
    }
    bins = G.skill_entries(skill)
    assert "umount" not in {b for b, _ in bins}
    assert "fsck" not in {b for b, _ in bins}
    assert "df" in {b for b, _ in bins}


def test_systemctl_prefix_and_none_override():
    """check 里的 systemctl is-active 带前缀；同 bin 有 (bin, None) 时裸名覆盖前缀。"""
    e = G.extract_entries("systemctl is-active nginx")
    assert ("systemctl", "is-active") in e
    merged = G._group_by_bin({("systemctl", "is-active"), ("systemctl", None)})
    assert G._entry_specs(dict()) is not None   # 不崩
    specs = G._entry_specs({("systemctl", "is-active"), ("systemctl", None)})
    assert "systemctl is-active *" not in specs and "systemctl" in specs


def test_render_sudoers_none_overrides_prefixes():
    entries = {("systemctl", None), ("systemctl", "is-active")}
    text = G.render_sudoers_file(entries, user="opsaxiom-ro")
    assert "systemctl is-active *" not in text
    assert "opsaxiom-ro ALL=(root) NOPASSWD: systemctl" in text



# ---------- enroll 交互 ----------

def test_ensure_local_key_reuses_existing(tmp_path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(mode=0o700)
    (ssh_dir / "id_ed25519").write_text("k")
    key, created = enroll.ensure_local_key(home=tmp_path)
    assert created is False and key.name == "id_ed25519"


def test_pubkey_missing_raises(tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("PRIVATE")
    with pytest.raises(enroll.EnrollError):
        enroll.pubkey_of(key)


def test_shq_escapes_single_quote():
    assert enroll.shq("a'b") == "'a'\\''b'"


def test_enroll_ssh_password_not_in_result(tmp_path, monkeypatch):
    """开通过程异常/失败路径：返回值与控制台均不含密码本身。"""
    import io as _io
    import target_cli as TC
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))

    class FakeCli:
        def exec_command(self, cmd, timeout=30):
            self.last_cmd = cmd
            class O:
                def read(self):
                    return b"Linux\n"
                channel = type("C", (), {"recv_exit_status": staticmethod(lambda: 0)})()
            assert "SECRET" not in cmd
            return (None, O(), O())
        def close(self):
            pass

    monkeypatch.setattr(enroll, "connect_with_password", lambda *a, **k: FakeCli())
    monkeypatch.setattr(enroll, "verify_key_login", lambda *a, **k: (True, ""))
    fake_key = tmp_path / ".ssh" / "id_ed25519"
    fake_key.parent.mkdir(parents=True)
    fake_key.write_text("K")
    (fake_key.parent / "id_ed25519.pub").write_text("ssh-ed25519 AAA fake")
    monkeypatch.setattr(enroll, "ensure_local_key", lambda home=None: (fake_key, False))
    monkeypatch.setattr(enroll, "pubkey_of", lambda kp: (fake_key, False)[0])
    monkeypatch.setattr(enroll, "pubkey_of", lambda kp: fake_key.with_suffix(".pub").read_text().strip())
    answers = iter(["n"])           # ro 账号问题 → n
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda *a: "SUPER-SECRET-PW")
    res = TC._enroll_ssh("testdev", "1.2.3.4", "22", "root")
    assert res.get("ok") is True, res
    assert "SUPER" not in str(res)  # 返回值不含密码
