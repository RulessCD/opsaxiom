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
    assert "诊断卷宗" in txt and "已证实" in txt


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
    # 报告导出为 markdown，含结论段
    md = inc.export_report(now=1.0)
    assert md.startswith("# 故障报告") and "已证实" in md


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
