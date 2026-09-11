"""V-4 CLI 接线：opsaxiom hub ...（挂到主 CLI）。"""
import hubtool


def _cmd_hub(args):
    sub = args.hub_cmd
    if sub == "init":
        reg = hubtool.hub_init(args.location)
        print(f"registry 已配置：{reg}")
        return 0
    if sub == "build-registry":
        n = hubtool.build_registry(args.skills, args.out,
                                   include_draft=not getattr(args, "no_draft", False))
        print(f"已从 {args.skills} 生成 registry 到 {args.out}（{n} 个 Skill，含 index.json）")
        return 0
    if sub == "sync":
        try:
            n = hubtool.hub_sync()
        except RuntimeError as e:
            print(f"🟡 同步失败（{e}）——离线时可用本地快照，恢复网络后重试或 hub init 换源。")
            return 1
        print(f"已同步，registry 现有 {n} 个 Skill。")
        return 0
    if sub == "search":
        hits = hubtool.hub_search(args.kw)
        if not hits:
            print("无匹配。")
            return 1
        badge = {"draft": "⚪", "sim_verified": "🔵", "field_verified": "🟢", "certified": "🟡"}
        for e in hits[:20]:
            print(f"  [{badge.get(e['maturity'],'?')}{e['maturity']}] {e['id']}  —— {e['name']}")
            print(f"       {e.get('summary','')}  (attestations: {e['attestations']})")
        return 0
    if sub == "pull":
        try:
            dst, rep = hubtool.hub_pull(args.id, allow_draft=args.allow_draft)
        except PermissionError as e:
            print(f"🔴 拒收：{e}")
            return 1
        print(f"✔ 已拉取到 {dst}")
        print(f"  门①校验：0 ERROR  门②验签：{rep['att_valid']} 有效 / {rep['att_trusted']} 可信"
              f"  门③徽章：{rep['maturity']}")
        if rep["att_trusted"] == 0 and rep["att_valid"] > 0:
            print("  ⚠ 签名有效但签名者不在 keyring（TOFU）——建立信任前谨慎用于自动化档位。")
        return 0
    if sub == "push":
        tar = hubtool.hub_push(args.id)
        print(f"已打包：{tar}")
        print("推送：把该 bundle 摆渡到 registry 侧 `hub import`，或 git add+PR。")
        return 0
    if sub == "keyring":
        return _cmd_keyring(args)
    return 2


def _cmd_keyring(args):
    op = args.keyring_op
    if op == "list":
        rows = hubtool.keyring_list()
        if not rows:
            print("信任 keyring 为空。add <公钥> --name <who> 加入签名者。")
            return 0
        for name, pub in rows:
            print(f"  {name}: {pub[:16]}…")
        return 0
    if op == "add":
        try:
            p = hubtool.keyring_add(args.pub, args.name)
        except ValueError as e:
            print(f"🔴 {e}")
            return 1
        print(f"已信任 {args.name}：{p}")
        return 0
    if op == "remove":
        print("已移除。" if hubtool.keyring_remove(args.name) else "没有这个签名者。")
        return 0
    if op == "export":
        print(hubtool.keyring_export())
        return 0
    return 2


def add_hub(subparsers):
    hp = subparsers.add_parser("hub", help="Skills Hub：拉取/发布可信 Skill")
    hs = hp.add_subparsers(dest="hub_cmd", required=True)
    i = hs.add_parser("init", help="配置 registry（本地目录或 git 地址）")
    i.add_argument("location")
    b = hs.add_parser("build-registry", help="从 skills/ 生成一个 registry")
    b.add_argument("skills")
    b.add_argument("out")
    b.add_argument("--no-draft", dest="no_draft", action="store_true",
                   help="过滤 draft（对外发布用，与收录政策一致）")
    hs.add_parser("sync", help="同步 registry 索引")
    se = hs.add_parser("search", help="搜索 registry")
    se.add_argument("kw")
    pl = hs.add_parser("pull", help="拉取一个 Skill（三道安全门）")
    pl.add_argument("id")
    pl.add_argument("--allow-draft", action="store_true", help="放开 draft 拒收门")
    pu = hs.add_parser("push", help="打包一个 Skill 为 bundle")
    pu.add_argument("id")
    kr = hs.add_parser("keyring", help="管理信任的签名者公钥")
    krs = kr.add_subparsers(dest="keyring_op", required=True)
    krs.add_parser("list")
    ka = krs.add_parser("add")
    ka.add_argument("pub"); ka.add_argument("--name", required=True)
    krm = krs.add_parser("remove"); krm.add_argument("name")
    krs.add_parser("export")
    hp.set_defaults(fn=_cmd_hub)
