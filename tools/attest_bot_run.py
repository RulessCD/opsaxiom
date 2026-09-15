"""
attest bot 批次驱动器（GitHub Actions 内运行）——被 registry-side/attest-bot.workflow.yml
第 Process 步调用。顺序遍历带 attestation 标签的开放 issue，逐条过 attest_batch
五道门，过验落树 + 重建 index，逐条 issue 回执。

环境变量（workflow 注入）：
  GH_TOKEN            bot 内置 token
  GITHUB_REPOSITORY   owner/repo（registry 自己）
  BATCH_* 输出        汇总给后续 commit/PR 步（写 $GITHUB_ENV）
"""
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yaml  # noqa: E402

FENCE_RE = re.compile(r"```ya?ml\s*\n(.*?)```", re.S)
LABEL_IN = "attestation"
LABEL_OK = "attest:accepted"
LABEL_BAD = "attest:rejected"


def gh(*args, **kw):
    env = dict(os.environ)
    env.setdefault("GH_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
    return subprocess.run(["gh", *args], capture_output=True, text=True,
                          env=env, check=True, **kw)


def registry_root():
    """workflow checkout 布局：仓库根 = cwd（registry 树）；opsaxiom 在 opsaxiom-src/。"""
    cwd = pathlib.Path.cwd()
    if (cwd / "opsaxiom-src" / "tools" / "attest_batch.py").exists():
        sys.path.insert(0, str(cwd / "opsaxiom-src" / "tools"))
        return cwd
    # 本地直跑兜底（开发调试）：仓库根上一级找 tools
    sys.path.insert(0, str(HERE))
    return HERE.parent


def parse_yaml_blocks(body):
    """issue body 里所有 ```yaml 块 → dict 列表。"""
    out = []
    for m in FENCE_RE.finditer(body or ""):
        try:
            d = yaml.safe_load(m.group(1))
            if isinstance(d, dict) and "signature" in d:
                out.append(d)
        except Exception:
            continue
    return out


def dest_dir_for(skill_id, skill_version, reg):
    """按 skill.yaml 的 metadata.id 找树内目录（版本字段仅参考，目录以树为准）。"""
    for p in (reg / "skills").rglob("skill.yaml"):
        try:
            if yaml.safe_load(p.read_text(encoding="utf-8")).get("metadata", {}).get("id") == skill_id:
                d = p.parent / "attestations"
                d.mkdir(parents=True, exist_ok=True)
                return d, True
        except Exception:
            continue
    return None, False


def index_rebuild(reg):
    """落树后原位重建 index.json（reindex_registry 单源——只读不动树）。"""
    import hubtool
    return hubtool.reindex_registry(reg)


def main():
    reg = registry_root()
    import attest_batch as AB

    repo = os.environ["GITHUB_REPOSITORY"]
    issues = json.loads(gh("issue", "list", "--state", "open",
                           "--label", LABEL_IN, "--json", "number,title,body,author",
                           "-L", "200").stdout)
    accepted = rejected = skipped = 0
    changed = False

    for it in issues:
        num, author, body = it["number"], it["author"]["login"], it.get("body") or ""
        atts = parse_yaml_blocks(body)
        if not atts:
            gh("issue", "comment", str(num), "-b",
               "attest-bot：未在 issue 内解析到签名 YAML 块（须为 ```yaml 包裹的完整凭据），关闭。")
            gh("issue", "edit", str(num), "--add-label", LABEL_BAD)
            gh("issue", "close", str(num))
            rejected += 1
            continue

        lines = []
        any_reject = False
        for att in atts:
            sid = att.get("skill", "?")
            sver = att.get("skill_version", "?")
            d, exists = dest_dir_for(sid, sver, reg)
            st, info = AB.check_one(att, issue_author=author, dest_dir=d)
            if st == "accept":
                (d / info).write_text(
                    yaml.safe_dump(att, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
                lines.append(f"✔ 已入库 `{sid}` → `skills/…/{info}`")
                accepted += 1
                changed = True
            elif st == "skip":
                lines.append(f"⏭ 已存在（幂等跳过）：`{info}`")
                skipped += 1
            else:
                lines.append(f"✘ 拒收：{info}")
                any_reject = True
                rejected += 1

        gh("issue", "comment", str(num), "-b", "\n".join(lines) or "（无条目）")
        gh("issue", "edit", str(num),
           "--add-label", LABEL_BAD if any_reject else LABEL_OK)
        gh("issue", "close", str(num))

    if changed:
        n = index_rebuild(reg)
        print(f"index.json 重建：{n} entries")

    envf = os.environ.get("GITHUB_ENV")
    if envf:
        with open(envf, "a") as f:
            f.write(f"BATCH_HAS_CHANGES={'1' if changed else '0'}\n"
                    f"BATCH_ACCEPTED={accepted}\nBATCH_REJECTED={rejected}\n"
                    f"BATCH_SKIPPED={skipped}\n"
                    f"BATCH_MODE={os.environ.get('MODE', 'direct')}\n")
    print(f"accepted={accepted} rejected={rejected} skipped={skipped}")


if __name__ == "__main__":
    main()
