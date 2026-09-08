"""
target_cli.py —— `opsaxiom target ...` 子命令（I-2 授权/清单，I-3 add/import/doctor）。

设备接入的用户入口。安全约束见 docs/12；凭证永不经此存储（只存引用）。
"""
import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "sim"))
import access          # noqa: E402
import sweep           # noqa: E402


def _home():
    return pathlib.Path(os.environ.get("OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom"))


# ---------- 纯逻辑（可单测，不碰 IO/交互）----------

def parse_ssh_config(text):
    """解析 ~/.ssh/config，产出可导入的 target 条目。跳过含通配符的 Host（*/?）。"""
    out = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition(" ")
        key = key.lower(); val = val.strip()
        if key == "host":
            for name in val.split():
                if "*" in name or "?" in name:      # 通配符条目跳过（不是具体设备）
                    cur = None; continue
                cur = {"name": name, "connector": "ssh", "auth": "ssh_config", "os": "linux"}
                out.append(cur)
        elif cur is not None:
            if key == "hostname":
                cur["host"] = val
            elif key == "user":
                cur["user"] = val
            elif key == "port":
                cur["port"] = int(val) if val.isdigit() else val
            elif key == "proxyjump":
                cur["reach"] = "jump:" + val.split(",")[0].strip()
    # 去掉只有通配符段落里冒出来的空条目
    return [t for t in out if t.get("name")]


def build_target_entry(connector, host=None, auth=None, user=None,
                       port=None, reach=None, context=None, os=None):
    """从向导答案组装 target 条目（不含 name）。auth 缺省按 connector 给合理引用。"""
    t = {"connector": connector}
    if auth is None:
        auth = {"ssh": "ssh_config", "network": None,
                "kubectl": "kubeconfig", "http": None}.get(connector)
    if auth:
        t["auth"] = auth
    if host:
        t["host"] = host
    if user:
        t["user"] = user
    if port:
        t["port"] = port
    if reach:
        t["reach"] = reach
    if context:
        t["context"] = context
    if os:
        t["os"] = os
    return t


def detect_default_auth(host, ssh_config_text=None, agent_has_keys=False, local_key=None):
    """自动试探默认 auth：config 里有该 Host 条目→ssh_config；本机有私钥→file: 引用
    （config 没写该 Host 时最常见可用路径，避开"agent 但无钥匙"坑）；
    agent 有钥匙→agent；都没有→ssh_config。local_key：None=自动探测 ~/.ssh，False=无钥匙。"""
    if ssh_config_text:
        for t in parse_ssh_config(ssh_config_text):
            if t["name"] == host:
                return "ssh_config"
    if local_key is None:
        import enroll as E
        local_key = E.find_local_key()
    if local_key:
        return "file:" + str(local_key)
    return "agent" if agent_has_keys else "ssh_config"


def diagnose_reach(rows):
    """给 doctor 的逐目标结果做 reach 分组诊断（docs/12 §4.5）。
    rows: [{target, reach, reachable}]；返回 [提示串]。
    同一 reach 标签下的目标【全部】不可达 → 判为网络前置未就绪，而非逐台报错。"""
    groups = {}
    for r in rows:
        if r.get("reach"):
            groups.setdefault(r["reach"], []).append(r)
    hints = []
    for tag, members in groups.items():
        if all(not m["reachable"] for m in members):
            kind, _, name = tag.partition(":")
            what = {"vpn": "VPN", "jump": "跳板"}.get(kind, kind)
            hints.append(f"⚠ {tag} 下 {len(members)} 个目标全部不可达——"
                         f"疑似{what}『{name}』未就绪（先连上再重试），而非这些设备都挂了")
    return hints


def _fmt_remaining(rem):
    if rem is None:
        return "永久"
    if rem <= 0:
        return "已过期"
    return f"剩 {rem:.0f} 天"


# 子命令路由表（_cmd_menu 和 delegate 共用）
_SUBCOMMAND_FN = {
    "list": "_cmd_list",
    "add": "_cmd_add",
    "grant": "_cmd_grant",
    "revoke": "_cmd_revoke",
    "delete": "_cmd_delete",
    "import-ssh-config": "_cmd_import",
    "doctor": "_cmd_doctor",
}


