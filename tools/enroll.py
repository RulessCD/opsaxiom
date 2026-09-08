"""
enroll.py —— SSH 首次开通（I-4 / docs/12 §3.5，整合进 target add 的 ssh 分支）。

流程（由 target_cli._cmd_add 依序调用）：
  1. ensure_local_key()        本机密钥（无则 ssh-keygen 生成 ed25519）
  2. connect_with_password()   密码 paramiko 上门一次（getpass，零痕迹——用完即 del）
  3. install_pubkey()          公钥写入该账号 authorized_keys（幂等 grep 去重）
  4. probe_os()                uname 探测 os
  5. create_ro_user()（可选）  建 opsaxiom-ro + 公钥（低权账号，不带 sudo）
                               （sudoers 白名单待 runtime 接线后启用，docs/12 §5.6）
  6. verify_key_login()        改用密钥重连 + 只读探针验证

安全红线：
  密码零痕迹：getpass 输入（不回显）、仅函数局部变量、用完即 del；
  不落 targets.yaml / 日志 / 审计（对抗测试 grep 全工程文件树）。
"""
import pathlib
import subprocess

DEFAULT_KEY_NAME = "id_ed25519"
ENROLL_USER = "opsaxiom-ro"


class EnrollError(Exception):
    pass


def shq(s):
    """单引号 shell 转义（'→'"'"'）。把任意内容安全带过 ssh exec。"""
    return "'" + str(s).replace("'", "'\\''") + "'"


# ---------- 本机密钥 ----------

def find_local_key(home=None):
    """本机已有私钥则返回路径（首选 ed25519），否则 None。"""
    home = pathlib.Path(home) if home else pathlib.Path.home()
    for name in (DEFAULT_KEY_NAME, "id_rsa", "id_ecdsa"):
        p = home / ".ssh" / name
        if p.exists():
            return p
    return None


def ensure_local_key(home=None):
    """无默认私钥则生成 ed25519（空口令；打印提示如需口令自行 ssh-keygen 替换）。
    返回 (key_path, created: bool)。失败抛 EnrollError。"""
    existing = find_local_key(home)
    if existing:
        return existing, False
    home = pathlib.Path(home or pathlib.Path.home())
    key_path = home / ".ssh" / DEFAULT_KEY_NAME
    key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    r = subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "",
                        "-C", "opsaxiom-enroll"], capture_output=True, text=True)
    if r.returncode != 0:
        raise EnrollError(f"ssh-keygen 失败：{r.stderr.strip()[:150]}")
    return key_path, True


def pubkey_of(key_path):
    """读配对公钥。缺 .pub 报错（可 ssh-keygen -y 恢复）。"""
    pub = pathlib.Path(str(key_path) + ".pub")
    if not pub.exists():
        raise EnrollError(f"找不到公钥 {pub}（私钥 {key_path} 存在但 .pub 缺失）")
    return pub.read_text(encoding="utf-8").strip()


# ---------- 密码一次性上门 ----------

def connect_with_password(host, port, username, password, timeout=15):
    """密码认证建连（enroll 专用一次性通道）。只信密码，不试探密钥。
    返回 paramiko client（调用方负责 close）。
    密码只存在于调用栈内存；本模块不落任何持久化。"""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import paramiko
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # 首次开通：主机未在 known_hosts 属预期
    cli.connect(hostname=host, port=int(port), username=username,
                password=password, timeout=timeout,
                banner_timeout=timeout, auth_timeout=timeout,
                allow_agent=False, look_for_keys=False)
    return cli


def rc_out(cli, cmd, timeout=30):
    """exec 一条命令 → (rc, stdout, stderr)。"""
    _in, _out, _err = cli.exec_command(cmd, timeout=timeout)
    out = _out.read().decode("utf-8", "replace")
    err = _err.read().decode("utf-8", "replace")
    rc = _out.channel.recv_exit_status()
    return rc, out, err


def install_pubkey(cli, pub):
    """公钥写入【当前登录账号】authorized_keys（幂等：grep 精确去重）。
    返回 (ok: bool, err: str)。"""
    cmd = ("mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys"
           f" && grep -qxF {shq(pub)} ~/.ssh/authorized_keys 2>/dev/null"
           f" || echo {shq(pub)} >> ~/.ssh/authorized_keys;"
           " chmod 600 ~/.ssh/authorized_keys")
    rc, out, err = rc_out(cli, cmd)
    return rc == 0, err.strip()[:200]


def probe_os(cli):
    """uname -s 探测目标 os（linux/darwin/freebsd）。失败返回 None。"""
    rc, out, _err = rc_out(cli, "uname -s", timeout=10)
    return out.strip().lower() or None


# ---------- 可选：只读账号 opsaxiom-ro + sudoers 白名单 ----------

def create_ro_user(cli, pub, sudoers_text, user=ENROLL_USER):
    """建只读账号（useradd）+ 装公钥 + 写 sudoers 白名单。
    需当前账号有 root 直登或 sudo 免密。返回 (ok, err)。"""
    home = f"/home/{user}"
    cmds = [
        # 已存在则幂等跳过
        f"id -u {user} >/dev/null 2>&1 || useradd -m -s /bin/bash {shq(user)}",
        f"mkdir -p {home}/.ssh && chmod 700 {home}/.ssh"
        f" && touch {home}/.ssh/authorized_keys",
        f"grep -qxF {shq(pub)} {home}/.ssh/authorized_keys 2>/dev/null"
        f" || echo {shq(pub)} >> {home}/.ssh/authorized_keys",
        f"chmod 700 {home}/.ssh && chmod 600 {home}/.ssh/authorized_keys",
        f"chown -R {user}:{user} {home}/.ssh",
    ]
    if sudoers_text:
        # sudoers.d 内容经 stdin（tee）写 root 文件，440 权限
        cmds += [
            f"echo {shq(sudoers_text.rstrip(chr(10)))} | "
            f"sudo tee /etc/sudoers.d/opsaxiom-ro > /dev/null",
            "chmod 440 /etc/sudoers.d/opsaxiom-ro",
        ]
    return _run_all(cli, cmds)


def _run_all(cli, cmds):
    """顺序执行，任一失败即停。返回 (ok, err)。"""
    for c in cmds:
        rc, out, err = rc_out(cli, c)
        if rc != 0:
            return False, f"远端失败：{c[:70]}… → {err.strip()[:120]}"
    return True, ""


# ---------- 密钥验证（收尾） ----------

def verify_key_login(host, port, username, key_path, timeout=15):
    """改用密钥重连 + 跑一条只读探针。返回 (ok, err)。
    只用 file 认证（不经 agent/config），与 targets.yaml 最终 auth 一致。"""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import paramiko
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(hostname=host, port=int(port), username=username,
                    key_filename=str(key_path), timeout=timeout,
                    banner_timeout=timeout, auth_timeout=timeout,
                    allow_agent=False, look_for_keys=False)
        rc, out, err = rc_out(cli, "df -B1 /", timeout=15)
        if rc != 0:
            return False, f"探针执行失败 rc={rc}: {err.strip()[:120]}"
        return True, ""
    except Exception as e:
        return False, f"密钥登录失败：{e}"
    finally:
        try:
            cli.close()
        except Exception:
            pass
