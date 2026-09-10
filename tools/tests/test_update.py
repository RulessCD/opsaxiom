"""#12 opsaxiom update —— 自更新子命令（git pull → 依赖重装检测 → hub sync → doctor）。"""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import update as U  # noqa: E402


# ---------- git pull：rc/详情/跳过支路 ----------

def test_git_pull_skip_non_git_dir(tmp_path):
    """非 git 检出面 → rc=0 跳过，不算失败。"""
    rc, detail = U._git_pull(tmp_path)
    assert rc == 0 and "跳过" in detail


def test_git_pull_real_repo_up_to_date(tmp_path):
    """真 git 仓库无远程（no tracking）→ rc=0 跳过不阻断（气隙兜底面）。"""
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    rc, detail = U._git_pull(tmp_path)
    assert rc == 0
    assert "跳过" in detail or "已是最新" in detail


def test_git_pull_failure_returns_rc(monkeypatch, tmp_path):
    """pull 真失败（rc!=0 且非跳过语）→ 透传 rc 红停。"""
    (tmp_path / ".git").mkdir()          # 过形态检查，让 run 被 mock 替换
    monkeypatch.setattr(
        U.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess([], 1, "", "fatal: conflict"))
    rc, detail = U._git_pull(tmp_path)
    assert rc != 0
    assert "fatal" in detail


def test_git_pull_skip_non_git_dir_with_gitdir_check(tmp_path):
    """目录存在 .git 才走 pull；否则跳过——同形态、不同 rc。"""
    rc, detail = U._git_pull(tmp_path)   # 无 .git → 跳过不走 subprocess
    assert rc == 0 and "跳过" in detail


# ---------- 依赖哈希：变了才重装 ----------

def test_deps_hash_roundtrip(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "requirements.txt").write_text("pyyaml>=6.0\n")
    assert U.deps_hash_of(tmp_path) is not None
    assert U.saved_deps_hash(tmp_path) is None    # 未落盘过
    assert U.deps_changed(tmp_path) is True       # 默认视为变化
    U.write_deps_hash(tmp_path)
    assert U.deps_changed(tmp_path) is False      # 落盘后一致


def test_deps_missing_requirements_is_none(tmp_path):
    assert U.deps_hash_of(tmp_path) is None


# ---------- 时序：git 失败即停，不继续 hub sync / doctor ----------

