"""Z-4 端到端：两个真实链路（plan→取证→事实→干跑→卷宗），用真实解析器。

这是交互 v2 的"answers 脚本"等价物——不注入 rows，而是喂真实命令输出，
让真实解析器解析、真实事实库存取、真实 exprlang 判读，断言诊断卷宗。
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


def _fake_runner(table):
    """按命令子串返回预置命令输出（真实解析器会解析它）。"""
    def run(cmd, timeout=15):
        for key, out in table.items():
            if key in cmd:
                return out
        return ""
    return run


# ---- 链路一：disk-full 本机协驾全自动取证 → 卷宗已证实 ----
def test_e2e_disk_full_local_auto_sweep():
    inc = I.Incident("磁盘满了但 df 有空间", params={"mount": "/data"}, target="local")
    inc.add_hypotheses([_load("skills/host/disk-full/skill.yaml")])
    # 假 runner 提供各只读命令的真实形态输出（供真实解析器解析）
    runner = _fake_runner({
        "df -B1 --output=target,size,used,avail,pcent /data":
            "Mounted 1B-blocks Used Avail Use%\n/data 100 96 4 96%",
        "df -i -P /data":
            "Filesystem Inodes IUsed IFree IUse% Mounted on\n/data 1000 400 600 40% /data",     # ipcent<95 → 走 deleted-open
        "lsof +L1 /data": "0",                                     # 无残留句柄
        "du -xB1 /data":
            "2147483648\t/data/var/log/app.log\n1073741824\t/data/cache",
    })
    inc.auto_sweep(runner=runner, now=1000.0)      # Z-2 plan → Z-3 execute → 事实入库
    inc.dry_run(now=1000.0)                         # Z-4 干跑
    d = inc.dossier(now=1000.0)
    assert len(d[I.CONFIRMED]) == 1                 # 诊断确立（收敛到处置 ask）
    h = inc.hyps[0]
    assert h.status == I.CONFIRMED and h.pending    # 待人工选择处置文件
    # 证据链带真实解析出的字段
    ev = d[I.CONFIRMED][0]["evidence"]
    assert any(e["field"] == "rows[0].pcent" and e["value"] == 96 for e in ev)
    # 卷宗可渲染、报告可导出
    assert "诊断卷宗" in inc.render_dossier(now=1000.0)
    assert inc.export_report(now=1000.0).startswith("# 故障报告")


# ---- 链路二：bgp 导航档一次粘贴（远端设备，零凭据）→ 干跑 ----
def test_e2e_bgp_navigator_single_paste():
    import sweep as S
    inc = I.Incident("交换机 bgp 邻居掉了", params={"peer_ip": "10.0.0.1"},
                     target="switch-a")
    inc.add_hypotheses([_load("skills/network/bgp-neighbor-down/skill.yaml")])
    plan = inc.plan()
    # 远端目标：无自动执行，全部进粘贴块
    assert plan["auto_count"] == 0 and plan["manual_count"] >= 1
    nonce = "e2enonce"
    block, probes = inc.paste_block(nonce)
    assert nonce in block
    # 人在设备上执行、一次性贴回（每段裹 nonce 边界）
    seg = []
    for p in probes:
        seg.append(S._begin(nonce, p["index"]))
        seg.append("BGP peer 10.0.0.1 state = Idle")     # 设备输出（占位）
        seg.append(S._end(nonce, p["index"]))
    res = inc.ingest("\n".join(seg), nonce, now=5.0)
    assert res["ingested"]                                # 至少一段被回灌
    inc.dry_run(now=5.0)
    # 干跑得到确定结论（证实/排除/证据不足其一），且卷宗可渲染
    assert any(h.status in (I.CONFIRMED, I.REFUTED, I.INSUFFICIENT) for h in inc.hyps)
    assert "诊断卷宗" in inc.render_dossier(now=5.0)
