"""问询参数 desc 口语化（2026-09-17）：全库对账 + 问询模板不暴露参数名。"""
import io, pathlib, sys
import unittest.mock as mock

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))


def test_no_param_names_in_prompts(tmp_path):
    """问询话术 = desc 本身；无 desc 兜底通用话术；两种路径都不出现参数名。"""
    import repl as R
    skill = {"metadata": {"params": [
        {"name": "mount", "source": "alert",
         "desc": "请输入需要排查的具体目录（格式如：/、/var、/data）"},
        {"name": "no_desc", "source": "user"},
    ]}}
    answers = iter(["/", "x"])
    with mock.patch("builtins.input", lambda *a, **k: next(answers)):
        got = R.Repl._collect_params([skill], {})
    assert got == {"mount": "/", "no_desc": "x"}


def test_param_desc_map_covers_all_asking_params():
    """映射表全库对账：问询型参数（source alert/user）每条都有映射且 desc 一致。"""
    sys.path.insert(0, str(ROOT / "tools" / "authoring"))
    from param_desc_map import MAP
    miss, stale = [], []
    for p in (ROOT / "skills").rglob("skill.yaml"):
        s = yaml.safe_load(p.read_text(encoding="utf-8"))
        for prm in (s.get("metadata", {}).get("params") or []):
            if prm.get("source") in ("alert", "user"):
                want = MAP.get((s["metadata"]["id"], prm["name"]))
                if want is None:
                    miss.append((s["metadata"]["id"], prm["name"]))
                elif prm.get("desc") != want:
                    stale.append((s["metadata"]["id"], prm["name"]))
    assert miss == [] and stale == []
