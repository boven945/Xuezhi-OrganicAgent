#!/usr/bin/env bash
# 启动本地 qwen2.5-7B 的 OpenAI 兼容服务（2026-11-23 本机实测方案）。
#
# 权重复用 本地权重来源项目的 qwen2.5-7B-Instruct Q4_K_M GGUF（两个分片，约 4.4GB）。
# GGUF 分片只需指定第一个，llama.cpp 会自动发现同目录后续分片。
#
# 关键参数 --chat_format chatml-function-calling 不能省：
# 不加时工具调用会以 <tool_call> 原文落在 content 里，Agent 拿不到标准 tool_calls。
# 另一限制：该格式在 stream=true + tools 时返回空流，故本项目本地端点默认关闭
# 流式（XUEZHI_LLM_STREAMING），Agent 走同步调用。
#
# 用法：
#   bash scripts/run-local-qwen.sh
#   QWEN_MODEL_PATH=/path/to/model.gguf bash scripts/run-local-qwen.sh
#   QWEN_PORT=8081 bash scripts/run-local-qwen.sh      # 须同步改 MAAS_BASE_URL
#
# 环境变量：
#   QWEN_PYTHON      装了 llama-cpp-python[server] 的解释器（默认 conda base）
#   QWEN_MODEL_PATH  权重首分片路径（默认 本地权重来源项目内的 qwen2.5-7B Q4_K_M）
#   QWEN_PORT        监听端口（默认 8080，须与 MAAS_BASE_URL 一致）
#   QWEN_CTX         上下文长度（默认 4096）
#   QWEN_THREADS     CPU 线程数（默认 8）

set -euo pipefail

PYTHON="${QWEN_PYTHON:-/d/anaconda3/python.exe}"
MODEL_PATH="${QWEN_MODEL_PATH:-/path/to/local/qwen2.5-7b-q4_k_m.gguf}"
PORT="${QWEN_PORT:-8080}"
CTX="${QWEN_CTX:-4096}"
THREADS="${QWEN_THREADS:-8}"
ALIAS="qwen2.5-7b-local"

if [ ! -f "$PYTHON" ]; then
  echo "找不到 Python 解释器：$PYTHON" >&2
  echo "请用 QWEN_PYTHON 指向装有 llama-cpp-python[server] 的解释器。" >&2
  exit 2
fi

if [ ! -f "$MODEL_PATH" ]; then
  echo "找不到模型权重：$MODEL_PATH" >&2
  echo "请用 QWEN_MODEL_PATH 指向 qwen2.5-7B-Instruct Q4_K_M 的第一个分片。" >&2
  exit 2
fi

if ! "$PYTHON" -c "import llama_cpp.server" 2>/dev/null; then
  echo "解释器缺少 llama_cpp.server，请先安装：" >&2
  echo "  $PYTHON -m pip install \"llama-cpp-python[server]==0.3.30\" starlette_context" >&2
  exit 2
fi

echo "模型：$MODEL_PATH"
echo "端点：http://127.0.0.1:$PORT/v1  (alias=$ALIAS, ctx=$CTX)"
echo "提示：--chat_format chatml-function-calling 已启用；本地默认关流式。"

exec "$PYTHON" -m llama_cpp.server \
  --model "$MODEL_PATH" \
  --model_alias "$ALIAS" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --n_ctx "$CTX" \
  --n_threads "$THREADS" \
  --chat_format chatml-function-calling
