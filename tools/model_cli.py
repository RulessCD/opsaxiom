"""
opsaxiom model —— 模型配置 CLI（M-2）。

四个动作 + 首次向导（REPL 调用）：
  model show            当前配置与各后端健康状态（诚实：差什么直说）
  model use <backend>   一键切换/写 model.yaml（builtin/ollama/remote/pi/off）
  model test            发一条真实探针走 intake，打印结果或降级原因
  model pull            下载内置小模型（千问 Qwen2.5-0.5B GGUF，ModelScope 直连）
                        并（可选 --with-deps）安装 llama-cpp-python

设计边界：模型永远只做理解/叙事/建议（llm.py 三调用点），这里只管"接哪个模型"。
所有后端不可用时系统全功能可用（降级链），所以 use/off 都是安全操作。
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


def _write_cfg(cfg):
    p = llm.config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return p


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
    r = subprocess.run([node, "-e",
                        "import('@earendil-works/pi-ai/providers/all')"
                        ".then(()=>process.exit(0),()=>process.exit(1))"],
                       capture_output=True, timeout=15, cwd=str(HERE))
    if r.returncode != 0:
        return False, "缺 @earendil-works/pi-ai（npm install @earendil-works/pi-ai）"
    return True, f"就绪：node {v} + pi-ai"


# ---------- 动作 ----------
def cmd_show(args):
    cfg = llm.load_config()
    p = llm.config_path()
    print(f"配置文件: {p}  {'(存在)' if p.exists() else '(不存在)'}")
    if cfg is None:
        print("当前: 未接模型（全走降级：关键词匹配 + 模板叙事——全功能可用）")
    else:
        shown = {k: ("***" if k == "api_key" else v) for k, v in cfg.items()}
        print(f"当前: {shown}")
    print("\n各后端可用性：")
    ok, msg = probe_builtin()
    print(f"  {'🟢' if ok else '🟡'} builtin  内置小模型(Qwen2.5-0.5B)  {msg}")
    ok, msg = probe_ollama(cfg if cfg and cfg.get('backend') == 'ollama' else None)
    print(f"  {'🟢' if ok else '🟡'} ollama   本地 Ollama              {msg}")
    print("  ⚪ remote   OpenAI 兼容远程 API       填 endpoint/model/api_key 即用")
    ok, msg = probe_pi()
    print(f"  {'🟢' if ok else '🟡'} pi       Pi 多 provider 网关       {msg}")
    print("\n切换：opsaxiom model use builtin|ollama|remote|pi|off  [--endpoint/--model/--api-key/--provider]")


def cmd_use(args):
    cfg = make_config(args.backend, endpoint=args.endpoint, model=args.model,
                      api_key=args.api_key, provider=args.provider)
    p = _write_cfg(cfg)
    if args.backend == "off":
        print(f"已关闭模型（{p}）。系统全功能可用（降级链）。")
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
    print(f"后端: {cfg.get('backend')} · 发真实探针（intake：从陈述抽实体）…")
    r = llm.intake("主机 web-01 的 /data 磁盘满了", config=cfg)
    if r.get("degraded"):
        print("✘ 未通（已降级）。可能：后端不可达/模型未就绪/输出不合法。opsaxiom model show 看缺口。")
        return 1
    print(f"✔ 通。抽到实体: params={r['params']} entities={r['entities'][:5]}")
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
    _write_cfg(make_config("ollama", model=OLLAMA_MODEL))
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
        urllib.request.urlretrieve(llm.BUILTIN_MODEL_URL, dst, reporthook=hook)
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
    print("  1) 内置小模型（千问 0.5B，本机离线跑——需先 model pull 下载）")
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
    _write_cfg(make_config(choice, **kw))
    if choice == "off":
        print("好，先不接（随时 opsaxiom model use <backend> 开启）。")
    else:
        print(f"已配 {choice}。验证：opsaxiom model test" +
              ("；模型未下载的话先 opsaxiom model pull" if choice == "builtin" else ""))


# ---------- argparse 挂载（REPL _delegate 与主 CLI 共用）----------
def add_model(sub):
    ap = sub.add_parser("model", help="配置/测试 LLM 后端")
    s2 = ap.add_subparsers(dest="model_cmd")
    p = s2.add_parser("show");  p.set_defaults(fn=cmd_show)
    p = s2.add_parser("use")
    p.add_argument("backend", choices=BACKENDS)
    p.add_argument("--endpoint"); p.add_argument("--model")
    p.add_argument("--api-key", dest="api_key"); p.add_argument("--provider")
    p.set_defaults(fn=cmd_use)
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
    ap.set_defaults(fn=cmd_show)     # 裸 `model` = show
