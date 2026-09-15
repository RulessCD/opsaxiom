"""attest 主 CLI 接线 + auth 补发闭环（2026-09-11）。"""
import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))


def test_attest_subcommand_wired():
    """opsaxiom attest --help 走主 CLI 可达（docs/10:174 承诺的补交入口）。"""
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "bin" / "opsaxiom"), "attest", "--help"],
        capture_output=True, text=True)
    assert r.returncode == 0
    assert "--from-session" in r.stdout


def test_attest_keygen_via_main_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "bin" / "opsaxiom"), "attest", "--keygen"],
        capture_output=True, text=True)
    assert r.returncode == 0 and "公钥" in r.stdout
    assert (tmp_path / "keys" / "attest_ed25519.pub").exists()


# ---------- auth 补发 ----------

def _mk_repl(tmp_path, monkeypatch, issues):
    """构造最小 REPL 实例：只用到 _flush_pending_attest/_github_create_issue。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    import repl as R
    inst = object.__new__(R.Repl)
    sent = []
    inst._github_create_issue = (
        lambda title, body, labels, token=None:
        sent.append(title) or True)
    return inst, sent


def test_first_auth_marks_existing_attests_not_resent(tmp_path, monkeypatch):
    """首次启用：存量签名全部标记已同步，不补发（旧文件名实不符）。"""
    home = tmp_path
    adir = home / "hub" / "registry" / "skills" / "host.x" / "0.1.0" / "attestations"
    adir.mkdir(parents=True)
    (adir / "2026-01-01-aaaa.yaml").write_text("attestor: gh:old\n")
    inst, sent = _mk_repl(tmp_path, monkeypatch, [])
    inst._flush_pending_attest()
    assert sent == []
    assert (home / ".attest_synced").exists()          # 已建标记
    inst._flush_pending_attest()                        # 第二次：名单已初始化
    assert sent == []


def test_flush_sends_unsigned_skip_and_real_send(tmp_path, monkeypatch):
    """补发只发非 anonymous；anonymous 记同步跳过。"""
    home = tmp_path
    marker = home / ".attest_synced"
    marker.write_text("")                               # 名单已初始化（空）
    a1 = home / "hub" / "registry" / "skills" / "host.x" / "0.1.0" / "attestations"
    a1.mkdir(parents=True)
    (a1 / "2026-01-01-bbbb.yaml").write_text("skill: host.x\nattestor: anonymous\n")
    (a1 / "2026-01-02-cccc.yaml").write_text("skill: host.y\nattestor: gh:alice\n")
    inst, sent = _mk_repl(tmp_path, monkeypatch, [])
    inst._flush_pending_attest()
    assert len(sent) == 1 and "host.y" in sent[0]
    assert "bbbb" in marker.read_text()                 # anonymous 也记避免反复扫
    assert "cccc" in marker.read_text()


def test_flush_respects_synced_marker(tmp_path, monkeypatch):
    home = tmp_path
    a1 = home / "hub" / "registry" / "skills" / "host.x" / "0.1.0" / "attestations"
    a1.mkdir(parents=True)
    (a1 / "2026-01-01-dddd.yaml").write_text("skill: host.x\nattestor: gh:alice\n")
    (home / ".attest_synced").write_text("2026-01-01-dddd.yaml\n")
    inst, sent = _mk_repl(tmp_path, monkeypatch, [])
    inst._flush_pending_attest()
    assert sent == []


# ---------- 三发件话术矩阵（mock ghutil，不出网） ----------

def _run_feedback(tmp_path, monkeypatch, feedback, token_state, login_who="alice"):
    """造一个会话走到 done 终点，反馈 feedback；ghutil 桩为 token_state。"""
    import yaml
    import runtime
    import ghutil as G
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    monkeypatch.setattr(G, "check_token",
                        lambda force=False: (token_state, login_who if token_state == "valid" else None,
                                             ""))
    monkeypatch.setattr(G, "read_token", lambda: "tok" if token_state != "missing" else "")

    def fake_post(self, title, body, labels):
        fake_post.calls.append((title, body, labels))
        return True
    fake_post.calls = []
    monkeypatch.setattr(runtime.Session, "_gh_post_issue", fake_post)

    skill = next(x for x in (ROOT / "skills").rglob("skill.yaml")
                 if yaml.safe_load(x.read_text())["metadata"]["id"]
                 == "host.storage.capacity.disk-full")
    # registry 缓存里放同id条目（attestation 落盘 + 补发扫描的目标）
    dst = tmp_path / "hub" / "registry" / "skills" / "host.storage.capacity.disk-full" / "0.1.0"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "skill.yaml").write_text(skill.read_text(encoding="utf-8"))

    a = yaml.safe_load((ROOT / "demos" / "disk-full-guided.answers.yaml").read_text())
    a["answers"]["done_ok:fb"] = feedback
    io = runtime.IO(answers=a["answers"], echo=False)
    sess = runtime.Session(skill, params=a["params"], mode="guided", io=io, sid="fb-" + feedback)
    r = sess.run()
    return r, fake_post.calls, tmp_path, dst


def test_y_valid_token_sends_issue_with_signature(tmp_path, monkeypatch):
    import yaml as _y
    r, calls, tmp_path, dst = _run_feedback(tmp_path, monkeypatch, "y", "valid")
    assert r["outcome"] == "done"
    assert len(calls) == 1 and calls[0][2] == ["attestation"]
    assert "signature: ed25519:" in calls[0][1]         # 签名体随附
    assert "anonymous" not in calls[0][1]
    # marker 记了已同步
    assert (tmp_path / ".attest_synced").exists()


def test_y_invalid_token_no_issue_but_signed(tmp_path, monkeypatch):
    r, calls, tmp_path, dst = _run_feedback(tmp_path, monkeypatch, "y", "invalid")
    assert calls == []                                   # 不发
    files = list((dst / "attestations").glob("*.yaml"))
    assert files and "anonymous" in files[0].read_text() # 签名照落（anonymous）


def test_y_offline_no_issue_but_signed(tmp_path, monkeypatch):
    r, calls, tmp_path, dst = _run_feedback(tmp_path, monkeypatch, "y", "offline")
    assert calls == []
    assert list((dst / "attestations").glob("*.yaml"))


def test_y_missing_no_issue(tmp_path, monkeypatch):
    # missing + 首问 marker 已存在 → 不再弹问（首问仅一次）
    (tmp_path / ".attest_asked").write_text("")
    r, calls, tmp_path, dst = _run_feedback(tmp_path, monkeypatch, "y", "missing")
    assert calls == []


def test_y_valid_attestor_is_login(tmp_path, monkeypatch):
    r, calls, tmp_path, dst = _run_feedback(tmp_path, monkeypatch, "y", "valid")
    import yaml
    att = yaml.safe_load(list((dst / "attestations").glob("*.yaml"))[0].read_text())
    assert att["attestor"] == "alice"                    # login 派生
    assert "scale" not in str(att)                       # scale 语义退场
    assert att["env_fingerprint"].get("arch")            # arch 替位


def test_n_valid_sends_report_bug(tmp_path, monkeypatch):
    r, calls, tmp_path, dst = _run_feedback(tmp_path, monkeypatch, "n", "valid")
    assert len(calls) == 1 and calls[0][2] == ["report:bug"]


def test_n_invalid_no_browser(monkeypatch, tmp_path):
    """invalid → 明说失效不开浏览器（offline/invalid 均不开——没网/坏 token
    开了也提交不了）。"""
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u))
    r, calls, tmp_path, dst = _run_feedback(tmp_path, monkeypatch, "n", "invalid")
    assert calls == []
    assert opened == []
