#!/usr/bin/env bash
# OpsAxiom 离线包打包器（#13）。在有网的机器上跑，产出 opsaxiom-offline-<版本>.tar.gz。
#
#   ./pack-offline.sh                      # 打 linux_x86_64 离线包（气隙目标机=Linux 服务器）
#   ./pack-offline.sh --with-model         # 捎带内置 GGUF 小模型（+469MB，默认不打）
#   ./pack-offline.sh --out /tmp/pkgs      # 产物目录（默认 ./pack-output）
#
# 产物：仓库全量快照（不含 .venv/.git）+ wheels/linux_x86_64/ + registry 快照 + model/（可选）
# 气隙侧：tar xzf → ./install.sh --offline（仅适用 Linux x86_64，Python 3.9~3.12）
#
# 产物不进 git（属于发布物，发起人裁定 2026-09-10：本地自产自摆渡，不放 Release）；
# 脚本与文档进 git。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${ROOT}/pack-output"
WITH_MODEL=0
ONLY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --with-model) WITH_MODEL=1; shift ;;
    --out) OUT="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

# 平台集（发起人裁定 2026-09-10：只做 linux_x86_64——气隙目标机是 Linux 服务器；
# Mac 开发机在线装即可，不进离线包。win64 不做。）
# Python 版本集（Fable 评审返工 + 发起人裁定 2026-09-10"多版本收 wheel"）：
#   abi3/纯 py wheel 天然通用；严格 ABI 的编译型 wheel（pyyaml/cffi/rpds-py，
#   实测仅此 3 个）按 3.9/3.10/3.11/3.12 各收一份，pip 安装时按目标机版本自动选。
#   支持口径 = 3.9~3.12：3.13 上游生态未稳且气隙机几乎不预装，不收不承诺；
#   3.8 及以下编译 wheel 上游停发，红停。各版本独立跑 pip download（cffi 在
#   cp39/cp310+ 解析出的版本不同，不能只换标签复用一份）。
PLATFORMS="linux_x86_64"
PYVERS="3.9 3.10 3.11 3.12"
[ -n "${ONLY:-}" ] && PLATFORMS="$ONLY"

args_for() {
  # $1=平台 $2=python 版本 → pip download 平台参数
  case "$1" in
    linux_x86_64) echo "--python-version $2 --platform manylinux2014_x86_64 --implementation cp" ;;
    *) echo "未知平台：$1（可选 linux_x86_64）" >&2; return 1 ;;
  esac
}

VER=$(python3 - <<'PY'
import pathlib, re
for m in re.finditer(r'v[0-9]+\.[0-9.]+', pathlib.Path("README.md").read_text(encoding="utf-8")):
    print(m.group(0)); break
PY
)
VER="${VER:-v0.1}"

echo "==> 打离线包：版本=${VER} 平台=[${PLATFORMS}] with_model=${WITH_MODEL}"
OUT="${OUT}"
mkdir -p "$OUT"
PKGDIR="${OUT}/stage"

# ---- 仓库代码（不含 .venv/.git/本地产物）——先铺底，wheels/registry 随后添进 vendor/ ----
SRCDIR="${PKGDIR}/opsaxiom"
rm -rf "$SRCDIR"
mkdir -p "$SRCDIR"
tar -C "$ROOT" \
  --exclude .git --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  --exclude .claude --exclude .agents --exclude .DS_Store \
  --exclude "pack-output" --exclude "dist" \
  --exclude "需求梳理" --exclude "nouse_需求梳理" \
  --exclude skills-community --exclude skills-drafts \
  -cf - . | tar -C "$SRCDIR" -xf -
echo "==> 仓库快照就位（不含 .git/.venv）"

