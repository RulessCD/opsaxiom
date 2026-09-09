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
    """B-1 修复后语义：复合型二进制（systemctl）永不产生裸名条目——
    extract 只给 (bin, 子命令)；`sudo -n systemctl restart` 之类未登记形态
    物理不可达（远端 sudoers 无裸 systemctl 行）。"""
    e = G.extract_entries("systemctl is-active nginx")
    assert ("systemctl", "is-active") in e
    assert ("systemctl", None) not in e         # 复合型禁止裸名（B-1）
    specs = G._entry_specs({("systemctl", "is-active")})
    assert "systemctl is-active *" in specs and "systemctl is-active" in specs
    assert "systemctl" not in specs             # 无裸名覆盖条


def test_composite_bare_probe_fail_closed():
    """复合型二进制无子命令/flag 前缀（如裸 journalctl）→ 零条目（fail-closed）：
    该探针不进白名单，执行端转贴回。纯 {{...}} 首参同理不固定。"""
    assert G.extract_entries("journalctl") == set()
    assert G.extract_entries("ip {{ifname}}") == set()


# ---------- v2：引号感知切段 / 段首-only / 解释器硬拒 / 路径重渲染 ----------

def test_b1_write_subcommand_physically_absent(tmp_path, monkeypatch):
    """B-1/v3 端到端回归门槛：把当前 registry 扫一遍渲染 sudoers（带 bin_paths
    ——enroll 落盘的真实形态；F-20 教训：断言必须锚定落盘形态本身），
    断言任何复合二进制的"未登记写子命令/写 flag"不出现为放行形态，裸名
    复合型行物理不在场。牙口由 test_b1_gate_has_teeth 以缺陷重建锁定。"""
    root = G.default_skills_root()
    if not root:
        pytest.skip("无 registry（本机未 hub sync）")
    usage = G.scan_skills_dir(root)
    assert usage, "registry 扫描出零条目——更新机制坏了？"
    bin_paths = {b: f"/usr/bin/{b}" for b, _p in usage}
    text = G.render_sudoers_file(usage, user="opsaxiom-ro",
                                 bin_paths=bin_paths)
    text_preview = G.render_sudoers_file(usage, user="opsaxiom-ro")
    # 复合型二进制（_COMPOSITE_LEAD）+ 已确认写面的基名/flag 黑名单
    ALLOWED_SUBCMDS = {"is-active", "status", "show", "dmon", "topo", "list"}
    # 裸名行（len==1）的基名若属复合型/执行器即违规——放行的只允许
    # "子命令前缀双形态"（bin sub / bin sub *）
    violations = []
    for line in text.splitlines():
        if line.startswith("#") or "NOPASSWD" not in line:
            continue
        for spec in line.split("NOPASSWD: ", 1)[1].split(", "):
            parts = spec.split()
            if not parts:
                continue
            base = parts[0].rsplit("/", 1)[-1]
            if base in _COMPOSITE_BASES and len(parts) == 1:
                violations.append(f"复合型裸名行（B-1 本体）: {spec!r}")
            if len(parts) >= 2 and base in _COMPOSITE_BASES:
                if parts[1].startswith("-"):
                    # flag 打头条目：尾通配关不住后续写子命令/写 flag（F-19）
                    violations.append(f"flag 前缀条目在场: {spec!r}")
                elif parts[1] not in ALLOWED_SUBCMDS:
                    violations.append(f"未登记写子命令在场: {spec!r}")
    assert not violations, "B-1/v3 回归——sudoers 放行形态违规:\n  " + \
        "\n  ".join(violations)
    # 预览形态同样不得出现裸名复合行（写入远端前的最后防线）
    for line in text_preview.splitlines():
        if line.startswith("#") or "NOPASSWD" not in line:
            continue
        for spec in line.split("NOPASSWD: ", 1)[1].split(", "):
            parts = spec.split()
            assert not (parts and parts[0] in _COMPOSITE_BASES and len(parts) == 1), \
                f"预览版裸名复合型行在场（写入远端前会带路径遗漏检查）: {spec!r}"


_COMPOSITE_BASES = {"systemctl", "journalctl", "numactl", "timedatectl",
                    "nvidia-smi", "ip", "networkctl"}
"""复合型/受限二进制基名：裸名行与未登记 flag/写子命令前缀一律不允许出现。"""


def test_b1_gate_has_teeth():
    """F-20 教训的牙口锁定：把 B-1 缺陷条目（裸 systemctl + flag 前缀条）
    人为掺进渲染输入，上面的断言体必须炸——无牙测试比没有测试更糟。"""
    defect = {("df", None), ("systemctl", None),      # B-1 本体：复合型裸名
              ("systemctl", "--failed"),              # F-19：flag 前缀通配
              ("numactl", None)}                      # F-23 root shell 执行器
    bp = {"df": "/usr/bin/df", "systemctl": "/usr/bin/systemctl",
          "numactl": "/usr/bin/numactl"}
    text = G.render_sudoers_file(defect, user="opsaxiom-ro", bin_paths=bp)
    # 逐字复放 test_b1_write_subcommand_physically_absent 的断言核心
    violations = []
    for line in text.splitlines():
        if line.startswith("#") or "NOPASSWD" not in line:
            continue
        for spec in line.split("NOPASSWD: ", 1)[1].split(", "):
            parts = spec.split()
            if not parts:
                continue
            base = parts[0].rsplit("/", 1)[-1]
            if base in _COMPOSITE_BASES and len(parts) == 1:
                violations.append(spec)
            if len(parts) >= 2 and parts[1].startswith("-") and \
                    base in _COMPOSITE_BASES:
                violations.append(spec)
    assert violations, "牙口失效：掺入 B-1 缺陷条目未被断言体拦截（F-20 复发）"


def test_flag_prefix_form_fail_closed():
    """v3（F-19）语义反转：flag 前缀不再是放行形态——flag 与命令词正交
    （systemd CLI），`--failed *` 这类条目挡不住其后的写子命令/写 flag。
    flag 探针（systemctl --failed / journalctl -u / --disk-usage）零条目：
    需日志的服务取证转贴回或 target grant 升 root 档。"""
    e = G.extract_entries("systemctl --failed --type=mount 2>/dev/null | grep -c x")
    assert e == set()                              # flag 前缀 fail-closed（F-19）
    e2 = G.extract_entries("journalctl -u cron --since '-1h'")
    assert e2 == set()                             # 同上：-u 是 flag 不可前缀
    e3 = G.extract_entries("journalctl --disk-usage")
    assert e3 == set()
    e4 = G.extract_entries("nvidia-smi --query-gpu=count --format=csv,noheader")
    assert e4 == set()                             # flag 形态同禁（写 flag 同源）

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
    """B-1 后语义：裸名条目只可能来自非复合型二进制；复合型只有带子命令/
    flag 的受限条目（`*` 尾通配 + 裸前缀两形态）。"""
    entries = {("df", None), ("systemctl", "is-active")}
    text = G.render_sudoers_file(entries, user="opsaxiom-ro")
    assert "systemctl is-active *" in text
    assert "systemctl is-active" in text
    assert "NOPASSWD: df" in text                       # 非复合型保持裸名全参
    assert "systemctl restart" not in text              # 写子命令物理不在场



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
