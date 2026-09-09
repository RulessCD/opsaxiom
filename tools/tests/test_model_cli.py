"""M-1/M-2/M-3 测试：builtin/pi 后端 + model CLI 配置往返 + 降级诚实性。

真模型推理不进 CI（模型文件 469MB 可能不存在）——builtin 用假 llama_cpp 模块注入；
pi 桥用假 subprocess。真机端到端另有冒烟（见 HANDOFF，人工/演示跑）。
"""
import json
import pathlib
import sys
import types

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sim"))
import llm        # noqa: E402
import model_cli  # noqa: E402


# ---------- builtin 后端 ----------
def test_builtin_no_module_degrades(monkeypatch, tmp_path):
    """llama_cpp 未装 → None（降级），不炸。"""
    monkeypatch.setitem(sys.modules, "llama_cpp", None)  # import 得到 None → 触发异常路径
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    out = llm._builtin_call({"backend": "builtin"}, "p", "s")
    assert out is None


def test_builtin_missing_model_file_degrades(monkeypatch, tmp_path):
    """llama_cpp 可导入但模型文件缺失 → None。"""
    fake = types.ModuleType("llama_cpp")
    fake.Llama = object
    monkeypatch.setitem(sys.modules, "llama_cpp", fake)
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))   # 空目录，无 gguf
    assert llm._builtin_call({"backend": "builtin"}, "p", "s") is None


def test_builtin_with_fake_llama(monkeypatch, tmp_path):
    """注入假 Llama：整链（路径解析→加载→chat）走通并返回文本。"""
    gguf = tmp_path / "models" / llm.BUILTIN_MODEL_FILE
    gguf.parent.mkdir(parents=True)
    gguf.write_bytes(b"GGUF-fake")
    calls = {}

    class FakeLlama:
        def __init__(self, model_path, **kw):
            calls["path"] = model_path

        def create_chat_completion(self, messages, **kw):
            calls["sys"] = messages[0]["content"]
            return {"choices": [{"message": {"content": '{"params":{"mount":"/data"}}'}}]}

    fake = types.ModuleType("llama_cpp")
    fake.Llama = FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake)
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    llm._BUILTIN_CACHE.clear()
    out = llm._builtin_call({"backend": "builtin"}, "磁盘满了", "抽实体")
    assert out and "mount" in out
    assert calls["path"] == str(gguf)
    # 且 intake 端到端消化它（白名单校验照旧生效）
    r = llm.intake("磁盘满了", config={"backend": "builtin"})
    assert r["params"] == {"mount": "/data"} and not r["degraded"]
    llm._BUILTIN_CACHE.clear()


# ---------- pi 后端 ----------
def test_pi_call_parses_bridge_output(monkeypatch):
    def fake_run(cmd, **kw):
        assert cmd[0] == "node" and cmd[1].endswith("pi_bridge.mjs")
        req = json.loads(kw["input"])
        assert req["provider"] == "openai" and req["system"] == "s"
        return types.SimpleNamespace(returncode=0,
                                     stdout=json.dumps({"text": "hello"}), stderr="")
    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = llm._pi_call({"backend": "pi", "provider": "openai", "model": "gpt-4o-mini"},
                       "p", "s")
    assert out == "hello"


def test_pi_call_bridge_error_degrades(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=2, stdout='{"error":"x"}', stderr=""))
    assert llm._pi_call({"backend": "pi"}, "p", "s") is None


def test_pi_call_no_node_degrades(monkeypatch):
    import subprocess
    def boom(*a, **k):
        raise FileNotFoundError("node")
    monkeypatch.setattr(subprocess, "run", boom)
    assert llm._pi_call({"backend": "pi"}, "p", "s") is None


# ---------- model CLI 配置往返 ----------
def test_make_config_variants():
    assert model_cli.make_config("off") == {"enabled": False}
    b = model_cli.make_config("builtin")
    assert b["enabled"] and b["backend"] == "builtin" and b["model_path"].endswith(".gguf")
    r = model_cli.make_config("remote", endpoint="http://x/v1", model="m", api_key="k")
    assert r["backend"] == "openai-compatible" and r["api_key"] == "k"
    p = model_cli.make_config("pi", provider="anthropic", model="claude")
    assert p["backend"] == "pi" and p["provider"] == "anthropic"


