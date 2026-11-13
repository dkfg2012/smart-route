#!/usr/bin/env bash
set -euo pipefail

echo "=========================================="
echo " 端-边-云混合推理调度系统 启动脚本"
echo "=========================================="

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已创建 .env。请编辑密钥和后端地址后重新运行 ./start.sh"
  exit 1
fi

set -a
source .env
set +a

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "未找到 docker compose 或 docker-compose。"
  exit 1
fi

GPU_MODE=""
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  GPU_MODE="--profile gpu"
  echo "检测到 NVIDIA GPU，将启用 vLLM profile。"
else
  echo "未检测到 NVIDIA GPU，将跳过 vLLM。"
fi

"${COMPOSE[@]}" up -d postgres redis ollama
"${COMPOSE[@]}" run --rm ollama-init

if [ -n "$GPU_MODE" ]; then
  "${COMPOSE[@]}" $GPU_MODE up -d vllm
fi

"${COMPOSE[@]}" up -d gateway dashboard open-webui prometheus grafana

echo ""
echo "Open WebUI:     http://localhost:3000"
echo "Dashboard:      http://localhost:${DASHBOARD_PORT:-8080}"
echo "Gateway:        http://localhost:${LITELLM_PORT:-4000}"
echo "Grafana:        http://localhost:${GRAFANA_PORT:-3001}"
echo "Prometheus:     http://localhost:${PROMETHEUS_PORT:-9090}"