def _route_subcommand(ns):
    """按 ns.target_cmd 调用对应 _cmd_* 函数（绕过 argparse routing）。"""
    fn_name = "_cmd_" + ns.target_cmd.replace("-", "_")
    fn = globals().get(fn_name)
    if fn is None:
        print(f"未知子命令：{ns.target_cmd}"); return 1
    return fn(ns)


def _cmd_menu(args):
    """target 不带子命令时的交互菜单——列出 6 个子命令供数字选择。"""
    subs = [
        ("list", "列出设备与授权状态"),
        ("add", "添加一台设备（交互向导）"),
        ("grant", "授权自动执行只读命令"),
        ("revoke", "收回授权"),
        ("delete", "删除一台设备"),
        ("import-ssh-config", "从 ~/.ssh/config 批量导入"),
        ("doctor", "逐目标体检（连通/凭证/reach）"),
    ]
    print("请选择操作：")
    for i, (cmd, desc) in enumerate(subs, 1):
        print(f"  {i} target {cmd:<18} {desc}")
    ans = input("  选择数字 [1-7]：").strip()
    if ans not in {str(i) for i in range(1, len(subs) + 1)}:
        print("  无效选择。"); return 1
    idx = int(ans) - 1
    chosen = subs[idx][0]
    # 构造 namespace 模拟 argparse 结果，转调到对应子命令
    ns = type("NS", (), {"target_cmd": chosen, "name": None, "ttl_days": 30, "fn": None})()
    return _route_subcommand(ns)


def _pick_target_for_subcommand(action_verb):
    """grant / revoke / delete 缺 name 时列出设备供用户选择。返回选中设备名或 None。"""
    try:
        targets = access.load_targets()
    except access.AccessError as e:
        print(e); return None
    if not targets:
        print(f"  尚无设备——请先使用 target add 指令添加一台再 {action_verb}。")
        return None
    names = sorted(targets.keys())
    print(f"  选择要{action_verb}的目标（回车取消）：")
    for i, name in enumerate(names, 1):
        t = targets[name]
        print(f"  {i} {name}  ({t.get('connector','?')} | {t.get('host','')})")
    ans = input("  > ").strip()
    if not ans:
        print("  已取消。"); return None
    if ans.isdigit() and 1 <= int(ans) <= len(names):
        return names[int(ans) - 1]
    print(f"  无效选择。"); return None


def _cmd_list(args):
    try:
        targets = access.load_targets()
    except access.AccessError as e:
        print(e); return 1
    grants = {g["target"]: g for g in sweep.list_grants()}
    if not targets:
        print("尚无设备。请使用 target add 指令添加。"); return 0
    print(f"{'目标':<16} {'连接器':<9} {'主机':<18} {'授权':<10} reach")
    for name, t in targets.items():
        g = grants.get(name)
        auth = _fmt_remaining(g["remaining_days"]) if g else "未授权"
        print(f"{name:<16} {t.get('connector',''):<9} {str(t.get('host','')):<18} "
              f"{auth:<10} {t.get('reach','') or ''}")
    return 0


def _cmd_grant(args):
    if not args.name:
        args.name = _pick_target_for_subcommand("授权")
        if not args.name:
            return 1
    try:
        targets = access.load_targets()
    except access.AccessError as e:
        print(e); return 1
    if args.name not in targets:
        print(f"未知目标：{args.name}（请先 target add {args.name}）"); return 1
    sweep.grant_trust(args.name, ttl_days=args.ttl_days, scope="readonly")
    print(f"✔ 已授权 {args.name} 自动执行只读命令（{args.ttl_days} 天后到期，"
          f"target revoke {args.name} 可随时收回）")
    return 0


def _cmd_revoke(args):
    if not args.name:
        args.name = _pick_target_for_subcommand("收回")
        if not args.name:
            return 1
    if sweep.revoke_trust(args.name):
        print(f"✔ 已收回 {args.name} 的授权。")
    else:
        print(f"{args.name} 本就没有授权，无需收回。")
    return 0


