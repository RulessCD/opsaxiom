#!/usr/bin/env python3
"""
maturity 流水线（P-4）—— 唯一被授权写 metadata.maturity 的工具（docs/03：maturity 由流水线写入）。

promote <skill.yaml>            依据校验 + 仿真证据晋级到 sim_verified
field   <skill.yaml>            ≥3 份独立且验签有效的 attestation → field_verified（X-4）
demote  <skill.yaml> <reason>   降级回 draft 并记录原因

晋级 sim_verified 的门槛（docs/05 §1 + S8）：
  1. 校验器零 ERROR；
  2. 该 Skill 至少一个可执行 sim 场景通过（sim/scenarios/*.yaml 中 skill 指向它）；
  3. 若该 Skill 的 tests 有 rollback_assert，则须有场景实际通过回滚往返；
  4. tests 非空且至少一个 rollback_assert（S8）。

写法：只文本替换 `maturity:` 一行（保留注释/格式），证据写入 <skill_dir>/.maturity/。
"""
import argparse
import datetime
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "sim"))
import yaml            # noqa: E402
import validate as V   # noqa: E402
import run_sim         # noqa: E402

SCEN_DIR = ROOT / "sim" / "scenarios"


def _scenarios_for(skill_path):
    skill_rel = str(skill_path.resolve().relative_to(ROOT))
    out = []
    for sc in sorted(SCEN_DIR.glob("*.yaml")):
        data = yaml.safe_load(sc.read_text())
        if data.get("skill") == skill_rel:
            out.append(sc)
    return out


def _maturity_line_replace(path, new):
    raw = path.read_text(encoding="utf-8")
    new_raw, n = re.subn(r"(?m)^(\s*maturity:\s*)\S+", rf"\g<1>{new}", raw, count=1)
    if n != 1:
        raise RuntimeError("未找到唯一的 maturity 行")
    path.write_text(new_raw, encoding="utf-8")


def _evidence_dir(skill_path):
    d = skill_path.parent / ".maturity"
    d.mkdir(exist_ok=True)
    return d


