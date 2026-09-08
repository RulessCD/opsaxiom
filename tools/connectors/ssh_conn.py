"""
ssh_conn.py —— SSH 连接器（docs/12 §4，I-1）。

只负责"拨通并执行"，不做任何安全判断——白名单/授权/审计全在 gate.py（执行门）里。
这是 docs/12 §4 的架构决定：连接器可替换，安全逻辑只写一次。

硬化（每条都缩小攻击面）：
  - 无 tty（get_pty=False）：不给交互 shell；
  - 禁 agent 转发、禁端口转发（不 open_forward，不 request agent）；
  - 连接与执行超时（默认 10s）；
  - 只认凭证【引用】解析出来的 Credential（access.resolve），自己不碰密码。

支持 auth：agent（SSH_AUTH_SOCK 里的密钥）/ ssh_config（~/.ssh/config 的
ProxyJump/IdentityFile/User/Port 原样生效）/ file（指定私钥文件）。
"""
import logging
import pathlib
import socket

import paramiko

# transport 后台线程的异常（banner reset 等）已由 exec_readonly 转成带解释的
# SSHError 抛给上层；paramiko 自己再打的那段 stderr traceback 是纯噪声（真机
# 教训：VPN 抖动一次刷两屏栈）。此模块被 import 即静音 paramiko 全局日志。
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


class SSHError(Exception):
    pass


class SSHConnectError(SSHError):
    """连接建立失败（握手/banner/认证/网络不可达）——与"命令已执行但失败"区分：
    连不上时目标基本不可达，上层应一次性给修复指引，不应逐条转人工贴回
    （真机 banner reset 教训：VPN 抖动一次，11 条探针差点盘问 11 轮）。"""


def _client_for(host, user, port, cred, timeout):
    """按 Credential 建立一个 SSHClient（含 ssh_config 的 ProxyJump 处理）。"""
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.RejectPolicy())   # 未知主机拒绝（不盲信）
    cli.load_system_host_keys()
    uk = pathlib.Path.home() / ".ssh" / "known_hosts"          # 用户 known_hosts（与 ssh 一致）
    if uk.exists():
        cli.load_host_keys(str(uk))
    kw = dict(hostname=host, username=user, port=port, timeout=timeout,
              banner_timeout=timeout, auth_timeout=timeout,
              allow_agent=(cred.kind == "agent"),
              look_for_keys=(cred.kind in ("agent", "ssh_config")))
    sock = None
    if cred.kind == "ssh_config" and cred.get("config_path"):
        cfg = paramiko.SSHConfig()
        cfg.parse(open(cred.get("config_path"), encoding="utf-8"))
        h = cfg.lookup(host)
        kw["hostname"] = h.get("hostname", host)
        if h.get("user"):
            kw["username"] = h["user"]
        if h.get("port"):
            kw["port"] = int(h["port"])
        if h.get("identityfile"):
            kw["key_filename"] = h["identityfile"]
        if h.get("proxyjump"):
            sock = _proxy_sock(h["proxyjump"], cfg, timeout)
    elif cred.kind == "file":
        kw["key_filename"] = cred.get("path")
        kw["look_for_keys"] = False
        kw["allow_agent"] = False
    if sock is not None:
        kw["sock"] = sock
    cli.connect(**kw)
    return cli


def _proxy_sock(jump, cfg, timeout):
    """多级 ProxyJump：为最终目标建一条经跳板的 channel。"""
    hop = jump.split(",")[0].strip()          # 首跳（paramiko 逐跳需递归，首版支持单跳）
    h = cfg.lookup(hop)
    jcli = paramiko.SSHClient()
    jcli.set_missing_host_key_policy(paramiko.RejectPolicy())
    jcli.load_system_host_keys()
    jcli.connect(hostname=h.get("hostname", hop), username=h.get("user"),
                 port=int(h.get("port", 22)), timeout=timeout,
                 key_filename=h.get("identityfile"))
    return jcli.get_transport().open_channel(
        "direct-tcpip", (cfg.lookup(hop).get("hostname", hop), 22), ("", 0))


def exec_readonly(target, cred, cmd, timeout=60):
    """执行一条命令，返回 (exit_code, stdout, stderr)。不做安全判断（gate 已做）。

    target: targets.yaml 里的条目（含 host/user/port）。
    timeout: 命令执行超时；连接握手单独限制在 15s（连不上快速失败）。
    """
    host = target.get("host")
    user = target.get("user")
    port = int(target.get("port", 22))
    if not host and cred.kind != "ssh_config":
        raise SSHError("ssh 目标缺 host")
    cli = None
    connected = False
    try:
        cli = _client_for(host or target.get("name", ""), user, port, cred,
                          timeout=min(timeout, 15))
        connected = True
        # 无 pty、无转发；命令原样执行（安全已在 gate 校验）
        _in, _out, _err = cli.exec_command(cmd, timeout=timeout, get_pty=False)
        out = _out.read().decode("utf-8", "replace")
        err = _err.read().decode("utf-8", "replace")
        rc = _out.channel.recv_exit_status()
        return rc, out, err
    except paramiko.SSHException as e:
        if not connected:
            raise SSHConnectError(f"SSH 连接失败：{e}") from e
        raise SSHError(f"SSH 执行失败：{e}") from e
    except (socket.timeout, ConnectionError, OSError) as e:
        # 三类底层网络故障：执行超时（str 本身为空）/ 连接被对端掐（banner reset、
        # VPN 抖动）。都要给带解释的消息（真机教训：空 err 或裸栈漏到报告不可读）
        if isinstance(e, socket.timeout):
            if not connected:
                raise SSHConnectError(f"SSH 连接超时（>{min(timeout, 15)}s 拨不通）——"
                                      f"检查网络/VPN 或 target doctor") from e
            raise SSHError(f"命令执行超时（>{timeout}s）——重活考虑贴回人工执行") from e
        raise SSHConnectError(f"网络连接失败（{type(e).__name__}: {e}）——"
                              f"检查 VPN/reach（target doctor）后重试") from e
    finally:
        if cli is not None:
            cli.close()
