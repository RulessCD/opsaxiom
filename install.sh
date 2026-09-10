#!/usr/bin/env bash
# OpsAxiom 一键安装（V-1）。目标：10 分钟内可用。
#   ./install.sh              # 有网/内网源：创建 venv、装依赖、软链、初始化、自检
#   ./install.sh --offline    # 气隙：从 vendor/wheels 装依赖，不出网
#   ./install.sh --prefix ~/.local/bin   # 软链目标目录（默认 ~/.local/bin）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OFFLINE=0
PREFIX="${HOME}/.local/bin"
while [ $# -gt 0 ]; do
  case "$1" in
    --offline) OFFLINE=1; shift ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

echo "==> OpsAxiom 安装（root=${ROOT}, offline=${OFFLINE}）"

# 1) Python 检查
command -v python3 >/dev/null || { echo "🔴 需要 python3" >&2; exit 1; }
PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "==> python $PYV"

# Python 版本检查
_PY_MAJOR=$(python3 -c 'import sys;print(sys.version_info[0])')
_PY_MINOR=$(python3 -c 'import sys;print(sys.version_info[1])')
if [ "$_PY_MAJOR" -lt 3 ] || { [ "$_PY_MAJOR" -eq 3 ] && [ "$_PY_MINOR" -lt 9 ]; }; then
  if [ "$OFFLINE" -eq 1 ]; then
    # 包内 wheels 按 Python ≥3.9 收集（上游编译型包已停发 3.8 wheel），3.8 物理装不上
    echo "🔴 Python $PYV 无法离线安装：离线包内 wheels 按 Python ≥3.9 打包。" >&2
    echo "   先升级 Python 再执行本命令；系统另有 python3.9+ 时：改用该解释器（如" >&2
    echo "   python3.9 ./install.sh --offline）或调整 PATH 后重试。" >&2
    exit 1
  fi
  echo "🟡 Python $PYV 较旧，部分依赖可能无法安装。建议升级到 3.9+。"
fi

# macOS: 检测 Command Line Tools 是否安装（/usr/bin/python3 占位桩拦截）
if [ "$(uname)" = "Darwin" ] && [ "$(which python3)" = "/usr/bin/python3" ]; then
  if ! xcode-select -p >/dev/null 2>&1; then
    echo "🔴 macOS: 需要安装 Command Line Tools（xcode-select --install）" >&2
    exit 1
  fi
fi

# 2) venv + 依赖
VENV="$ROOT/.venv"
_VENV_ERR=$(mktemp)
python3 -m venv "$VENV" 2>"$_VENV_ERR" || {
  echo "🔴 创建虚拟环境失败。"
  if grep -q "ensurepip" "$_VENV_ERR" 2>/dev/null; then
    echo "   原因：当前系统将 Python 3 拆分为多个包，venv 模块未包含在默认安装中。"
    echo "   常见于 Debian / Ubuntu，执行以下命令后重试 install.sh："
    echo "     sudo apt install python3-venv"
    echo "   其他发行版请用对应的包管理安装 python3-venv 等效包。"
  else
    echo "   错误详情："; cat "$_VENV_ERR"
    echo "   请提交 issue 附上完整安装日志。"
  fi
  rm -f "$_VENV_ERR"
  exit 1
}
rm -f "$_VENV_ERR"
# shellcheck disable=SC1091
. "$VENV/bin/activate"
if [ "$OFFLINE" -eq 1 ]; then
  # 离线包只带 linux_x86_64 wheels（发起人裁定 2026-09-10：气隙目标机=Linux 服务器）
  if [ "$(uname -s)" != "Linux" ]; then
    echo "🔴 离线包只含 Linux x86_64 的 wheels，本机（$(uname -s)-$(uname -m)）不适用。" >&2
    echo "   本机请用在线安装：./install.sh" >&2
    exit 1
  fi
  WHEELS="$ROOT/vendor/wheels/linux_x86_64"
  [ -d "$WHEELS" ] || WHEELS="$ROOT/vendor/wheels"   # 兼容旧布局（单目录）
  echo "==> 离线装依赖（wheels/linux_x86_64，本机 $(uname -m)）"
  "$VENV/bin/pip" install --no-index --find-links "$WHEELS" -r "$ROOT/tools/requirements.txt" \
    || echo "🟡 离线依赖不全，核心功能仍可用（缺 cryptography 时 attest 降级 HMAC）"
  # registry 快照就位（离线包自带 Skill 库；有网安装跳过，走 hub sync）
  if [ -f "$ROOT/vendor/registry/index.json" ] || [ -f "$ROOT/registry/index.json" ]; then
    REGSRC="$ROOT/vendor/registry"; [ -f "$ROOT/registry/index.json" ] && REGSRC="$ROOT/registry"
    "$VENV/bin/python" "$ROOT/tools/bin/opsaxiom" hub init "$REGSRC" >/dev/null 2>&1 \
      && echo "==> Skill 库快照就位（离线 registry：$REGSRC）" \
      || echo "🟡 registry 快照接入失败（不阻断；有网后 opsaxiom hub sync 可补）"
  fi
