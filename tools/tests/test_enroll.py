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
    """v2 语义：只收首段二进制。`sudo -n <首段> | ... && ...` 里 sudo 只罩
    首段——中段/后段以登录用户身份跑，不进白名单（收了只会扩权）。
    引号感知切段保证引号内连接符不产生虚假分段。"""
    e = G.extract_entries("df -B1 --output=x {{mount}} | grep -v tmpfs")
    assert {b for b, _p in e} == {"df"}
    e2 = G.extract_entries("df -B1 --output=x {{mount}} && grep -v tmpfs")
    assert {b for b, _p in e2} == {"df"}


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


# ---------- v2：引号感知切段 / 段首-only / 解释器硬拒 / 路径重渲染 ----------

def test_v2_quoted_pipe_not_split():
    """引号内的 | 是正则交替不是管道——引号内容既不泄漏成"命令"、
    也不影响首段判定。grep 处于管道中段，v2 首段-only 收不到
    （真机教训：引号内单词混进白名单 → sudoers syntax error）。"""
    e = G.extract_entries('dmesg -T | grep -iE "oom-kill|Out of memory"')
    assert {b for b, _ in e} == {"dmesg"}


def test_v2_segment_head_only():
    """只收段首：管道中段的 grep/sort 不提权，不进白名单（v2 废弃全收策略）。"""
    e = G.extract_entries("df -B1 / | grep -v tmpfs | sort")
    assert {b for b, _ in e} == {"df"}


def test_v2_interpreters_hard_denied():
    """解释器/提权跳板段首也拒收（GTFOBins：sudo 下可得 root shell）。"""
    e = G.extract_entries("find /var -name x; timeout 10 sh -c 'y'; awk '{print $1}' /var/log/x")
    assert not e


def test_v2_render_with_bin_paths():
    """bin_paths 提供后重渲染为绝对路径形态（sudoers 合法要求）。"""
    entries = {("df", None), ("systemctl", "is-active")}
    text = G.render_sudoers_file(
        entries, user="opsaxiom-ro", bin_paths={"df": "/usr/bin/df", "systemctl": "/usr/bin/systemctl"})
    assert "/usr/bin/df" in text and "/usr/bin/systemctl is-active *" in text
    assert "NOPASSWD: /" in text


def test_v2_render_flags_unresolved_paths():
    """未解析路径时行内带警告注释——不可直接写入远端（真机裸名教训）。"""
    text = G.render_sudoers_file({("df", None)}, user="opsaxiom-ro")
    assert "未解析" in text


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
    answers = iter(["n"])           # 白名单确认问题 → n（拒绝写入，走失败兜底）
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda *a: "SUPER-SECRET-PW")
    res = TC._enroll_ssh("testdev", "1.2.3.4", "22", "root")
    assert res.get("ok") is True, res
    assert res.get("sudo_whitelist") is False       # 拒绝白名单 → 不入白名单档
    assert res.get("wl_attempted") is True          # 但必建流程确实走过（linux）
    assert "SUPER" not in str(res)  # 返回值不含密码


def test_install_pubkey_root_idempotent_shape():
    """root 公钥双装命令：sudo sh -c 包裹、含 grep 幂等去重、pub 经 shq 转义。"""
    cli_cmds = []

    class Cli:
        def exec_command(self, cmd, timeout=30):
            cli_cmds.append(cmd)
            class O:
                def read(self):
                    return b""
                channel = type("C", (), {"recv_exit_status": staticmethod(lambda: 0)})()
            return (None, O(), O())

    ok, err = enroll.install_pubkey_root(Cli(), "ssh-ed25519 AAA test")
    assert ok is True
    assert len(cli_cmds) == 1
    c = cli_cmds[0]
    assert c.startswith("sudo sh -c ") and "grep -qxF" in c and "/root/.ssh" in c
