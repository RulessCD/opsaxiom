"""
gen_sudoers.py —— sudoers 只读白名单生成器（I-4 / docs/12 §5.6，"技能库即权限清单"）。

扫描 registry 缓存（唯一权威 Skill 源，见需求0723 #7）里全部 skill.yaml 的
linux 命令，提取每条命令里的可执行体（含管道/复合命令段首），输出两大产物：

  1) 命令清单（裸二进制名列表）——enroll 上门时在目标机用 `command -v` 解析成
     绝对路径后写入 /etc/sudoers.d/opsaxiom-ro（sudoers 要求绝对路径，本机
     无法预知远端发行版的路径，因此路径解析发生在远端）
  2) （调试用）打印清单/条目数

抽取边界：
  - 只看 linux 键的 run（tree.nodes[].run、preflight.watch[].run、rollback.run/snapshot）
  - `sudo X` 穿透取 X；shell 内建/控制词（for/echo/grep 之外的纯控制流、重定向）不算
  - grep/awk/sed/head/tail 等只读文本工具通常出现于管道中段——白名单同时收录，
    以便远端非交互 shell 下这些命令以绝对路径声明的形式获得通过
"""
import argparse
import pathlib
import re

import yaml

# 段切分：复合命令连接符（管道 + 逻辑符 + 分号 + 子 shell 开括号）
_SEG_RE = re.compile(r"&&|\|\||;|\|")
# shell 控制词与重定向段不算白名单内容
_CTRL = {"for", "if", "then", "else", "fi", "do", "done", "while", "case", "esac",
         "in", "echo", "true", "false", "exit", "return", "export", "set", "cd",
         "local", "shift", "xargs"}
# 常见只读文本工具（管道中段也收集，保证非交互 shell 可用）
_TEXT_TOOLS = {"grep", "awk", "sed", "head", "tail", "sort", "wc", "cut", "tr",
               "uniq", "cat", "tee", "column", "numfmt", "sha256sum", "find"}
# 排除名单：客户端 CLI（能执行写 SQL/写命令/服务管理），这类进白名单会让
# 只读账号获得远超"取证"的能力——它们的只读使用场景走各 connector（mysql 键）
# 与专用账号，不走 sudoers。
_DENY_BINS = {"mysql", "mysqldump", "psql", "mongosh", "mongo", "redis-cli",
              "rabbitmqctl", "nginx", "sshd", "curl", "fail2ban-client",
              "kubectl", "kubectl.x", "auditctl"}


def extract_entries(cmd):
    """一条命令字符串 → set[(bin, arg_prefix)]。
    段首 token；`sudo`/`-n`/`-u` 穿透；控制词与 flag 不算。
    arg_prefix = 二进制后的第一个非 flag token（如 systemctl 的子命令）；
    其余二进制参数为 `*`（只读命令参数不可枚举）。二进制名形态校验。"""
    out = set()
    for seg in _SEG_RE.split(cmd):
        toks = seg.strip().split()
        # 穿过段首的数字重定向（2>/dev/null 形态粘在段首）
        while toks and re.match(r"^\d*>(/|\S)", toks[0]):
            toks = toks[1:]
        if not toks:
            continue
        i = 0
        while i < len(toks) and toks[i] in ("sudo", "-n", "-u"):
            i += 1
        if i >= len(toks):
            continue
        name = toks[i]
        name = name.rsplit("/", 1)[-1]     # 相对路径取基名
        if not name or name in _CTRL or name.startswith("-") or name in _DENY_BINS:
            continue
        if not re.match(r"^[A-Za-z0-9_.@+\-]+$", name):
            continue
        # 首个非 flag token 作为参数前缀（systemctl is-active → "is-active"）；
        # 二进制直跟 flag（如 df -h / ps aux）→ 无前缀（全参放行）
        j = i + 1
        prefix = ""
        while j < len(toks):
            t = toks[j]
            if t.startswith("-") or re.match(r"^\d*>", t) or t in _CTRL:
                j += 1
                continue
            if re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", t):
                arg_prefix = t
                # 模板占位符/引号内容不算（{{...}} 会被渲染，不算固定参数）
                if "{{" not in seg:
                    out.add((name, arg_prefix))
                break
            break
        out.add((name, None))              # 无参形态也登记一份（args 任意）
    return out