else
  echo "==> 在线装依赖"
  pip install --upgrade pip -q || true
  pip install -r "$ROOT/tools/requirements.txt" \
    || echo "🟡 部分依赖装失败，doctor 会指出影响面"
fi

# 3) 安装 opsaxiom-* 到 PATH（wrapper 脚本，确保始终使用 venv 的 python）
mkdir -p "$PREFIX"
for t in "$ROOT"/tools/bin/opsaxiom*; do
  name="$(basename "$t")"
  # wrapper: exec 本 venv 的 python 执行原脚本，避免系统 python 找不到依赖
  cat > "$PREFIX/$name" <<WRAPPER_EOF
#!/bin/sh
exec "$VENV/bin/python3" "$t" "\$@"
WRAPPER_EOF
  chmod +x "$PREFIX/$name"
done
echo "==> 已安装到 ${PREFIX}：$(ls "$ROOT"/tools/bin/opsaxiom* | xargs -n1 basename | tr '\n' ' ')"
case ":$PATH:" in
  *":$PREFIX:"*) : ;;
  *) echo "🟡 ${PREFIX} 不在 PATH。请将以下行加入 ~/.zshrc 或 ~/.bashrc 后重启终端："
     echo "   export PATH=\"${PREFIX}:\$PATH\"" ;;
esac

# 4) 初始化 ~/.opsaxiom + 生成签名密钥
OPS_HOME="${OPSAXIOM_HOME:-$HOME/.opsaxiom}"
mkdir -p "$OPS_HOME/sessions" "$OPS_HOME/keys"
"$VENV/bin/python" "$ROOT/tools/bin/opsaxiom-attest" --keygen >/dev/null 2>&1 \
  || echo "🟡 签名密钥生成失败（不影响核心功能，attest 将降级 HMAC）"
echo "==> 初始化 ${OPS_HOME}（会话/密钥目录已建）"

# 5) doctor 自检
echo "==> 运行 doctor 自检"
"$VENV/bin/python" "$ROOT/tools/bin/opsaxiom" doctor || {
  echo "🔴 doctor 报告必需项未通过，请按上方提示修复。"; exit 1; }

# 6) 同步社区 Skill 库（离线模式已用包内快照装好，跳过在线同步）
if [ "$OFFLINE" -eq 1 ] && { [ -f "$ROOT/vendor/registry/index.json" ] || [ -f "$ROOT/registry/index.json" ]; }; then
  echo "==> Skill 库用离线快照（跳过在线同步）"
else
  echo "==> 同步社区 Skill 库"
  "$VENV/bin/python" "$ROOT/tools/bin/opsaxiom" hub sync 2>/dev/null || \
    echo "🟡 网络不可用，Skill 库同步跳过。启动 opsaxiom 后执行 hub sync 即可获取。"
fi

echo
echo "✅ 安装完成。直接输入 opsaxiom 进入交互态，描述你的问题即可。"
echo "   可选：接一个模型让它更懂人话（不接也全功能可用）——"
echo "        opsaxiom model pull --with-deps   # 内置千问 0.5B（本机离线，≈469MB）"
echo "        opsaxiom model show               # 或看 ollama/远程API/pi 怎么接"
