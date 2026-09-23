"""
LLM 适配层（Z-5，docs/09 §3）——可选、可降级，只做三件事。

这是 docs/01 §1"编译时智能 vs 运行时选择"的最终兑现：**模型是外壳，引擎是法律。**
LLM 只做理解/叙事/建议，绝不碰命令与判读。三个调用点每一处都有无模型降级路径，
**无 model.yaml 时全功能可用**（导航档零依赖承诺不变，R3）。

铁律（对应宪法条款，全部由本模块的代码结构强制，不靠提示词自觉）：
- LLM 永不生成命令——本模块不设"出命令"的调用点；命令只来自已验证 Skill（R10）。
- LLM 永不判分支——判读只由 exprlang + 解析器完成（R9）；本模块不求值任何分支。
- LLM 输出全部过 schema/白名单，不符即静默丢弃走降级（R10）。
- 送 LLM 的上下文先过 redact 脱敏（R11）。
- LLM 抽取的 param 值再过 shell 安全校验（不安全即丢），因为它会流进命令（T-3）。

后端：ollama 与 openai-compatible 两种，均走 stdlib urllib（不加依赖）。
可测：所有调用点接受注入的 caller(prompt, system)->str|None，测试喂假响应（含攻击载荷）。
"""
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from redact import redact          # noqa: E402
from sweep import is_shell_safe    # noqa: E402  param 值安全校验复用


def config_path():
    base = pathlib.Path(os.environ.get(
        "OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom"))
    return base / "model.yaml"


def _flatten_profile(doc):
    """model.yaml v2（profiles+active）→ 单份 cfg；v1（旧单段）原样返回。"""
    if not isinstance(doc, dict):
        return doc
    if "profiles" not in doc:
        return doc                                   # v1 旧格式：单段 enabled/backend/...
    profiles = doc.get("profiles") or {}
    act = doc.get("active")
    if not act or act not in profiles:
        return {"enabled": False}                    # active 无效 = 未接
    flat = dict(profiles[act])
    flat["enabled"] = True
    flat["_profile"] = act                           # 展示用：当前 profile 名
    return flat


def load_config(path=None):
    """读 model.yaml（v1 单段 / v2 profiles 均可）；缺文件/未启用/解析失败 → None。"""
    import yaml
    p = pathlib.Path(path) if path else config_path()
    if not p.exists():
        return None
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    cfg = _flatten_profile(cfg)
    return cfg if cfg.get("enabled") else None


_mtime_cache = {"t": None, "cfg": None}


def load_config_live(path=None):
    """带 mtime 缓存的 load_config：文件没变就返回上次结果（纳秒级 stat）。

    供运行时（REPL）每次用模型前调用——外部终端改了 model.yaml，下一句
    自动静默生效；文件未变时零额外开销。
    """
    p = pathlib.Path(path) if path else config_path()
    try:
        t = p.stat().st_mtime_ns
    except OSError:
        t = None
    if _mtime_cache["t"] != t or _mtime_cache["cfg"] is None:
        _mtime_cache["t"] = t
        _mtime_cache["cfg"] = load_config(path)
    return _mtime_cache["cfg"]


# ---------- builtin：内置本地小模型（M-1，开箱即用的 floor）----------
# 默认落地模型：千问最小档 Qwen2.5-0.5B-Instruct（GGUF q4_k_m ≈ 469MB），
# 用 llama-cpp-python 纯本机推理——气隙可用、无常驻服务。国内直连 ModelScope 下载。
BUILTIN_MODEL_FILE = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
BUILTIN_MODEL_URL = ("https://modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF"
                     "/resolve/master/" + BUILTIN_MODEL_FILE)


def models_dir():
    base = pathlib.Path(os.environ.get(
        "OPSAXIOM_HOME", pathlib.Path.home() / ".opsaxiom"))
    return base / "models"


def builtin_model_path(cfg=None):
    """解析 builtin 模型文件：cfg.model_path 优先，否则 models/ 下默认名，
    再兜底 models/ 里任一 *.gguf。不存在返回 None。"""
    if cfg and cfg.get("model_path"):
        p = pathlib.Path(cfg["model_path"]).expanduser()
        return p if p.exists() else None
    p = models_dir() / BUILTIN_MODEL_FILE
    if p.exists():
        return p
    ggufs = sorted(models_dir().glob("*.gguf")) if models_dir().is_dir() else []
    return ggufs[0] if ggufs else None