def skill_entries(skill):
    """一个 skill（dict）→ set[(bin, arg_prefix|None)]。只收"探针/判定"类命令——
    action（写变更）节点结构性排除：白名单的读者是远端只读账号，放行
    umount/fsck 等写命令等于白名单形同虚设（fs-readonly 的 umount 教训）。
    rollback/verify/watch 挂在 action 下，排除 action 即一并排除。"""
    bins = set()
    nodes = (skill.get("tree", {}) or {}).get("nodes", []) or []
    for node in nodes:
        if node.get("type") == "action":
            continue
        for block in run_blocks(node):
            cmd = block.get("linux")
            if isinstance(cmd, str) and cmd.strip():
                bins |= extract_entries(cmd)
    return bins


def run_blocks(node):
    r = node.get("run")
    if isinstance(r, dict):
        yield r
    for w in (node.get("preflight", {}) or {}).get("watch", []) or []:
        wr = w.get("run")
        if isinstance(wr, dict):
            yield wr


def scan_skills_dir(skills_dir):
    """目录扫描 → {(bin, prefix|None): set(skill_id)}（审计可溯：谁用到这条）。"""
    usage = {}
    for p in sorted(pathlib.Path(skills_dir).rglob("skill.yaml")):
        try:
            s = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = (s.get("metadata", {}) or {}).get("id", p.parent.name)
        for entry in skill_entries(s):
            usage.setdefault(entry, set()).add(sid)
    return usage


def default_skills_root():
    """registry 缓存为唯一权威源（需求0723 #7：官方 skills 以 registry 为准）。"""
    import os
    home = pathlib.Path(os.environ.get("OPSAXIOM_HOME",
                                       pathlib.Path.home() / ".opsaxiom"))
    reg = home / "hub" / "registry" / "skills"
    return reg if reg.is_dir() else None


def _group_by_bin(entries):
    """{(bin, prefix|None)} → {bin: set(prefix|None)}。"""
    by_bin = {}
    for name, prefix in entries:
        by_bin.setdefault(name, set()).add(prefix)
    return by_bin


def _entry_specs(entries):
    """{(bin, prefix|None)} → 排序后的 sudoers 片段列表。
    同 bin 若存在 None（无固定子命令 → 参数任意），裸 bin 覆盖一切前缀条目；
    否则每个前缀一条 `bin prefix *`。"""
    specs = []
    by_bin = _group_by_bin(entries)
    for name in sorted(by_bin):
        prefixes = by_bin[name]
        if None in prefixes:
            specs.append(name)
        else:
            specs.extend(f"{name} {p} *" for p in sorted(prefixes))
    return specs


def render_sudoers_file(entries, user="opsaxiom-ro", header_note=""):
    """命令集 → /etc/sudoers.d/opsaxiom-ro 完整文本（裸名版；绝对路径由 enroll
    在目标机 `command -v` 解析后重渲染——本机无法预知远端发行版路径）。"""
    specs = _entry_specs(entries) if entries else []
    if not specs:
        return ("# OpsAxiom 只读白名单（技能库即权限清单，docs/12 §5.6）\n"
                "# 未发现任何 Linux 探针命令——不生成白名单行\n")
    lines = ["# OpsAxiom 只读白名单（技能库即权限清单，docs/12 §5.6）",
             "# 本文件由 opsaxiom target add 的开通流程生成；Skill 库更新请重跑向导刷新。"]
    if header_note:
        lines.append(f"# 来源：{header_note}")
    lines.append(f"{user} ALL=(root) NOPASSWD: " + ", ".join(specs))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="从 registry Skill 命令集生成 sudoers 白名单（裸名清单）")
    ap.add_argument("--dir", help="skill 目录（默认 registry 缓存）")
    ap.add_argument("--user", default="opsaxiom-ro")
    ap.add_argument("--list", action="store_true", help="只列清单不生成 sudoers 文本")
    args = ap.parse_args(argv)

    root = args.dir or default_skills_root()
    if not root:
        print("❌ 未找到 registry 缓存（~/.opsaxiom/hub/registry/skills）。先 hub sync。")
        return 1
    usage = scan_skills_dir(root)
    if args.list:
        for (name, prefix) in sorted(usage, key=lambda e: (e[0], e[1] or "")):
            prefix_disp = f" {prefix} *" if prefix else ""
            print(f"{name:<20} {prefix or '(任意参数)':<12} ← {len(usage[(name, prefix)])} 个 skill")
        print(f"\n共 {len(usage)} 条条目（{len(set(n for n, _ in usage))} 个二进制）")
        return 0
    print(render_sudoers_file(usage.keys(), user=args.user))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
