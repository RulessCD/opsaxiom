"""
Skills Hub 客户端（V-4，docs/08 §3）——registry = git 仓库，本工具是它的客户端。

核心决策：registry 就是一个目录/ git 仓库（内网镜像/气隙 bundle 皆可），
本工具不发明同步协议，只做：建索引、搜索、拉取（三道安全门）、打包推送。

registry 结构：
  index.json                         # 生成物：id/version/maturity/域/名/签名者
  skills/<id>/<version>/skill.yaml   # 原样收录（含 tests/ attestations/）
  keyring/trusted.pub                # 社区信任签名者公钥（每行一个 b64）

三道安全门（pull 入口，docs/08 §3.3）：
  ① 本地重跑 validate（不信远端"已校验"声明）
  ② attestation 验签 + 对照 keyring（不在 keyring → TOFU，徽章降级）
  ③ maturity 徽章原样展示，draft 默认拒收（--allow-draft 放开）
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import yaml            # noqa: E402


def _home():
    return pathlib.Path(os.environ.get("OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom"))


def _hub_cfg():
    d = _home() / "hub"
    d.mkdir(parents=True, exist_ok=True)
    return d / "config.json"


def _load_cfg():
    p = _hub_cfg()
    return json.loads(p.read_text()) if p.exists() else {}


def _skill_summary(s):
    # 摘要取入口 check 的第一条 caution，否则 feedback
    for n in s.get("tree", {}).get("nodes", []):
        if n.get("cautions"):
            return n["cautions"][0][:80]
    return s.get("feedback", {}).get("ask", "")


# ---------- 建 registry（从 skills/ 生成，供演示/发布）----------
def build_registry(skills_dir, out_dir, include_draft=True):
    """include_draft=False：过滤 ⚪draft（对外发布的 registry 用——与 policy.md
    第 2 条、CI 的 draft 拒收一致；含 draft 的 registry 会被自己的 CI 打红）。"""
    skills_dir, out = pathlib.Path(skills_dir), pathlib.Path(out_dir)
    (out / "skills").mkdir(parents=True, exist_ok=True)
    (out / "keyring").mkdir(parents=True, exist_ok=True)
    index = []
    import localskill
    for sp in sorted(skills_dir.rglob("skill.yaml")):
        s = yaml.safe_load(sp.read_text(encoding="utf-8"))
        m = s["metadata"]
        localskill.assert_shareable(m, where=str(sp))    # 个人层混进公共库→当场拒绝
        if not include_draft and m.get("maturity") == "draft":
            continue
        ver = m["version"]
        dst = out / "skills" / m["id"] / ver
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(sp.parent, dst)
        atts = list((sp.parent / "attestations").glob("*.yaml")) if (sp.parent / "attestations").is_dir() else []
        signers = []
        for a in atts:
            sig = (yaml.safe_load(a.read_text(encoding="utf-8")) or {}).get("signature", "")
            if sig.count(":") >= 2:
                signers.append(sig.split(":")[1][:12])
        index.append({
            "id": m["id"], "version": ver, "maturity": m["maturity"],
            "taxonomy": m["taxonomy"], "domain": m["taxonomy"].split("/")[0],
            "name": m["name"], "summary": _skill_summary(s),
            "attestations": len(atts), "signers": sorted(set(signers)),
        })
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(index)


# ---------- 客户端 ----------
def hub_init(location):
    """location 可为本地目录或 git URL。git URL → clone 到缓存；本地目录 → 直接引用。"""
    cache = _home() / "hub" / "registry"
    if location.startswith(("http://", "https://", "git@", "ssh://")):
        if cache.exists():
            shutil.rmtree(cache)
        subprocess.run(["git", "clone", "--depth", "1", location, str(cache)], check=True)
        reg = cache
    else:
        reg = pathlib.Path(location).resolve()
    _hub_cfg().write_text(json.dumps({"registry": str(reg)}, ensure_ascii=False))
    return reg


# 官方社区 registry——未配置时自动接入（零配置：pull/search 开箱即用）。
# 私有环境用 `hub init <内网地址>` 覆盖即可，此默认不会覆盖已有配置。
DEFAULT_REGISTRY = "https://github.com/hf4Academic/opsaxiom-registry.git"


def _registry():
    cfg = _load_cfg()
    reg = cfg.get("registry")
    if reg:
        p = pathlib.Path(reg)
        if (p / "index.json").exists():
            return p
        # 自愈：配置还在但 registry 没了（典型：曾 init 到 /tmp 被清）——重接官方社区
        print(f"（配置的 registry 已失效：{reg}，自动重新接入官方社区）")
    else:
        print(f"（首次使用：自动接入官方社区 {DEFAULT_REGISTRY}，可用 hub init 换）")
    try:
        return hub_init(DEFAULT_REGISTRY)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"接入官方社区失败（网络到 github 不通？）。可稍后重试，"
            f"或 hub init <内网镜像/本地目录>。原因：git clone 退出码 {e.returncode}") from e


def _index():
    idx = _registry() / "index.json"
    if not idx.exists():
        raise RuntimeError(f"registry 无 index.json：{idx}")
    return json.loads(idx.read_text(encoding="utf-8"))


def hub_sync():
    """git registry → git pull；本地目录 → 无操作。
    返回条目数；git pull 真失败（网络断等）抛 RuntimeError——调用方决定降级文案。
    （诚实修复：原先 check=False 静默吞掉 pull 失败仍报"已同步"。）"""
    reg = _registry()
    if (reg / ".git").exists():
        r = subprocess.run(["git", "-C", str(reg), "pull", "--ff-only"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                (r.stderr or r.stdout or "").strip().splitlines()[-1][:120]
                if (r.stderr or r.stdout or "").strip() else f"git pull rc={r.returncode}")
        low = ((r.stdout or "") + (r.stderr or "")).lower()
        if "could not resolve host" in low or "connection timed out" in low \
                or "unable to access" in low:
            raise RuntimeError("network unreachable")
    return len(_index())


def hub_search(kw):
    kw = (kw or "").lower()
    return [e for e in _index()
            if kw in e["id"].lower() or kw in e["name"].lower()
            or kw in e["taxonomy"].lower() or kw in (e.get("summary") or "").lower()]


def _verify_attestations(skill_dir):
    """返回 (valid, trusted)：签名有效数 / keyring 可信数。"""
    import importlib.util
    from importlib.machinery import SourceFileLoader
    attest = SourceFileLoader("attest_v", str(HERE / "bin" / "opsaxiom-attest")).load_module()
    adir = skill_dir / "attestations"
    valid = trusted = 0
    if adir.is_dir():
        for a in adir.glob("*.yaml"):
            att = yaml.safe_load(a.read_text(encoding="utf-8"))
            ok, note = attest.verify_att(att)
            if ok:
                valid += 1
                if "可信" in note:
                    trusted += 1
    return valid, trusted


def hub_pull(skill_id, allow_draft=False):
    """三道安全门后拉到 skills-community/。返回 (dst_path, report)。"""
    import json as _j
    import validate
    from jsonschema import Draft202012Validator
    entry = next((e for e in _index() if e["id"] == skill_id), None)
    if not entry:
        raise RuntimeError(f"registry 中无此 Skill：{skill_id}")
    src = _registry() / "skills" / skill_id / entry["version"]
    if not (src / "skill.yaml").exists():
        raise RuntimeError(f"registry 内容缺失：{src}")

    report = {"id": skill_id, "maturity": entry["maturity"]}
    # 门③ maturity
    if entry["maturity"] == "draft" and not allow_draft:
        raise PermissionError(f"{skill_id} 是 draft，默认拒收（--allow-draft 放开）")
    # 门① 本地重跑 validate
    sv = Draft202012Validator(_j.loads(validate.SCHEMA_PATH.read_text(encoding="utf-8")))
    rep = validate.validate_file(src / "skill.yaml", sv)
    report["validate_errors"] = len(rep.errors)
    if rep.errors:
        raise PermissionError(f"{skill_id} 本地校验未通过（{len(rep.errors)} ERROR），拒收")
    # 门② 验签
    valid, trusted = _verify_attestations(src)
    report["att_valid"], report["att_trusted"] = valid, trusted

    slug = skill_id.replace(".", "-")
    dst = ROOT / "skills-community" / slug
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    # 打 origin 标记
    sf = dst / "skill.yaml"
    s = yaml.safe_load(sf.read_text(encoding="utf-8"))
    s["metadata"].setdefault("provenance", {})["origin"] = str(_registry())
    sf.write_text(yaml.safe_dump(s, allow_unicode=True, sort_keys=False), encoding="utf-8")
    report["dst"] = str(dst)
    return dst, report


def _keyring_dir():
    d = _home() / "keys" / "trusted"
    d.mkdir(parents=True, exist_ok=True)
    return d


def keyring_list():
    """返回 [(name, pubkey)]。"""
    out = []
    for f in sorted(_keyring_dir().glob("*.pub")):
        out.append((f.stem, f.read_text(encoding="utf-8").strip()))
    return out


def keyring_add(pub_b64, name):
    """把某人的公钥加入本地信任 keyring（签核 = 决定信任谁的签名）。"""
    import base64
    try:
        raw = base64.b64decode(pub_b64)
        if len(raw) != 32:               # Ed25519 公钥恒 32 字节
            raise ValueError("长度非 32 字节")
    except Exception as e:
        raise ValueError(f"公钥格式非法（应为 base64 的 Ed25519 公钥）：{e}")
    (_keyring_dir() / f"{name}.pub").write_text(pub_b64.strip(), encoding="utf-8")
    return _keyring_dir() / f"{name}.pub"


def keyring_remove(name):
    p = _keyring_dir() / f"{name}.pub"
    if p.exists():
        p.unlink()
        return True
    return False


def keyring_export():
    """汇出全部信任公钥（每行一个 b64），供 registry 侧 trusted.pub 合入。"""
    return "\n".join(pub for _, pub in keyring_list())


def hub_push(skill_id, out_dir=None):
    """打包 skill+tests+attestations 为 bundle（气隙摆渡）；返回 tar 路径。"""
    import localskill
    localskill.assert_shareable({"id": skill_id}, where="hub push")  # local. 前缀直接拒
    sp = next((p for p in (ROOT / "skills").rglob("skill.yaml")
               if yaml.safe_load(p.read_text(encoding="utf-8"))["metadata"]["id"] == skill_id), None)
    if not sp:
        raise RuntimeError(f"本地无此 Skill：{skill_id}")
    localskill.assert_shareable(yaml.safe_load(sp.read_text(encoding="utf-8"))["metadata"],
                                where="hub push")           # visibility:local 也拒
    out = pathlib.Path(out_dir or (_home() / "hub" / "outbox"))
    out.mkdir(parents=True, exist_ok=True)
    tar = out / f"{skill_id}.tar.gz"
    import tarfile
    with tarfile.open(tar, "w:gz") as t:
        t.add(sp.parent, arcname=skill_id)
    return tar