_BUILTIN_CACHE = {}          # path -> Llama 实例（0.5B 常驻内存 ~600MB，进程内单例）


def _builtin_call(cfg, prompt, system):
    """本机 GGUF 推理。llama_cpp 未装/模型缺失/推理异常 → None（降级）。"""
    try:
        from llama_cpp import Llama
    except Exception:
        return None
    path = builtin_model_path(cfg)
    if path is None:
        return None
    key = str(path)
    if key not in _BUILTIN_CACHE:
        try:
            _BUILTIN_CACHE[key] = Llama(
                model_path=key, n_ctx=int(cfg.get("n_ctx", 2048)),
                n_threads=os.cpu_count() or 4, verbose=False)
        except Exception:
            return None
    try:
        out = _BUILTIN_CACHE[key].create_chat_completion(
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            max_tokens=int(cfg.get("max_tokens", 256)), temperature=0)
        return out["choices"][0]["message"]["content"]
    except Exception:
        return None


# ---------- pi：Pi Agent Harness 的多 provider 网关（M-3）----------
# 通过 tools/pi_bridge.mjs 调 @earendil-works/pi-ai（OpenAI/Anthropic/Google/…统一 API）。
# 要求 node>=22.19 且已 npm install @earendil-works/pi-ai；不满足 → None（降级），
# `opsaxiom model test` 会诚实报出差什么。
def _pi_call(cfg, prompt, system, timeout=60):
    import subprocess
    bridge = pathlib.Path(__file__).resolve().parent / "pi_bridge.mjs"
    if not bridge.exists():
        return None
    payload = json.dumps({
        "provider": cfg.get("provider", "openai"),
        "model": cfg.get("model", ""),
        "endpoint": cfg.get("endpoint"),
        "apiKey": cfg.get("api_key") or os.environ.get(cfg.get("api_key_env", ""), ""),
        "system": system, "prompt": prompt,
        "maxTokens": int(cfg.get("max_tokens", 512))})
    try:
        r = subprocess.run(["node", str(bridge)], input=payload,
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return None
        obj = json.loads(r.stdout.strip() or "{}")
        return obj.get("text") or None
    except Exception:
        return None


# ---------- 后端调用（stdlib urllib）----------
def _ssl_context():
    """HTTPS 信任库兜底：python.org 发行版 macOS 常不带默认 CA 包
    （CERTIFICATE_VERIFY_FAILED——合法站点也报）。certifi 在就用它的
    信任库（不跳过验证，R10 同款宁缺勿滥：curl 过、Python 不过的根因）。
    无 certifi 时返回 None = urlopen 默认行为。"""
    try:
        import certifi
        import ssl
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _http_json(url, payload, timeout=20):
    import urllib.request
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    ctx = _ssl_context()
    with urllib.request.urlopen(req, timeout=timeout, **({"context": ctx} if ctx else {})) as r:  # noqa: E501
        return json.loads(r.read().decode("utf-8"))


def _err_head(e):
    """异常 → 一行人话主干，带得上就带码：401 Unauthorized / 连接被拒…"""
    if isinstance(e, ImportError) or not isinstance(e, Exception):
        return str(e)
    # urlopen 把 HTTPError 包成 URLError("<...>")——拆包拿内层
    inner = getattr(e, "reason", None)
    code = getattr(inner, "code", None)
    if code:
        return f"{code} {inner}"
    return (str(e) or type(e).__name__).split("\n")[0].strip()


_last_error = ""


def last_error():
    """最近一次 backend_call 失败原因（人话串）。降级文案配套，纯诊断展示。"""
    return _last_error


def backend_call(cfg, prompt, system):
    """按 cfg.backend 调后端，返回文本；任何异常/超时 → None（触发降级）。
    失败原因留在模块级 last_error()，供 model test 探针讲人话。"""
    global _last_error
    _last_error = ""
    try:
        # backend 缺失时按 kind 回退（手改 yaml 少写 backend 不至于静默失联）
        backend = cfg.get("backend") or cfg.get("kind")
        model = cfg.get("model", "")
        if backend == "builtin":
            return _builtin_call(cfg, prompt, system)
        if backend == "pi":
            return _pi_call(cfg, prompt, system)
        if backend == "ollama":
            url = cfg.get("endpoint", "http://localhost:11434") + "/api/chat"
            data = _http_json(url, {
                "model": model, "stream": False,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}]})
            return (data.get("message") or {}).get("content")
        if backend in ("openai", "openai-compatible"):
            url = cfg.get("endpoint", "").rstrip("/") + "/chat/completions"
            headers_key = cfg.get("api_key")
            payload = {"model": model,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": prompt}]}
            import urllib.request
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         **({"Authorization": f"Bearer {headers_key}"} if headers_key else {})})
            ctx = _ssl_context()
            with urllib.request.urlopen(req, timeout=20,
                                        **({"context": ctx} if ctx else {})) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        _last_error = _err_head(e)                    # 供 cmd_test 探针讲人话
        return None
    return None


