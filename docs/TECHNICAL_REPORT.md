# 端-边-云混合推理调度系统技术报告

## 1. 系统定位

本系统实现的是一个 OpenAI-compatible 的端-边-云混合推理调度网关。用户可以从 Open WebUI、自定义 Dashboard 聊天测试台或脚本入口发起聊天请求，所有请求统一进入 Gateway，由真实分类器、隐私分级器、Token 预估、语义缓存和路由策略共同决定请求应该落在端侧、边侧还是云侧。

当前部署模式中，分类器、隐私过滤、路由决策、缓存、轨迹、指标与 Dashboard 均为真实链路。模型生成后端支持 mock 与真实 OpenAI-compatible 两种模式；当前演示环境已切到 `MODEL_BACKEND_MODE=real`，三层逻辑模型均通过 DeepSeek OpenAI-compatible 接口调用 `deepseek-v4-pro`，用于重点展示端/边/云路由判断是否正确。

## 2. 总体架构

系统由七类服务组成：

| 层级 | 服务 | 端口 | 作用 |
| --- | --- | --- | --- |
| 用户接入 | Open WebUI | 3000 | 标准聊天界面，连接 Gateway 的 OpenAI-compatible API |
| 用户接入 | Dashboard | 8080 | 路由可视化、Demo Case、聊天测试台 |
| 智能网关 | Gateway | 4000 | 鉴权、分类、隐私、路由、缓存、模型调用、轨迹记录 |
| 状态存储 | Redis | 6379 | 分类结果缓存、语义缓存、路由轨迹 |
| 计费/扩展存储 | PostgreSQL | 5432 | 预留给 LiteLLM 计费数据库 |
| 可观测性 | Prometheus | 9090 | 抓取 Gateway/Dashboard 指标 |
| 可观测性 | Grafana | 3001 | 展示预置监控面板 |

典型请求链路：

```text
用户 / Open WebUI / Dashboard
  -> Gateway /v1/chat/completions
  -> API Key 鉴权
  -> semantic-router 高置信分类
  -> RouteLLM mf 兜底分类
  -> 隐私分级与脱敏
  -> Token 预估
  -> 路由策略决策 device / edge / cloud
  -> Redis 语义缓存查询
  -> 模型后端调用
  -> Redis 记录 RouteTrace
  -> Prometheus 指标更新
  -> 返回 OpenAI-compatible 响应
```

## 3. Gateway 核心设计

Gateway 是系统的控制面与数据面入口，代码入口为 `gateway/app.py`。

对外接口：

| 接口 | 说明 |
| --- | --- |
| `GET /health` | 返回分类器、Redis、模型后端模式健康状态 |
| `GET /metrics` | Prometheus 指标 |
| `GET /v1/models` | Open WebUI 使用的模型列表接口 |
| `POST /v1/chat/completions` | OpenAI-compatible 聊天接口，支持非流式与 SSE 流式 |
| `GET /debug/traces` | 最近路由轨迹 |
| `GET /debug/summary` | 汇总指标 |

请求进入 `/v1/chat/completions` 后，Gateway 不直接调用模型，而是先完成分类、隐私、Token、路由、缓存、轨迹这些控制逻辑。这样 Open WebUI 虽然看到的是标准 OpenAI 接口，底层实际执行的是混合调度策略。

## 4. 任务复杂度分类器

分类器位于 `gateway/classifier.py`，遵循 PRD v1.1 的要求，不使用自研 LLM-as-Judge，也没有启发式兜底。

分类流程：

1. 从用户消息中抽取最近 4000 字文本。
2. 先查 Redis 分类缓存，命中则直接返回。
3. 调用 `semantic-router[local]` 做本地向量路由。
4. 如果 semantic-router 低于置信阈值，则调用 `routellm` 的 mf 路由器。
5. 两个分类器都不可用时返回 `ClassifierUnavailable`，网关返回 503。

复杂度等级：

| 等级 | 含义 | 典型路由倾向 |
| --- | --- | --- |
| `SIMPLE` | 改写、翻译、简单问答 | 端侧 |
| `MEDIUM` | 邮件、会议纪要、短文档总结 | 端侧或边侧 |
| `COMPLEX` | 多步骤分析、结构化提取 | 边侧 |
| `RESEARCH` | 长报告、多来源综合 | 边侧或云侧 |
| `REASONING` | 高复杂推理、超长上下文 | 云侧，隐私允许时 |

国内网络适配：

- `USE_MODELSCOPE=true` 时，semantic-router encoder 优先通过 ModelScope 缓存下载。
- RouteLLM mf 的 embedding 调用被补丁为本地 `sentence-transformers`，避免运行时依赖 OpenAI embedding。

## 5. 隐私分级与脱敏

