"""
enroll.py —— SSH 首次开通（I-4 / docs/12 §3.5，整合进 target add 的 ssh 分支）。

流程（由 target_cli._cmd_add 依序调用）：
  1. ensure_local_key()        本机密钥（无则 ssh-keygen 生成 ed25519）
  2. connect_with_password()   密码 paramiko 上门一次（getpass，零痕迹——用完即 del）
  3. install_pubkey()          公钥写入该账号 authorized_keys（幂等 grep 去重）
  4. probe_os()                uname 探测 os
  5. create_ro_user()（可选）  建 opsaxiom-ro + 公钥 + /etc/sudoers.d/opsaxiom-ro
                               （sudoer_bins → 远端 command -v 解析绝对路径 →
                               visudo -c 校验 → 过了才落位；白名单来自
                               gen_sudoers 扫 registry Skill 命令集）
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


def install_pubkey_root(cli, pub):
    """公钥写入 root 的 authorized_keys（公钥双装，方案 A 升档通道）：
    grant 后切 admin 直登时不再要密码。需当前账号能免密 sudo（enroll 通行证
    本就有）。幂等：grep 精确去重。返回 (ok, err)。"""
    cmd = ("mkdir -p /root/.ssh && chmod 700 /root/.ssh && touch /root/.ssh/authorized_keys"
           f" && grep -qxF {shq(pub)} /root/.ssh/authorized_keys 2>/dev/null"
           f" || echo {shq(pub)} >> /root/.ssh/authorized_keys;"
           " chmod 600 /root/.ssh/authorized_keys")
    rc, out, err = rc_out(cli, f"sudo sh -c {shq(cmd)}", timeout=15)
    return rc == 0, err.strip()[:200]


def probe_os(cli):
    """uname -s 探测目标 os（linux/darwin/freebsd）。失败返回 None。"""
    rc, out, _err = rc_out(cli, "uname -s", timeout=10)
    return out.strip().lower() or None


# ---------- 可选：只读账号 opsaxiom-ro + sudoers 白名单 ----------

def resolve_bin_paths(cli, bins):
    """在目标机逐 bin `command -v` 解析绝对路径（sudoers 要求绝对路径，
    本机无法预知发行版路径）。返回 {裸名: 绝对路径}；解析不到的 bin 不入表。"""
    paths = {}
    for b in bins:
        rc, out, _err = rc_out(cli, f"command -v {shq(b)}", timeout=10)
        p = out.strip()
        if rc == 0 and p.startswith("/"):
            paths[b] = p
    return paths


def create_ro_user(cli, pub, sudoers_text, user=ENROLL_USER, sudoer_bins=None):
    """建只读账号（useradd）+ 装公钥 +（可选）写 sudoers 白名单。
    需当前账号有 root 直登或 sudo 免密。返回 (ok, err)。
    sudoer_bins 提供时：先在目标机 `command -v` 解析绝对路径重渲染白名单，
    写 /tmp → visudo -cf 校验 → 过了才 install 落正位（440）——
    校验不过不会污染 /etc/sudoers.d（"坏白名单比没有白名单糟"红线，
    真机裸名 syntax error 教训）。"""
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
    if sudoer_bins:
        import gen_sudoers as G
        # sudoer_bins 可能为 [(bin, prefix), ...] 完整条目（新）或 [bin, ...]
        # 裸名清单（旧签名兼容）。前缀决定远端 sudoers 的参数范围（B-1：
        # 复合型二进制 systemctl/ip 等若丢前缀 = 任意子命令提权，物理闸穿透）。
        entries = []
        for item in sudoer_bins:
            if isinstance(item, tuple):
                entries.append(item)
            else:
                entries.append((item, None))
        bins = sorted({b for b, _p in entries})
        paths = resolve_bin_paths(cli, bins)
        if not paths:
            return False, "白名单命令在目标机全部解析不到绝对路径（command -v 全空）"
        path_text = G.render_sudoers_file(
            [e for e in entries if e[0] in paths],
            user=user, bin_paths=paths)
        cmds += [
            # 临时文件 → visudo 校验 → 过了才 install 落位
            f"echo {shq(path_text.rstrip(chr(10)))} | "
            f"sudo tee /tmp/opsaxiom-ro.sudoers.tmp > /dev/null",
            f"sudo visudo -cf /tmp/opsaxiom-ro.sudoers.tmp",
            f"sudo install -m 440 /tmp/opsaxiom-ro.sudoers.tmp /etc/sudoers.d/opsaxiom-ro",
            f"rm -f /tmp/opsaxiom-ro.sudoers.tmp",
        ]
    elif sudoers_text:
        # 裸名预览版直接写入是禁止的（真机教训）；此分支仅兼容旧签名不再使用
        return False, "裸名 sudoers 不允许直接写入（需 sudoer_bins 走路径解析）"
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
    只用 file 认证（不经 agent/config），与 targets.yaml 最终 auth 一致。
    可复用于验证任意账号的密钥通道（如 root 通道，方案 A 升档闸门）。"""
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
