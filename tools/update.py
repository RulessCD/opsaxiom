"""
opsaxiom update —— 自更新（#12）。四步时序，任一必需步失败即停：

  ① git pull --ff-only   拉 ROOT 仓库最新代码（非 git 检出面 / 未配置远程 → 跳过不阻断）
  ② 依赖重装检测         requirements.txt 哈希 vs 上次安装落盘哈希（.venv/deps.sha256）
                          变了 → venv 内 pip install；失败🟡不阻断（doctor 会指出影响面）
  ③ hub sync             同步社区 Skill 库；离线降级提示，不阻断
  ④ doctor               部署自检收尾（必需项红 → 更新判失败）
"""
import hashlib
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import doctor   # noqa: E402
import hubtool  # noqa: E402

DEPS_HASH_REL = pathlib.PurePosixPath(".venv") / "deps.sha256"


def _last_line(s):
    lines = [ln for ln in (s or "").strip().splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def _git_pull(root):
    """返回 (rc, 一行详情)。rc==0 继续（含跳过支路），!=0 红停。"""
    if not (pathlib.Path(root) / ".git").exists():
        return 0, "（非 git 检出面：跳过代码更新）"
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "pull", "--ff-only"],
            capture_output=True, text=True, timeout=120)
    except Exception as e:
        return 1, str(e)
    if r.returncode == 0:
        detail = _last_line(r.stdout)
        if "already up to date" in detail.lower() or "up to date" in detail.lower():
            return 0, "已是最新"
        return 0, detail or "已更新"
    err = (r.stderr or "") + (r.stdout or "")
    # 干净跳过：没配远程/无法到上游（气隙兜底面）——不算失败
    low = err.lower()
    if "no tracking information" in low or "no remote repository" in low \
       or "does not appear to be a git repository" in low:
        return 0, "（未配置远程：跳过代码更新）"
    return r.returncode, _last_line(err) or "git pull 失败"


def deps_hash_of(root):
    p = pathlib.Path(root) / "tools" / "requirements.txt"
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def saved_deps_hash(root):
    p = pathlib.Path(root) / ".venv" / "deps.sha256"
    return p.read_text().strip() if p.exists() else None


def deps_changed(root):
    return saved_deps_hash(root) != deps_hash_of(root)


def write_deps_hash(root):
    h = deps_hash_of(root)
    if h:
        p = pathlib.Path(root) / ".venv" / "deps.sha256"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(h)


def _pip_install(root):
    """venv 内重装依赖，成功落盘新哈希。返回 True/False（失败不阻断后续步骤）。"""
    py = pathlib.Path(root) / ".venv" / "bin" / "python3"
    if not py.exists():
        py = pathlib.Path(sys.executable)
    try:
        r = subprocess.run(
            [str(py), "-m", "pip", "install", "-q",
             "-r", str(pathlib.Path(root) / "tools" / "requirements.txt")],
            capture_output=True, text=True, timeout=600)
    except Exception as e:
        print(f"  🟡 依赖重装失败（{e}）——doctor 会指出影响面")
        return False
    if r.returncode == 0:
        write_deps_hash(root)
        return True
    print(f"  🟡 部分依赖装失败：{_last_line(r.stderr or r.stdout)}（doctor 会指出影响面）")
    return False


def run(root=ROOT):
    print("==> OpsAxiom 自更新")
    rc, detail = _git_pull(root)
    if rc != 0:
        print(f"  🔴 无法拉取代码：{detail}")
        print("     手动处理：git status 看本地改动；解决后重跑 opsaxiom update。")
        return 1
    print(f"==> 代码：{detail}")

    if deps_changed(root):
        print("==> 依赖有更新，重装（pip install -r tools/requirements.txt）")
        print("     ✔ 依赖已更新" if _pip_install(root) else "     依赖重装未完成（见上）")
    else:
        print("==> 依赖未变化，跳过重装")

    try:
        n = hubtool.hub_sync()
        print(f"==> Skill 库已同步（{n} 个）")
    except Exception as e:
        print(f"  🟡 Skill 库同步跳过（{e}）——恢复网络后可执行 opsaxiom hub sync")

    print("==> doctor 自检")
    return doctor.run()


def add_update(subparsers):
    p = subparsers.add_parser("update", help="自更新：代码→依赖→Skill 库→自检")
    p.set_defaults(fn=lambda args: run())