def test_git_failure_stops_before_hub_sync(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(U, "_git_pull", lambda root: (1, "fatal: xxx"))
    monkeypatch.setattr(U.hubtool, "hub_sync",
                        lambda: (_ for _ in ()).throw(AssertionError("不应执行")))
    rc = U.run(root=tmp_path)
    assert rc == 1
    assert "无法拉取" in capsys.readouterr().out


def test_order_and_deps_skip(monkeypatch, tmp_path, capsys):
    """四步时序真断言（修复原恒真断言）：git → (deps 跳过) → hub → doctor。"""
    calls = []
    monkeypatch.setattr(U, "_git_pull", lambda root: (calls.append("git"), (0, "已是最新"))[1])
    monkeypatch.setattr(U, "deps_changed", lambda root: calls.append("deps") or False)
    monkeypatch.setattr(U.hubtool, "hub_sync", lambda: calls.append("hub") or 0)
    monkeypatch.setattr(
        U.doctor, "run",
        lambda *a, **kw: (calls.append("doctor"), 0)[1])
    rc = U.run(root=tmp_path)
    assert calls == ["git", "deps", "hub", "doctor"]
    assert rc == 0


def test_order_with_deps_pip(monkeypatch, tmp_path, capsys):
    """deps 有变化支路的时序：git → pip 重装 → hub → doctor。
    （deps_changed 在 run 内被直接调用非 mock 链，故不记 calls。）"""
    calls = []
    monkeypatch.setattr(U, "_git_pull", lambda root: (calls.append("git"), (0, ""))[1])
    monkeypatch.setattr(U, "deps_changed", lambda root: True)
    monkeypatch.setattr(U, "_pip_install", lambda root: (calls.append("pip"), True)[1])
    monkeypatch.setattr(U.hubtool, "hub_sync", lambda: calls.append("hub") or 0)
    monkeypatch.setattr(U.doctor, "run", lambda *a, **kw: (calls.append("doctor"), 0)[1])
    rc = U.run(root=tmp_path)
    assert calls == ["git", "pip", "hub", "doctor"]
    assert rc == 0


def test_deps_changed_triggers_pip(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(U, "_git_pull", lambda root: (0, ""))
    monkeypatch.setattr(U, "deps_changed", lambda root: True)
    ran = []
    monkeypatch.setattr(U, "_pip_install", lambda root: (ran.append(True), True)[1])
    monkeypatch.setattr(U.hubtool, "hub_sync", lambda: 0)
    monkeypatch.setattr(U.doctor, "run", lambda *a, **kw: 0)
    rc = U.run(root=tmp_path)
    assert ran == [True]
    assert "依赖有更新" in capsys.readouterr().out
    assert rc == 0


# ---------- pip 失败不阻断后续步骤 ----------

def test_pip_failure_does_not_block(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(U, "_git_pull", lambda root: (0, ""))
    monkeypatch.setattr(U, "deps_changed", lambda root: True)
    monkeypatch.setattr(
        U.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess([], 1, "", "ERROR: x"))
    monkeypatch.setattr(U.hubtool, "hub_sync", lambda: 3)
    monkeypatch.setattr(U.doctor, "run", lambda *a, **kw: 0)
    rc = U.run(root=tmp_path)
    out = capsys.readouterr().out
    assert "依赖重装未完成" in out
    assert "Skill 库已同步（3 个）" in out
    assert rc == 0


# ---------- hub sync 离线降级 ----------

def test_hub_sync_offline_degrades(monkeypatch, tmp_path, capsys):
    """无网络（git pull 抛异常）→ 🟡 提示，继续 doctor。"""
    monkeypatch.setattr(U, "_git_pull", lambda root: (0, ""))
    monkeypatch.setattr(U, "deps_changed", lambda root: False)
    def boom():
        raise RuntimeError("unable to access")
    monkeypatch.setattr(U.hubtool, "hub_sync", boom)
    monkeypatch.setattr(U.doctor, "run", lambda *a, **kw: 0)
    rc = U.run(root=tmp_path)
    out = capsys.readouterr().out
    assert "Skill 库同步跳过" in out
    assert rc == 0


def test_git_pull_network_unreachable_degrades(monkeypatch, tmp_path, capsys):
    """断网真报错（Could not resolve host）→ 🟡 降级 rc=0 继续后续步骤，不红停。"""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        U.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            [], 128, "", "fatal: Could not resolve host github.com"))
    rc, detail = U._git_pull(tmp_path)
    assert rc == 0
    assert "网络不可达" in detail


def test_git_pull_timeout_exception_degrades(monkeypatch, tmp_path):
    """git 进程异常（如超时）→ 降级 rc=0，不红停。"""
    (tmp_path / ".git").mkdir()
    def slow(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="git", timeout=120)
    monkeypatch.setattr(U.subprocess, "run", slow)
    rc, detail = U._git_pull(tmp_path)
    assert rc == 0 and "跳过" in detail


def test_git_pull_conflict_still_red(monkeypatch, tmp_path):
    """真 git 失败（conflict 等非网络错误）仍红停——降级只限网络类。"""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        U.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess([], 1, "", "fatal: conflict"))
    rc, detail = U._git_pull(tmp_path)
    assert rc != 0


# ---------- 端到端（真 git 仓库，全程不出网）----------

def test_e2e_real_repo_green(tmp_path, monkeypatch, capsys):
    """真 git 仓库：pull 跳过 + 哈希一致 + hub 本地降级 → doctor 收尾退出码 0。"""
    import os
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path / "home"))
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "requirements.txt").write_text("pyyaml>=6.0\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    U.write_deps_hash(tmp_path)                   # 模拟上次 update 已落盘
    rc = U.run(root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "已是最新" in out or "跳过" in out
    assert "依赖未变化" in out
