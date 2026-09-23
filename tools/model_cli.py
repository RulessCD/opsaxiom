"""
opsaxiom model —— 模型配置 CLI（M-2）。

动作：
  model show              当前配置与各后端健康状态（诚实：差什么直说）
  model                   交互态菜单（REPL）：5 行可选后端，状态尾巴动态生成
  model use <backend>     接入后端（remote/pi 可配 --name 存为 profile）
  model switch <名>       在已维护的 remote/pi 配置间切换
  model list              列出已维护配置
  model remove <名>       删除一份配置（y/n 确认）
  model test              发一条真实探针走 intake，打印结果或降级原因
  model pull              下载本机小模型（Qwen2.5-0.5B GGUF，ModelScope 直连）
                          并（可选 --with-deps）安装 llama-cpp-python

存储：model.yaml v2 —— profiles（remote/pi 多份命名配置）+ active 指针；
builtin/ollama 是环境级单例，active 直接用其字面量。v1 旧单段格式在
llm.load_config 里自动兼容，首次 use 时迁移为 v2。

设计边界：模型永远只做理解/叙事/建议（llm.py 三调用点），这里只管"接哪个模型"。
所有后端不可用时系统全功能可用（关键词匹配模式），所以 use/off 都是安全操作。
"""
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yaml   # noqa: E402
import llm    # noqa: E402

BACKENDS = ("builtin", "ollama", "remote", "pi", "off")


# ---------- v2 存储协议（profiles + active） ----------
def _load_doc():
    """读 model.yaml 原始文档；无文件/坏文件 → v2 空文档。v1 自动迁移不落盘
    （落盘在首次 upsert 时发生，避免只读操作改用户文件）。"""
    p = llm.config_path()
    doc = None
    if p.exists():
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            doc = None
    if not isinstance(doc, dict):
        return {"active": None, "profiles": {}}, p
    if "profiles" not in doc:                        # v1 单段 → v2 迁移（内存中）
        if doc.get("enabled"):
            name = "default"
            profiles = {name: {k: v for k, v in doc.items() if k != "enabled"}}
            doc = {"active": name, "profiles": profiles}
        else:
            doc = {"active": None, "profiles": {}}
    doc.setdefault("profiles", {})
    doc.setdefault("active", None)
    return doc, p


