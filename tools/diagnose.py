"""
症状 → Skill 匹配（T-2，docs/04 §4 最小实现）。
分层：L1 关键词分类(域加权) + 域内按"入口症状/名称"词重合打分。不引入 embedding，先关键词。
入口症状取自 docs/04 §5（`taxonomy 路径 — "口语症状"`）。
"""
import json
import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

_HOME = pathlib.Path(os.environ.get("OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom"))

# registry 缓存中 skills 目录（含版本子目录：skills/<id>/<version>/skill.yaml）
_SKILLS_CACHE = _HOME / "hub" / "registry" / "skills"
# 个人 fork 目录（skills-local/<local.id>/skill.yaml）
_SKILLS_LOCAL = _HOME / "skills-local"

# L1 域关键词（命中则给该域 skill 加权）
_L1_KEYWORDS = {
    "host": ["主机", "cpu", "内存", "memory", "磁盘", "disk", "进程", "process", "swap",
             "文件系统", "负载", "load", "僵尸", "fd", "句柄", "时钟", "证书", "oom", "系统"],
    "network": ["网络", "交换机", "路由", "bgp", "ospf", "vlan", "丢包", "光模块", "acl",
                "mtu", "接口", "trunk", "stp", "环路", "流量", "带宽"],
    "k8s": ["k8s", "kubernetes", "pod", "容器", "deployment", "svc", "service", "ingress",
            "crashloop", "调度", "pvc", "rollout", "发布", "hpa", "namespace", "节点"],
    "middleware": ["mysql", "redis", "kafka", "数据库", "主从", "复制", "慢查询", "死锁", "锁",
                   "大key", "热key", "淘汰", "积压", "消费", "副本", "连接数"],
    "aicomp": ["gpu", "显卡", "xid", "ecc", "nvlink", "ib", "infiniband", "roce", "nccl",
               "训练", "掉卡", "显存", "算力", "hang", "慢节点"],
}


def _load_symptoms():
    """解析 docs/04 §5：taxonomy 路径 → 症状文本。"""
    doc = (ROOT / "docs" / "04-taxonomy.md").read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"`([a-z0-9][a-z0-9/\-]+)`[^\n—]*—\s*[\*\"“]*([^\n]+)", doc):
        path = m.group(1)
        sym = m.group(2).strip().strip('"“”*')
        out[path] = sym
    return out


def _terms(text):
    """把症状/名称切成关键词（按 /／、，空格 等分隔），英文转小写。"""
    parts = re.split(r"[/／、，,；;（）()\s]+", text.lower())
    return [p for p in parts if p and len(p) >= 2]


def load_index():
    import yaml
    sym = _load_symptoms()
    idx = []

    def _scan(dir_path):
        for p in sorted(dir_path.rglob("skill.yaml")):
            try:
                s = yaml.safe_load(p.read_text(encoding="utf-8"))
                meta = s["metadata"]
                tax = meta.get("taxonomy", "")
                l1 = tax.split("/")[0] if tax else "unknown"
                terms = set(_terms(meta.get("name", "")))
                if tax in sym:
                    terms |= set(_terms(sym[tax]))
                terms |= set(_terms(tax.split("/")[-1]) if tax else set())
                blob = (meta.get("name", "") + " " + sym.get(tax, "") + " " + tax)
                idx.append({"id": meta["id"], "name": meta["name"],
                            "taxonomy": tax, "l1": l1,
                            "maturity": meta.get("maturity", "draft"),
                            "terms": terms,
                            "symptom": sym.get(tax, ""),
                            "bigrams": _bigrams(blob),
                            "source": "local"})
            except Exception:
                pass

    if _SKILLS_CACHE.is_dir():
        _scan(_SKILLS_CACHE)
    if _SKILLS_LOCAL.is_dir():
        _scan(_SKILLS_LOCAL)

    return idx


def _bigrams(text):
    """中文用字符 2-gram（对'主从延迟很大'vs'主从延迟大'远比子串鲁棒）。"""
    t = re.sub(r"\s+", "", text.lower())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def score(query, skill):
    q = query.lower()
    s = 0.0
    for t in skill["terms"]:
        if t in q or (len(t) >= 3 and q in t):
            s += 1 + min(len(t), 6) * 0.2
    # 字符 bigram 重合（中文主力信号）
    qb = _bigrams(query)
    s += len(qb & skill.get("bigrams", set())) * 0.6
    # L1 域加权
    for kw in _L1_KEYWORDS.get(skill["l1"], []):
        if kw in q:
            s += 1.5
            break
    return s


# 噪声地板：单个字符 2-gram 偶然重合得分 0.6，属噪声不应浮出为假设。
# 真实匹配（哪怕弱相关）都 ≥1.2（两个 bigram 或任一词元命中），故 1.0 干净切分。
NOISE_FLOOR = 1.0


def match(query, idx=None, top=5):
    idx = idx or load_index()
    scored = [(score(query, sk), sk) for sk in idx]
    scored = [(sc, sk) for sc, sk in scored if sc >= NOISE_FLOOR]
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return scored[:top]
