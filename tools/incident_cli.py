"""
opsaxiom incident —— 取证式诊断的一次性/机器可读入口（N-1）。

两个消费方：
  1. pi 扩展（tools/pi/opsaxiom.ts）的工具后端：--json 输出全链路结果，
     模型只消费结构化卷宗，不碰命令与判读（宪法 R7/R9 在这条边界上成立）。
  2. 脚本/自动化：与 diagnose --json 同风格。

行为：
  opsaxiom incident "<症状>" [--param k=v]... [--target local|<name>] [--json]
    · target=local：diagnose→假设→本机自动取证（需已授权 trust.yaml；--grant 补授权）
      →干跑→卷宗 JSON（confirmed/refuted/insufficient + 证据 + 处置指引）。
    · target=远端：不执行任何命令，输出取证计划（粘贴块命令清单）——
      调用方拿到 plan 后让人执行，再用 --ingest-file 回灌出卷宗。
  安全边界与 REPL 完全同源：同一套 evidence/sweep/incident 模块，无旁路。
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "sim"))
import diagnose        # noqa: E402
import incident as I   # noqa: E402
import sweep           # noqa: E402


def run_incident(symptom, params=None, target="local", top=3,
                 grant=False, ingest_file=None, nonce=None, runner=None, now=None):
    """全链路：返回可 JSON 序列化的结果 dict（pi 工具与测试共用）。"""
    idx = diagnose.load_index()
    inc = I.Incident.from_diagnose(symptom, idx, params=params or {},
                                   top=top, target=target)
    if not inc.hyps:
        return {"symptom": symptom, "hypotheses": [], "dossier": None,
                "note": "没匹配到任何 Skill，换个说法或 opsaxiom list 浏览。"}

    result = {"symptom": symptom, "target": target, "params": params or {},
              "hypotheses": [{"id": h.meta["id"], "name": h.name,
                              "maturity": h.meta.get("maturity")} for h in inc.hyps]}

    plan = inc.plan()
    if target == I.LOCAL:
        if grant:
            sweep.grant_trust(I.LOCAL)
        if not sweep.is_trusted(I.LOCAL):
            # 未授权：不执行，明说差什么（调用方 UI 负责问人，--grant 落授权）
            result["needs_grant"] = True
            result["auto_count"] = plan["auto_count"]
            result["note"] = ("本机自动诊断未授权。确认后加上 --grant 重试"
                              "（只检查不修改，命令均出自已验证技能）。")
            return result
        report = inc.auto_sweep(runner=runner, now=now)
        result["sweep"] = [{"node": r["node"], "status": r["status"]} for r in report]
    else:
        # 远端：只出计划，不执行（导航档零凭据语义）
        nonce = nonce or sweep.make_nonce()
        if ingest_file:
            text = pathlib.Path(ingest_file).read_text(encoding="utf-8")
            result["ingest"] = inc.ingest(text, nonce, now=now)
        else:
            block, probes = inc.paste_block(nonce, only_manual=False)
            result["plan"] = {"nonce": nonce, "paste_block": block,
                              "commands": [p["cmd"] for p in probes]}
            result["note"] = ("远端设备：请人在设备上执行以上命令，"
                              "输出存文件后用 --ingest-file 导入分析。")
            return result

    inc.dry_run(now=now)
    result["dossier"] = inc.dossier(now=now)
    result["dossier_text"] = inc.render_dossier(now=now)
    result["report_markdown"] = inc.export_report(now=now)
    if all(h.status != I.CONFIRMED for h in inc.hyps):
        result["handover"] = inc.handover(now=now)
    # 处置指引：卷宗只到"建议"，执行永远走审批门（R3/R6——模型无权越过）
    treats = [h.meta["id"] for h in inc.hyps
              if h.status == I.CONFIRMED and h.pending]
    if treats:
        result["treatment"] = {
            "skills": treats,
            "how": "修复需人工决策：在终端执行 opsaxiom run <id>（含变更影响说明+确认+校验）。"}
    return result


def cmd_incident(args):
    params = {}
    for kv in args.param or []:
        if "=" in kv:
            k, v = kv.split("=", 1)
            params[k] = v
    r = run_incident(args.symptom, params=params, target=args.target,
                     top=args.top, grant=args.grant, ingest_file=args.ingest_file,
                     nonce=args.nonce)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, default=str))
        return 0
    if r.get("dossier_text"):
        print(r["dossier_text"])
    elif r.get("plan"):
        print(r["plan"]["paste_block"])
    else:
        print(r.get("note", ""))
    return 0


def add_incident(sub):
    ap = sub.add_parser("incident", help="自动诊断：一次完成匹配、取证、卷宗（适合脚本/pi）")
    ap.add_argument("symptom")
    ap.add_argument("--param", action="append", metavar="k=v")
    ap.add_argument("--target", default="local")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--grant", action="store_true",
                    help="允许在本机自动运行只看不改的命令来收集信息")
    ap.add_argument("--ingest-file", dest="ingest_file",
                    help="远端排查：把你在目标机器上跑完命令的输出存成文件传给它")
    ap.add_argument("--nonce", help="与命令清单配套的一次性校验码")
    ap.add_argument("--json", action="store_true")
    ap.set_defaults(fn=cmd_incident)