隐私模块位于 `gateway/privacy_filter.py`。

系统将请求分为三级：

| 隐私等级 | 策略 |
| --- | --- |
| `S1` | 高敏数据，禁止上云，只能走端或边 |
| `S2` | 中敏数据，允许脱敏后上云 |
| `S3` | 普通数据，可按复杂度和成本策略路由 |

检测内容包括身份证、手机号、银行卡、邮箱、IP、工资、财务、合同、专利、涉密关键词等。S2 数据会进行占位符脱敏，例如 `【PHONE-0001】`、`【ID-0001】`、`【AMOUNT-0001】`。如果最终路由到云侧，Gateway 会把最后一条用户消息替换为脱敏文本。

硬约束：`S1` 请求无论复杂度多高都不会进入云侧。

## 6. 路由策略

路由策略位于 `gateway/router_middleware.py`。

决策输入：

- 分类器输出的复杂度等级；
- 隐私等级与是否允许上云；
- 上下文 Token 数；
- 边侧 GPU 队列深度；
- 边侧是否可用；
- 环境变量中的阈值配置。
- 本地文件、本地动作等能力边界策略。

核心规则：

| 条件 | 路由结果 |
| --- | --- |
| `S1` 且上下文较短 | 端侧 |
| `S1` 且上下文超过边侧阈值 | 边侧 |
| 本地动作，例如打开浏览器、启动应用、执行终端命令 | 固定端侧，禁止上云 |
| 本地文件检索，例如搜索当前目录、本机日志 | 端侧，禁止上云 |
| 本地文件总结/分析且端侧模型能力不足 | 边侧，禁止上云 |
| 超过云侧上下文阈值 | 云侧，S1 除外 |
| `REASONING` | 云侧，S1 除外 |
| `RESEARCH` 且长上下文 | 云侧，S1 除外 |
| `COMPLEX` 及以上 | 优先边侧 |
| `MEDIUM` 且上下文较大 | 边侧 |
| 简单或边侧不可用 | 端侧 |
| 边侧队列超过 80% | 简单任务降到端侧，复杂任务可升到云侧 |

模型映射：

| 路由层级 | 默认模型 |
| --- | --- |
| device | `qwen3:1.5b` |
| edge | `deepseek-ai/DeepSeek-V4-Flash` |
| cloud | `deepseek-v4-pro` |

本地能力策略位于 `gateway/task_policy.py`。这层不是替代分类器，而是在分类器之外补充“数据和动作发生在哪里”的硬约束。例如“搜索下本地文件”即使被分类为复杂任务，也不会进入云端；“打开浏览器”属于本地动作，必须由端侧处理或规划。

## 7. 模型后端

模型调用封装在 `gateway/model_client.py`。

系统支持两种模式：

| 模式 | 说明 |
| --- | --- |
| `MODEL_BACKEND_MODE=mock` | 只模拟模型生成结果，用于无 GPU、无云 API、演示路由链路 |
| 非 mock | 按 device/edge/cloud 调用 OpenAI-compatible 后端 |

真实后端映射支持两种方式：

1. 分层真实后端：device 默认 `OLLAMA_HOST/v1`，edge 默认 `VLLM_HOST/v1`，cloud 默认 `DEEPSEEK_API_BASE`。
2. 共享真实后端：配置 `REAL_BACKEND_BASE_URL`、`REAL_BACKEND_API_KEY`、`REAL_BACKEND_MODEL` 后，device/edge/cloud 三个逻辑层可以共用一个 OpenAI-compatible 后端，同时仍保留路由层级、模型名和轨迹展示。

当前演示配置：

```text
MODEL_BACKEND_MODE=real
REAL_BACKEND_BASE_URL=https://api.deepseek.com
REAL_BACKEND_MODEL=deepseek-v4-pro
DEVICE_BACKEND_MODEL=deepseek-v4-pro
EDGE_BACKEND_MODEL=deepseek-v4-pro
CLOUD_BACKEND_MODEL=deepseek-v4-pro
```

这样做的目的是先统一真实生成能力，把展示重点放在“路由是否正确、隐私是否被拦截、轨迹是否可解释”。后续替换为真实端侧 Ollama、边侧 vLLM、云端 DeepSeek 时，只需要调整 endpoint 与模型配置，不需要改路由策略。

## 8. 缓存与轨迹

缓存与轨迹由 Redis 承载。

分类缓存：

- key 基于用户文本 SHA-256；
- TTL 使用 `SEMANTIC_CACHE_TTL`；
- 避免重复调用 semantic-router/RouteLLM。

语义缓存：

- 当前实现使用隐私等级 + 用户文本哈希作为缓存 key；
- 命中后复用已有模型响应；
- 命中请求仍会生成新的 Trace，标记 `cache_hit=true`。

