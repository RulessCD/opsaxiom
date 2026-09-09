"""
gen_sudoers.py —— sudoers 只读白名单生成器 v2（B 轮"白名单即路由表"/ docs/12 §5.6）。

扫描 registry 缓存（唯一权威 Skill 源，见需求0723 #7）里全部 skill.yaml 的
linux 探针命令，提取**段首**可执行体，产出一份与"远端 /etc/sudoers.d/opsaxiom-ro
内容"同源的成员清单。runtime 执行时用它做路由：名单内 → 统一 `sudo -n` 直跑；
名单外 → 优雅降级贴回（复用 mixed 交互）。

抽取边界（v2 收紧，三角教训入库）：
  - 只看 linux 键的 run（tree.nodes[].run、preflight.watch[].run；action（写变更）
    节点结构性排除——fs-readonly 的 umount/fsck 教训）
  - **引号感知切段**：解析 `'`/`"` 状态后再切 `&& || ; |`——`grep -iE "a|b"`
    引号内的 `|` 不再泄漏成"命令"（真机语法错误教训）
  - **只收段首**：管道中段（grep/awk 等）不提权、不进白名单（v2 废弃
    _TEXT_TOOLS 全收策略）
  - `sudo X` 穿透取 X；shell 控制词/重定向不算
  - **解释器类硬拒**：bash/sh/awk/perl/python/env/find/timeout/xargs/socat/nc
    等经 sudo 可得 root shell（GTFOBins）——段首也拒收，宁可探针拿不到 sudo
  - 客户端 CLI（mysql/kubectl 等）走各自 connector，不进白名单
  - 产物是裸名清单；sudoers 要求绝对路径，远端 enroll 用 `command -v` 解析后
    重渲染（本机无法预知发行版路径）
"""
import argparse
import pathlib
import re

import yaml

# 段切分（引号感知版）：复合命令连接符（管道 + 逻辑符 + 分号）
_SEG_RE = re.compile(r"&&|\|\||;|\|")
# 复合型二进制（十七轮返工 v3 语义，F-19）：命令词与 flag 正交（systemd CLI 的
# "OPTIONS COMMAND" 结构），flag 打头的 sudoers 条目关不死后面的写子命令——
# `systemctl --failed *` 同时匹配 `systemctl --failed restart nginx`（真机 registry
# 已登记 --failed 前缀，实测双侧放行写命令）。因此复合型二进制【只认下方
# _RO_COMPOSITE_SUBCMDS 登记的只读子命令】作前缀；flag/选项前缀一律 fail-closed
# （不产出条目，该探针白名单档转贴回），复合型也永不发裸名。
_COMPOSITE_LEAD = {"systemctl", "journalctl", "timedatectl", "networkctl",
                   "nvidia-smi",
                   "ip", "nft", "iptables", "firewall-cmd"}
# 复合型二进制的【结构性只读子命令】白名单：这些子命令之后只跟参数，不可能
# 携带写子命令词；不在名单里的子命令/flag 前缀一律不登记（fail-closed 转贴回——
# journalctl 的 -u/--disk-usage 是 flag 不是子命令，fnmatch 尾通配关不住
# --vacuum-size/--rotate 这类销毁证据的写 flag，现网 journalctl 探针因此全部
# 降级贴回；这是"写侧焊死"的代价，发起人已确认）。
_RO_COMPOSITE_SUBCMDS = {
    "systemctl": {"is-active", "status", "show"},
    "timedatectl": {"status", "show"},
    "networkctl": {"status", "list"},
    # nvidia-smi：写动词全走 flag/子命令后参（-r 换卡复位/-pl 功耗/-ac 锁频/
    # mig -cgi 建 MIG），裸名关不住——按复合型从严，只登纯观察子命令；
    # nvlink/mig 不放（nvlink -r 重置计数器、mig 可写），flag 前缀（-q/-L/
    # --query-*）结构性 fail-closed，相关 GPU 探针降级贴回（写侧焊死代价）。
    "nvidia-smi": {"dmon", "topo"},
    # journalctl 的常用形态全是 flag（-u/--disk-usage）→ 结构性无前缀可放行；
    # ip/nft/iptables/firewall-cmd 的 OBJECT 之后可接 flush/add 等写动词，单
    # token 前缀（ip neigh）关不住 `ip neigh flush` → 全部 fail-closed
}
# shell 控制词与重定向段不算白名单内容
_CTRL = {"for", "if", "then", "else", "fi", "do", "done", "while", "case", "esac",
         "in", "echo", "true", "false", "exit", "return", "export", "set", "cd",
         "local", "shift", "xargs"}
