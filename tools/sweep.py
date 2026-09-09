"""
批量取证执行（Z-3，docs/09 §1.2）。

把 evidence.build_plan 产出的取证计划变成事实：
- **本机协驾**：auto 探针自动执行（执行时二次校验 _is_readonly + T-3 参数注入防护），
  解析入事实库。首次本机自动执行需一次性授权，记 trust.yaml。
- **导航档 / 远端**：渲染成【一个】粘贴块（nonce 分隔符），人跑一次贴回；
  或 `collect` 出自包含脚本、`ingest` 回灌。回合数从 O(节点) 降到 O(1 粘贴)。

三条对抗纪律（docs/09 §6，均带测试）：
1. 分隔符伪造：贴回文本里伪造的边界不带会话 nonce → 判为数据，不切段。
2. 参数注入（T-3）：param 值含 shell 元字符 → 拒绝自动执行该探针（模板作者可信、
   param 值来自告警/人不可信；模板自带的管道不受影响，只查 param 值）。
3. 白名单外命令绝不自动执行：auto 由 Z-2 依 _is_readonly 判，执行时再校验一次。
"""
import pathlib
import re
import secrets
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "sim"))
import parsers          # noqa: E402
import yaml             # noqa: E402
from run_sim import _is_readonly, _default_parse  # noqa: E402
from facts import LOCAL  # noqa: E402

# param 值里出现即视为注入企图的 shell 元字符（命令链接/替换/重定向/换行）。
# 注意：只查 param 的【值】，不查整条命令——模板自带的管道(dmesg|grep)是可信的。
_META_VAL = re.compile(r"[;&|`$<>(){}\n]")


def is_shell_safe(value):
    return not _META_VAL.search(str(value))


def unsafe_values(params):
    """params 里不安全的值集合（供执行时比对：命令含此值即拒自动执行）。"""
    return {str(v) for v in params.values() if not is_shell_safe(v)}


def flatten(plan):
    """按波次顺序展开去重后的探针，附全局 index（供 nonce 段映射）。"""
    out = []
    for w in plan["waves"]:
        for p in w["probes"]:
            q = dict(p)
            q["index"] = len(out)
            out.append(q)
    return out


# ---------- 解析入库（复用确定性解析器，R9）----------
def _parse(cmd, parser, stdout):
    pfn = None
    if parser and "{{" not in parser:      # 模板化 parser 名未定→退默认解析
        pfn = parsers.get_parser(parser)
    out = pfn(stdout) if pfn else _default_parse(stdout)
    if not isinstance(out, dict):
        out = {"rows": out}
    out.setdefault("lines", stdout.splitlines())
    return out


def _store_result(store, probe, stdout, now=None):
    parsed = _parse(probe["cmd"], probe.get("parser"), stdout)
    store.put_parsed(probe["cmd"], parsed, target=probe.get("target", LOCAL),
                     parser=probe.get("parser"), now=now)
    return parsed


# ---------- 本机协驾：自动执行 ----------
def _default_runner(cmd, timeout=15):
    r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                       timeout=timeout)
    return r.stdout


def execute_auto(plan, params, store, now=None, runner=None):
    """自动执行 auto 探针（本机只读），解析入库。返回执行报告列表。

    每条报告：{node, cmd, status, fields?}。status ∈
      executed / blocked-injection / blocked-not-readonly / not-auto / error。
    runner 可注入（测试用），默认真实 bash -c。
    """
    runner = runner or _default_runner
    bad = unsafe_values(params)
    report = []
    for p in flatten(plan):
        if not p["auto"]:
            report.append({"node": p["node"], "cmd": p["cmd"], "status": "not-auto"})
            continue
        # 对抗①：param 注入——命令含任一不安全 param 值 → 拒（T-3）
        if any(v in p["cmd"] for v in bad):
            report.append({"node": p["node"], "cmd": p["cmd"],
                           "status": "blocked-injection"})
            continue
        # 对抗②：执行时二次校验只读（纵深防御，防计划被篡改）
        if not _is_readonly(p["cmd"]):
            report.append({"node": p["node"], "cmd": p["cmd"],
                           "status": "blocked-not-readonly"})
            continue
        try:
            stdout = runner(p["cmd"])
        except Exception as e:                       # noqa: BLE001
            report.append({"node": p["node"], "cmd": p["cmd"],
                           "status": "error", "err": str(e)})
            continue
        parsed = _store_result(store, p, stdout, now)
        report.append({"node": p["node"], "cmd": p["cmd"], "status": "executed",
                       "fields": [k for k in parsed if k not in ("rows", "lines")]})
    return report