def _caller(cfg, caller):
    """解析实际调用器：优先注入的 caller（测试），否则真实后端。"""
    if caller is not None:
        return caller
    if cfg is None:
        return None
    return lambda prompt, system: backend_call(cfg, prompt, system)


def _extract_json(text):
    """从模型输出里抠第一个 JSON 对象；失败 → None。"""
    if not text:
        return None
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        return json.loads(text[s:e + 1])
    except Exception:
        return None


# ---------- 调用点 1：intake 理解 ----------
# few-shot 是给 0.5B 级小模型的（M-1 实测：无示例时字段会放错层级），大模型同样受益。
_INTAKE_SYS = (
    "你是运维分诊助手。把用户的故障陈述转成 JSON，所有实体一律放进 params 对象："
    "{\"params\":{键:值},\"entities\":[原文片段]}。常见键：mount(挂载点) host(主机) "
    "peer_ip(对端IP) xid(GPU错误码) pod svc topic。没有就留空。只输出 JSON，不要解释。\n"
    "示例1 输入：主机 web-01 的 /data 磁盘满了\n"
    "示例1 输出：{\"params\":{\"host\":\"web-01\",\"mount\":\"/data\"},\"entities\":[\"web-01\",\"/data\"]}\n"
    "示例2 输入：交换机 10.0.0.1 的 bgp 邻居掉了\n"
    "示例2 输出：{\"params\":{\"peer_ip\":\"10.0.0.1\"},\"entities\":[\"10.0.0.1\"]}\n"
    "示例3 输入：gpu 掉卡 xid 79\n"
    "示例3 输出：{\"params\":{\"xid\":\"79\"},\"entities\":[\"xid 79\"]}")


# 已知键的形状校验（确定性，R9/R10）：小模型的抽取不能裸信——M-1 真机实测
# 0.5B 会把 mount 抽成 "/目录磁盘满了" 这类脏值，流进 df 命令即污染判读。
# 不合形状 → 丢弃（宁缺勿错：缺参走"证据不足→粘贴块"，错参会静默毒化卷宗）。
import re as _re
_PARAM_SHAPE = {
    "mount": _re.compile(r"^/[\x21-\x7e]*$"),                    # 绝对路径，仅可见 ASCII
    "host": _re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-_]{0,62}$"),  # 主机名
    "peer_ip": _re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"),          # IPv4
    "ip": _re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"),
    "xid": _re.compile(r"^\d{1,3}$"),
    "pod": _re.compile(r"^[a-z0-9][a-z0-9.\-]{0,63}$"),
    "svc": _re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-_]{0,63}$"),
    "topic": _re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-_]{0,127}$"),
}


def _shape_ok(key, value):
    pat = _PARAM_SHAPE.get(key)
    if pat is not None:
        return bool(pat.match(value))
    # 未知键：无专用形状，仅要求可见 ASCII 且不超长（进命令模板的保守底线）
    return len(value) <= 128 and all(0x21 <= ord(c) <= 0x7e for c in value)


def intake(symptom, config=None, caller=None):
    """NL → 结构化 params（供渲染命令）。返回 {"params":{...}, "entities":[...]}。

    降级（无模型/超时/输出不合法/无 JSON）→ {"params":{}, "entities":[]}，
    调用方照常用 bigram diagnose。抽出的 param 值过两道确定性校验：
    shell 安全（T-3）+ 已知键形状（_PARAM_SHAPE）——模型输出永远不裸信。
    """
    fallback = {"params": {}, "entities": [], "degraded": True}
    call = _caller(config, caller)
    if call is None:
        return fallback
    raw = call(redact(symptom), _INTAKE_SYS)     # R11：送模型前脱敏
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        return fallback
    params = {}
    for k, v in (obj.get("params") or {}).items():
        if not (isinstance(k, str) and k.isidentifier()):
            continue                              # 键须是合法标识符（能进模板）
        v = str(v)
        if is_shell_safe(v) and _shape_ok(k, v):  # T-3 + 形状：任一不过即丢
            params[k] = v
    ents = [str(x) for x in (obj.get("entities") or [])][:12]
    return {"params": params, "entities": ents, "degraded": False}


