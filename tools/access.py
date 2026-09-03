"""
access.py —— 设备清单（targets.yaml）加载与凭证引用解析（docs/12，I-0 金标准）。

三条不可协商的红线（本模块结构性强制，测试对抗覆盖）：
  R-A1 清单只存引用：targets.yaml 出现明文凭证形态（password/token/secret 字段、
       auth 里带冒号值直给密码等）→ 整个文件拒绝加载，指出违规行。
  R-A2 凭证不出内存：resolve() 返回的凭证对象禁止落盘/入日志——repr 打码，
       且不提供序列化方法。
  R-A3 只读默认：target 的 scope 只有 readonly 一种（写操作不进自动通道，
       docs/12 §7 结构性保证）。

auth 引用语法（唯一合法形态）：
  agent            用 ssh-agent 里的密钥
  ssh_config       全权委托 ~/.ssh/config（ProxyJump/IdentityFile 原样生效）
  kubeconfig       复用 ~/.kube/config（可配 context 锁定）
  keyring:<name>   本机系统钥匙串条目（P1，解析由 cred.py 提供，此处只认语法）
  file:<path>      指向密钥文件的路径（路径本身不是秘密）
"""
import os
import pathlib
import re

import yaml

VALID_CONNECTORS = ("ssh", "network", "kubectl", "http")
VALID_OS = {"linux", "darwin", "windows", "freebsd", "macos"}
# auth 引用的合法语法（白名单——不在这里面的形态一律拒绝）
_AUTH_RE = re.compile(r"^(agent|ssh_config|kubeconfig|keyring:[\w.-]+|file:[\w./~-]+)$")
# 明文凭证嗅探：这些键名出现在 target 里即拒绝（R-A1）
_FORBIDDEN_KEYS = {"password", "passwd", "pass", "token", "secret", "api_key", "apikey"}


class AccessError(Exception):
    pass


def _home():
    return pathlib.Path(os.environ.get("OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom"))


def targets_file():
    return _home() / "targets.yaml"


def load_targets(path=None):
    """加载并校验设备清单。任何一条违规 → 整个文件拒绝加载（红线不做部分放行）。"""
    f = pathlib.Path(path) if path else targets_file()
    if not f.exists():
        return {}
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    targets = data.get("targets", {})
    errs = []
    for name, t in targets.items():
        if not isinstance(t, dict):
            errs.append(f"{name}: 条目必须是映射"); continue
        # R-A1 明文凭证嗅探（键名 + auth 值形态双重检查）
        bad_keys = _FORBIDDEN_KEYS & {k.lower() for k in t}
        if bad_keys:
            errs.append(f"{name}: 清单只存引用不存凭证——发现疑似明文凭证字段 {sorted(bad_keys)}；"
                        f"密码/token 请存钥匙串（opsaxiom cred set {name}），此处写 auth: keyring:{name}")
        conn = t.get("connector")
        if conn not in VALID_CONNECTORS:
            errs.append(f"{name}: connector 必须是 {VALID_CONNECTORS} 之一，得到 {conn!r}")
        auth = t.get("auth")
        if auth is not None and not _AUTH_RE.match(str(auth)):
            errs.append(f"{name}: auth 只能是引用（agent/ssh_config/kubeconfig/keyring:名字/file:路径），"
                        f"得到 {auth!r}")
        # R-A3 scope 只读
        if t.get("scope", "readonly") != "readonly":
            errs.append(f"{name}: scope 只支持 readonly（写操作不进自动通道，docs/12 §7）")
        # reach 标签形态（可选）
        reach = t.get("reach")
        if reach is not None and not re.match(r"^[\w-]+:[\w-]+$", str(reach)):
            errs.append(f"{name}: reach 形如 vpn:office-vpn，得到 {reach!r}")
        # os 字段（可选，用于远程 target 时的 skill 平台过滤）
        t_os = t.get("os")
        if t_os is not None and t_os not in VALID_OS:
            errs.append(f"{name}: os 必须是 {sorted(VALID_OS)} 之一（或留空），得到 {t_os!r}")
    if errs:
        raise AccessError("targets.yaml 拒绝加载：\n  " + "\n  ".join(errs))
    return targets


class Credential:
    """已解析凭证的内存载体（R-A2：repr 打码、不可序列化）。"""

    def __init__(self, kind, **kw):
        self.kind = kind          # agent | ssh_config | kubeconfig | keyring | file
        self._kw = kw             # 私有：可能含敏感材料

    def get(self, k, default=None):
        return self._kw.get(k, default)

    def __repr__(self):
        return f"<Credential kind={self.kind} ***>"

    # 禁止意外序列化（yaml/json/pickle 都会经过这些口）
    def __reduce__(self):
        raise AccessError("凭证对象禁止序列化（R-A2）")


def resolve(target: dict, master=None) -> Credential:
    """凭证引用 → 凭证对象。P0 三种零配置来源；keyring 由 P1 的 cred.py 提供。
    master：keyring 走降级加密文件时的解锁口令（OS keyring 时忽略）。"""
    auth = str(target.get("auth", "ssh_config"))
    if auth == "agent":
        sock = os.environ.get("SSH_AUTH_SOCK")
        if not sock:
            raise AccessError("auth: agent 但 SSH_AUTH_SOCK 未设置——ssh-agent 没在跑？"
                              "（eval $(ssh-agent) && ssh-add）")
        return Credential("agent", sock=sock)
    if auth == "ssh_config":
        cfg = pathlib.Path.home() / ".ssh" / "config"
        return Credential("ssh_config", config_path=str(cfg) if cfg.exists() else None)
    if auth == "kubeconfig":
        kc = os.environ.get("KUBECONFIG", str(pathlib.Path.home() / ".kube" / "config"))
        if not pathlib.Path(kc).exists():
            raise AccessError(f"auth: kubeconfig 但 {kc} 不存在")
        return Credential("kubeconfig", path=kc, context=target.get("context"))
    if auth.startswith("file:"):
        p = pathlib.Path(auth[5:]).expanduser().expanduser()
        if not p.exists():
            raise AccessError(f"auth: {auth} 指向的文件不存在")
        return Credential("file", path=str(p))
    if auth.startswith("keyring:"):
        import cred
        try:
            fields = cred.get_cred(auth[8:], master=master)
        except cred.CredError as e:
            raise AccessError(str(e)) from e
        if not fields:
            raise AccessError(f"keyring 里没有凭证 {auth[8:]}——先 opsaxiom cred set {auth[8:]}")
        return Credential("keyring", **fields)
    if auth.startswith("1password:"):
        ref = auth[10:]
        if not ref:
            raise AccessError("auth: 1password 引用格式 1password:op://Vault/Item/field")
        import subprocess
        try:
            r = subprocess.run(
                ["op", "read", ref],
                capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                raise AccessError(f"1password 读取失败：{r.stderr.strip()[:120]}")
            return Credential("1password", secret=r.stdout.strip(), ref=ref)
        except FileNotFoundError:
            raise AccessError(
                "auth: 1password 需要 op CLI。brew install 1password-cli 然后 op signin")
    raise AccessError(f"未知 auth 引用：{auth}")
