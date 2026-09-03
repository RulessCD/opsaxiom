"""一键认证（新版静默流）：done 终点 y 反馈 → 静默签名（audit 留痕）+ 社区同步提示。"""
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sim"))
import runtime  # noqa: E402


def _disk_full_skill():
    return next(x for x in (ROOT / "skills").rglob("skill.yaml")
                if yaml.safe_load(x.read_text())["metadata"]["id"] == "host.storage.capacity.disk-full")


def _run_session(tmp_path, feedback):
    a = yaml.safe_load((ROOT / "demos" / "disk-full-guided.answers.yaml").read_text())
    a["answers"]["done_ok:fb"] = "👍y" if feedback == "y" else feedback
    io = runtime.IO(answers=a["answers"], echo=False)
    sess = runtime.Session(_disk_full_skill(), params=a["params"], mode="guided",
                           io=io, sid="att-" + feedback)
    return sess.run()


def test_done_with_y_feedback_runs_silent_attest(tmp_path, monkeypatch):
    """done + y 反馈 → 静默签名（audit 留痕）；签名落 registry 条目旁（registry 为准）。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    # 模拟已 hub sync：把 disk-full 塞进 registry 缓存（仓库 skills/ 是历史存档不参与）
    dst = tmp_path / "hub" / "registry" / "skills" / "host.storage.capacity.disk-full" / "0.1.0"
    dst.mkdir(parents=True)
    (dst / "skill.yaml").write_text(_disk_full_skill().read_text(encoding="utf-8"),
                                    encoding="utf-8")
    # 无 gh_token 时静默流会问一次"是否配置同步"——喂 n（跳过）
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    a = yaml.safe_load((ROOT / "demos" / "disk-full-guided.answers.yaml").read_text())
    a["answers"]["done_ok:fb"] = "y"
    io = runtime.IO(answers=a["answers"], echo=False)
    sess = runtime.Session(_disk_full_skill(), params=a["params"], mode="guided", io=io, sid="v3y")
    r = sess.run()
    assert r["outcome"] == "done"
    # meta.json 留存（供 attest --from-session 预填）
    assert (tmp_path / "sessions" / "v3y.meta.json").exists()
    # 审计含 feedback 与 attest 记录；签名落 registry 条目旁
    audit = pathlib.Path(r["audit_file"]).read_text()
    assert '"type": "feedback"' in audit
    assert '"type": "attest"' in audit and '"ok": true' in audit
    adir = dst / "attestations"
    assert adir.is_dir() and list(adir.glob("*.yaml")), "签名应落在 registry 条目旁"


def test_negative_feedback_triggers_issue_report(tmp_path, monkeypatch):
    """新版：n 反馈 → 上报社区（无 token → 浏览器预填， Tested via _report_issue 被调）。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    a = yaml.safe_load((ROOT / "demos" / "disk-full-guided.answers.yaml").read_text())
    a["answers"]["done_ok:fb"] = "n"
    reported = {}
    # mock 掉 _report_issue 避免真开浏览器
    io = runtime.IO(answers=a["answers"], echo=False)
    sess = runtime.Session(_disk_full_skill(), params=a["params"], mode="guided", io=io, sid="w3neg")
    monkeypatch.setattr(sess, "_report_issue", lambda n: {"reported": n["id"]})
    captured = {}
    sess._report_issue = lambda n: captured.setdefault("nid", n["id"])
    sess.run()
    assert captured  # n 反馈 → 走了上报路径
    # meta.json 留存
    assert (tmp_path / "sessions" / "w3neg.meta.json").exists()