# ---------- 混合取证：本机 + 多远程目标（I-7）----------
def classify_target(name, targets=None, authorized=None, reachable=None):
    """把单个目标归类为可自动 / 需粘贴。返回 (mode, reason)。

    mode: 'local' 本机 / 'auto' 远程已授权可自动 / 'paste' 远程需人工粘贴。
    reachable=None 表示未知（不判），doctor 已过的场景由调用方传入。
    """
    if name == LOCAL:
        return "local", "本机"
    if authorized and not authorized(name):
        return "paste", "未授权自动执行（opsaxiom target grant）"
    if reachable is not None and reachable(name) is False:
        return "paste", "拨不通（见 target doctor）"
    return "auto", "远程已授权"


def execute_mixed(plan, params, store, *, targets=None, now=None,
                  local_runner=None, remote_runner=None, authorized=None,
                  sudo_routed=None):
    """混合取证：本机探针走 bash，远程已授权目标走 gate，未授权/不可达目标跳过
    （由调用方渲染成粘贴块）。返回 {executed:[...], manual:{target:[probe,...]}}。

    remote_runner: (target_name, cmd, params) -> stdout，默认 gate.run_remote。
    authorized: name -> bool，默认 gate.is_authorized（读 trust）。
    sudo_routed: (target_name, cmd) -> bool——白名单档分流（B 轮 v2）：仅对
                  【未授权】的 sudo_whitelist ssh 目标有意义（root 档直登不路由）；
                  返回 False = 名单外 → 降级贴回（人工举证）。None = 接 gate 同源谓词。
    """
    import gate
    authorized = authorized or gate.is_authorized
    if sudo_routed is None:
        sudo_routed = gate.sudo_routed            # 默认接 gate 同源判定
    remote_runner = remote_runner or (
        lambda tn, cmd, pr: gate.run_remote(tn, cmd, params=pr, now=now))
    local_runner = local_runner or _default_runner
    bad = unsafe_values(params)
    executed, manual = [], {}
    for p in flatten(plan):
        tname = p["target"]
        # 本机探针：沿用 execute_auto 的注入/只读双闸
        if tname == LOCAL:
            if not p["auto"] or any(v in p["cmd"] for v in bad) or not _is_readonly(p["cmd"]):
                manual.setdefault(LOCAL, []).append(p); continue
            try:
                out = local_runner(p["cmd"])
            except Exception as e:                       # noqa: BLE001
                from gate import err_kind
                msg = str(e).strip() or type(e).__name__   # 空 str 异常兜底（真机教训）
                executed.append({"node": p["node"], "cmd": p["cmd"],
                                 "status": "error", "err": msg, "err_kind": err_kind(e)})
                continue
            parsed = _store_result(store, p, out, now)
            executed.append({"node": p["node"], "cmd": p["cmd"], "status": "executed",
                             "target": tname,
                             "fields": [k for k in parsed if k not in ("rows", "lines")]})
            continue
        # 远程目标分流（B 轮 v2 档位，docs/12 §5.6）：
        # 白名单目标（ssh + sudo_whitelist）分两档——
        #   root 档（已 grant 且有通道：admin_user 或登录用户非 ro）= 管理账号
        #     直登，与普通目标同闸（classify 决定）；
        #   白名单档（未 grant，或 ro 账号且无 admin_user——没通道不给假 root）
        #     = 名单内命令 sudo 自动（远端物理闸），名单外贴回。
        # 判定顺序关键：先看授权（root 档豁免名单检查），再按名单分流。
        # 与 gate.run_remote 的 root_capable 同条件（gate 是执行时二次确认）。
        t_entry = {}
        t_is_wl = False
        try:
            import access as _access
            t_entry = _access.load_targets().get(tname) or {}
            t_is_wl = bool(t_entry.get("sudo_whitelist"))
        except Exception:
            t_entry, t_is_wl = {}, False
        root_capable = (not t_is_wl) or bool(t_entry.get("admin_user")) or \
            t_entry.get("user") != "opsaxiom-ro"
        if t_is_wl and not (authorized(tname) and root_capable):
            # 白名单档：只放行名单内（sudo_routed 谓词含成员判定）
            if any(v in p["cmd"] for v in bad) or not sudo_routed(tname, p["cmd"]):
                manual.setdefault(tname, []).append(p); continue
        else:
            # root 档 / 普通目标：授权闸照旧（未授权 → paste）
            mode, _ = classify_target(tname, authorized=authorized)
            if mode != "auto" or any(v in p["cmd"] for v in bad):
                manual.setdefault(tname, []).append(p); continue
        try:
            out = remote_runner(tname, p["cmd"], params)
        except Exception as e:                       # noqa: BLE001
            # str(e) 可能为空（socket.timeout 等真机教训）——诚实标明异常类名；
            # err_kind 结构化类别供 repl fail-fast 判定（弃错误文本匹配，裁定 3）
            from gate import err_kind
            msg = str(e).strip() or type(e).__name__
            executed.append({"node": p["node"], "cmd": p["cmd"], "status": "error",
                             "target": tname, "err": msg, "err_kind": err_kind(e)})
            continue
        parsed = _store_result(store, p, out, now)
        executed.append({"node": p["node"], "cmd": p["cmd"], "status": "executed",
                         "target": tname,
                         "fields": [k for k in parsed if k not in ("rows", "lines")]})
    return {"executed": executed, "manual": manual}


