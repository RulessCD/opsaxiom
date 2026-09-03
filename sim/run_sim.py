#!/usr/bin/env python3
"""
仿真执行器 v0（O-6）—— 无需 docker/root，在本地临时沙箱里驱动 Skill 决策树。

它做三件真事：
  1. 用真实的表达式求值器(exprlang.evaluate)走决策树，按 scenario 提供的每节点上下文选分支；
  2. 断言实际走过的路径 == 场景声明的 expect_path（回归测试决策逻辑）；
  3. 对标注了 real_action 的 action 节点，真实执行 action 的 rollback 往返
     （当前支持 opsaxiom-quarantine 的 move→restore），验证"回滚真的能把状态还原"（R1）。

场景格式见 sim/scenarios/*.yaml。这是 v0：check 节点的输出由场景直接给出结构化上下文
（相当于解析器的产物），无需真实靶机；action 的回滚往返用真实文件操作验证。
网络设备仿真(containerlab)留待下一轮。
"""
import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import yaml

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tools"))
import exprlang  # noqa: E402

QUARANTINE_BIN = ROOT / "tools" / "bin" / "opsaxiom-quarantine"
DEPLOY_BIN = ROOT / "tools" / "bin" / "opsaxiom-deploy"
MYSQLVAR_BIN = ROOT / "tools" / "bin" / "opsaxiom-mysqlvar"
SVCSTATE_BIN = ROOT / "tools" / "bin" / "opsaxiom-svcstate"
FSNAPSHOT_BIN = ROOT / "tools" / "bin" / "opsaxiom-fsnapshot"
TXN_BIN = ROOT / "tools" / "bin" / "opsaxiom-txn"


class SimError(Exception):
    pass


def _load_skill(path):
    skill = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in skill["tree"]["nodes"]}
    return skill, nodes, skill["tree"]["entry"]


# ---- 真实靶机执行器 v1（Q-3）：本地沙箱、只读命令白名单 ----
_ALLOW_LEAD = {"cat", "df", "du", "free", "ps", "ss", "uptime", "nproc", "vmstat",
               "iostat", "mpstat", "lsof", "findmnt", "systemctl", "dmesg",
               "journalctl", "ip", "ls", "find", "for", "echo", "grep", "true",
               # 自研只读采集器（U-2）：只读指标，无副作用
               "opsaxiom-collect", "nvidia-smi"}
# mount 特例：无参数形式是纯查询（打印挂载表），带参数/选项即写（挂载/remount）
_MOUNT_INFO = re.compile(r"^mount\s*(2>\S+\s*)*(\|\s|$|2>$)")
_DENY = re.compile(r"\b(rm|mv|cp|dd|mkfs\w*|reboot|shutdown|kill|pkill|tee|truncate|chmod|chown)\b|>\s*/(?!dev/null)")
# kubectl 子命令级白名单（X-2）：只读动词才放行；apply/delete/edit/scale/patch/exec 一律拒。
# exec 尤其危险——能在容器内跑任意写命令，绝不算只读。用 token 集合判定，
# 避开 `kubectl -n <ns> get ...` 里 flag 与其值的干扰。
_KUBECTL_RO = {"get", "describe", "logs", "top", "explain", "version",
               "api-resources", "cluster-info"}
_KUBECTL_WRITE = {"apply", "delete", "edit", "scale", "patch", "exec", "create",
                  "replace", "cordon", "drain", "annotate", "label", "set",
                  "taint", "uncordon", "undo", "attach", "cp", "port-forward"}


# shell 元字符：命令链接/替换/重定向。只读 kubectl 绝不需要它们，出现即拒（F-16 防注入）。
_SHELL_META = re.compile(r"[;&|`$<>(){}]")


def _is_readonly(cmd):
    lead = cmd.strip().split()[0] if cmd.strip() else ""
    if lead == "kubectl":
        if _SHELL_META.search(cmd):        # 含 shell 元字符 → 拒（防 `; rm -rf /` / $(...) / `...`）
            return False
        toks = set(cmd.split())
        if toks & _KUBECTL_WRITE:          # 含任何写动词 → 拒
            return False
        return bool(toks & _KUBECTL_RO)    # 且含至少一个只读动词
    if lead == "mount":
        # mount 无参数 = 打印挂载表（纯查询）；带任何参数/选项 = 挂载操作 → 拒
        return bool(_MOUNT_INFO.match(cmd.strip())) and not _DENY.search(cmd)
    return lead in _ALLOW_LEAD and not _DENY.search(cmd)


