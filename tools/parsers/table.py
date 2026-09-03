"""
自研表格类解析器：把系统命令的定宽/空白分隔输出转成 rows 列表。
每个解析器返回 {"rows": [ {列: 值}, ... ]}，数值列尽量转成 int/float。

覆盖金标准依赖：table/df-v1, table/df-inode-v1, table/du-v1。
其余 O-3/O-4 引用的解析器（vmstat/ps/iostat...）留待后续补齐，
本模块提供通用工具函数便于扩展。
"""
import re


def _pct(s):
    """'75%' -> 75 (int)；非法返回 None。"""
    m = re.match(r"\s*(\d+)\s*%\s*$", s)
    return int(m.group(1)) if m else None


def _int(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def parse_df(text):
    """df -B1 --output=target,size,used,avail,pcent 的输出。
    表头行含 'Mounted on' 或列名，跳过；每行末列是百分比。"""
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("filesystem", "mounted", "target")):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pct = _pct(parts[-1])
        if pct is None:
            continue  # 非数据行
        # 约定列顺序：target size used avail pcent（target 可能含空格，用末4列反推）
        target = " ".join(parts[:-4]) if len(parts) >= 5 else parts[0]
        size, used, avail = (_int(x) for x in parts[-4:-1])
        rows.append({"target": target, "size": size, "used": used,
                     "avail": avail, "pcent": pct})
    return {"rows": rows}


def parse_df_inode(text):
    """df -i -P 的输出（POSIX 列序：Filesystem Inodes IUsed IFree IUse% Mounted on）。"""
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("filesystem", "target", "inodes")):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        pct = _pct(parts[4])        # IUse% 在倒数第 3 列（POSIX 固定列序）
        if pct is None:
            continue
        itotal = _int(parts[1])
        iused = _int(parts[2])
        target = " ".join(parts[5:]) if len(parts) >= 7 else parts[-1]
        rows.append({"target": target, "itotal": itotal, "iused": iused, "ipcent": pct})
    return {"rows": rows}


def parse_du(text):
    """du -xB1 ... | sort -rn | head 的输出：每行 '<bytes>\\t<path>'。"""
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        size = _int(parts[0])
        if size is None:
            continue
        rows.append({"size": size, "path": parts[1]})
    return {"rows": rows}


def parse_vmstat(text):
    """`vmstat 1 N` → rows[{r,si,so,us,sy,wa}]（表头两行跳过）。"""
    rows = []
    for line in text.strip().splitlines():
        parts = line.split()
        if len(parts) < 17 or not parts[0].isdigit():
            continue   # 跳过表头/非数据行
        rows.append({"r": _int(parts[0]), "si": _int(parts[6]), "so": _int(parts[7]),
                     "us": _int(parts[12]), "sy": _int(parts[13]), "wa": _int(parts[15])})
    return {"rows": rows}


def parse_free(text):
    """`free -m` → available_pct / mem_total(MB) / slab_unreclaim_mb(缺省 0)。"""
    mem_total = avail = None
    for line in text.splitlines():
        p = line.split()
        if p and p[0].startswith("Mem"):
            mem_total = _int(p[1])
            if len(p) >= 7:
                avail = _int(p[6])
    available_pct = round(avail / mem_total * 100, 1) if (avail and mem_total) else None
    m = re.search(r"SUnreclaim:\s*(\d+)", text)
    slab = int(m.group(1)) // 1024 if m else 0
    return {"mem_total": mem_total, "available_pct": available_pct, "slab_unreclaim_mb": slab}


def _ps_rows(text, valcol):
    rows = []
    for line in text.strip().splitlines():
        p = line.split(None, 2)
        if len(p) < 2 or not p[0].isdigit():
            continue
        row = {"pid": _int(p[0]), valcol: _num(p[1]), "comm": p[2] if len(p) > 2 else ""}
        rows.append(row)
    return {"rows": rows}


def _num(s):
    try:
        return float(s) if "." in s else int(s)
    except (ValueError, TypeError):
        return None


def install(register):
    register("table/df-v1", parse_df,
             {"rows": ["target", "size", "used", "avail", "pcent"], "lines": False})
    register("table/df-inode-v1", parse_df_inode,
             {"rows": ["target", "itotal", "iused", "ipcent"], "lines": False})
    register("table/du-v1", parse_du,
             {"rows": ["size", "path"], "lines": False})
    register("table/vmstat-v1", parse_vmstat,
             {"rows": ["r", "si", "so", "us", "sy", "wa"], "lines": False})
    register("table/free-v1", parse_free,
             {"scalars": ["mem_total", "available_pct", "slab_unreclaim_mb"], "lines": False})
    register("table/ps-v1", lambda t: _ps_rows(t, "pcpu"),
             {"rows": ["pid", "pcpu", "comm"], "lines": False})
    register("table/ps-rss-v1", lambda t: _ps_rows(t, "rss"),
             {"rows": ["pid", "rss", "comm"], "lines": False})