# ---------- 调用点 2：叙事 ----------
def targeted_intake(symptom, wanted, config=None, caller=None):
    """调用点 1b（#33 两段式）：假说确定后，按 skill 的 params 清单定向抽取。
    wanted: [{"name": ..., "desc": ...}]（skill metadata.params 的 alert/user 档）。
    返回 {"params": {...}}——仅含 wanted 里的键；两道确定性校验与 intake 全同
    （T-3 shell 安全 + _PARAM_SHAPE 形状），模型输出永远不裸信。
    降级（无模型/超时/不合法）→ {"params": {}}，调用方对缺失项照常走问询。"""
    fallback = {"params": {}}
    call = _caller(config, caller)
    if call is None or not wanted:
        return fallback
    lines = "\n".join(f"- {w['name']}: {w.get('desc') or w['name']}" for w in wanted)
    sys_prompt = (
        "你是运维分诊助手。从用户陈述里抽取下面这些参数的值；"
        "陈述里没有提到的键就省略，不要编造。只输出 JSON：{\"参数名\":\"值\"}。\n"
        "需要的参数：\n" + lines + "\n只输出 JSON，不要解释。")
    raw = call(redact(symptom), sys_prompt)       # R11：送模型前脱敏
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        return fallback
    params = {}
    allow = {w["name"] for w in wanted}           # 只收清单内的键（定向）
    for k, v in obj.items():
        if k in allow and isinstance(k, str) and k.isidentifier():
            v = str(v)
            if is_shell_safe(v) and _shape_ok(k, v):
                params[k] = v
    return {"params": params}


_NARRATE_SYS = (
    "你是运维助手。把给定的结构化判读用一句中文人话讲清楚，"
    "只解释、不建议命令、不超过 40 字。只输出这句话。")


def narrate(conclusion, evidence=None, config=None, caller=None):
    """结构化结论 → 一句人话。降级 → 原样返回 conclusion（模板渲染结果）。

    叙事是纯展示：返回值只用于打印，绝不进任何执行/判读路径（注入无从生效）。
    """
    call = _caller(config, caller)
    if call is None:
        return conclusion
    ev = "; ".join(f"{e.get('field')}={e.get('value')}" for e in (evidence or [])[:6])
    prompt = redact(f"结论：{conclusion}\n证据：{ev}")
    out = call(prompt, _NARRATE_SYS)
    if not out or not out.strip():
        return conclusion
    return redact(out.strip()[:120])              # 出站再脱敏一次，且截断


# ---------- 调用点 3：escalate 助理 ----------
_SUGGEST_SYS = (
    "你是运维分诊助手。给定故障摘要与【可选 Skill id 白名单】，"
    "只从白名单里挑最相关的一个 id 输出 JSON：{\"skill_id\":\"<白名单里的 id>\"}。"
    "白名单外的一律不许输出。只输出 JSON。")


def suggest_skill(handover, idx, config=None, caller=None):
    """移交时建议下一个库内 Skill id。返回 id（∈ idx）或 None。

    白名单强制：模型给的 id 不在库内 → 丢弃（返回 None）。这挡住"模型越权
    推荐不存在/编造的 Skill"。徽章由调用方从 idx 查，模型无权抬升可信度（R8）。
    """
    call = _caller(config, caller)
    if call is None:
        return None
    ids = {s["id"] for s in idx}
    listing = ", ".join(sorted(ids))
    prompt = redact(f"故障：{handover.get('symptom','')}\n"
                    f"已排除：{handover.get('refuted', [])}\n"
                    f"白名单 id：{listing}")
    obj = _extract_json(call(prompt, _SUGGEST_SYS))
    if not isinstance(obj, dict):
        return None
    sid = obj.get("skill_id")
    return sid if sid in ids else None            # 越权/编造 id → None