def _default_parse(stdout):
    lines = [ln for ln in stdout.splitlines()]
    m = re.search(r"-?\d+", stdout)
    return {"lines": lines, "output": {"value": int(m.group()) if m else 0}}


def run_real(skill_path, scenario_path):
    """真实模式：本地执行 linux check 命令 → 真实解析 → 真实分支，走到终点。
    不断言固定路径（真机状态不可控），断言'管道端到端跑通且终止'。"""
    import parsers as _parsers
    skill, nodes, entry = _load_skill(skill_path)
    sc = yaml.safe_load(pathlib.Path(scenario_path).read_text())
    bindings = sc.get("bindings", {})
    answers = sc.get("answers", {})

    def subst(cmd):
        for k, v in bindings.items():
            cmd = cmd.replace("{{%s}}" % k, str(v))
        return cmd

    notes, path, node, guard = [], [entry], entry, 0
    while guard < 60:
        guard += 1
        n = nodes[node]
        t = n["type"]
        if t in ("done", "escalate"):
            break
        if t == "action":
            notes.append(f"到达 action '{node}'（真实模式只读，不执行写操作，停止）")
            break
        if t == "ask":
            if node in answers:
                node = answers[node]; path.append(node); continue
            notes.append(f"到达 ask '{node}'（无预设答案，停止）")
            break
        # check：跑真实命令（取任一连接器键的命令：linux/kubectl/ssh/http…，X-2）
        run_d = n.get("run", {}) or {}
        cmd = subst(next(iter(run_d.values()), "") if isinstance(run_d, dict) else "")
        if not cmd or not _is_readonly(cmd):
            notes.append(f"节点 {node} 命令非只读白名单或为空，跳过真实执行：{cmd[:60]!r}")
            node = n.get("otherwise", "escalate"); path.append(node); continue
        try:
            r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=15)
            stdout = r.stdout
        except Exception as e:
            notes.append(f"节点 {node} 命令执行异常：{e}")
            node = n.get("otherwise", "escalate"); path.append(node); continue
        pfn = _parsers.get_parser(n["parser"]) if n.get("parser") else None
        ctx = pfn(stdout) if pfn else _default_parse(stdout)
        if not isinstance(ctx, dict):
            ctx = {"rows": ctx}
        ctx.setdefault("lines", stdout.splitlines())
        for k, v in bindings.items():
            ctx.setdefault(k, v)
        nxt = None
        for br in n.get("branch", []):
            try:
                if exprlang._truthy(exprlang.evaluate(br["when"], ctx)):
                    nxt = br["goto"]; break
            except exprlang.EvalError:
                pass
        node = nxt or n.get("otherwise", "escalate")
        path.append(node)
    completed = nodes[path[-1]]["type"] in ("done", "escalate") or path[-1] in ("done", "escalate")
    return {"path": path, "completed": completed, "evidence": "real_roundtrip", "notes": notes}


def _real_quarantine_roundtrip(sandbox_file, notes):
    """真实执行 move→restore，验证隔离与恢复。返回 True/False。"""
    with tempfile.TemporaryDirectory() as td:
        qroot = pathlib.Path(td) / "q"
        target = pathlib.Path(td) / sandbox_file
        target.write_text("payload-to-quarantine")
        env = dict(os.environ, OPSAXIOM_QUARANTINE_ROOT=str(qroot))
        sid = "sim"
        # move
        subprocess.run([sys.executable, str(QUARANTINE_BIN), "move", "--session", sid, str(target)],
                       check=True, env=env, capture_output=True)
        if target.exists():
            notes.append("回滚测试失败：move 后原文件仍在")
            return False
        # rollback = restore
        subprocess.run([sys.executable, str(QUARANTINE_BIN), "restore", "--session", sid],
                       check=True, env=env, capture_output=True)
        if not target.exists() or target.read_text() != "payload-to-quarantine":
            notes.append("回滚测试失败：restore 未还原原文件")
            return False
        notes.append("回滚往返成功：move 使文件消失，restore 精确还原")
        return True


