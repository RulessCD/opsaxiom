"""Z-4 incident 会话与诊断卷宗测试：干跑三态 + 卷宗证据引用 + 移交 + 报告导出。

用 seed_fact 注入取证结果（等价 context_walk：给定结构化事实走树），
断言干跑判读与卷宗——判读逻辑全走 exprlang，与 sim 一致。
"""
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sim"))
import incident as I  # noqa: E402


def _load(rel):
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


DF = "skills/host/disk-full/skill.yaml"


def _disk_full_incident(params):
    inc = I.Incident("磁盘满了但 df 有空间", params=params, target="local")
    inc.add_hypotheses([_load(DF)])
    return inc


def test_dry_run_confirmed_inode_reaches_treatment():
    """inode 耗尽：走通 locate→check_inode→inode_exhaustion 到 ask（处置）→ 已证实。"""
    inc = _disk_full_incident({"mount": "/data"})
    # 逐 check 注入事实（命令须与渲染后一致）
    inc.seed_fact("df -B1 --output=target,size,used,avail,pcent /data",
                  {"rows": [{"target": "/data", "pcent": 96}]}, now=1.0)
    inc.seed_fact("df -i -P /data",
                  {"rows": [{"ipcent": 99}]}, now=1.0)
    inc.seed_fact(
        "find /data -xdev -type d -exec sh -c 'echo \"$(ls -a \"$1\" | wc -l) $1\"' _ {} \\; 2>/dev/null | sort -rn | head -10",
        {"rows": [{"path": "/data/sess", "n": 500000}]}, now=1.0)
    inc.dry_run(now=1.0)
    h = inc.hyps[0]
    assert h.status == I.CONFIRMED
    assert h.terminal.startswith("ask:") and h.pending["kind"] == "ask"
    d = inc.dossier(now=1.0)
    assert len(d[I.CONFIRMED]) == 1
    # 证据引用可回溯到具体命令与字段
    ev = d[I.CONFIRMED][0]["evidence"]
    assert any(e["field"] == "rows[0].ipcent" and e["value"] == 99 for e in ev)


def test_dry_run_refuted_false_alarm():
    """使用率 < 90 → locate_mount 判 false_alarm(done) → 已证实为误报（confirmed done）。"""
    inc = _disk_full_incident({"mount": "/data"})
    inc.seed_fact("df -B1 --output=target,size,used,avail,pcent /data",
                  {"rows": [{"target": "/data", "pcent": 40}]}, now=1.0)
    inc.dry_run(now=1.0)
    h = inc.hyps[0]
    assert h.status == I.CONFIRMED and h.terminal.startswith("done:")
    assert "40" in h.conclusion                       # summary 模板已渲染事实


def test_dry_run_insufficient_when_facts_missing():
    """没采到 locate_mount 的 df → 证据不足，且诚实标出还差哪条命令。"""
    inc = _disk_full_incident({"mount": "/data"})
    inc.dry_run(now=1.0)                               # 未 seed 任何事实
    h = inc.hyps[0]
    assert h.status == I.INSUFFICIENT
    assert h.missing and "df -B1" in h.missing
    d = inc.dossier(now=1.0)
    assert len(d[I.INSUFFICIENT]) == 1


def test_expired_facts_do_not_confirm():
    """过期事实不参与干跑（TTL 已过 → 视作未采）。"""
    inc = _disk_full_incident({"mount": "/data"})
    inc.seed_fact("df -B1 --output=target,size,used,avail,pcent /data",
                  {"rows": [{"target": "/data", "pcent": 96}]}, now=1.0)
    inc.dry_run(now=1000.0)                            # 999s 后，早过 300s TTL
    assert inc.hyps[0].status == I.INSUFFICIENT


def test_render_dossier_three_columns():
    inc = _disk_full_incident({"mount": "/data"})
    inc.seed_fact("df -B1 --output=target,size,used,avail,pcent /data",
                  {"rows": [{"target": "/data", "pcent": 40}]}, now=1.0)
    inc.dry_run(now=1.0)
    txt = inc.render_dossier(now=1.0)
    assert "诊断卷宗" in txt and "已排查" in txt