def _cmd_delete(args):
    if not args.name:
        args.name = _pick_target_for_subcommand("删除")
        if not args.name:
            return 1
    try:
        targets = access.load_targets()
    except access.AccessError as e:
        print(e); return 1
    if args.name not in targets:
        print(f"未知目标：{args.name}"); return 1
    # 授权自动清除（sweep.revoke_trust 幂等，不存在的授权也无妨）
    sweep.revoke_trust(args.name)
    del targets[args.name]
    try:
        _save_targets(targets)
    except access.AccessError as e:
        print(f"✘ {e}"); return 1
    print(f"✔ 已删除 {args.name}（同时自动收回授权）。")
    return 0


def _save_targets(targets):
    f = _home() / "targets.yaml"
    f.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    f.write_text(yaml.safe_dump({"targets": targets}, allow_unicode=True), encoding="utf-8")
    # 存完立即回读校验（走 access 三红线），有问题当场暴露
    access.load_targets(f)
    return f


def _enroll_ssh(name, host, port, user):
    """SSH 首次开通编排（docs/12 §3.5）：密钥 → 密码上门一次 →
    公钥写入 + os 探测 +（可选）只读账号 —— 全部在一次密码连接内完成；
    连接关闭后密码立即清除；最后密钥重连验证。任何一步失败/放弃返回
    {'ok': False, 'err': …}（调用方退化普通模式）；成功返回
    {'ok': True, 'key_path', 'user'（可能降为 opsaxiom-ro）, 'os'}。
    （只读账号 v1 不写 sudoers 白名单——runtime 尚未消费 sudo 授权，
    见 docs/12 §5.6 标注；生成器 gen_sudoers.py 保留待接线。）"""
    import getpass
    import enroll as E
    res = {"ok": False, "err": ""}
    try:
        # ① 本机密钥（无则生成）
        key_path, created = E.ensure_local_key()
        if created:
            print(f"  已生成本机密钥 {key_path}（口令为空；如需口令请自行 ssh-keygen 替换）")
        else:
            print(f"  使用本机已有密钥 {key_path}")
        pub = E.pubkey_of(key_path)

        # ② 密码一次（getpass 不回显；空/取消 → 退化普通模式）
        user = user or "root"
        try:
            pw = getpass.getpass(f"  请输入 {user}@{host} 的密码（只用一次，不保存）：")
        except (EOFError, KeyboardInterrupt):
            res["err"] = "用户取消"; return res
        if not pw:
            res["err"] = "未输入密码"; return res

        print(f"  ▶ 连接 {host}:{port or 22} …")
        cli = E.connect_with_password(host, port or 22, user, pw)

        # ③ 同一连接上完成全部远端操作（密码只在连接建立一刻被用到）
        os_name = None
        try:
            ok, errtxt = E.install_pubkey(cli, pub)
            if not ok:
                res["err"] = f"公钥写入失败：{errtxt[:120]}"
                return res
            print("  ✔ 公钥已写入该账号 authorized_keys")
            os_name = E.probe_os(cli)
            if os_name:
                print(f"  目标 os：{os_name}（自动探测）")

            # ④ 可选：只读账号（低权；探针以其自身权限跑，需 root 的探针会失败——
            #    sudoers 白名单线路 runtime 尚未接线，本轮摘除，见 docs/12 §5.6）
            try:
                want_ro = input("  创建只读账号 opsaxiom-ro（仅诊断探针可用，需 root 的探针会受限）？[y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                want_ro = ""
            if want_ro in ("y", "yes", "是"):
                ok, rerr = E.create_ro_user(cli, pub, sudoers_text=None)
                if ok:
                    print(f"  ✔ 只读账号 {E.ENROLL_USER} 已创建")
                    user = E.ENROLL_USER      # targets.yaml 的登录用户降为只读账号
                else:
                    print(f"  ⚠ 只读账号创建失败（{rerr[:100]}）——继续使用 {user}。")
        finally:
            try:
                cli.close()
            except Exception:
                pass
        del pw                                  # 密码通道关闭后清除（零痕迹红线）

        # ⑤ 密钥验证（file 认证，与 targets.yaml 的 auth 一致）
        ok, verr = E.verify_key_login(host, port or 22, user, key_path)
        if not ok:
            res["err"] = verr
            return res
        print("  ✔ 密钥登录验证通过（只读探针 df -B1 / 正常）")
        res.update({"ok": True, "key_path": str(key_path), "user": user, "os": os_name})
        return res
    except Exception as ex:
        res["err"] = str(ex)[:150]
        return res


def _cmd_add(args):
    if not args.name:
        args.name = input("  设备名：").strip()
        if not args.name:
            print("  设备名不能为空。"); return 1
    try:
        targets = access.load_targets()
    except access.AccessError as e:
        print(e); return 1
    if args.name in targets:
        print(f"{args.name} 已存在。"); return 1
    cfg_path = pathlib.Path.home() / ".ssh" / "config"
    cfg_text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    agent_has = bool(os.environ.get("SSH_AUTH_SOCK"))

    def ask(prompt, default=""):
        v = input(f"{prompt}" + (f"（{default}）" if default else "") + "：").strip()
        return v or default

    conn = ask("连接方式 [ssh/network/kubectl/http]", "ssh")
    entry = {}
    reach_label = ""
    if conn == "kubectl":
        entry = build_target_entry("kubectl", auth="kubeconfig",
                                   context=ask("kube context（留空=当前）") or None)
    else:
        while True:
            host = ask("主机地址（IP/域名）")
            if host:
                break
            print("  主机地址不能为空，请输入。")
        default_port = "22" if conn == "ssh" else ""
        p = input(f"端口（回车默认 {default_port}）：" if default_port else "端口（回车跳过）：").strip()
        port = p or default_port
        user = (ask("登录用户") or None) if conn != "http" else None
        reach_label = ask("需要先连 VPN/跳板吗？输入标签名如 office（留空=直连）")

        if conn == "ssh":
            # ---- SSH 首次开通（I-4）：密钥 → 密码上门一次 →（可选只读账号）→ 验证 ----
            enroll_res = _enroll_ssh(args.name, host, port, user)
            if enroll_res.get("ok"):
                entry = build_target_entry(
                    "ssh", host=host,
                    auth="file:" + str(enroll_res["key_path"]),
                    user=enroll_res["user"],
                    port=(int(port) if port and port.isdigit() else None),
                    os=enroll_res.get("os"))
            else:
                # 开通失败（密码错/连不上/取消）→ 退化为普通模式（现状行为）
                auth = detect_default_auth(host, cfg_text, agent_has)
                print(f"  ⚠ 开通未完成（{enroll_res.get('err', '')}）——已按普通模式（{auth}）保存引用，"
                      f"可自行处理认证后用 target doctor 验证。")
                entry = build_target_entry(conn, host=host, auth=auth,
                                           port=(int(port) if port and port.isdigit() else None),
                                           user=user)
        else:
            auth = "keyring:" + args.name
            # 1password 选项
            if shutil.which("op"):
                print(f"  自动检测凭证方式：{auth}")
                use_op = ask("  改用 1Password？输入引用如 op://Vault/Item/field（留空=保持 {auth}）") or ""
                if use_op:
                    auth = "1password:" + use_op
            entry = build_target_entry(conn, host=host, auth=auth, port=(int(port) if port and port.isdigit() else None),
                                       user=user)
    if reach_label:
        entry["reach"] = "vpn:" + reach_label
    if conn == "ssh" and entry.get("os"):
        pass                                    # ssh 开通已 uname 自动探测，不再问
    else:
        t_os = ask("目标操作系统 [linux/macos/windows/freebsd]（回车跳过）")
        if t_os:
            entry["os"] = t_os.lower()
    targets[args.name] = entry
    try:
        _save_targets(targets)
    except access.AccessError as e:
        print(f"✘ {e}"); return 1
    print(f"✔ 已添加 {args.name}（凭证用引用 {entry.get('auth','-')}，不保存任何密码）。"
          f"请使用 target grant 指令授权自动执行")
    return 0


def _cmd_import(args):
    cfg = pathlib.Path.home() / ".ssh" / "config"
    if not cfg.exists():
        print("没有 ~/.ssh/config，无法导入。"); return 1
    cands = parse_ssh_config(cfg.read_text(encoding="utf-8"))
    try:
        targets = access.load_targets()
    except access.AccessError as e:
        print(e); return 1
    new = [c for c in cands if c["name"] not in targets]
    if not new:
        print("没有可导入的新条目（通配符 Host 已跳过）。"); return 0
    print("将导入以下设备（凭证仍走你现有的 ssh 配置，此处只存引用）：")
    for c in new:
        print(f"  {c['name']:<16} {c.get('host', c['name'])}  "
              f"{'via ' + c['reach'] if c.get('reach') else ''}")
    if input("确认导入？[y/N] ").strip().lower() != "y":
        print("已取消。"); return 0
    for c in new:
        name = c.pop("name")
        targets[name] = c
    _save_targets(targets)
    print(f"✔ 已导入 {len(new)} 台。逐台授权请使用 target grant 指令")
    return 0


def _default_probe(name, t, timeout=3):
    """默认可达性探针：TCP 连通 + 凭证可解析。权限检测留给真实连接（此处不强连）。"""
    import socket
    reachable = False
    host, port = t.get("host"), int(t.get("port", 22 if t.get("connector") == "ssh" else 0))
    if host and port:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                reachable = True
        except OSError:
            reachable = False
    elif t.get("connector") == "kubectl":
        reachable = True                     # kubectl 走本机 kubeconfig，连通性交给 kubectl
    cred_ok, cred_msg = True, ""
    try:
        access.resolve(t)
    except access.AccessError as e:
        cred_ok, cred_msg = False, str(e)
    return {"reachable": reachable, "cred_ok": cred_ok, "cred_msg": cred_msg}


def _cmd_doctor(args, probe=None):
    probe = probe or _default_probe
    try:
        targets = access.load_targets()
    except access.AccessError as e:
        print(e); return 1
    if not targets:
        print("尚无设备。"); return 0
    rows = []
    for name, t in targets.items():
        r = probe(name, t)
        if not r["reachable"]:
            light = "🔴"; note = "连不上"
        elif not r["cred_ok"]:
            light = "🟡"; note = r.get("cred_msg", "凭证待配")
        else:
            light = "🟢"; note = "可达 · 凭证就绪"
        print(f"{light} {name:<16} {note}")
        rows.append({"target": name, "reach": t.get("reach"), "reachable": r["reachable"]})
    for hint in diagnose_reach(rows):
        print(hint)
    return 0


def add_target(subparsers):
    tp = subparsers.add_parser("target", help="接入你的设备（清单只存引用，凭证不出本机）")
    tp.set_defaults(fn=_cmd_menu)                         # 不带子命令时进入菜单
    ts = tp.add_subparsers(dest="target_cmd")

    ts.add_parser("list", help="列出设备与授权状态").set_defaults(fn=_cmd_list)

    g = ts.add_parser("grant", help="授权某目标自动执行只读命令（带 TTL）")
    g.add_argument("name", nargs="?", default=None)       # 可选：缺省时交互选设备
    g.add_argument("--ttl-days", type=int, default=30, dest="ttl_days")
    g.set_defaults(fn=_cmd_grant)

    r = ts.add_parser("revoke", help="收回某目标的授权")
    r.add_argument("name", nargs="?", default=None)       # 可选：缺省时交互选设备
    r.set_defaults(fn=_cmd_revoke)

    d = ts.add_parser("delete", help="删除一台设备（同步收回授权）")
    d.add_argument("name", nargs="?", default=None)        # 可选：缺省时交互选设备
    d.set_defaults(fn=_cmd_delete)

    a = ts.add_parser("add", help="添加一台设备（交互向导）")
    a.add_argument("name", nargs="?", default=None)       # 可选：缺省时交互输入
    a.set_defaults(fn=_cmd_add)

    ts.add_parser("import-ssh-config",
                  help="从 ~/.ssh/config 批量导入").set_defaults(fn=_cmd_import)

    ts.add_parser("doctor", help="逐目标体检：连通/凭证/reach 分组诊断"
                  ).set_defaults(fn=_cmd_doctor)