def test_use_writes_and_load_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    model_cli._write_cfg(model_cli.make_config("ollama", model="qwen2.5:3b"))
    cfg = llm.load_config()
    assert cfg and cfg["backend"] == "ollama" and cfg["model"] == "qwen2.5:3b"
    # off → load_config 返回 None（= 未接模型，全走降级）
    model_cli._write_cfg(model_cli.make_config("off"))
    assert llm.load_config() is None


def test_off_config_means_wizard_wont_reask(monkeypatch, tmp_path):
    """向导任何选择都落盘：off 也写文件 → 文件存在 → REPL 不再问。"""
    monkeypatch.setenv("OPSAXIOM_HOME", str(tmp_path))
    model_cli._write_cfg(model_cli.make_config("off"))
    assert llm.config_path().exists()
    d = yaml.safe_load(llm.config_path().read_text())
    assert d == {"enabled": False}


# ---------- install-local 体检（发起人需求：装前查依赖与空间） ----------
def _du(free_gb):
    import collections
    DU = collections.namedtuple("du", "total used free")
    return lambda p: DU(100 * 1024**3, 0, int(free_gb * 1024**3))


def test_check_local_ready_all_pass(tmp_path):
    mem = tmp_path / "meminfo"
    mem.write_text("MemTotal: 8000000 kB\nMemAvailable: 4000000 kB\n")
    probs = model_cli.check_local_ready(
        disk_usage=_du(50), meminfo_path=str(mem),
        which=lambda x: "/usr/bin/" + x,          # ollama/sudo 都有
        geteuid=lambda: 1000, net_probe=lambda: True,
        system="Linux")                           # 平台注入（macOS 开发机也能全绿）
    assert probs == []


def test_check_local_ready_no_root_no_sudo(tmp_path):
    mem = tmp_path / "meminfo"
    mem.write_text("MemAvailable: 4000000 kB\n")
    probs = model_cli.check_local_ready(
        disk_usage=_du(50), meminfo_path=str(mem),
        which=lambda x: None,                     # 无 ollama 也无 sudo
        geteuid=lambda: 1000, net_probe=lambda: True)
    assert any("root 或 sudo" in p for p in probs)


def test_check_local_ready_low_disk(tmp_path):
    mem = tmp_path / "meminfo"
    mem.write_text("MemAvailable: 4000000 kB\n")
    probs = model_cli.check_local_ready(
        disk_usage=_du(1.2), meminfo_path=str(mem),
        which=lambda x: "/usr/bin/" + x, geteuid=lambda: 0,
        net_probe=lambda: True)
    assert any("磁盘空间不足" in p for p in probs)


def test_check_local_ready_low_mem(tmp_path):
    mem = tmp_path / "meminfo"
    mem.write_text("MemAvailable: 500000 kB\n")   # ~0.5GB
    probs = model_cli.check_local_ready(
        disk_usage=_du(50), meminfo_path=str(mem),
        which=lambda x: "/usr/bin/" + x, geteuid=lambda: 0,
        net_probe=lambda: True)
    assert any("内存不足" in p for p in probs)


def test_check_local_ready_net_down(tmp_path):
    mem = tmp_path / "meminfo"
    mem.write_text("MemAvailable: 4000000 kB\n")
    probs = model_cli.check_local_ready(
        disk_usage=_du(50), meminfo_path=str(mem),
        which=lambda x: "/usr/bin/sudo" if x == "sudo" else None,   # 无 ollama
        geteuid=lambda: 1000, net_probe=lambda: False)
    assert any("ollama.com" in p for p in probs)


def test_check_local_ready_ollama_installed_skips_net_and_root(tmp_path):
    """已装 ollama：不再要求 root/网络（只需空间与内存）。"""
    mem = tmp_path / "meminfo"
    mem.write_text("MemAvailable: 4000000 kB\n")
    probs = model_cli.check_local_ready(
        disk_usage=_du(50), meminfo_path=str(mem),
        which=lambda x: "/usr/bin/ollama" if x == "ollama" else None,
        geteuid=lambda: 1000, net_probe=lambda: False,
        system="Linux")
    assert probs == []