def _real_deploy_roundtrip(notes):
    """真实执行 opsaxiom-deploy install→uninstall，验证部署与回滚(卸载)对称。"""
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, OPSAXIOM_DEPLOY_ROOT=td)
        state = pathlib.Path(td) / "state" / "node_exporter.json"
        subprocess.run([sys.executable, str(DEPLOY_BIN), "node_exporter", "--port", "9100"],
                       check=True, env=env, capture_output=True)
        if not state.exists():
            notes.append("回滚测试失败：install 未落 state"); return False
        subprocess.run([sys.executable, str(DEPLOY_BIN), "node_exporter", "--uninstall"],
                       check=True, env=env, capture_output=True)
        if state.exists() or (pathlib.Path(td) / "bin" / "node_exporter").exists():
            notes.append("回滚测试失败：uninstall 未清干净"); return False
        notes.append("回滚往返成功：install 落盘，uninstall 精确清除")
        return True


def _real_mysqlvar_roundtrip(notes):
    """真实执行 SET GLOBAL 的 mock set→restore，验证 inverse 回滚精确还原旧值。"""
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, OPSAXIOM_MYSQLVAR_ROOT=td, OPSAXIOM_MYSQLVAR_INIT="151")
        def run(*a):
            return subprocess.run([sys.executable, str(MYSQLVAR_BIN), *a],
                                  env=env, capture_output=True, text=True)
        run("set", "max_connections", "300")            # 上调（记 before=151）
        if run("get", "max_connections").stdout.strip() != "300":
            notes.append("回滚测试失败：set 未生效"); return False
        run("restore", "max_connections")               # 回滚
        if run("get", "max_connections").stdout.strip() != "151":
            notes.append("回滚测试失败：restore 未还原旧值 151"); return False
        notes.append("回滚往返成功：SET GLOBAL 300→restore 精确还原 151")
        return True


def _real_svcstate_roundtrip(notes):
    """service_restore 型：restart 改变服务状态→restore 精确还原动作前状态。"""
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, OPSAXIOM_SVCSTATE_ROOT=td, OPSAXIOM_SVCSTATE_INIT="failed")
        def run(*a):
            return subprocess.run([sys.executable, str(SVCSTATE_BIN), *a],
                                  env=env, capture_output=True, text=True)
        run("restart", "app.service")            # 动作（记 before=failed）
        if run("get", "app.service").stdout.strip() != "active":
            notes.append("回滚测试失败：restart 未使服务进入 active"); return False
        run("restore", "app.service")            # 回滚 = 回到动作前状态
        if run("get", "app.service").stdout.strip() != "failed":
            notes.append("回滚测试失败：restore 未还原动作前状态 failed"); return False
        notes.append("回滚往返成功：restart failed→active，restore 精确还原 failed")
        return True


def _real_fsnapshot_roundtrip(sandbox_file, notes):
    """snapshot 型：先快照→操作改动数据→恢复快照后逐字节一致。"""
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, OPSAXIOM_FSNAPSHOT_ROOT=str(pathlib.Path(td) / "snaps"))
        target = pathlib.Path(td) / sandbox_file
        target.write_text("fs-metadata-original")
        def run(*a):
            return subprocess.run([sys.executable, str(FSNAPSHOT_BIN), *a],
                                  check=True, env=env, capture_output=True)
        run("snap", str(target))                 # 操作前快照（前置红线）
        target.write_text("fs-metadata-mutated-by-fsck")   # 模拟修复操作改动数据
        run("restore", str(target))              # 回滚 = 恢复快照
        if target.read_text() != "fs-metadata-original":
            notes.append("回滚测试失败：restore 后数据与快照不一致"); return False
        notes.append("回滚往返成功：snap→改动→restore 逐字节还原")
        return True