# ---------- 导航档：单粘贴块 + collect/ingest ----------
def make_nonce():
    return secrets.token_hex(8)


def _begin(nonce, idx):
    return f"<<<OPSAXIOM:{nonce}:BEGIN:{idx}>>>"


def _end(nonce, idx):
    return f"<<<OPSAXIOM:{nonce}:END:{idx}>>>"


def render_paste_block(plan, nonce, only_manual=True):
    """把探针渲染成一个粘贴块：命令清单 + 供 ingest 切段的 nonce 边界。

    only_manual=True（导航档默认）：只列非自动探针（自动的已本机执行）；
    False：全部（纯导航档零凭据场景，人跑全部命令）。
    """
    probes = [p for p in flatten(plan)
              if not (only_manual and p["auto"])]
    lines = ["# 请把下面整块在目标上执行，输出连同边界标记一并贴回（END 结束）：",
             f"# 会话校验码 {nonce}（伪造的边界标记会被当作数据忽略）"]
    for p in probes:
        lines.append(_begin(nonce, p["index"]))
        lines.append(f"$ {p['cmd']}")
        lines.append(_end(nonce, p["index"]))
    return "\n".join(lines), probes


def build_collect_script(plan, nonce, only_manual=True):
    """自包含取证脚本：在目标上运行，产出 nonce 分隔的输出，供 ingest 回灌。"""
    probes = [p for p in flatten(plan)
              if not (only_manual and p["auto"])]
    out = ["#!/bin/sh",
           "# OpsAxiom 取证脚本（自包含，均为已验证 Skill 的只读命令）。",
           f"NONCE={nonce}"]
    for p in probes:
        cmd = p["cmd"].replace("'", "'\\''")
        out.append(f"echo \"<<<OPSAXIOM:$NONCE:BEGIN:{p['index']}>>>\"")
        out.append(f"eval '{cmd}' 2>&1")
        out.append(f"echo \"<<<OPSAXIOM:$NONCE:END:{p['index']}>>>\"")
    return "\n".join(out) + "\n"


