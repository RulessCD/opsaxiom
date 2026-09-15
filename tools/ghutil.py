"""
GitHub token 门面——三处发件端（y 反馈 / n 反馈 / sug 上报）与 auth 指令共用的
唯一 token 读写与探活入口。此前 runtime 与 repl 各有一套读取器、对失效无感
（静默吞异常 / 悄悄降级浏览器），本模块统一收口：

    check_token()  探活：返回 (状态, login, 原因)
                   状态 ∈ "valid" | "invalid" | "missing" | "offline"
                   —— offline（网络不通）≠ invalid（token 坏），话术必须分开；
                      offline 不判 token 死刑。
    login()        token 有效时的 GitHub 账号名（attestor 派生源）。

探活结果带 10 分钟内存缓存：一次会话多轮回反馈不重复打 API（进程内，不落盘）。
"""
import json
import os
import pathlib
import subprocess
import time

_API = "https://api.github.com/user"
_TTL = 600  # 10min

_cache = {"t": 0.0, "r": None}   # (status, login, reason)


def token_path():
    home = pathlib.Path(
        os.environ.get("OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom"))
    return home / "gh_token"


def read_token():
    """读 token 原文（不发请求）。无文件/空 → ""。"""
    p = token_path()
    try:
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""
    except Exception:
        return ""


def check_token(force=False):
    """探活。只认 /user 的真实回应：200 valid / 401 invalid；
    涉网异常一律 offline（token 可能是好的人只是断网）。"""
    global _cache
    if not force and _cache["r"] is not None and time.time() - _cache["t"] < _TTL:
        return _cache["r"]
    tok = read_token()
    if not tok:
        r = ("missing", None, "未配置 token")
    else:
        try:
            proc = subprocess.run(
                ["curl", "-s", "--max-time", "5", "-w", "\n%{http_code}",
                 "-H", "Authorization: Bearer " + tok,
                 "-H", "User-Agent: OpsAxiom", _API],
                capture_output=True, text=True, timeout=8)
            out = proc.stdout.strip().rsplit("\n", 1)
            code = out[-1].strip() if out else ""
            body = {}
            try:
                body = json.loads(out[0]) if len(out) == 2 and out[0] else {}
            except Exception:
                pass
            if code == "200":
                r = ("valid", body.get("login") or "unknown", "")
            elif code == "401":
                r = ("invalid", None, "token 已失效（401）——去 github.com/settings/tokens 重新生成")
            else:
                # 5xx/429 限流等：token 无法定生死，按 offline 处理不判死刑
                r = ("offline", None, f"github 应答异常（HTTP {code or '无'}）")
        except Exception as e:
            r = ("offline", None, f"网络不可达（{type(e).__name__}）")
    _cache = {"t": time.time() if r[0] != "offline" else 0.0, "r": r}
    return r


def login():
    """token 有效 → login；其余 → None。"""
    st, who, _ = check_token()
    return who if st == "valid" else None


def invalidate_cache():
    """auth 重配后调用：下一条反馈立即真探，不吃旧缓存。"""
    global _cache
    _cache = {"t": 0.0, "r": None}