def _real_txn_roundtrip(notes):
    """transaction 型：begin 切到目标修订→revert 精确回到变更前修订。"""
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, OPSAXIOM_TXN_ROOT=td, OPSAXIOM_TXN_INIT="5")
        def run(*a):
            return subprocess.run([sys.executable, str(TXN_BIN), *a],
                                  env=env, capture_output=True, text=True)
        run("begin", "deploy", "3")              # 动作：undo 到 rev 3（记 before=5）
        if run("get", "deploy").stdout.strip() != "3":
            notes.append("回滚测试失败：begin 未切到目标修订 3"); return False
        run("revert", "deploy")                  # 超时未确认路径：平台自动回退
        if run("get", "deploy").stdout.strip() != "5":
            notes.append("回滚测试失败：revert 未回到变更前修订 5"); return False
        notes.append("回滚往返成功：rev 5→3（begin），revert 精确回到 5")
        return True


_ROUNDTRIP = {"quarantine": _real_quarantine_roundtrip, "deploy": _real_deploy_roundtrip,
              "mysqlvar": _real_mysqlvar_roundtrip, "svcstate": _real_svcstate_roundtrip,
              "snapshot": _real_fsnapshot_roundtrip, "txn": _real_txn_roundtrip}


def run(skill_path, scenario_path):
    sc0 = yaml.safe_load(pathlib.Path(scenario_path).read_text(encoding="utf-8"))
    if sc0.get("mode") == "real":
        return run_real(skill_path, scenario_path)
    skill, nodes, entry = _load_skill(skill_path)
    sc = yaml.safe_load(pathlib.Path(scenario_path).read_text(encoding="utf-8"))
    node_ctx = sc.get("node_ctx", {})
    answers = sc.get("answers", {})
    base_ctx = sc.get("base_ctx", {})
    real_action = sc.get("real_action")          # 需要真实回滚验证的 action 节点 id
    real_kind = sc.get("real_action_kind", "quarantine")   # quarantine | deploy
    sandbox_file = sc.get("sandbox_file", "victim.dat")

    notes, path = [], [entry]
    node, guard = entry, 0
    rollback_ok = None
    while guard < 100:
        guard += 1
        n = nodes[node]
        t = n["type"]
        if t in ("done", "escalate"):
            break
        ctx = dict(base_ctx)
        ctx.update(node_ctx.get(node, {}))
        if t == "check":
            nxt = None
            for br in n["branch"]:
                try:
                    if exprlang._truthy(exprlang.evaluate(br["when"], ctx)):
                        nxt = br["goto"]; break
                except exprlang.EvalError as e:
                    raise SimError(f"节点 {node} 表达式求值失败: {br['when']} ({e})")
            node = nxt or n["otherwise"]
        elif t == "ask":
            if node not in answers:
                raise SimError(f"ask 节点 {node} 场景未提供 answers")
            node = answers[node]
        elif t == "action":
            if real_action == node:
                fn = _ROUNDTRIP[real_kind]
                # 文件级往返（quarantine/snapshot）需要 sandbox_file，其余只要 notes
                rollback_ok = (fn(sandbox_file, notes)
                               if real_kind in ("quarantine", "snapshot") else fn(notes))
            node = n.get("goto") or n.get("verify", {}).get("on_fail", "escalate")
        else:
            raise SimError(f"未知节点类型 {t} @ {node}")
        path.append(node)

    expect = sc.get("expect_path")
    path_ok = (path == expect) if expect else None
    return {"path": path, "expect": expect, "path_ok": path_ok,
            "rollback_ok": rollback_ok, "notes": notes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    args = ap.parse_args()
    sc = yaml.safe_load(pathlib.Path(args.scenario).read_text())
    skill_path = (ROOT / sc["skill"]) if not os.path.isabs(sc["skill"]) else pathlib.Path(sc["skill"])
    r = run(skill_path, args.scenario)
    print(f"场景: {sc.get('scenario')}")
    print(f"走过路径: {' -> '.join(r['path'])}")
    if sc.get("mode") == "real":
        print(f"证据: {r['evidence']}  终止: {'✔' if r['completed'] else '✘'}")
        for nt in r["notes"]:
            print(f"  · {nt}")
        return 0 if r["completed"] else 1
    print(f"期望路径: {' -> '.join(r['expect'] or [])}")
    print(f"路径匹配: {'✔' if r['path_ok'] else '✘'}")
    if r["rollback_ok"] is not None:
        print(f"回滚往返: {'✔' if r['rollback_ok'] else '✘'}")
    for nt in r["notes"]:
        print(f"  · {nt}")
    ok = r["path_ok"] and (r["rollback_ok"] is not False)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
