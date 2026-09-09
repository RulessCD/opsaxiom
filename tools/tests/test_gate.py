"""I-1 执行门对抗测试：写命令/注入/未授权拒绝，凭证不入审计。"""
import json
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import sweep  # noqa: E402


def _setup(tmp_path, monkeypatch, authorized=True):
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/fake-agent.sock")  # resolve(agent) 需要
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {
        "web-01": {"connector": "ssh", "host": "10.0.0.9", "user": "opsaxiom-ro",
                   "auth": "agent"},
    }}, allow_unicode=True), encoding="utf-8")
    if authorized:
        (tmp_path / "trust.yaml").write_text(
            yaml.safe_dump({"auto_exec": ["web-01"]}), encoding="utf-8")
    # 记录凭证材料，供"凭证不入审计"断言
    SECRET = "SECRET-KEY-MATERIAL-xyz"

    def fake_conn(target, cred, cmd):
        # 连接器不做安全判断——只回显；把秘密塞进 cred 以验证它不外泄
        cred._kw["material"] = SECRET
        return 0, f"ran:{cmd}", ""
    return fake_conn, SECRET


def test_readonly_command_passes(tmp_path, monkeypatch):
    fn, _ = _setup(tmp_path, monkeypatch)
    out = gate.run_remote("web-01", "cat /proc/loadavg", connector_fn=fn, now="T")
    assert out == "ran:cat /proc/loadavg"


@pytest.mark.parametrize("cmd", [
    "rm -rf /data", "tee /etc/passwd", "echo x > /etc/hosts",
    "cat /etc/shadow && reboot", "dd if=/dev/zero of=/dev/sda",
])
def test_write_commands_rejected(tmp_path, monkeypatch, cmd):
    fn, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(gate.GateError):
        gate.run_remote("web-01", cmd, connector_fn=fn, now="T")


def test_param_injection_rejected(tmp_path, monkeypatch):
    fn, _ = _setup(tmp_path, monkeypatch)
    # 命令本身过只读白名单（grep 前导、无写词），但 param 值含命令替换元字符——
    # 这正是 T-3 要拦的：模板可信、param 值不可信。
    cmd = "grep abc$(id) /var/log/app.log"
    with pytest.raises(gate.GateError, match="注入"):
        gate.run_remote("web-01", cmd, params={"pat": "abc$(id)"},
                        connector_fn=fn, now="T")


def test_unauthorized_target_rejected(tmp_path, monkeypatch):
    fn, _ = _setup(tmp_path, monkeypatch, authorized=False)
    with pytest.raises(gate.GateError, match="未授权"):
        gate.run_remote("web-01", "uptime", connector_fn=fn, now="T")


def test_unknown_target_rejected(tmp_path, monkeypatch):
    fn, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(gate.GateError, match="未知目标"):
        gate.run_remote("nope", "uptime", connector_fn=fn, now="T")


def test_credential_never_in_audit(tmp_path, monkeypatch):
    fn, SECRET = _setup(tmp_path, monkeypatch)
    gate.run_remote("web-01", "uptime", connector_fn=fn, now="T")
    audit = (tmp_path / "audit" / "remote.jsonl").read_text(encoding="utf-8")
    assert SECRET not in audit                       # 凭证材料绝不落审计
    rec = json.loads(audit.splitlines()[-1])
    assert rec["decision"] == "allow" and rec["cred_kind"] == "agent"
    assert "material" not in audit and "sock" not in audit


def test_denials_are_audited(tmp_path, monkeypatch):
    fn, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(gate.GateError):
        gate.run_remote("web-01", "rm -rf /", connector_fn=fn, now="T")
    rec = json.loads((tmp_path / "audit" / "remote.jsonl").read_text().splitlines()[-1])
    assert rec["decision"] == "deny"                 # 被拦的写命令也留痕（安全事件）


# ---------- B 轮 v2：档位切换（root 档直登 / 白名单档 sudo 路由 / 名单外贴回） ----------

