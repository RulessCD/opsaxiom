"""
gate.py —— 执行门（docs/12 §4，I-1）。远程命令的【唯一】入口。

一条命令要打到远端，必须穿过这道门，顺序不可调换：
  1. 目标存在（load_targets，access 三红线已在加载时把关）
  2. 授权档位（B 轮 v2）——root 档：per-target trust 已授权；白名单档：
     未授权但 sudo_whitelist 目标且命令在 registry 名单内（授权点在 add
     时刻确认白名单，物理边界在远端 /etc/sudoers.d）；两档皆非 → 拒
  3. 只读白名单（按 connector：ssh/kubectl 走 _is_readonly；network/http 见 I-5）
     —— 写命令结构性进不来（R-A3），任何档都不豁免
  4. param 注入防护（T-3：不可信 param 值含 shell 元字符且出现在命令里 → 拒）
  5. 解析凭证（access.resolve）→ 连接器执行
     —— ssh 目标按档切身份：root 档且带 admin_user → 切管理账号直登
     （方案 A，公钥开通时已双装）；白名单档 → 首段 sudo -n
  6. 审计落盘（命令/实际身份/输出摘要/目标/时刻；【绝不】写凭证——R-A2）

连接器只负责"拨通"，不做任何安全判断——安全全在这道门里（docs/12 §4）。
拒绝也审计：一次被拦的写命令是安全事件，要留痕。
"""
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "sim"))
import access          # noqa: E402
import sweep           # noqa: E402
from run_sim import _is_readonly  # noqa: E402


class GateError(Exception):
    pass


class GateRemoteNotAllowed(GateError):
    """白名单档（未 grant 的 ssh 白名单目标）上，名单外命令 ro 账号物理上
    跑不动（远端 sudoers 只放行名单内命令）——不是安全事件，是能力边界。
    gate 不 import runtime（避免循环导入），定义同构异常；调用方（repl/
    sweep）按需要捕获转人工贴回，语义与 runtime.RemoteNotAllowed 一致。"""

    def __init__(self, target_name, cmd):
        self.target_name = target_name
        self.cmd = cmd
        super().__init__(
            f"该命令不在 {target_name} 的只读白名单内——ro 账号无法执行需 root 的探针")


ENROLL_USER = "opsaxiom-ro"        # 与 enroll.py 的只读账号名保持一致（避免循环 import）


def _home():
    return pathlib.Path(os.environ.get("OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom"))


def _audit_file():
    return _home() / "audit" / "remote.jsonl"


def is_authorized(target_name):
    """per-target 授权检查。I-1 复用 sweep.is_trusted（auto_exec 列表）；
    I-2 升级为带 TTL 的结构，此函数签名不变。"""
    return sweep.is_trusted(target_name)


# network 只读动词（首词）——配置/提权类一律不放行，即使前缀碰巧匹配
_NET_READONLY_LEAD = {"show", "display", "help", "get"}
_NET_FORBIDDEN = re.compile(
    r"\b(enable|configure|conf t|system-view|commit|copy|write|erase|delete|"
    r"reload|shutdown|no |undo |set |edit|request|clear)\b", re.IGNORECASE)


def _network_readonly(platform, cmd):
    """network 只读判：首词是只读动词 + 语法库前缀白名单 + 无配置/提权词。"""
    lead = cmd.strip().split()[0].lower() if cmd.strip() else ""
    if lead not in _NET_READONLY_LEAD:
        return False
    if _NET_FORBIDDEN.search(cmd):
        return False
    import syntax_check
    # 前缀白名单（S6 同源）：非 ERROR 即匹配了已知合法只读前缀
    return not syntax_check.check_command(platform, cmd)


def _http_readonly(method):
    """http 只读判：方法必须是 GET。"""
    return str(method).upper() == "GET"


def _readonly_ok(connector, cmd, target=None):
    """按 connector 类型判只读。ssh/kubectl 走派生名单（T-6 同源）：
    registry 白名单 ∪ sim/run_sim._ALLOW_LEAD（_runtime_ro_leads），再叠加
    sim 的 _DENY 写词/重定向拒绝与 kubectl 写动词判定。原先直接复用
    _is_readonly（手写 _ALLOW_LEAD），与 registry 白名单差 15 个命令，
    白名单档放行后执行门误拒（真机暴露）。"""
    if connector in ("ssh", "kubectl"):
        try:
            toks = cmd.strip().split()
        except Exception:
            return False
        lead = toks[0].rsplit("/", 1)[-1] if toks else ""
        if lead in ("kubectl", "mount"):
            return _is_readonly(cmd)          # 语义特判仍在 sim（kubectl 写动词 /
        from run_sim import _DENY             # mount 无参=查询、带参=挂载）
        return lead in _runtime_ro_leads() and not _DENY.search(cmd)
    if connector == "network":
        platform = (target or {}).get("platform", "cisco_ios")
        return _network_readonly(platform, cmd)
    if connector == "http":
        return _http_readonly(cmd)            # 这里 cmd 实为 HTTP 方法
    raise GateError(f"未知 connector：{connector}")


