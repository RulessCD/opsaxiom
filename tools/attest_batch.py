"""
attestation 批量校验器（bot 与 hub import 共用的唯一判定咽喉）。

输入一批"待入库凭据"（来自 issue body 解析或 export bundle），逐条过五道门：
  ① schema 校验（schema/attestation.schema.json，与 validate.py 同一份）
  ② verify_att 验签（签名完整性：内容未篡改、签名者持私钥）
  ③ 幂等去重（文件名 = 日期-内容哈希，目标已存在 → skip 不重不拒）
  ④ skill 存在性（给树里不存在的 Skill 签名的凭据 → 拒）
  ⑤ 身份交叉（attestor 必须 == issue 作者——attestor 是自报字段，
     唯有与 GitHub 发件账号一致才可信；不一致恒拒，无人工审核）
全过 → 返回落树相对路径；任一不过 → 返回拒因。判无副作用：写树由调用方做。
"""
import pathlib
import re

import yaml

FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([0-9a-f]{8})\.yaml$")


def check_one(att, *, issue_author, dest_dir, issue_date=None):
    """校验一条凭据。返回 (status, path_or_reason)：
    status ∈ {"accept", "skip", "reject"}；accept/skip 时带目标文件名。
    issue_date：issue 侧日期（YYYY-MM-DD）——凭据无日期字段（日期只编在
    文件名里），bot 从 issue 带入让落树文件名可读；缺省 0000-00-00。"""
    import validate
    av = validate._att_validator()
    if av is not None and av is not False:
        errs = list(av.iter_errors(att))
        if errs:
            loc = "/".join(str(p) for p in errs[0].path) or "<root>"
            return "reject", f"schema 校验未过（{loc}）：{errs[0].message}"

    from importlib.machinery import SourceFileLoader
    HERE = pathlib.Path(__file__).resolve().parent
    attest = SourceFileLoader(
        "attest_batch_v", str(HERE / "bin" / "opsaxiom-attest")).load_module()
    ok, note = attest.verify_att(att)
    if not ok:
        return "reject", f"验签未过：{note}"

    # 文件名按与 opsaxiom-attest 同一规则重建（日期 + 内容哈希前 8 位）
    import hashlib
    date = _date_of(att, issue_date)
    h = hashlib.sha256(yaml.dump(att, sort_keys=True).encode()).hexdigest()[:8]
    fname = f"{date}-{h}.yaml" if date else f"0000-00-00-{h}.yaml"
    if not FILE_RE.match(fname):
        return "reject", f"文件名构造异常：{fname}"

    if (att.get("attestor") or "").strip() == "anonymous":
        return "reject", "attestor=anonymous：无身份凭据不入树（发件端约定）"
    if not cross_check_author(att, issue_author):
        return "reject", f"身份交叉未过：attestor={att.get('attestor')!r} ≠ issue 作者 {issue_author!r}"

    if dest_dir is not None:
        d = pathlib.Path(dest_dir)
        # ④ 存在性：给树里不存在的 Skill 签名的凭据 → 拒
        if not (d.parent / "skill.yaml").exists():
            return "reject", f"skill 不存在：{d.parent.name}"
    if dest_dir is not None and (pathlib.Path(dest_dir) / fname).exists():
        return "skip", fname                    # 幂等：重复导入无害
    return "accept", fname


def _date_of(att, issue_date=None):
    """凭据无日期字段（日期只编在文件名里）——bot 场景从 issue 侧带入；
    bundle 场景由导出侧在包装层记录。缺省 0000-00-00 仍可入库（哈希去重不受影响）。"""
    return att.get("_export_date") or issue_date or "0000-00-00"


def cross_check_author(att, issue_author):
    """第五道门独立出来：attestor 与 issue 作者一致性。callable 形式供
    bot 直接调用（bundle/内网直推场景无 issue 作者概念，调用方决定是否启用）。"""
    who = (att.get("attestor") or "").strip()
    return who != "" and who == (issue_author or "").strip()