def _save_doc(doc, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path


def _set_active(active):
    """设置 active（None=未接/off，'builtin'/'ollama' 字面量，或 profile 名）。"""
    doc, p = _load_doc()
    doc["active"] = active
    return _save_doc(doc, p)


def _activate_singleton(backend):
    """builtin/ollama 环境级单例接入：active 记字面量，并把单段字段写进
    profiles[backend] 槽——否则 flatten 拍平不出来，指针悬空等于没接。"""
    p = _set_active(backend)
    doc, _ = _load_doc()
    flat = make_config(backend)
    doc["profiles"][backend] = {k: v for k, v in flat.items() if k != "enabled"}
    _save_doc(doc, p)
    return p


def _upsert_profile(name, cfg):
    """写/更新一份 remote/pi 配置并设为 active。"""
    doc, p = _load_doc()
    doc["profiles"][name] = cfg
    doc["active"] = name
    return _save_doc(doc, p)


def _upsert_profile_keep_active(name, cfg):
    """更新一份配置的字段值，不动 active 指针（编辑场景专用）。"""
    doc, p = _load_doc()
    doc["profiles"][name] = cfg
    return _save_doc(doc, p)


def _remove_profile(name):
    doc, p = _load_doc()
    if name not in doc["profiles"]:
        return False
    del doc["profiles"][name]
    if doc["active"] == name:
        doc["active"] = None
    _save_doc(doc, p)
    return True


def _probe_name(kind):                               # 统一探测：菜单/向导用
    if kind == "builtin":
        return probe_builtin()
    if kind == "ollama":
        return probe_ollama()
    if kind == "pi":
        return probe_pi()
    return (None, "")


def make_config(backend, endpoint=None, model=None, api_key=None, provider=None):
    """按后端生成 model.yaml 内容（use 与向导共用）。off = enabled:false。"""
    if backend == "off":
        return {"enabled": False}
    cfg = {"enabled": True, "backend": {"remote": "openai-compatible"}.get(backend, backend)}
    if backend == "builtin":
        cfg["model_path"] = str(llm.models_dir() / llm.BUILTIN_MODEL_FILE)
    if backend == "ollama":
        cfg["endpoint"] = endpoint or "http://localhost:11434"
        cfg["model"] = model or "qwen2.5:7b"
    if backend == "remote":
        cfg["endpoint"] = endpoint or "http://localhost:8000/v1"
        cfg["model"] = model or ""
        if api_key:
            cfg["api_key"] = api_key
    if backend == "pi":
        cfg["provider"] = provider or "openai"
        cfg["model"] = model or "gpt-4o-mini"
        if api_key:
            cfg["api_key"] = api_key
    return cfg


# ---------- 健康探测（show/test 用，全部诚实报缺口）----------
def probe_builtin():
    try:
        import llama_cpp  # noqa: F401
    except Exception:
        return False, "llama-cpp-python 未装（opsaxiom model pull --with-deps）"
    p = llm.builtin_model_path()
    if p is None:
        return False, f"模型文件缺失（opsaxiom model pull 下载 {llm.BUILTIN_MODEL_FILE}）"
    return True, f"就绪：{p.name}（{p.stat().st_size >> 20} MB）"


def probe_ollama(cfg=None):
    ep = (cfg or {}).get("endpoint", "http://localhost:11434")
    try:
        import urllib.request
        with urllib.request.urlopen(ep + "/api/tags", timeout=3) as r:
            return r.status == 200, f"可达：{ep}"
    except Exception:
        return False, f"不可达：{ep}（本机没跑 Ollama？）"


def probe_pi():
    node = shutil.which("node")
    if not node:
        return False, "缺 node（pi 后端需 node ≥ 22.19）"
    try:
        v = subprocess.run([node, "--version"], capture_output=True, text=True,
                           timeout=5).stdout.strip().lstrip("v")
        major = int(v.split(".")[0])
    except Exception:
        return False, "node 版本探测失败"
    if major < 22:
        return False, f"node {v} 过低（pi-ai 需 ≥ 22.19）"
    # 与 pi_bridge.loadPiAi 同一解析顺序：tools/ → ~/.local/pi-agent 兜底
    r = subprocess.run([node, "-e",
                        "import('@earendil-works/pi-ai/providers/all')"
                        ".then(()=>process.exit(0),async e=>{"
                        "if(e.code!=='ERR_MODULE_NOT_FOUND')process.exit(2);"
                        "const os=await import('node:os');"
                        "const alt=os.homedir()+'/.local/pi-agent/node_modules/"
                        "@earendil-works/pi-ai/dist/providers/all.js';"
                        "await import('file://'+alt).then(()=>process.exit(0),()=>process.exit(1))})"],
                       capture_output=True, timeout=15, cwd=str(HERE))
    if r.returncode == 0:
        return True, f"就绪：node {v} + pi-ai"
    if r.returncode == 1:
        return False, "缺 @earendil-works/pi-ai（npm install --prefix ~/.local/pi-agent @earendil-works/pi-ai）"
    return False, "pi-ai 探测异常（node 或包损坏？）"


# ---------- 动作 ----------
def cmd_show(args):
    cfg = llm.load_config()
    p = llm.config_path()
    print(f"配置文件: {p}  {'(存在)' if p.exists() else '(不存在)'}")
    if cfg is None:
        print("当前: 未接模型（使用关键词匹配——全功能可用）")
    else:
        shown = {k: ("***" if k == "api_key" else v) for k, v in cfg.items()}
        print(f"当前: {shown}")
    print("\n各后端可用性：")
    ok, msg = probe_builtin()
    print(f"  {'🟢' if ok else '🟡'} builtin  本机小模型(Qwen2.5-0.5B)  {msg}")
    ok, msg = probe_ollama(cfg if cfg and cfg.get('backend') == 'ollama' else None)
    print(f"  {'🟢' if ok else '🟡'} ollama   本地 Ollama              {msg}")
    print("  ⚪ remote   OpenAI 兼容远程 API       填 endpoint/model/api_key 即用")
    ok, msg = probe_pi()
    print(f"  {'🟢' if ok else '🟡'} pi       Pi 多 provider 网关       {msg}")
    print("\n切换：opsaxiom model use builtin|ollama|remote|pi|off  [--endpoint/--model/--api-key/--provider]")


def cmd_use(args):
    """接入后端。remote/pi 带 --name 时落为命名 profile（多配置管理）；
    builtin/ollama/off 是环境级单例，active 直接记字面量。"""
    if args.backend in ("remote", "pi") and getattr(args, "name", None):
        cfg = {k: v for k, v in make_config(args.backend, endpoint=args.endpoint,
                                            model=args.model, api_key=args.api_key,
                                            provider=args.provider).items()
               if v is not None and k != "enabled"}
        cfg["kind"] = "openai-compatible" if args.backend == "remote" else "pi"
        name = args.name
        _upsert_profile(name, cfg)
        print(f"已保存并启用配置 {name}（{cfg['kind']}）。验证：opsaxiom model test")
        return
    cfg = make_config(args.backend, endpoint=args.endpoint, model=args.model,
                      api_key=args.api_key, provider=args.provider)
    if args.backend in ("builtin", "ollama"):
        p = _activate_singleton(args.backend)
    else:
        p = _set_active(None if args.backend == "off" else args.backend)
    if args.backend == "off":
        print(f"已断开模型（{p}）。系统所有功能正常（关键词匹配模式）。")
        return
    print(f"已切到 {args.backend}（{p}）。验证：opsaxiom model test")
    if args.backend == "builtin":
        ok, msg = probe_builtin()
        if not ok:
            print(f"  ⚠ {msg}")


def cmd_test(args):
    cfg = llm.load_config()
    if cfg is None:
        print("未接模型（model.yaml 缺失或 enabled:false）。opsaxiom model use <backend> 先切一个。")
        return 1
    r = llm.intake("主机 web-01 的 /data 磁盘满了", config=cfg)
    if r.get("degraded"):
        err = (llm.last_error() or "").strip()
        # HTTPError 原文是机器形态，抽主干人话（如 "HTTP Error 401: Unauthorized"）
        head = err.split("(")[0].strip() if err else ""
        print(f"✘ 未通。失败原因：{head or '未得到具体报错（超时/输出不合法）'}")
        if "CERTIFICATE_VERIFY_FAILED" in err:
            print("   Python 信任链缺失（curl 过、Python 不过是典型症状）。修复：")
            print("      pip install certifi    # 装好后重跑 model test；绝不跳过 SSL 验证")
        return 1
    print("✔ 服务可用")
    return 0


def cmd_serve(args):
    """把内置 GGUF 起成 OpenAI 兼容服务（llama_cpp.server，含流式）——
    pi 入口的 opsaxiom-local provider 与任何 OpenAI 客户端都能接（N-4）。"""
    p = llm.builtin_model_path()
    if p is None:
        print("模型文件缺失，先 opsaxiom model pull")
        return 1
    try:
        import llama_cpp.server  # noqa: F401
        import uvicorn  # noqa: F401
    except Exception:
        print("缺 server 依赖：pip install 'llama-cpp-python[server]'"
              "（或 opsaxiom model pull --with-deps --serve-deps）")
        return 1
    # 结构：<port> 垫片代理（拍平 content parts / 字段名映射，pi 直连这里）
    #        → <port+1> llama_cpp.server 本体。
    # 真机实锤：pi-ai 发 OpenAI content parts，llama 的 jinja 模板只认字符串→500。
    up_port = args.port + 1
    print(f"OpenAI 兼容服务 → http://127.0.0.1:{args.port}/v1"
          f"（模型别名 qwen2.5-0.5b-instruct，Ctrl-C 停）")
    proc = subprocess.Popen([sys.executable, "-m", "llama_cpp.server",
                             "--model", str(p), "--host", "127.0.0.1",
                             "--port", str(up_port),
                             "--model_alias", "qwen2.5-0.5b-instruct",
                             "--n_ctx", "4096"])
    try:
        import llm_proxy
        llm_proxy.serve(listen_port=args.port, upstream_port=up_port)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
    return 0


# ---------- install-local：一键装 ollama + 最小千问（发起人需求） ----------
OLLAMA_MODEL = "qwen2.5:0.5b"          # ollama 上最小的千问
NEED_DISK_GB = 3                       # ollama 本体 + 模型 + 余量
NEED_MEM_GB = 1.5                      # 0.5b 推理常驻

FAIL_MSG = ("本地不具备安装本地小模型的依赖或资源，请连接远端模型"
            "（opsaxiom model use remote …，或 pi 界面里 /login），或离线使用"
            "（不接模型也全功能可用）。")


def check_local_ready(disk_usage=None, meminfo_path="/proc/meminfo",
                      which=None, geteuid=None, net_probe=None, system=None):
    """安装前体检：平台/权限/磁盘/内存/网络。返回问题清单（空=可装）。
    各探测点可注入（测试用），默认真实探测。"""
    import platform
    import urllib.request
    disk_usage = disk_usage or shutil.disk_usage
    which = which or shutil.which
    geteuid = geteuid or getattr(os, "geteuid", lambda: 0)
    system = system or platform.system()
    problems = []
    if system != "Linux":
        problems.append(f"仅支持 Linux 一键安装（当前 {system}）")
    have_ollama = bool(which("ollama"))
    if not have_ollama and geteuid() != 0 and not which("sudo"):
        problems.append("安装 ollama 需要 root 或 sudo（当前都没有）")
    try:
        free_gb = disk_usage(str(pathlib.Path.home())).free / 1024**3
        if free_gb < NEED_DISK_GB:
            problems.append(f"磁盘空间不足：需 ≥{NEED_DISK_GB}GB，当前可用 {free_gb:.1f}GB")
    except Exception:
        problems.append("无法读取磁盘空间")
    try:
        mem_kb = 0
        for line in open(meminfo_path):
            if line.startswith("MemAvailable"):
                mem_kb = int(line.split()[1])
                break
        if mem_kb and mem_kb / 1024**2 < NEED_MEM_GB:
            problems.append(f"可用内存不足：需 ≥{NEED_MEM_GB}GB，当前 {mem_kb/1024**2:.1f}GB")
    except Exception:
        pass                            # 读不到 meminfo 不拦（容器常见），装得动就装
    def _default_probe():
        try:
            urllib.request.urlopen("https://ollama.com/install.sh", timeout=8)
            return True
        except Exception:
            return False
    if not have_ollama and not (net_probe or _default_probe)():
        problems.append("网络不可达 ollama.com（无法下载安装脚本）")
    return problems


def cmd_install_local(args):
    """一键装 ollama + 最小千问并接入。先体检，不达标给明确提示不硬装。"""
    print(f"== 安装前体检（磁盘≥{NEED_DISK_GB}GB / 内存≥{NEED_MEM_GB}GB / 权限 / 网络）==")
    problems = check_local_ready()
    if problems:
        for p in problems:
            print(f"  ✗ {p}")
        print(f"\n{FAIL_MSG}")
        return 1
    print("  ✓ 体检通过")
    if getattr(args, "check_only", False):
        return 0
    # 1) 装 ollama（已装则跳过）
    if not shutil.which("ollama"):
        print("== 安装 ollama（官方脚本）==")
        import tempfile
        import urllib.request
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(urllib.request.urlopen(
                "https://ollama.com/install.sh", timeout=30).read().decode())
            script = f.name
        cmd = ["sh", script] if getattr(os, "geteuid", lambda: 1)() == 0 \
            else ["sudo", "sh", script]
        if subprocess.run(cmd).returncode != 0:
            print(f"ollama 安装失败。\n{FAIL_MSG}")
            return 1
    # 2) 确认服务在跑（systemd 场景安装即启动；容器/无 systemd 则拉起）
    import urllib.request as _u
    def _up():
        try:
            _u.urlopen("http://127.0.0.1:11434/api/tags", timeout=3)
            return True
        except Exception:
            return False
    if not _up():
        print("== 启动 ollama 服务 ==")
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        import time
        for _ in range(20):
            if _up():
                break
            time.sleep(1)
    if not _up():
        print(f"ollama 服务未能启动。\n{FAIL_MSG}")
        return 1
    # 3) 拉最小千问
    print(f"== 下载最小千问模型 {OLLAMA_MODEL}（约 400MB）==")
    if subprocess.run(["ollama", "pull", OLLAMA_MODEL]).returncode != 0:
        print(f"模型下载失败（网络到 ollama 模型库不通？）。\n{FAIL_MSG}")
        return 1
    # 4) 写配置并验证
    _set_active("ollama")
    print(f"== 已接入本地模型（ollama / {OLLAMA_MODEL}），发探针验证 ==")
    return cmd_test(args)


def cmd_pull(args):
    llm.models_dir().mkdir(parents=True, exist_ok=True)
    dst = llm.models_dir() / llm.BUILTIN_MODEL_FILE
    if dst.exists() and not args.force:
        print(f"模型已存在：{dst}（--force 重下）")
    else:
        print(f"下载 {llm.BUILTIN_MODEL_FILE}（≈469MB，ModelScope 直连）→ {dst}")
        import urllib.request
        last = [-1]

        def hook(n, bs, total):
            if total > 0:
                pct = min(100, n * bs * 100 // total)
                if pct // 10 != last[0]:
                    last[0] = pct // 10
                    print(f"  {pct}%", flush=True)
        try:
            urllib.request.urlretrieve(llm.BUILTIN_MODEL_URL, dst, reporthook=hook)
        except urllib.error.URLError as e:
            # python.org 发行版 macOS 常不带系统信任链（CERTIFICATE_VERIFY_FAILED）。
            # 不跳过验证（模型文件走供应链，宁可不下载），先试 certifi 信任库，再给指引。
            import ssl as _s
            try:
                import certifi
                ctx = _s.create_default_context(cafile=certifi.where())
                print("  系统证书缺失，改用 certifi 信任库重试…")
                with urllib.request.urlopen(llm.BUILTIN_MODEL_URL, timeout=60,
                                            context=ctx) as r, open(dst, "wb") as f:
                    got = 0
                    total = int(r.headers.get("Content-Length") or 0)
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        hook(1, got, total)
            except Exception:
                print(f"  ✗ 下载失败：{e}")
                print("  多数是 Python 证书链缺失（python.org 版 macOS 常见）。修复：")
                print("     pip install certifi      # 装好后重跑 opsaxiom model pull")
                print("  或改用系统 Python（/usr/bin/python3）/ Homebrew Python 再试。")
                return 1
        print(f"完成：{dst.stat().st_size >> 20} MB")
    if args.with_deps:
        pkg = "llama-cpp-python[server]" if args.serve_deps else "llama-cpp-python"
        try:
            import llama_cpp  # noqa: F401
            if args.serve_deps:
                import llama_cpp.server  # noqa: F401
            print(f"{pkg} 已装。")
        except Exception:
            print(f"安装 {pkg}（需编译，几分钟）…")
            subprocess.run([sys.executable, "-m", "pip", "install", "cmake"], check=False)
            r = subprocess.run([sys.executable, "-m", "pip", "install", pkg],
                               env={**os.environ, "CMAKE_ARGS": "-DGGML_NATIVE=OFF"})
            print("依赖安装" + ("成功" if r.returncode == 0 else "失败（见上方输出）"))
    ok, msg = probe_builtin()
    print(("🟢 " if ok else "🟡 ") + msg)


# ---------- 首次向导（REPL 首启调用；非 TTY 不问）----------
def first_run_wizard():
    """model.yaml 不存在时问一次。任何选择都落盘（含"不用"→enabled:false，不再重复问）。"""
    print("首次使用：要接一个模型吗？（不接也全功能可用；模型只做理解/叙事/建议）")
    print("  1) 本机小模型（Qwen2.5-0.5B，本机离线跑——未下载时选择可下载安装，约469MB）")
    print("  2) 本地 Ollama    3) 远程 OpenAI 兼容 API    4) Pi 多 provider 网关")
    try:
        ans = input("选择 [回车=先不用，之后可 opsaxiom model use 切]: ").strip()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    choice = {"1": "builtin", "2": "ollama", "3": "remote", "4": "pi"}.get(ans, "off")
    kw = {}
    if choice in ("remote", "pi"):
        try:
            kw["endpoint"] = input("  endpoint（remote 用，回车跳过）: ").strip() or None
            kw["model"] = input("  模型名: ").strip() or None
            kw["api_key"] = input("  api key（回车跳过）: ").strip() or None
            if choice == "pi":
                kw["provider"] = input("  provider [openai]: ").strip() or None
        except (EOFError, KeyboardInterrupt):
            pass
    _set_active(None if choice == "off" else choice)
    if choice in ("remote", "pi"):
        # 带参数的接入落为命名 profile（可复用、可切换）
        cfg = make_config(choice, **kw)
        prof = {k: v for k, v in cfg.items() if v is not None and k != "enabled"}
        prof["kind"] = "openai-compatible" if choice == "remote" else "pi"
        _upsert_profile(f"my-{choice}", prof)
    if choice == "off":
        print("好，先不接（随时 opsaxiom model use <backend> 开启）。")
    else:
        print(f"已配 {choice}。验证：opsaxiom model test" +
              ("；模型未下载的话先 opsaxiom model pull" if choice == "builtin" else ""))


# ---------- 交互态菜单（REPL `model` 裸敲；5 行恒显，状态尾巴动态生成） ----------
_MENU_ROWS = [
    ("remote",  "大模型 API（endpoint/model/api_key）"),
    ("builtin", "本机小模型（Qwen2.5-0.5B）"),
    ("ollama",  "本地 Ollama"),
    ("pi",      "Pi 多 provider 网关"),
    ("off",     "不接模型（关键词匹配模式）"),
]


def _disp_w(s):
    """终端显示宽度：全角/宽字符算 2 列，其余 1 列（str len 会数错全角）。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad_disp(s, w):
    return s + " " * max(0, w - _disp_w(s))


def _menu_status_row(kind, cfg):
    """一行菜单的状态尾巴：按'état'生成，不是静态文案。"""
    doc, _ = _load_doc()
    profiles = doc.get("profiles") or {}
    if kind == "off":
        return "⚪ 当前未接" if cfg is None else "⚪ 未接（当前接的是其他后端）"
    if kind == "remote":
        names = [n for n, c in profiles.items() if c.get("kind") == "openai-compatible"]
        n = len(names)
        if n == 0:
            return "⚪ 已维护 0 个——选择可新建配置并接入"
        cur = "，当前使用 " + doc["active"] if doc.get("active") in names else ""
        return f"🔵 已维护 {n} 个{cur}——选择可切换/新建"
    if kind == "pi":
        ok, msg = probe_pi()
        if not ok:
            return f"🟡 {msg}——选择可查看安装指引" if "npm install" not in msg \
                else f"🟡 {msg}"
        names = [n for n, c in profiles.items() if c.get("kind") == "pi"]
        if not names:
            return "🟢 就绪——选择可接入（向导填 provider/model）"
        cur = "，当前使用 " + doc["active"] if doc.get("active") in names else ""
        return f"🔵 已维护 {len(names)} 个{cur}——选择可切换"
    ok, msg = _probe_name(kind)
    if not ok:
        if kind == "builtin":
            return f"🟡 未下载——选择可下载安装（约 469MB）"
        if kind == "ollama":
            return "🟡 未检测到——选择可查看安装/启动指引"
        return f"🟡 {msg}"
    if doc.get("active") == kind:
        return f"🟢 当前使用"
    return f"🟢 就绪——选择可接入"


def _prompt_remoteProfiles(profiles, kind):
    """列某一类（remote/pi）的命名 profiles 供序号切换。返回名字清单。
    kind 传 "remote" 或 "pi"；remote 档落库 kind 是 "openai-compatible"，
    这里统一换算，别处一律用菜单语义的 "remote"。"""
    kind_key = "openai-compatible" if kind == "remote" else kind
    names = [n for n, c in profiles.items() if c.get("kind") == kind_key]
    if names:
        title = "已维护的大模型 API 配置：" if kind == "remote" else "已维护的 Pi 配置："
        print(f"  {title}")
        for i, n in enumerate(names, 1):
            c = profiles[n]
            key = "api_key: ***" if c.get("api_key") else ""
            if kind == "remote":
                print(f"  {i}) {n}   {c.get('endpoint','')}  {c.get('model','')}  {key}")
            else:
                env = "env 凭据" if not c.get("api_key") else "key: ***"
                print(f"  {i}) {n}   provider={c.get('provider','openai')}"
                      f"  {c.get('model','')}  {env}")
        print("  n) 新建另一份配置    回车=取消")
    elif kind == "remote":
        print("  还没有保存过大模型 API 配置，为你新建一份：")
    else:
        print("  还没有保存过 Pi 配置，为你新建一份（选 provider 和模型，填 API key；")
        print("  也可留空 key 用环境变量，如 export ANTHROPIC_API_KEY=sk-ant-...）：")
    return names


def _profile_detail(name, c, kind):
    """配置序号子菜单的速览行（打码，与清单同一口径）。"""
    if kind == "remote":
        return f"endpoint={c.get('endpoint','')}  model={c.get('model','')}  api_key={'***' if c.get('api_key') else '-'}"
    return f"provider={c.get('provider','openai')}  model={c.get('model','')}  api_key={'***' if c.get('api_key') else '（空→env 凭据）'}"


def _profile_edit(name, kind):
    """编辑一份已维护配置：逐项回显旧值，回车保留；新值直接覆盖。"""
    doc, _ = _load_doc()
    c = doc["profiles"][name]
    fields = (["endpoint", "model", "api_key"] if kind == "remote"
              else ["provider", "model", "api_key"])
    hints = {"endpoint": "（如 https://api.deepseek.com/v1）",
             "model": "（如 deepseek-chat）",
             "provider": f"（如 openai，当前 {c.get('provider','openai')}）",
             "api_key": "（回车=保留现有值）"}
    try:
        print("  逐项修改（回车=保留现值）：")
        for f in fields:
            cur = c.get(f, "")
            shown = "***" if f == "api_key" and cur else (cur or "-")
            nv = input(f"  {f} [{shown}]{hints.get(f, '')}: ").strip()
            if nv:
                c[f] = nv
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消，未做修改。")
        return
    # remote 档 kind/backend 落库名是 openai-compatible；编辑只动字段值不动 kind
    _upsert_profile_keep_active(name, c)
    print(f"  已更新 {name}。验证：model test")


def _profile_remove(name):
    """删除一份配置，y/n 确认（与 cmd_remove 同口径文案）。"""
    try:
        ok = input(f"  确认删除 {name}？[y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ok = ""
    if ok != "y":
        print("  未删除。")
        return
    _remove_profile(name)
    print(f"已删除 {name}（若它原是当前配置，现回到未接模型状态）。")


def _profile_menu(name, kind):
    """从清单选中一份配置后的子菜单：1 切换 / 2 编辑 / 3 删除 / 回车取消。"""
    doc, _ = _load_doc()
    c = doc["profiles"][name]
    cur = "（当前生效）" if doc.get("active") == name else ""
    print(f"  已选 {name} {cur}")
    print(f"    {_profile_detail(name, c, kind)}")
    try:
        print("  1 切换为此配置")
        print("  2 编辑")
        print("  3 删除")
        ans = input("  选择（回车取消）: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans == "1":
        _set_active(name)
        print(f"  已切换为 {name}。验证：model test")
    elif ans == "2":
        _profile_edit(name, kind)
    elif ans == "3":
        _profile_remove(name)
    else:
        print("  未做任何修改。")


def _remote_menu(profiles):
    names = _prompt_remoteProfiles(profiles, "remote")
    try:
        ans = input("  回车开始新建" if not names else
                    "  选择序号进入子菜单（切换/编辑/删除），n 新建，回车取消: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans == "" and names:
        return
    if ans in ("n", "new", "N") or (not names and ans == ""):
        _remote_wizard()
        return
    if ans.isdigit() and 1 <= int(ans) <= len(names):
        _profile_menu(names[int(ans) - 1], "remote")
        return
    print("  未做任何修改。")


def _remote_wizard():
    """新建/更新一份 remote 配置。"""
    try:
        name = input("  配置名称（用于切换，如 corp-gateway）: ").strip()
        if not name:
            print("  已取消（需要名称）。")
            return
        endpoint = input("  endpoint（如 https://api.deepseek.com/v1）: ").strip()
        model = input("  模型名（如 deepseek-chat）: ").strip()
        api_key = input("  api key: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消。")
        return
    if not endpoint or not api_key:
        print("  endpoint 和 api key 是必填项，已取消。")
        return
    cfg = {"kind": "openai-compatible", "backend": "openai-compatible",
           "endpoint": endpoint, "model": model or "", "api_key": api_key}
    _upsert_profile(name, cfg)
    print(f"  已保存并启用 {name}。验证：model test")


def _pi_wizard():
    """新建/更新一份 Pi 配置：provider + model + API key（可空→env 回落）。"""
    presets = {
        "1": ("openai", "gpt-4o-mini"),
        "2": ("anthropic", "claude-sonnet-4-5"),
        "3": ("google", "gemini-2.5-flash"),
        "4": (None, None),                      # 自定义
    }
    print("  选择 provider（pi 会按 provider 走各家原生协议）:")
    print("    1) openai    2) anthropic（Claude）    3) google（Gemini）    4) 自定义")
    try:
        pick = input("  provider [回车=1 openai]: ").strip()
        provider, default_model = presets.get(pick, presets["1"])
        if provider is None:                    # 自定义：手工输
            provider = input("  provider id（如 openrouter）: ").strip()
            default_model = ""
        model = input(f"  模型名（回车={default_model}）: ").strip() or default_model
        api_key = input("  api key（回车=留空，走环境变量）: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消。")
        return
    if not model:
        print("  需要模型名，已取消。")
        return
    name = input("  配置名称（用于切换，如 anthropic-主档）: ").strip()
    if not name:
        print("  已取消（需要名称）。")
        return
    cfg = {"kind": "pi", "backend": "pi", "provider": provider or "openai",
           "model": model}
    if api_key:
        cfg["api_key"] = api_key
    _upsert_profile(name, cfg)
    tail = "验证：model test" if api_key else \
        f"验证：model test（当前 shell 需 export {str(cfg['provider']).upper()}_API_KEY）"
    print(f"  已保存并启用 {name}。{tail}")


def _builtin_menu():
    ok, msg = probe_builtin()
    if ok:
        _activate_singleton("builtin")
        print(f"  已切换为 builtin（{llm.BUILTIN_MODEL_FILE}）。验证：model test")
        return
    big = 469
    try:
        ans = input(f"  本机小模型未下载（约 {big}MB + 编译依赖几分钟），现在下载？[y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans in ("y", "yes"):
        import argparse as _a
        ns = _a.Namespace(force=False, with_deps=True, serve_deps=False)
        cmd_pull(ns)
        ok2, msg2 = probe_builtin()
        if ok2:
            _activate_singleton("builtin")
            print("  已切换为 builtin。")
        else:
            print(f"  ⚠ 装好后重新 model → builtin 即可接入：{msg2}")
    else:
        print("  已取消。之后可随时 opsaxiom model pull --with-deps 下载。")


def _ollama_menu():
    ok, msg = probe_ollama()
    if ok:
        _activate_singleton("ollama")
        print("  已切换为 ollama。验证：model test")
        return
    have_cmd = bool(shutil.which("ollama"))
    if have_cmd:
        print("  本机已装 ollama，但服务未响应。到终端执行：")
        print("     ollama serve            # 启动服务（保持窗口开着）")
        print("     ollama pull qwen2.5:7b  # 首次还需拉一个模型（约 4.7GB）")
    else:
        print("  本机未安装 Ollama。到终端执行（官方脚本，Linux/macOS）：")
        print("     curl -fsSL https://ollama.com/install.sh | sh")
    print("  装好/启动后回来重跑 model → ollama，即可自动接入。")


def _pi_menu(profiles):
    names = [n for n, c in (profiles or {}).items() if c.get("kind") == "pi"]
    if names:
        # 已有存档：序号进子菜单 / n 新建（与 remote 子菜单同款交互）
        _prompt_remoteProfiles(profiles, "pi")
        try:
            ans = input("  选择序号进入子菜单（切换/编辑/删除），n 新建，回车取消: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if ans.isdigit() and 1 <= int(ans) <= len(names):
            _profile_menu(names[int(ans) - 1], "pi")
            return
        if ans in ("n", "new", "N"):
            _pi_wizard()
            return
        print("  未做任何修改。")
        return
    ok, msg = probe_pi()
    node = shutil.which("node")
    if ok:
        # 环境就绪但还没有存档：引导走向导（选 provider/模型，可留空 key）
        print("  Pi 网关就绪（node + pi-ai 已可用的检测通过），为你新建一份配置：")
        _pi_wizard()
        return
    print("  Pi 需要两样东西，当前探测：")
    print(f"  ① node ≥ 22.19 ：{'未安装' if not node else '已装 ' + node}")
    print("  ② @earendil-works/pi-ai 库：未安装" if ok is False and not msg.startswith("就绪") else f"  ② @earendil-works/pi-ai 库：{msg}")
    if not node:
        print("  缺 node 时到终端执行（macOS）：")
        print("     brew install node@22")
        print("  （Linux 用发行版包管理器，或官网下载：https://nodejs.org/）")
    print("  node 就绪后到终端执行：")
    print("     npm install --prefix ~/.local/pi-agent @earendil-works/pi-ai")
    print("  装好后回来重跑 model → pi，即可自动接入。")


def cmd_menu(args=None):
    """REPL `model` 裸敲入口。5 行恒显 + 选择进入分支。非 TTY 直接走 show。"""
    if not sys.stdin.isatty():
        return cmd_show(args)
    cfg = llm.load_config()
    print(f"配置文件: {llm.config_path()}  {'(存在)' if llm.config_path().exists() else '(不存在)'}")
    if cfg:
        pname = cfg.get("_profile", cfg.get("backend", "")) or "未命名"
        print(f"当前: {pname}（{cfg.get('backend','')}）")
    else:
        print("当前: 未接模型（全功能可用）")
    print("请选择指令：")
    label_w = max(_disp_w(lb) for _, lb in _MENU_ROWS)
    for i, (kind, label) in enumerate(_MENU_ROWS, 1):
        print(f"  {i} model {kind:<8} " + _pad_disp(label, label_w) + "  " + _menu_status_row(kind, cfg))
    try:
        ans = input("选择序号（回车取消）: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    doc, _ = _load_doc()
    profiles = doc.get("profiles") or {}
    pick = {"1": "remote", "2": "builtin", "3": "ollama", "4": "pi", "5": "off"}.get(ans)
    if pick == "remote":
        _remote_menu(profiles)
    elif pick == "builtin":
        _builtin_menu()
    elif pick == "ollama":
        _ollama_menu()
    elif pick == "pi":
        _pi_menu(profiles)
    elif pick == "off":
        _set_active(None)
        print("  已断开模型。系统所有功能正常（关键词匹配模式）。")
    else:
        print("  未做任何修改。")
    return 0


# ---------- switch / list / remove（多配置管理的命令面） ----------
def cmd_switch(args):
    doc, _ = _load_doc()
    name = args.name
    if name not in doc["profiles"]:
        print(f"没有名为 {name} 的配置。model list 看已维护清单。")
        return 1
    _set_active(name)
    print(f"已切换为 {name}。验证：opsaxiom model test")
    return 0


def cmd_list(args):
    doc, _ = _load_doc()
    act = doc.get("active")
    if not doc["profiles"]:
        print("尚未维护任何命名配置（remote/pi 的多配置管理）。用 model use <backend> --name <名> 新建。")
        return 0
    print("已维护配置：")
    for n, c in doc["profiles"].items():
        key = "***" if c.get("api_key") else "-"
        mark = "← 当前" if n == act else ""
        print(f"  {n:<20} {c.get('kind','?'):<18} {c.get('endpoint','')}  模型:{c.get('model','-') or '-'}  key:{key} {mark}")
    return 0


def cmd_remove(args):
    if _remove_profile(args.name):
        print(f"已删除 {args.name}（若它原是当前配置，现回到未接模型状态）。")
    else:
        print(f"没有名为 {args.name} 的配置。model list 看已维护清单。")
    return 0


# ---------- argparse 挂载（REPL _delegate 与主 CLI 共用）----------
def add_model(sub):
    ap = sub.add_parser("model", help="配置/测试 LLM 后端")
    s2 = ap.add_subparsers(dest="model_cmd")
    p = s2.add_parser("show");  p.set_defaults(fn=cmd_show)
    p = s2.add_parser("use")
    p.add_argument("backend", choices=BACKENDS)
    p.add_argument("--endpoint"); p.add_argument("--model")
    p.add_argument("--api-key", dest="api_key"); p.add_argument("--provider")
    p.add_argument("--name", help="remote/pi 落为命名配置（多配置管理，配合 switch）")
    p.set_defaults(fn=cmd_use)
    p = s2.add_parser("switch", help="切换到已维护的命名配置")
    p.add_argument("name")
    p.set_defaults(fn=cmd_switch)
    p = s2.add_parser("list", help="列出已维护配置")
    p.set_defaults(fn=cmd_list)
    p = s2.add_parser("remove", help="删除一份已维护配置")
    p.add_argument("name")
    p.set_defaults(fn=cmd_remove)
    p = s2.add_parser("test"); p.set_defaults(fn=cmd_test)
    p = s2.add_parser("pull")
    p.add_argument("--force", action="store_true")
    p.add_argument("--with-deps", dest="with_deps", action="store_true")
    p.add_argument("--serve-deps", dest="serve_deps", action="store_true",
                   help="连 server 依赖一起装（供 model serve / pi 入口用）")
    p.set_defaults(fn=cmd_pull)
    p = s2.add_parser("serve")
    p.add_argument("--port", type=int, default=11435)
    p.set_defaults(fn=cmd_serve)
    p = s2.add_parser("install-local",
                      help=f"一键装 ollama + 最小千问（{OLLAMA_MODEL}），先体检依赖与空间")
    p.add_argument("--check-only", dest="check_only", action="store_true",
                   help="只体检不安装")
    p.set_defaults(fn=cmd_install_local)
    ap.set_defaults(fn=cmd_menu)     # 裸 `model` = 交互菜单（非 TTY 自动落 show）