def promote(skill_path):
    skill_path = pathlib.Path(skill_path).resolve()
    skill = yaml.safe_load(skill_path.read_text())
    name = skill["metadata"]["id"]
    meta = skill["metadata"]

    # 1. 校验零 ERROR
    rep = V.validate_file(skill_path, V._default_validator())
    if rep.errors:
        print(f"✘ 校验未过，拒绝晋级：{[e[2] for e in rep.errors]}")
        return 1

    # 4. S8（含 action 才要求 rollback_assert；纯诊断路径覆盖即可，见 R-6）
    tests = skill.get("tests", [])
    nodes = skill.get("tree", {}).get("nodes", [])
    has_action = any(n.get("type") == "action" for n in nodes)
    if not tests:
        print("✘ S8 未满足：tests 为空")
        return 1
    if has_action and not any(t.get("rollback_assert") for t in tests):
        print("✘ S8 未满足：含 action 的 Skill 须有 rollback_assert 测试")
        return 1

    # 2/3. 仿真证据
    scens = _scenarios_for(skill_path)
    if not scens:
        print("✘ 无可执行 sim 场景（sim/scenarios/ 下没有指向该 Skill 的场景）")
        return 1
    results, rollback_seen, evidence = [], False, "context_walk"
    for sc in scens:
        import yaml as _yaml
        is_real = _yaml.safe_load(sc.read_text()).get("mode") == "real"
        r = run_sim.run(skill_path, sc)
        if is_real:
            ok = r.get("completed", False)
            results.append({"scenario": sc.name, "mode": "real", "completed": ok,
                            "terminal": r["path"][-1] if r.get("path") else None})
            if ok:
                evidence = "real_roundtrip"
        else:
            ok = r["path_ok"] and (r["rollback_ok"] is not False)
            results.append({"scenario": sc.name, "mode": "context_walk",
                            "path_ok": r["path_ok"], "rollback_ok": r["rollback_ok"]})
            if r["rollback_ok"] is True:
                rollback_seen = True
        if not ok:
            print(f"✘ 场景 {sc.name} 未通过：{r.get('path')}")
            return 1
    if has_action and any(t.get("rollback_assert") for t in tests) and not rollback_seen:
        print("✘ 该 Skill 声明了 rollback_assert，但没有场景实际验证回滚往返")
        return 1

    # 通过 → 晋级
    prev = skill["metadata"]["maturity"]
    _maturity_line_replace(skill_path, "sim_verified")
    ev = {
        "skill": name, "action": "promote", "from": prev, "to": "sim_verified",
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "validator": "0 ERROR", "evidence": evidence, "scenarios": results,
    }
    (_evidence_dir(skill_path) / "promote.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=2))
    print(f"✔ {name}: {prev} → sim_verified（证据={evidence}，{len(scens)} 场景，回滚往返={'有' if rollback_seen else '无'}）")

    # fork 自动去 local. 前缀 + 发布版本
    if meta.get("id", "").startswith("local."):
        _de_localize(skill_path, skill, meta)
    return 0


def _independent_valid_attestations(skill_dir):
    """返回独立且验签有效的 attestation 数（docs/05 §3：不同 attestor 且 env 分桶不同）。

    贪心选一个最大子集：attestor 互不相同 **且** env_fingerprint 分桶互不相同。
    """
    from importlib.machinery import SourceFileLoader
    attest = SourceFileLoader("attest_p", str(HERE / "bin" / "opsaxiom-attest")).load_module()
    adir = skill_dir / "attestations"
    if not adir.is_dir():
        return 0, []
    seen_attestor, seen_env, kept = set(), set(), []
    for af in sorted(adir.glob("*.yaml")):
        att = yaml.safe_load(af.read_text(encoding="utf-8")) or {}
        ok, _ = attest.verify_att(att)
        if not ok:
            continue
        who = att.get("attestor", "")
        env = att.get("env_fingerprint", {})
        os_ = env.get("os", {})
        envkey = (os_.get("family"), os_.get("version_bucket"), env.get("scale_bucket"))
        if who in seen_attestor or envkey in seen_env:
            continue                     # 不独立（同人或同环境分桶）
        seen_attestor.add(who); seen_env.add(envkey); kept.append(af.name)
    return len(kept), kept


def promote_field(skill_path):
    """sim_verified → field_verified：≥3 份独立且验签有效的 attestation（docs/05）。"""
    skill_path = pathlib.Path(skill_path).resolve()
    skill = yaml.safe_load(skill_path.read_text())
    name, cur = skill["metadata"]["id"], skill["metadata"]["maturity"]
    if cur != "sim_verified":
        print(f"✘ 需先到 sim_verified（当前 {cur}）")
        return 1
    rep = V.validate_file(skill_path, V._default_validator())
    if rep.errors:
        print(f"✘ 校验未过：{[e[2] for e in rep.errors]}")
        return 1
    n, kept = _independent_valid_attestations(skill_path.parent)
    if n < 3:
        print(f"✘ 独立有效 attestation 不足 3 份（当前 {n}）——field_verified 需 ≥3 份"
              f"（不同 attestor 且不同 env 分桶，验签有效）")
        return 1
    _maturity_line_replace(skill_path, "field_verified")
    ev = {"skill": name, "action": "promote_field", "from": "sim_verified",
          "to": "field_verified", "at": datetime.datetime.now().isoformat(timespec="seconds"),
          "independent_attestations": n, "sources": kept}
    (_evidence_dir(skill_path) / "field.json").write_text(json.dumps(ev, ensure_ascii=False, indent=2))
    print(f"✔ {name}: sim_verified → field_verified（{n} 份独立有效 attestation）")
    return 0


def demote(skill_path, reason):
    skill_path = pathlib.Path(skill_path).resolve()
    skill = yaml.safe_load(skill_path.read_text())
    prev = skill["metadata"]["maturity"]
    _maturity_line_replace(skill_path, "draft")
    ev = {
        "skill": skill["metadata"]["id"], "action": "demote", "from": prev, "to": "draft",
        "at": datetime.datetime.now().isoformat(timespec="seconds"), "reason": reason,
    }
    (_evidence_dir(skill_path) / "demote.json").write_text(json.dumps(ev, ensure_ascii=False, indent=2))
    print(f"✔ {skill['metadata']['id']}: {prev} → draft（原因：{reason}）")
    return 0


def _de_localize(skill_path, skill, meta):
    """fork 晋级后自动去 local. 前缀 → 放进 skills-drafts/，可直接 hub push。"""
    base_id = meta["derived_from"].split("@")[0] if meta.get("derived_from") else \
              meta["id"].replace("local.", "", 1)
    # 提取 attestor（从 authors 或 derived_from）
    att = "anonymous"
    authors = meta.get("authors", [])
    if authors:
        for a in authors:
            if str(a) != "opsagent-core" and str(a) != "captured":
                att = str(a); break
    # 拼新 ID
    vid = 1
    draft_dir = ROOT / "skills-drafts" / (base_id + "-" + att + "-" + str(vid))
    while draft_dir.exists():
        vid += 1
        draft_dir = ROOT / "skills-drafts" / (base_id + "-" + att + "-" + str(vid))
    new_id = base_id + "-" + att + "-" + str(vid)

    new_meta = dict(meta)
    new_meta["id"] = new_id
    new_meta.pop("visibility", None)
    new_meta["maturity"] = "sim_verified"
    new_skill = dict(skill)
    new_skill["metadata"] = new_meta

    draft_dir.mkdir(parents=True)
    (draft_dir / "skill.yaml").write_text(
        yaml.safe_dump(new_skill, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"✔ 已生成可发布版本：{draft_dir / 'skill.yaml'}")
    print(f"  id={new_id}  （hub push 即可提 PR 至社区）")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("promote"); sp.add_argument("skill")
    fp = sub.add_parser("field"); fp.add_argument("skill")   # X-4
    sd = sub.add_parser("demote"); sd.add_argument("skill"); sd.add_argument("reason")
    args = ap.parse_args()
    if args.cmd == "promote":
        return promote(args.skill)
    if args.cmd == "field":
        return promote_field(args.skill)
    return demote(args.skill, args.reason)


if __name__ == "__main__":
    sys.exit(main())