# 解释器/提权跳板硬拒名单：这些二进制经 sudo 等于 root shell 或任意读写
# （GTFOBins 结论），段首也不收——宁可探针拿不到 sudo，不给白名单开口子。
# find/timeout/xargs 的 -exec/--exec 派生 shell；awk/perl 的 system()；
# env 直接改环境起进程；socat/nc 是网络管道双刃。
_INTERPRETERS = {"bash", "sh", "zsh", "ksh", "dash", "awk", "gawk", "mawk",
                 "perl", "python", "python3", "env", "exec", "find", "timeout",
                 "xargs", "socat", "nc", "ncat", "expect", "lua", "ruby", "php",
                 # "策略 + 任意命令"执行器（F-23，GTFOBins 同族）：语法即
                 # "策略参数 + 任意命令"，sudo 下方第一个位置参数之后可起
                 # root shell——语句上与解释器无异，硬拒，探针转贴回。
                 "numactl", "taskset", "chrt", "ionice", "nice", "setsid",
                 "stdbuf", "nohup", "strace", "ltrace", "watch"}
# 排除名单：客户端 CLI（能执行写 SQL/写命令/服务管理），这类进白名单会让
# 只读账号获得远超"取证"的能力——它们的只读使用场景走各 connector（mysql 键）
# 与专用账号，不走 sudoers。
_DENY_BINS = {"mysql", "mysqldump", "psql", "mongosh", "mongo", "redis-cli",
              "rabbitmqctl", "nginx", "sshd", "curl", "fail2ban-client",
              "kubectl", "kubectl.x", "auditctl",
              # 裸名放行 = ro 账号直接可写/可破坏（F-23）："语义固定"不成立的
              # 伪装者——首参后可下达写动作，fnmatch 裸名条目关不住：
              #   sysctl -w（写内核参数）/ nvidia-smi -r -pl -ac（GPU reset、
              #   改功耗锁频）/ smartctl --smart=on -t online（改盘行为、起自检）
              #   chronyc settime（改时钟）/ coredumpctl delete（删转储）/
              #   tcpdump -w / ethtool -w。这些的只读使用场景在客户端 _is_readonly
              #   判定照常，白名单档收不到 sudo 即转贴回。
              "sysctl", "smartctl", "chronyc", "coredumpctl", "tcpdump",
              "modprobe", "insmod", "rmmod", "blockdev", "hdparm",
              "dmidecode"}


def split_segments(cmd):
    """按 `&& || ; |` 切段，但**跳过引号内的连接符**——
    grep -iE "oom-kill|Out of memory" 的引号内 `|` 是正则交替，不是管道。"""
    segs, start, i, n = [], 0, 0, len(cmd)
    quote = None
    while i < n:
        ch = cmd[i]
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        two = cmd[i:i + 2]
        if two in ("&&", "||"):
            segs.append(cmd[start:i])
            start = i = i + 2
            continue
        if ch in (";", "|"):
            segs.append(cmd[start:i])
            start = i = i + 1
            continue
        i += 1
    segs.append(cmd[start:])
    return segs


def extract_entries(cmd):
    """一条命令字符串 → set[(bin, arg_prefix)]。
    只收**命令首段**的二进制：sudo 的提权语义只罩住"我们发出的一整条命令"里
    以 sudo 运行的那一段——管道/复合的其他段不会以 sudo 身份跑，白名单里
    收它们只会扩权（v2 教训）。首段 = 引号感知切分后的第一段；
    若它是 `sudo`/`-n`/`-u` 变体则穿透；控制词/flag/解释器/deny 名单不算。
    arg_prefix = 二进制后的第一个非 flag token（如 systemctl 的子命令）；
    其余参数为 `*`（只读命令参数不可枚举）。二进制名形态校验。"""
    out = set()
    segs = split_segments(cmd)
    if not segs:
        return out
    seg = segs[0].strip()
    # 跳过子 shell 段首的 ( 与 {（v1 不进子 shell 内部）
    toks = [t for t in seg.split() if t not in ("(", ")")]
    # 穿过段首的数字重定向（2>/dev/null 形态粘在段首）
    while toks and re.match(r"^\d*>(/|\S)", toks[0]):
        toks = toks[1:]
    if not toks:
        return out
    i = 0
    while i < len(toks) and toks[i] in ("sudo", "-n", "-u"):
        i += 1
    if i >= len(toks):
        return out
    name = toks[i]
    name = name.rsplit("/", 1)[-1]         # 相对路径取基名
    if not name or name in _CTRL or name.startswith("-"):
        return out
    if name in _INTERPRETERS or name in _DENY_BINS:
        return out
    if not re.match(r"^[A-Za-z0-9_.@+\-]+$", name):
        return out
    # 前缀 = 二进制后的第一个"有区分度"的 token（仅对复合型二进制有意义）：
    #   - 子命令词（systemctl is-active → "is-active"）
    #   - type-1 flag（systemctl --failed / journalctl -u）——v3 起禁用：flag 与
    #     命令词正交，sudoers 尾通配关不住其后的写子命令/写 flag（F-19），
    #     故 flag 一律不作为放行前缀（fail-closed，探针转贴回）
    # 复合型二进制【不登记裸名】；前缀必须在 _RO_COMPOSITE_SUBCMDS 名单内。
    # 非复合型维持 v2 语义：裸名一条（args 任意，语义固定无节制必要）。
    prefix = None
    if name in _COMPOSITE_LEAD:
        j = i + 1
        while j < len(toks):
            t = toks[j]
            if t in _CTRL or re.match(r"^\d*>/", t):    # 控制词/段内重定向跳过
                j += 1
                continue
            if re.match(r"^\d*>", t):                    # 段尾重定向（2>/dev/null 自身）
                break
            if t.startswith("-"):
                break                        # flag 前缀 fail-closed（F-19）：不登记
            if re.match(r"^[A-Za-z][A-Za-z0-9_@.+-]*[A-Za-z0-9_-]$", t) or \
                    re.match(r"^[A-Za-z]$", t):
                if "{{" not in t and t in _RO_COMPOSITE_SUBCMDS.get(name, set()):
                    prefix = t               # 只读子命令：唯一可放行的前缀形态
                break
            break
    if name not in _COMPOSITE_LEAD:
        out.add((name, None))              # 单用途二进制：裸名形态（args 任意）
    if prefix is not None:
        out.add((name, prefix))
    return out