def _setup_wl(tmp_path, monkeypatch, allowed_bins, target_user="opsaxiom-ro",
              sudo_whitelist=True, monkey_reg=True, authorized=False,
              admin_user=None):
    """sudo_whitelist 目标 + 可控白名单 registry 缓存。authorized 控制 root 档。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/fake-agent.sock")
    t = {"connector": "ssh", "host": "10.0.0.9", "user": target_user, "auth": "agent"}
    if sudo_whitelist:
        t["sudo_whitelist"] = True
    if admin_user:
        t["admin_user"] = admin_user
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {"web-01": t}},
                                               allow_unicode=True), encoding="utf-8")
    if authorized:
        # grant_trust 用当前时间，TTL 恒未过期（手写 granted_at 会随真实时间过期）
        sweep.grant_trust("web-01", ttl_days=30, scope="readonly")
    # 造一个 mini registry：一个 skill 用 df（名单内），一个用 lsof（可不在名单）
    if monkey_reg:
        sk = tmp_path / "hub" / "registry" / "skills" / "t.x" / "0.1.0" / "skill.yaml"
        sk.parent.mkdir(parents=True)
        sk.write_text(yaml.safe_dump({
            "metadata": {"id": "t.x"}, "tree": {"entry": "c", "nodes": [
                {"id": "c", "type": "check", "run": {"linux": f"{sorted(allowed_bins)[0]} /x"}}]}}),
            encoding="utf-8")

    def fake_conn(target, cred, cmd):
        return 0, f"ran:{target.get('user')}:{cmd}", ""
    return fake_conn


def test_whitelist_tier_sudo_prefixes_member(tmp_path, monkeypatch):
    """白名单档（未授权）+ 名单内命令 → ro 账号 + 首段 sudo -n，审计 tier=whitelist。"""
    fn = _setup_wl(tmp_path, monkeypatch, {"df"})
    out = gate.run_remote("web-01", "df -B1 /", connector_fn=fn, now="T")
    assert out == "ran:opsaxiom-ro:sudo -n df -B1 /"
    rec = json.loads((tmp_path / "audit" / "remote.jsonl").read_text().splitlines()[-1])
    assert rec["via_sudo"] is True and rec["tier"] == "whitelist"
    assert rec["exec_as"] == "opsaxiom-ro"


def test_whitelist_tier_non_member_raises_not_allowed(tmp_path, monkeypatch):
    """白名单档 + 名单外命令 → GateRemoteNotAllowed（能力边界转贴回，非安全拒绝）。"""
    fn = _setup_wl(tmp_path, monkeypatch, {"df"})
    with pytest.raises(gate.GateRemoteNotAllowed):
        gate.run_remote("web-01", "lsof -i", connector_fn=fn, now="T")


def test_root_tier_direct_admin_login(tmp_path, monkeypatch):
    """root 档（已 grant）+ admin_user → 切管理账号直登，命令原样（不查名单、不加 sudo）。"""
    fn = _setup_wl(tmp_path, monkeypatch, {"df"}, sudo_whitelist=True,
                   authorized=True, admin_user="root")
    out = gate.run_remote("web-01", "lsof -i", connector_fn=fn, now="T")
    assert out == "ran:root:lsof -i"                      # 名单外也自动，admin 直登
    rec = json.loads((tmp_path / "audit" / "remote.jsonl").read_text().splitlines()[-1])
    assert rec["tier"] == "root" and rec["exec_as"] == "root" and rec["via_sudo"] is False


def test_root_tier_without_admin_user_uses_login_user(tmp_path, monkeypatch):
    """root 档但无 admin_user 字段（旧条目）→ 用 targets.yaml 的 user 直登，不加 sudo。
    （登录用户本就非 ro——非白名单目标或 root 登录，通道天然存在。）"""
    fn = _setup_wl(tmp_path, monkeypatch, {"df"}, sudo_whitelist=True,
                   authorized=True, target_user="root")
    out = gate.run_remote("web-01", "lsof -i", connector_fn=fn, now="T")
    assert out == "ran:root:lsof -i"


def test_granted_ro_without_admin_stays_whitelist_tier(tmp_path, monkeypatch):
    """已 grant 但 ro 账号且无 admin_user（旧流程开通）→ "没通道不给假 root"：
    仍按白名单档路由（名单内 sudo、名单外 GateRemoteNotAllowed），
    grant 只免去授权问答、不改变执行身份。"""
    fn = _setup_wl(tmp_path, monkeypatch, {"df"}, sudo_whitelist=True,
                   authorized=True)          # ro 账号、无 admin_user、已 grant
    out = gate.run_remote("web-01", "df -B1 /", connector_fn=fn, now="T")
    assert out == "ran:opsaxiom-ro:sudo -n df -B1 /"   # 白名单档路径，不假 root
    with pytest.raises(gate.GateRemoteNotAllowed):
        gate.run_remote("web-01", "lsof -i", connector_fn=fn, now="T")


def test_unauthorized_non_member_target_still_denied(tmp_path, monkeypatch):
    """非白名单目标未授权 → 还是"未授权"拒绝（ denies 也审计）。"""
    fn = _setup_wl(tmp_path, monkeypatch, {"df"}, sudo_whitelist=False)
    with pytest.raises(gate.GateError, match="未授权"):
        gate.run_remote("web-01", "uptime", connector_fn=fn, now="T")


def test_sudo_route_not_applied_to_root_target(tmp_path, monkeypatch):
    """root 直登目标（无 sudo_whitelist 标记、未授权）→ 未授权拒绝（不进白名单档）。"""
    fn = _setup_wl(tmp_path, monkeypatch, {"df"}, target_user="root",
                   sudo_whitelist=False)
    with pytest.raises(gate.GateError, match="未授权"):
        gate.run_remote("web-01", "df -B1 /", connector_fn=fn, now="T")


def test_sudo_routed_predicate_static_semantics(tmp_path, monkeypatch):
    """sudo_routed 谓词是纯静态判定（目标能力+命令成员），不读 trust——
    root 档/白名单档的选择由调用方（execute_mixed/gate.run_remote）先判授权。"""
    _setup_wl(tmp_path, monkeypatch, {"df"}, monkey_reg=True)
    assert gate.sudo_routed("web-01", "df -B1 /") is True      # 白名单目标+名单内
    assert gate.sudo_routed("web-01", "lsof -i") is False      # 名单外
    sweep.revoke_trust("web-01")
    assert gate.sudo_routed("web-01", "df -B1 /") is True      # trust 不影响谓词


# ---------- 十七轮返工：B-1 复合型前缀 + err_kind 结构化（裁定 3）----------

def _setup_wl_entries(tmp_path, monkeypatch, entries, cmd_probe):
    """可控白名单 registry：直接给定 gen_sudoers 条目（比 _setup_wl 灵活，
    复合型前缀测试需精确控制 (bin, prefix) 集合）。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    sk = tmp_path / "hub" / "registry" / "skills" / "t.x" / "0.1.0" / "skill.yaml"
    sk.parent.mkdir(parents=True, exist_ok=True)
    sk.write_text(yaml.safe_dump({
        "metadata": {"id": "t.x"}, "tree": {"entry": "c", "nodes": [
            {"id": "c", "type": "check", "run": {"linux": cmd_probe}}]}}),
        encoding="utf-8")
    return entries