def _audit(record):
    f = _audit_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _stamp(now=None):
    # 允许注入 now（Date.now 在某些环境不可用/测试需确定性）
    if now is not None:
        return now
    return datetime.datetime.now().isoformat(timespec="seconds")


def _allow_entries():
    """registry 白名单成员（(bin, prefix|None) 全集）。与写入远端
    /etc/sudoers.d/opsaxiom-ro 的清单**消费同一 scan_skills_dir/extract_entries
    产物**（gen_sudoers）——不是手写镜像：镜像必分叉（-u skip 事故、
    startswith≠fnmatch，T-6 注记），"互证"只测登记形态不测对称性；
    对称性由 test_wl_member_prefix_mirror_matches_sudoers 双向锁定。
    算不出 → 空集（fail-closed：不提权）。"""
    try:
        sys.path.insert(0, str(HERE / "authoring"))
        import gen_sudoers as G
        reg_root = G.default_skills_root()
        if not reg_root:
            return set()
        return set(G.scan_skills_dir(reg_root))
    except Exception:
        return set()


def _allow_bins():
    """白名单裸名集合（展示用）。成员判定走 _wl_member（含复合型前缀检查）。"""
    return {b for b, _p in _allow_entries()}


def _wl_member(cmd):
    """命令是否在 registry 白名单内（与写远端 sudoers 同源，gen_sudoers）。
    首段二进制在名单内还不够：
      + 复合型二进制（_COMPOSITE_LEAD）永不裸名放行（B-1）；
      + 复合型只认 _RO_COMPOSITE_SUBCMDS 登记的只读子命令作前缀（v3，F-19）：
        flag 前缀（--failed/-u/--query-*…）与命令词正交，sudoers 尾通配关不住
        其后的写子命令/写 flag（`systemctl --failed restart nginx` 实测穿透），
        故 flag 一律不放行——该探针白名单档转贴回；
      + 非复合型（语义固定单用途）裸名即过，但 deny/解释器名单不在登记里
        （gen_sudoers 已滤）。
    客户端与远端 sudoers 由 gen_sudoers 同一 extract_entries 产出，互证测试
    test_wl_member_prefix_mirror_matches_sudoers 锁定同源性。"""
    import shlex
    try:
        toks = shlex.split(cmd.strip())
    except ValueError:
        return False
    if not toks:
        return False
    first = toks[0].rsplit("/", 1)[-1]
    entries = _allow_entries()
    if (first, None) in entries:
        # 非复合型裸名条目：远端 sudoers 为 `bin *` 全参放行，同形态直通
        return first not in _composite_union()
    if first in _composite_union():
        # 复合型：须为「子命令」前缀形态且该前缀已登记（只读子命令白名单）
        sub = toks[1] if len(toks) > 1 else None
        if sub is None or sub.startswith("-") or sub.startswith("{{"):
            return False                      # 裸/flag/模板段：远端无此条目
        return (first, sub) in entries        # 精确匹配（无 startswith 宽松）
    return False


def _composite_union():
    """复合型二进制全集（gen_sudoers._COMPOSITE_LEAD 同源；取不到则空集从严）。"""
    try:
        sys.path.insert(0, str(HERE / "authoring"))
        import gen_sudoers as G
        return set(G._COMPOSITE_LEAD)
    except Exception:
        return set()                          # 分不清时退"无复合型"——裸名条目
                                              # 本就只可能来自非复合型，安全