轨迹记录字段包括：

- `trace_id`
- 用户输入摘要
- 复杂度等级
- 隐私等级
- Token 数
- 路由层级
- 使用模型
- 路由原因
- 延迟
- 输入/输出 Token
- 成本估算
- 是否命中缓存
- 是否脱敏
- 分类器来源
- 响应预览

Dashboard、Grafana 和调试接口都依赖这些轨迹。

## 9. Dashboard 与演示入口

Dashboard 后端位于 `dashboard/main.py` 与 `dashboard/routers/`，前端位于 `dashboard/frontend/src/App.tsx`。

Dashboard 提供：

- 系统健康状态；
- KPI 汇总；
- device/edge/cloud 分布；
- 路由轨迹列表；
- 路由轨迹默认展示 200 条，服务端最大允许 500 条，右侧列表内部滚动；
- 10 个 Demo Case 触发；
- 聊天测试台，可以输入自造数据验证隐私与路由。

当前可直接访问：

- Dashboard 聊天测试台：`http://127.0.0.1:8080`
- Gateway API：`http://127.0.0.1:4000`
- Grafana：`http://127.0.0.1:3001`
- Prometheus：`http://127.0.0.1:9090`
- Open WebUI：镜像下载完成后为 `http://127.0.0.1:3000`

## 10. Open WebUI 集成

`docker-compose.yml` 中的 `open-webui` 服务通过以下环境变量连接 Gateway：

```text
OPENAI_API_BASE_URL=http://gateway:4000/v1
OPENAI_API_BASE_URLS=http://gateway:4000/v1
OPENAI_API_KEY=${LITELLM_MASTER_KEY}
ENABLE_OLLAMA_API=false
WEBUI_AUTH=false
```

Gateway 已提供 Open WebUI 需要的：

- `GET /v1/models`
- `POST /v1/chat/completions`
- `stream=true` SSE 响应

因此 Open WebUI 只是聊天壳，所有模型选择和端边云调度仍由 Gateway 控制。

## 11. 可观测性

Gateway 暴露 Prometheus 指标：

- `hybrid_router_requests_total{tier=...}`
- `hybrid_router_request_latency_ms{tier=...}`

Dashboard 也暴露 `/metrics`。Prometheus 负责抓取，Grafana 使用预置 Dashboard 展示调度层级、延迟和请求分布。业务级轨迹由 Redis 保存，并通过 Dashboard 展示。

## 12. 测试覆盖

测试目录为 `tests/`，当前覆盖：

- 分类器优先级：semantic-router 命中优先；
- RouteLLM 兜底；
- 分类器不可用时不走启发式兜底；
- 隐私 S1 禁止上云；
- S2 脱敏；
- S3 普通请求；
- 路由策略，包括复杂任务走边、Reasoning 走云、边侧队列高时重路由；
- 本地文件检索、本地文件总结、本地动作的特殊路由策略；
- 10 个 Demo Case 结构与执行逻辑。

当前环境已完成 Python 语法检查与关键路由策略自检；由于宿主机和容器内未安装 `pytest`，完整 pytest 测试需要补装测试依赖后执行。

## 13. 当前实现边界

当前系统已经完整实现调度链路，但演示环境有三个明确边界：

1. 当前演示环境已切到 `MODEL_BACKEND_MODE=real`，真实生成请求通过 DeepSeek API 完成；但 device/edge/cloud 暂时共用同一个真实后端，主要用于验证路由决策与可视化展示。
2. vLLM GPU 服务在无合适 GPU 或镜像未启动时不参与实际生成，但路由层仍会做边侧决策和轨迹记录。切到真实边侧只需要启动 `gpu` profile，并把 `EDGE_OPENAI_BASE_URL` / `EDGE_BACKEND_MODEL` 指向真实 vLLM 服务。
3. 本地文件检索与本地动作当前完成的是路由与安全边界判断，尚未接入实际文件扫描器或本机动作执行器。后续接入时应限制搜索根目录、权限范围和动作白名单。

## 14. 演示建议

建议演示四类输入：

1. 简单公开问题，例如“帮我把这句话改得正式一点”，应走 device。
2. 复杂分析问题，例如“分析这些销售数据并给出策略”，应走 edge。
3. 本地能力问题，例如“搜索下本地文件”或“打开浏览器”，应走 device，并显示禁止上云的路由理由。
4. 含隐私数据的问题，例如“客户张三手机号 13800138000，合同金额 1280万元，请总结风险”，应触发 S2 脱敏；如果使用“机密/涉密/专利”等词，应触发 S1 并禁止上云。

演示时可以同时打开：

- 聊天测试台发送请求；
- Dashboard 查看路由轨迹；
- Grafana 查看指标；
- Gateway `/debug/traces` 查看原始 Trace。