def test_wl_member_composite_prefix_scoped(tmp_path, monkeypatch):
    """B-1/v3：复合型二进制（systemctl）成员判定须带【已登记只读子命令】——
    未登记写子命令（restart）、flag 前缀（--failed，F-19）、非登记裸形态
    一律 False（远端物理闸同源）。"""
    _setup_wl_entries(tmp_path, monkeypatch, [], "systemctl is-active x")
    assert gate._wl_member("systemctl is-active nginx") is True
    assert gate._wl_member("systemctl restart nginx") is False   # 写子命令未登记
    assert gate._wl_member("systemctl") is False                 # 无前缀 fail-closed
    # F-19 核心：flag 打头穿透写子命令——客户端必须与远端同拒（都不可能有
    # --failed 前缀条目，物理上远端也不会放行）
    assert gate._wl_member("systemctl --failed restart nginx") is False
    assert gate._wl_member("df -B1 /") is False                  # 不在名单
    # 非复合型裸名即过：再造 df 条目
    _setup_wl_entries(tmp_path, monkeypatch, [], "df -B1 /")
    assert gate._wl_member("df -B1 /") is True


def test_wl_member_prefix_mirror_matches_sudoers(tmp_path, monkeypatch):
    """客户端成员判定与远端 sudoers 条目【对称】（F-18/F-21 异议修复）：
    遍历代表形态集合，客户端 True ⟺ sudoers fnmatch 该命令 args 命中。
    覆盖子命令前缀/flag 打头/裸名/写子命令/带参/无参各象限——对称性破坏
    （客户端 True/远端 False = 死路由；客户端 False/远端 True = 白名单资产
    无谓贴回）任何一边都应转红。"""
    _setup_wl_entries(tmp_path, monkeypatch, [], "systemctl is-active x")
    import gen_sudoers as G
    import fnmatch
    entries = gate._allow_entries()
    assert ("systemctl", "is-active") in entries
    text = G.render_sudoers_file(sorted(entries), user="opsaxiom-ro",
                                 bin_paths={"systemctl": "/usr/bin/systemctl",
                                            "df": "/usr/bin/df"})
    specs = []
    for line in text.splitlines():
        if line.startswith("#") or "NOPASSWD" not in line:
            continue
        raw = line.split("NOPASSWD: ", 1)[1].split(", ")
        specs = [s.replace("/usr/bin/", "", 1) for s in raw]
        break

    def remote_allows(shape):
        """sudoers(5) 语义近似：spec 'bin ARGS' 对命令 bin ARGS 做 fnmatch——
        尾 `*` 通配剩余整串；无参条目只匹配无参调用。"""
        first, _, args = shape.partition(" ")
        for s in specs:
            sname, _, sargs = s.partition(" ")
            if sname != first:
                continue
            if fnmatch.fnmatch(args, sargs):
                return True
        return False

    for shape in ["systemctl is-active x", "systemctl is-active",
                  "systemctl restart nginx", "systemctl",
                  "systemctl --failed x", "df -B1 /", "df", "df -h /x"]:
        remote = remote_allows(shape)
        client = gate._wl_member(shape)
        # 方向一：客户端 True 但远端拒 → 死路由（F-18 型功能回滚）
        assert not (client and not remote), \
            f"死路由（客户端放行/远端拒绝）: {shape!r} sudoers specs={specs}"
        # 方向二：远端放行而客户端拒 = 无谓贴回（镜像失真）
        assert not (remote and not client), \
            f"镜像失真（客户端过严）: {shape!r} sudoers specs={specs}"