def _runtime_ro_leads():
    """运行时只读动词全集（T-6 同源派生，非手写镜像）。
    历史：执行门原先用 sim/run_sim._ALLOW_LEAD 手写动词表，与 registry 白名单
    差 15 个命令（iotop/numastat/getent/tail/top…真机白名单档批量取证暴露），
    身为白名单路由放行、执行门误拒——分叉实锤后废弃手抄，改为：
      registry 白名单（gen_sudoers extract 产物，结构性排除 action/解释器/
      deny）∪ sim/run_sim._ALLOW_LEAD（本机 sim 侧既有的动词表，覆盖 echo/
      for/find/uptime 等本机专用形态）。
    自研采集器 opsaxiom-collect 亦从 sim 名单继承。算不出 registry 时退化
    仅 sim 名单（本机行为不变，远端白名单档 fail-closed 由名单空集保证）。"""
    sim_leads = set()
    try:
        from run_sim import _ALLOW_LEAD       # sim 侧既有动词表（单一来源）
        sim_leads = set(_ALLOW_LEAD)
    except Exception:
        pass
    try:
        sys.path.insert(0, str(HERE / "authoring"))
        import gen_sudoers as G
        reg_root = G.default_skills_root()
        if reg_root:
            sim_leads |= {b for b, _p in G.scan_skills_dir(reg_root)}
    except Exception:
        pass                                  # registry 不可用：仅 sim 名单
    return sim_leads


def sudo_routed(target_name, cmd, targets=None):
    """白名单即路由（B 轮 v2，docs/12 §5.6）公开谓词：白名单档上该命令能否
    sudo 自动执行。只反映"目标能力 + 命令成员"两个静态事实（trust 状态由
    调用方先判——root 档根本不会走到这个谓词，execute_mixed/gate 均如此）：
      + ssh sudo_whitelist 目标（ro 账号）→ 命令首段二进制在 registry 名单内；
      + 其他目标（root 直登等）恒 False。
    """
    targets = access.load_targets() if targets is None else targets
    t = targets.get(target_name) or {}
    if not t.get("sudo_whitelist") or t.get("user") != ENROLL_USER:
        return False                           # root 直登目标：root 档语义，不路由
    return _wl_member(cmd)