def test_handover_and_report_export():
    inc = _disk_full_incident({"mount": "/data"})
    inc.seed_fact("df -B1 --output=target,size,used,avail,pcent /data",
                  {"rows": [{"target": "/data", "pcent": 96}]}, now=1.0)
    inc.seed_fact("df -i -P /data", {"rows": [{"ipcent": 99}]}, now=1.0)
    inc.seed_fact(
        "find /data -xdev -type d -exec sh -c 'echo \"$(ls -a \"$1\" | wc -l) $1\"' _ {} \\; 2>/dev/null | sort -rn | head -10",
        {"rows": [{"path": "/data/sess", "n": 500000}]}, now=1.0)
    inc.dry_run(now=1.0)
    # 移交卷宗带事实与时间线
    ho = inc.handover(now=1.0)
    assert ho["symptom"] and ho["facts"] and ho["timeline"]
    assert all(e.get("ts") for e in ho["timeline"])   # #39：时间线事件带 ts
    # 报告导出为 markdown：头部五行 + 排查结果 + 时间线（#39 口径）
    md = inc.export_report(now=1.0, conclusion="模型总结。")
    assert md.startswith("# 故障报告：\n")
    assert "## 排查结果" in md and "结论（已排查）" not in md
    assert "- 时间：" in md and "- 目标：local（本机）" in md
    assert "- 症状：" in md and "- 关键参数：mount=/data" in md
    assert "- 结论：模型总结。" in md
    assert "## 时间线" in md and "匹配到 1 个排查方法" in md
    assert "## 处置" not in md and "状态与下一步" not in md


def test_report_timeline_events_rendered_and_ts():
    """#39：timeline 事件流水按事件类型渲染人话，无 ts 的旧条目跳过。"""
    inc = _disk_full_incident({"mount": "/data"})
    import time as _t
    inc.timeline.append({"event": "mixed_sweep", "ts": _t.time(),
                         "executed": 10, "manual_targets": ["web-01"]})
    inc.timeline.append({"event": "legacy_event"})     # 无 ts → 跳过不渲染
    md = inc.export_report(now=_t.time())
    assert "自动取证 10 条（1 个目标转人工）" in md
    assert "legacy_event" not in md


def test_report_target_label_appends_ssh_os(tmp_path, monkeypatch):
    """#39：目标行对远程目标附 ssh·os 括注（查 targets.yaml）；查不到只打名字。"""
    import yaml as _yaml
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    (tmp_path / "targets.yaml").write_text(_yaml.safe_dump({"targets": {
        "高等云肆": {"connector": "ssh", "host": "1.2.3.4", "user": "root",
                     "auth": "agent", "os": "linux"}}}, allow_unicode=True),
        encoding="utf-8")
    inc = I.Incident("磁盘满了", params={"mount": "/var"}, target="高等云肆")
    md = inc.export_report(now=1.0)
    assert "- 目标：高等云肆（ssh · linux）" in md
    inc2 = I.Incident("磁盘满了", params={}, target="不在清单")
    assert "- 目标：不在清单" in inc2.export_report(now=1.0)


def test_next_action_none_when_gated_by_ask():
    """disk-full 处置在 ask 之后 → 干跑不越过 ask，next_action 为空（不自动执行写操作）。"""
    inc = _disk_full_incident({"mount": "/data"})
    inc.seed_fact("df -B1 --output=target,size,used,avail,pcent /data",
                  {"rows": [{"target": "/data", "pcent": 96}]}, now=1.0)
    inc.seed_fact("df -i -P /data", {"rows": [{"ipcent": 99}]}, now=1.0)
    inc.seed_fact(
        "find /data -xdev -type d -exec sh -c 'echo \"$(ls -a \"$1\" | wc -l) $1\"' _ {} \\; 2>/dev/null | sort -rn | head -10",
        {"rows": [{"path": "/data/sess", "n": 500000}]}, now=1.0)
    inc.dry_run(now=1.0)
    p, node = inc.next_action()
    assert p is None                                  # ask 门在前，不越权到 action