def skill_entries(skill):
    """一个 skill（dict）→ set[(bin, arg_prefix|None)]。只收"探针/判定"类命令——
    action（写变更）节点结构性排除：白名单的读者是远端只读账号，放行
    umount/fsck 等写命令等于白名单形同虚设（fs-readonly 的 umount 教训）。
    rollback/verify/watch 挂在 action 下，排除 action 即一并排除。
    discovery（发现类探针）与 tree.nodes 并列，一并收集。"""
    bins = set()
    nodes = list((skill.get("tree", {}) or {}).get("nodes", []) or [])
    nodes += skill.get("discovery") or []          # discovery 探针也进名单
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


def bin_names(entries):
    """{(bin, prefix|None)} → 排序后的裸二进制名清单（成员判断和展示用）。"""
    return sorted({name for name, _ in entries})


def _group_by_bin(entries):
    """{(bin, prefix|None)} → {bin: set(prefix|None)}。"""
    by_bin = {}
    for name, prefix in entries:
        by_bin.setdefault(name, set()).add(prefix)
    return by_bin


def _entry_specs(entries):
    """{(bin, prefix|None)} → 排序后的 sudoers 片段列表。
    非 None 前缀 → `bin prefix *` + 裸 `bin prefix` 两条（sudoers 参数匹配是
    fnmatch 整串，`prefix *` 只匹配带参形态，无参调用需裸前缀条覆盖——
    B-1 修复时实测确认）。None 前缀（裸名，args 任意）只出现在非复合型
    二进制上（复合型在 extract_entries 已禁止裸名登记，B-1）。
    specs 为裸名形态——写远端前由 enroll 经 `command -v` 解析为绝对路径后
    重渲染（sudoers 要求绝对路径）。"""
    specs = []
    by_bin = _group_by_bin(entries)
    for name in sorted(by_bin):
        for p in sorted(by_bin[name], key=lambda x: (x is not None, x or "")):
            if p is None:
                specs.append(name)
            else:
                specs.extend([f"{name} {p} *", f"{name} {p}"])
    return specs


def render_sudoers_file(entries, user="opsaxiom-ro", header_note="",
                        bin_paths=None):
    """命令集 → /etc/sudoers.d/opsaxiom-ro 完整文本。
    bin_paths: {裸名: 绝对路径}（enroll 在目标机 command -v 解析后传入）；
    提供则生成路径版（sudoers 合法形态），否则裸名版（仅供预览，**不可直接
    写入远端**——真机教训：裸名导致 syntax error）。"""
    def resolve(spec):
        name, _, rest = spec.partition(" ")
        p = (bin_paths or {}).get(name)
        return f"{p} {rest}".strip() if p else spec

    specs = [resolve(s) for s in (_entry_specs(entries) if entries else [])]
    if not specs:
        return ("# OpsAxiom 只读白名单（技能库即权限清单，docs/12 §5.6）\n"
                "# 未发现任何 Linux 探针命令——不生成白名单行\n")
    pending = [s for s in specs if not s.startswith("/")]
    lines = ["# OpsAxiom 只读白名单（技能库即权限清单，docs/12 §5.6）",
             "# 本文件由 opsaxiom target add 的开通流程生成；Skill 库更新请重跑向导刷新。"]
    if header_note:
        lines.append(f"# 来源：{header_note}")
    if pending:
        lines.append(f"# ⚠ 以下 {len(pending)} 条未解析到绝对路径，写入远端前必须解析："
                     + ", ".join(pending))
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
