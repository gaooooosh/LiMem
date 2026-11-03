#!/usr/bin/env bash
set -euo pipefail

LIMEM_PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LIMEM_QWEN_ENV="${LIMEM_QWEN_ENV:-${HOME}/.venvs/limem-qwen}"
LIMEM_MODEL_PATH="${LIMEM_MODEL_PATH:-${LIMEM_PROJECT_ROOT}/llm/Qwen3}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "请在 WSL/Linux 中运行本脚本。" >&2
  exit 1
fi
if [[ ! -x "${LIMEM_QWEN_ENV}/bin/vllm" ]]; then
  echo "未找到模型环境：${LIMEM_QWEN_ENV}。请先按 docs/workflow1/README.md 安装。" >&2
  exit 1
fi

"${LIMEM_QWEN_ENV}/bin/python" "${LIMEM_PROJECT_ROOT}/scripts/check_qwen_environment.py" \
  --model-path "${LIMEM_MODEL_PATH}"

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export VLLM_NO_USAGE_STATS=1

exec "${LIMEM_QWEN_ENV}/bin/vllm" serve "${LIMEM_MODEL_PATH}" \
  --served-model-name qwen \
  --host 127.0.0.1 \
  --port "${LIMEM_QWEN_PORT:-8001}" \
  --dtype auto \
  --generation-config vllm \
  --max-model-len "${LIMEM_QWEN_MAX_MODEL_LEN:-16384}" \
  --max-num-seqs "${LIMEM_QWEN_MAX_NUM_SEQS:-4}" \
  --gpu-memory-utilization "${LIMEM_QWEN_GPU_MEMORY_UTILIZATION:-0.50}" \
  --guided-decoding-backend guidance \
  --guided-decoding-disable-any-whitespace \
  --guided-decoding-disable-fallback \
  --reasoning-parser qwen3