def ingest(text, plan, nonce, store, params=None, now=None):
    """回灌：按【带本会话 nonce 的】边界切段，映射到探针，解析入库。

    对抗①：只有精确匹配 nonce 的边界才是切点；伪造/无 nonce 的标记留在段内当数据。
    返回：{ingested:[node...], ignored_forged:int}。
    """
    probes = {p["index"]: p for p in flatten(plan)}
    begin = re.compile(r"^<<<OPSAXIOM:" + re.escape(nonce) + r":BEGIN:(\d+)>>>$")
    end = re.compile(r"^<<<OPSAXIOM:" + re.escape(nonce) + r":END:(\d+)>>>$")
    lines = text.splitlines()
    ingested, forged = [], 0
    # 统计伪造边界：形似 OPSAXIOM 边界但 nonce 不符（诚实计数，便于告警）
    forged_pat = re.compile(r"^<<<OPSAXIOM:.*:(?:BEGIN|END):\d+>>>$")
    i, cur_idx, buf = 0, None, []
    while i < len(lines):
        ln = lines[i]
        mb = begin.match(ln)
        me = end.match(ln)
        if cur_idx is None and mb:
            cur_idx = int(mb.group(1)); buf = []
        elif cur_idx is not None and me and int(me.group(1)) == cur_idx:
            p = probes.get(cur_idx)
            if p is not None:
                _store_result(store, p, "\n".join(buf), now)
                ingested.append(p["node"])
            cur_idx, buf = None, []
        else:
            if forged_pat.match(ln) and not (mb or me):
                forged += 1                          # 伪造边界：计数后并入数据
            if cur_idx is not None:
                buf.append(ln)
        i += 1
    return {"ingested": ingested, "ignored_forged": forged}


# ---------- 本机自动执行的一次性授权（trust.yaml）----------
def _trust_file():
    base = pathlib.Path(__import__("os").environ.get(
        "OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom"))
    return base / "trust.yaml"


def _now():
    import datetime
    return datetime.datetime.now()


def _parse_ts(s):
    import datetime
    return datetime.datetime.fromisoformat(s)


def _remaining_days(grant, now=None):
    """授权剩余天数；ttl_days=None（本机）视为永不过期，返回 None。"""
    if grant.get("ttl_days") is None:
        return None
    import datetime
    now = now or _now()
    expires = _parse_ts(grant["granted_at"]) + datetime.timedelta(days=grant["ttl_days"])
    return (expires - now).total_seconds() / 86400.0


def _load_trust(path=None):
    f = pathlib.Path(path) if path else _trust_file()
    if not f.exists():
        return {}, f
    return (yaml.safe_load(f.read_text(encoding="utf-8")) or {}), f


def is_trusted(target=LOCAL, path=None, now=None):
    """per-target 授权 + TTL（I-2）。grants[target] 未过期即授权；
    兼容旧格式 auto_exec 列表（视为永不过期）。"""
    data, _ = _load_trust(path)
    if target in (data.get("auto_exec") or []):        # 旧格式兼容（LOCAL 等）
        return True
    g = (data.get("grants") or {}).get(target)
    if not g:
        return False
    rem = _remaining_days(g, now=now)
    return rem is None or rem > 0                       # None=永不过期；>0=未过期


def grant_trust(target=LOCAL, path=None, ttl_days=None, scope="readonly", now=None):
    """授权对该目标自动执行只读取证。逐目标累积、带 TTL、可过期（R3 + docs/12 §1 决定三）。
    ttl_days=None：不过期（本机 LOCAL 默认）；远程目标由调用方传 30。"""
    data, f = _load_trust(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    grants = data.setdefault("grants", {})
    grants[target] = {"granted_at": (now or _now()).isoformat(timespec="seconds"),
                      "ttl_days": ttl_days, "scope": scope}
    f.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return f


def revoke_trust(target, path=None):
    """收回对某目标的授权（grants + 旧 auto_exec 两处都清）。返回是否有改动。"""
    data, f = _load_trust(path)
    changed = False
    if target in (data.get("grants") or {}):
        del data["grants"][target]; changed = True
    if target in (data.get("auto_exec") or []):
        data["auto_exec"].remove(target); changed = True
    if changed:
        f.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return changed


def list_grants(path=None, now=None):
    """列出所有授权目标及剩余天数（供 target list）。expired 的仍列出但标记。"""
    data, _ = _load_trust(path)
    out = []
    for t in (data.get("auto_exec") or []):
        out.append({"target": t, "scope": "readonly", "remaining_days": None,
                    "expired": False, "legacy": True})
    for t, g in (data.get("grants") or {}).items():
        rem = _remaining_days(g, now=now)
        out.append({"target": t, "scope": g.get("scope", "readonly"),
                    "remaining_days": rem,
                    "expired": (rem is not None and rem <= 0), "legacy": False})
    return out
