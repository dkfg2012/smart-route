#!/usr/bin/env sh
set -eu

python -m vllm.entrypoints.openai.api_server \
  --model "${VLLM_EDGE_MODEL:-deepseek-ai/DeepSeek-V4-Flash}" \
  --tensor-parallel-size "${VLLM_TENSOR_PARALLEL:-1}" \
  --max-model-len "${VLLM_MAX_MODEL_LEN:-131072}" \
  --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION:-0.90}" \
  --enable-prefix-caching \
  --served-model-name deepseek-v4-flash \
  --trust-remote-code