def run_remote(target_name, cmd, *, params=None, targets=None,
               connector_fn=None, now=None):
    """把一条只读命令打到远端目标，返回 stdout。任何一关不过 → GateError（并审计）。

    档位（B 轮 v2，docs/12 §5.6）：ssh 白名单目标按 trust 状态切档——
      + root 档（已 grant）：管理账号（admin_user，缺省=targets.yaml 的 user）
        直登原样执行（约束=客户端四闸 + 显式 grant + TTL）。
      + 白名单档（未 grant）：ro 账号登录，名单内命令首段 sudo -n（远端物理闸
        /etc/sudoers.d），名单外命令 → GateRemoteNotAllowed（调用方转贴回）。
    非 ssh 连接器无档位（授权语义不变：trust 已 grant 才自动执行）。
    connector_fn 便于测试注入 (target, cred, cmd) -> (rc, out, err)。
    """
    targets = access.load_targets() if targets is None else targets
    if target_name not in targets:
        raise GateError(f"未知目标：{target_name}（先 opsaxiom target add {target_name}）")
    t = dict(targets[target_name]); t.setdefault("name", target_name)
    conn = t.get("connector")

    def deny(reason):
        _audit({"ts": _stamp(now), "target": target_name, "host": t.get("host"),
                "connector": conn, "cmd": cmd, "decision": "deny", "reason": reason})
        raise GateError(reason)

    # 2. 授权档位（先判 root 档；白名单档只兜"不可升档"的情形）
    #    root 档需要物理通道：非白名单目标照旧（user 直登）；白名单目标需要
    #    admin_user（双装公钥）或登录用户本就非 ro。ro 账号且无 admin_user 时
    #    grant 不能凭空给 root——sudoers 未放行的命令 ro 账号跑不动，此时即便
    #    已 grant 也永远按白名单档路由（grant 只免去授权问答，不改变执行身份
    #    ——"没通道不给假 root"）。
    is_wl = conn == "ssh" and bool(t.get("sudo_whitelist"))
    root_capable = (not is_wl) or bool(t.get("admin_user")) or \
        t.get("user") != ENROLL_USER
    root_tier = bool(is_authorized(target_name) and root_capable)
    wl_tier = bool(not root_tier and is_wl and _wl_member(cmd))
    if not root_tier and not wl_tier:
        if is_wl:
            # 白名单目标上的名单外命令：能力边界需贴回，不是"未授权"
            raise GateRemoteNotAllowed(target_name, cmd)
        deny(f"目标 {target_name} 未授权自动执行——先 opsaxiom target grant {target_name}")
    # 3. 只读白名单（http 传方法，其余传命令）
    try:
        ok = _readonly_ok(conn, (params or {}).get("method", "GET") if conn == "http" else cmd,
                          target=t)
    except GateError as e:
        deny(str(e))
    if not ok:
        deny(f"写/非只读命令被执行门拒绝：{cmd!r}")
    # 4. T-3 param 注入防护（不可信 param 值含元字符且出现在命令里）
    if params:
        for bad in sweep.unsafe_values(params):
            if bad in cmd:
                deny(f"param 注入被拒（值含 shell 元字符）：{bad!r}")
    # 5. 解析凭证 + 执行（按档位切实际身份：root 档=admin 直登；白名单档=ro+首段 sudo）
    cred = access.resolve(t)
    fn = connector_fn or _default_connector(conn)
    exec_as = t.get("user")                     # 实际执行身份（审计用）
    exec_cmd = cmd
    if conn == "ssh" and root_tier and t.get("admin_user"):
        exec_as = t["admin_user"]               # root 档：管理账号直登（公钥已双装）
    elif conn == "ssh" and wl_tier:
        exec_as = ENROLL_USER
        exec_cmd = "sudo -n " + cmd.strip()     # 白名单档：首段 sudo（远端物理闸）
    if exec_as != t.get("user"):
        t = dict(t); t["user"] = exec_as        # 连接身份切到 admin（ro 账号不变）
    try:
        rc, out, err = fn(t, cred, exec_cmd)
    except Exception as e:                       # noqa: BLE001
        # 连接器异常（连不上/执行超时）也审计——命令已打到远端，无痕即盲区；
        # 空 str 异常（socket.timeout）记类名，别留空 err（真机教训）；
        # err_kind 结构化类别供上层 fail-fast 判定（不靠错误文本猜，裁定 3）
        _audit({"ts": _stamp(now), "target": target_name, "host": t.get("host"),
                "connector": conn, "cmd": exec_cmd, "decision": "error",
                "cred_kind": cred.kind, "exec_as": exec_as,
                "tier": "root" if root_tier else "whitelist",
                "err_kind": err_kind(e),
                "err": (str(e).strip() or type(e).__name__)[:200]})
        raise
    # 6. 审计（凭证绝不入审计——只记 kind，不记材料；实际身份与档位单独记录）
    _audit({"ts": _stamp(now), "target": target_name, "host": t.get("host"),
            "connector": conn, "cmd": exec_cmd, "decision": "allow",
            "cred_kind": cred.kind, "exit": rc,
            "exec_as": exec_as, "via_sudo": exec_cmd != cmd,
            "tier": "root" if root_tier else "whitelist",
            "out_sha256": hashlib.sha256(out.encode("utf-8", "replace")).hexdigest()[:16],
            "out_bytes": len(out)})
    if rc != 0 and not out:
        raise GateError(f"远端执行返回码 {rc}：{err.strip()[:200]}")
    return out


def err_kind(e):
    """异常 → 结构化错误类别（十七轮评审裁定 3：fail-fast 判定弃用错误文本
    匹配——远端 stderr 会拼进 err 消息（rc 分支），中文报错含"连接"二字会把
    rc 级失败误判成连接级，整轮静默跳过贴回丢证据）。类别：
      connect  连接级失败（拨不通/banner reset/VPN 抖动）——唯一可 fail-fast
      timeout  执行超时（命令已到远端）→ 转贴回
      exec     其他执行/连接器异常（含 SSHError）→ 转贴回
    边界语义（F-22 校准）：ssh 连接器已把底层网络故障（socket.timeout（Py3.8
    类名 timeout）/ConnectionError/OSError）在连接器层包成 SSHConnectError/
    SSHError，故按类映射即得正确二分；network 连接器只抛 NetworkError 且统一
    归 exec——拨不通与执行失败不区分是【有意保守】（network 无 fail-fast，
    不静默丢证据），T-5 注记同此口径。"""
    name = type(e).__name__
    if name == "SSHConnectError":
        return "connect"
    if name in ("ConnectionError", "ConnectionResetError", "ConnectionRefusedError"):
        return "connect"
    if name == "TimeoutError":
        return "timeout"
    return "exec"


def _default_connector(connector):
    if connector == "ssh":
        from connectors import ssh_conn
        return ssh_conn.exec_readonly
    if connector == "network":
        from connectors import network_conn
        return network_conn.exec_readonly
    if connector == "http":
        from connectors import http_conn
        return http_conn.get_readonly
    raise GateError(f"connector={connector} 尚无真实实现")
