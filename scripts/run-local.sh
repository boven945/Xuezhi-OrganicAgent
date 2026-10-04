#!/usr/bin/env bash
# 本机启动脚本。
#
# 目标：**一条命令跑起来**，且失败时给出可行动的原因，
# 而不是让人对着堆栈猜。
#
# 用法：
#   ./scripts/run-local.sh# 起服务（默认 127.0.0.1:8000）
#   ./scripts/run-local.sh --check    # 只做环境自检，不起服务
#   ./scripts/run-local.sh --port9000 # 指定端口
#   ./scripts/run-local.sh --reload   # 开发模式（改代码自动重启）
#
# 环境变量（也可写进 .env，见 .env.example）：
#   MAAS_API_KEY   模型服务密钥。**未设置时服务仍可启动**，
#                  但 /ready 返回 503、问答接口报 llm_not_configured
#   XUEZHI_CHROMA_PATH  向量库目录，默认 data/chroma
#
# 本脚本只做"启动"这一件事。部署到服务器请看
# docs/deployment-operations.md，不要用这个脚本。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORT=8000
HOST=127.0.0.1
MODE="serve"
RELOAD=""

# ---------- 参数 ----------
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE="check"; shift ;;
    --port) PORT="${2:?--port 需要一个值}"; shift 2 ;;
    --host) HOST="${2:?--host 需要一个值}"; shift 2 ;;
    --reload) RELOAD="--reload"; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1（用 --help 看用法）" >&2; exit 2 ;;
  esac
done

# ---------- 颜色（Windows Git Bash 也支持）----------
if [ -t 1 ]; then
  R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; D=$'\033[2m'; N=$'\033[0m'
else
  R=""; G=""; Y=""; D=""; N=""
fi
ok()   { echo "${G}✓${N} $*"; }
warn() { echo "${Y}!${N} $*"; }
bad()  { echo "${R}✗${N} $*"; }
dim()  { echo "${D}$*${N}"; }

# ---------- 找 Python ----------
find_python() {
  # 优先用项目内 venv（版本与依赖都已固定）
  if [ -x ".venv-xuezhi312/Scripts/python.exe" ]; then
    echo ".venv-xuezhi312/Scripts/python.exe"; return 0
  fi
  if [ -x ".venv-xuezhi312/bin/python" ]; then
    echo ".venv-xuezhi312/bin/python"; return 0
  fi
  # 退到系统 Python（须>= 3.12，见决策登记表 H1）
  for cand in py python python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      echo "$cand"; return 0
    fi
  done
  return 1
}

PY="$(find_python)" || {
  bad "找不到 Python。"
  dim  "装 Python 3.12 后重试；或建 venv：python -m venv .venv-xuezhi312"
  exit 1
}

# ---------- 载入 .env ----------
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
  ok "已载入 .env"
fi

# ---------- 环境自检 ----------
check() {
  echo
  echo "环境自检"
  echo "─────────────────────────────────────────"

  "$PY" - <<'PYEOF'
import importlib
import sys

print(f"  Python {sys.version.split()[0]}  ({sys.executable})")

# (模块, 名称, 缺失时是否致命)
CHECKS = [
    ("fastapi", "Web 框架", True),
    ("uvicorn", "ASGI 服务器", True),
    ("pydantic", "数据校验", True),
    ("httpx2", "测试客户端", False),
    ("langchain_openai", "模型适配", False),
    ("chromadb", "向量库", False),
    ("sentence_transformers", "嵌入模型", False),
    ("torch", "推理框架", False),
]

missing_optional = []
for mod, label, required in CHECKS:
    try:
        importlib.import_module(mod)
        print(f"  [OK]   {label:12} {mod}")
    except Exception as exc:  # noqa: BLE001
        kind = type(exc).__name__
        # WDAC 拦截的典型文案
        blocked = "应用程序控制策略" in str(exc)
        mark = "[被拦]" if blocked else "[缺失]"
        print(f"  {mark} {label:12} {mod}  ({kind})")
        if required and not blocked:
            print(f"         -> 必需依赖，请 pip install {mod}")
        elif blocked:
            print(f"         -> 被 Windows 应用控制策略拦截，非缺依赖；"
                  f"该组件会降级但服务可用")

# RDKit 单独查：import rdkit 成功不代表 Chem 可用
try:
    from rdkit import Chem  # noqa: F401
    print("  [OK]   化学引擎     rdkit.Chem")
except Exception as exc:  # noqa: BLE001
    blocked = "应用程序控制策略" in str(exc)
    mark = "[被拦]" if blocked else "[缺失]"
    print(f"  {mark} 化学引擎     rdkit.Chem  ({type(exc).__name__})")
    if blocked:
        print("         -> 服务会降级为「仅知识检索」，/health 如实报告 chem 不可用")

# 密钥
import os
if os.environ.get("MAAS_API_KEY"):
    print("  [OK]   模型密钥     MAAS_API_KEY")
else:
    print("  [缺省] 模型密钥     MAAS_API_KEY")
    print("         -> 服务仍可启动；/ready 返回 503，问答接口报未配置")
PYEOF
}

if [ "$MODE" = "check" ]; then
  check
  echo
  exit 0
fi

# ---------- 启动前自检 ----------
check

echo
echo "启动服务"
echo "─────────────────────────────────────────"
dim "  地址   http://${HOST}:${PORT}"
dim "  文档   http://${HOST}:${PORT}/docs"
dim "  健康   http://${HOST}:${PORT}/health"
dim "  停止   Ctrl+C"
echo

export PYTHONPATH="${REPO_ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1

exec "$PY" -m uvicorn app.api.app:app \
  --host "$HOST" --port "$PORT" --app-dir backend $RELOAD