for PLAT in $PLATFORMS; do
  echo "==> [${PLAT}] 下载 wheels（Python ${PYVERS}）"
  WDIR="${PKGDIR}/opsaxiom/vendor/wheels/${PLAT}"
  mkdir -p "$WDIR"
  for PV in $PYVERS; do
    PLAT_ARGS="$(args_for "$PLAT" "$PV")"
    # shellcheck disable=SC2086
    python3 -m pip download -r "${ROOT}/tools/requirements.txt" \
      -d "$WDIR" --only-binary=:all: $PLAT_ARGS 2>&1 | \
      { grep -iv "File was already downloaded" || true; } \
      || { echo "🔴 wheel 下载失败：${PLAT} py${PV}" >&2; exit 1; }
  done
  # 同名 wheel（纯 py/abi3 各版本重复下载）pip 自动跳过（File was already
  # downloaded）；严格 ABI 的编译 wheel 每版本文件名不同，天然共存。
  echo "==> [${PLAT}] $(ls "$WDIR" | wc -l | tr -d ' ') 个 wheel（${PYVERS}），$(du -sh "$WDIR" | cut -f1)"
done

# ---- 模型（可选，默认不打——发起人裁定，离线包保持轻量）----
if [ "$WITH_MODEL" -eq 1 ]; then
  echo "==> 下载内置小模型（千问 0.5B GGUF ≈469MB，ModelScope）"
  GGUF="qwen2.5-0.5b-instruct-q4_k_m.gguf"
  URL="https://modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/master/${GGUF}"
  mkdir -p "${PKGDIR}/opsaxiom/vendor/model"
  curl -L --fail -o "${PKGDIR}/opsaxiom/vendor/model/${GGUF}" "$URL" \
    || { echo "🔴 模型下载失败（不影响其余部分；去掉 --with-model 重打）" >&2; exit 1; }
fi

# ---- registry 快照（Skill 库，~2.4M）----
REG="${OPSAXIOM_HOME:-$HOME/.opsaxiom}/hub/registry"
if [ -f "${REG}/index.json" ]; then
  mkdir -p "${PKGDIR}/opsaxiom/vendor/registry"
  rsync -a --exclude .git "${REG}/" "${PKGDIR}/opsaxiom/vendor/registry/"
  N=$(python3 -c "import json;print(len(json.load(open('${REG}/index.json'))))" 2>/dev/null || echo '?')
  echo "==> registry 快照就位（${N} 个 Skill）"
else
  echo "🟡 本机无 registry 快照，包内不带 Skill 库——先在有网机 opsaxiom hub sync 后重打。"
fi

# ---- 压缩（单平台包，目标机=Linux 服务器）----
TARNAME="opsaxiom-offline-${VER}.tar.gz"
tar -C "$OUT" -czf "${OUT}/${TARNAME}" "$(basename "$PKGDIR")"
TAR_SIZE=$(du -sh "${OUT}/${TARNAME}" | cut -f1)

echo
echo "✅ 离线包就绪：${OUT}/${TARNAME}（${TAR_SIZE}）"
echo "   内容：opsaxiom/（仓库快照 + wheels/linux_x86_64 + registry/$( [ $WITH_MODEL -eq 1 ] && echo ' + model/' )）"
echo
echo "── 目标机（气隙）安装操作 ──────────────────────────"
echo "  1) tar xzf ${TARNAME}"
echo "  2) cd $(basename "$PKGDIR")/opsaxiom"
echo "  3) ./install.sh --offline"
echo "   · 仅适用 Linux x86_64 目标机（其他平台请在线安装）"
echo "   · 装完 doctor 全绿即可用；Skill 库用包内快照，无须网络"
echo
echo "── ⚠ Python 版本前置（目标机，安装前自查）──────────"
echo "   离线 wheels 按 Python 3.9~3.12 收集（pyyaml/cffi/rpds 等编译型按版本"
echo "   各备一份，安装时 pip 自动选）。检查：python3 --version"
echo "   <3.9 或 ≥3.13 先升级/改用对应解释器再装（python3.12 ./install.sh 无效，"
echo "   需将其 bin 目录前置 PATH，例：PATH=/usr/local/py312/bin:\$PATH ./install.sh）"
