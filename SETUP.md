# 端-边-云混合推理调度系统

## 系统要求

- Linux x86_64 或 ARM64。
- Docker 24.0+，Docker Compose 2.26+。
- Python 3.11+ 用于本地脚本和测试。
- 无 GPU 可运行端+云+mock 模式；边侧 vLLM 需要 NVIDIA GPU 和可用显存。
- 首次启动分类器会下载 semantic-router encoder 和 RouteLLM mf 权重；国内环境默认启用 ModelScope 缓存 semantic encoder，并使用 HuggingFace mirror 拉取 RouteLLM 权重。

## 快速启动

1. 进入项目目录：`cd hybrid-router`
2. 配置环境：`cp .env.example .env`，填写 `DEEPSEEK_API_KEY` 或保持 `MODEL_BACKEND_MODE=mock` 使用模拟模型响应。
3. 启动：`./start.sh`
4. 打开 Dashboard：`http://localhost:8080`
5. 运行 Demo：`python3 scripts/run_demo.py`

## 配置说明

- `LITELLM_MASTER_KEY`：OpenAI-compatible 网关鉴权密钥。
- `MODEL_BACKEND_MODE`：`mock` 表示只模拟模型生成；非 `mock` 时调用端/边/云 OpenAI-compatible 接口。
- `DEVICE_OPENAI_BASE_URL`：可选，端侧模拟或真实 OpenAI-compatible 地址。
- `EDGE_OPENAI_BASE_URL`：可选，边侧模拟或真实 OpenAI-compatible 地址。
- `OLLAMA_HOST` / `OLLAMA_DEVICE_MODEL`：Ollama 服务和端侧模型。
- `VLLM_HOST` / `VLLM_EDGE_MODEL`：vLLM 服务和边侧模型。
- `DEEPSEEK_API_BASE` / `DEEPSEEK_API_KEY`：DeepSeek 云端 API。
- `ROUTELLM_ROUTER` / `ROUTELLM_MF_CHECKPOINT`：RouteLLM 路由器和权重，默认 `mf` / `routellm/mf_gpt4_augmented`。
- `ROUTELLM_LOCAL_EMBEDDINGS=true`：用本地 sentence-transformer 为 RouteLLM mf 提供 embedding，避免运行时访问 OpenAI。
- `SEMANTIC_ROUTER_CONFIDENCE_THRESHOLD`：semantic-router 高置信直出阈值。
- `CONTEXT_EDGE_THRESHOLD_TOKENS`：超过后优先走边侧。
- `CONTEXT_CLOUD_THRESHOLD_TOKENS`：超过后优先走云端，S1 除外。
- `PRIVACY_CLOUD_ALLOWED_LEVELS`：云端允许的隐私等级，默认 `S2,S3`。
- `REDIS_URL`：语义缓存和轨迹存储。
- `GRAFANA_PASSWORD`：Grafana admin 密码。

## 无 GPU 降级方案

`start.sh` 未检测到 `nvidia-smi` 时不会启动 `vllm` profile。此时：

- SIMPLE/MEDIUM 默认走端侧。
- 边侧不可用时，复杂任务会根据隐私等级和路由策略自动走端或云。
- S1 永远不会走云。
- `MODEL_BACKEND_MODE=mock` 可用于没有任何模型服务时验证网关、隐私、路由、缓存和 Dashboard。

## 飞腾 ARM 平台适配

- 飞腾 D3000 节点只运行 Ollama、gateway、dashboard、redis/postgres 等通用服务。
- vLLM 服务放在带 NVIDIA GPU 的边侧节点或使用 Compose `gpu` profile。
- Ollama 官方镜像支持 ARM64；如果目标发行版镜像源有限，可先离线拉取 `qwen3:1.5b` 和 `nomic-embed-text`。

## Demo Case

- Dashboard：打开 `http://localhost:8080`，在 Demo Runner 中点击 10 个用例。
- CLI：`python3 scripts/run_demo.py`
- 单个用例：`python3 scripts/run_demo.py --case 3`
- 压测：`python3 scripts/benchmark.py --requests 50 --concurrency 8`

## 测试

本地安装测试依赖后执行：

```bash
python3 -m pip install -r gateway/requirements.txt -r dashboard/requirements.txt pytest
pytest tests/ -v
```

生产分类器不使用启发式兜底。测试中的 fake classifier engine 只用于依赖注入验证路由流程，不会进入容器运行路径。

## 常见问题

1. Gateway 健康检查很久才通过：首次下载 HuggingFace 和 RouteLLM 权重，等待或提前配置缓存。
2. `/v1/chat/completions` 返回 503 classifier dependency error：安装 `semantic-router[local]`、`routellm`、`tiktoken`，并检查网络下载权限。
3. S1 数据为什么没有走云：这是硬约束，任何复杂度都不能覆盖。
4. 无 GPU 时 vLLM 不启动：这是预期行为，使用 mock 或云端 API 验证。
5. Dashboard 没有轨迹：先通过 Open WebUI、Demo Runner 或 `scripts/run_demo.py` 发送请求。
6. DeepSeek 调用失败：确认 `DEEPSEEK_API_KEY`、`DEEPSEEK_API_BASE` 和出网权限。
7. Redis 不可用：Gateway 会继续工作，但跨进程轨迹和缓存不可用。
8. Grafana 没有数据：先产生网关请求，并确认 Prometheus 能抓取 `gateway:4000/metrics`。
9. Open WebUI 无响应：检查 `OPENAI_API_BASE_URL=http://gateway:4000/v1` 和网关健康状态。
10. ARM 平台构建慢：分类器依赖包含本地向量库，建议使用预构建镜像或提前构建 wheel 缓存。

## 卸载和清理

```bash
docker compose down
docker compose down -v
docker system prune
```

`docker compose down -v` 会删除 Postgres、Redis、Ollama、Grafana 和 RouteLLM 缓存卷。
