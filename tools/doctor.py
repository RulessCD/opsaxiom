"""
opsaxiom doctor —— 部署后自检（V-1）。红黄绿输出，也是日后排障第一命令。

检查项分三类：
  必需（红）：python 版本、pyyaml/jsonschema、tools/bin 可执行、~/.opsaxiom 可写
  推荐（黄）：cryptography（Ed25519 签名，缺则 attest 降级 HMAC）、pytest、ntc-templates、
             paramiko（远程 SSH/网络自动执行，缺则远程探针降级人工贴回）
  连接器（黄/灰）：ssh/kubectl/mysql/redis-cli 是否在 PATH（缺只影响对应域的真实执行）
"""
import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

OK, WARN, BAD = "🟢", "🟡", "🔴"


def _check_import(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def run(in_repl=False):
    rows = []          # (level, 名称, 详情)

    # --- 必需 ---
    pyok = sys.version_info >= (3, 8)
    rows.append((OK if pyok else BAD, "Python ≥ 3.8",
                 f"{sys.version_info.major}.{sys.version_info.minor}"))
    for mod in ("yaml", "jsonschema"):
        ok = _check_import(mod)
        rows.append((OK if ok else BAD, f"依赖 {mod}", "已装" if ok else "缺失！pip install -r tools/requirements.txt"))

    binp = ROOT / "tools" / "bin" / "opsaxiom"
    binok = binp.exists() and os.access(binp, os.X_OK)
    rows.append((OK if binok else BAD, "opsaxiom 可执行", str(binp) if binok else "不可执行，chmod +x"))

    home = pathlib.Path(os.environ.get("OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom"))
    try:
        home.mkdir(parents=True, exist_ok=True)
        (home / ".probe").write_text("x"); (home / ".probe").unlink()
        homeok = True
    except Exception:
        homeok = False
    rows.append((OK if homeok else BAD, "~/.opsaxiom 可写", str(home) if homeok else "不可写"))

    # --- 推荐 ---
    crypto = _check_import("cryptography")
    rows.append((OK if crypto else WARN, "cryptography (Ed25519)",
                 "已装" if crypto else "缺失→attest 降级 HMAC(不可跨主体验证)"))
    for mod in ("pytest", "ntc_templates", "paramiko"):
        ok = _check_import(mod)
        if mod == "paramiko":
            rows.append((OK if ok else WARN, f"可选 {mod}",
                         "已装" if ok else "未装(远程 SSH/网络设备自动执行不可用，降级人工贴回)"))
        else:
            rows.append((OK if ok else WARN, f"可选 {mod}", "已装" if ok else "未装(部分功能受限)"))

    # --- 连接器 ---
    for tool, dom in [("ssh", "host/aicomp"), ("kubectl", "k8s"), ("mysql", "middleware/mysql"),
                      ("redis-cli", "middleware/redis")]:
        present = shutil.which(tool) is not None
        rows.append((OK if present else WARN, f"连接器 {tool}",
                     "在 PATH" if present else f"未找到(影响 {dom} 域真实执行，导航档不受影响)"))

    # 1Password CLI
    op_present = shutil.which("op") is not None
    if op_present:
        import subprocess
        try:
            r = subprocess.run(["op", "account", "get"],
                              capture_output=True, text=True, timeout=5)
            op_logged = r.returncode == 0
        except Exception:
            op_logged = False
        op_msg = "已安装，已登录" if op_logged else "已安装，未登录"
    else:
        op_msg = "未安装(可作为凭证来源供设备接入)"
    rows.append((OK if op_present else WARN, "1Password CLI (op)", op_msg))

    # 输出
    print("OpsAxiom doctor —— 部署自检\n")
    for lvl, name, detail in rows:
        print(f"  {lvl} {name:<26} {detail}")
    reds = sum(1 for r in rows if r[0] == BAD)
    warns = sum(1 for r in rows if r[0] == WARN)
    print()
    if reds:
        print(f"🔴 {reds} 项必需检查未通过——请先修复再使用。")
        return 1
    print(f"🟢 必需项全部通过（{warns} 项推荐/连接器提示，不阻断使用）。")
    if in_repl:
        print("自检完成，回到交互态继续（直接说你的问题，或  update/overlay/model 等配置命令）。",
              file=sys.stdout)
    else:
        print("下一步：直接输入 opsaxiom 进入交互态，描述你的问题即可。")
    return 0


def add_doctor(subparsers, in_repl=False):
    p = subparsers.add_parser("doctor", help="部署后自检（红黄绿）")
    p.set_defaults(fn=lambda args: run(in_repl=in_repl))