def test_gate_audits_error_kind(tmp_path, monkeypatch):
    """连接器异常审计带 err_kind；SSHError（exec 级）不误标 connect。"""
    _setup_wl_entries(tmp_path, monkeypatch, [], "df -B1 /")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/fake-agent.sock")
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {
        "web-01": {"connector": "ssh", "host": "10.0.0.9", "user": "opsaxiom-ro",
                   "auth": "agent", "sudo_whitelist": True},
    }}, allow_unicode=True), encoding="utf-8")

    def boom(target, cred, cmd):
        from connectors.ssh_conn import SSHError
        raise SSHError("SSH 执行失败：x")
    with pytest.raises(Exception):
        gate.run_remote("web-01", "df -B1 /", connector_fn=boom, now="T")
    rec = json.loads((tmp_path / "audit" / "remote.jsonl").read_text().splitlines()[-1])
    assert rec["decision"] == "error" and rec["err_kind"] == "exec"


# ---------- Fable 裁定 3 对抗：错误文本不可作为 fail-fast 信号 ----------

def test_rc_level_failure_with_connect_word_is_exec_not_connect(tmp_path, monkeypatch):
    """对抗（裁定 3 核心）：rc 级失败的消息拼了远端 stderr——中文报错常含
    "连接"二字，若靠文本匹配会误判成连接级。err_kind 按异常类判定：
    SSHError("...连接...") 必须是 exec，而不是 connect。"""
    _setup_wl_entries(tmp_path, monkeypatch, [], "df -B1 /")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/fake-agent.sock")
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": {
        "web-01": {"connector": "ssh", "host": "10.0.0.9", "user": "opsaxiom-ro",
                   "auth": "agent", "sudo_whitelist": True},
    }}, allow_unicode=True), encoding="utf-8")

    def boom(target, cred, cmd):
        from connectors.ssh_conn import SSHError
        raise SSHError("远端返回码 2：无法连接到数据库服务器")   # rc 级，但含"连接"
    with pytest.raises(Exception):
        gate.run_remote("web-01", "df -B1 /", connector_fn=boom, now="T")
    rec = json.loads((tmp_path / "audit" / "remote.jsonl").read_text().splitlines()[-1])
    assert rec["err_kind"] == "exec"          # 文本含"连接"≠连接级
    assert gate.err_kind(Exception("连接超时")) == "exec"   # 裸 Exception 同样从宽

