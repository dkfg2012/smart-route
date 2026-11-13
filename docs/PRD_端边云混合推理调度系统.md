# PRD · 端-边-云混合推理调度系统
# Hybrid Device-Edge-Cloud Inference Routing System

**版本** v1.1  
**日期** 2026-05-19  
**执行对象** Codex（全自动实现，读完本文档后不需要额外确认，直接完整实现）  
**交付形式** 可运行的 Docker Compose 项目，含完整源码、配置、测试脚本

> **v1.1 变更摘要**：分类器实现从自研 LLM-as-Judge 替换为 `semantic-router[local]` + `routellm` 组合。
> `classifier.py` 代码量减少约 60%，无需自维护 Prompt、JSON 解析和重试逻辑；
> `routellm` 提供有 ICLR 2025 论文验证的预训练 `mf` 路由器权重，`semantic-router` 负责
> 毫秒级向量路由兜底。其余模块（隐私过滤、路由决策、网关配置、Dashboard）保持不变。

---

## 0. 执行约定（Codex 必读）

- 所有代码写入项目根目录 `hybrid-router/`
- 每个服务对应一个子目录，内含独立 `Dockerfile`（如需）和配置
- 根目录提供一键启动脚本 `start.sh` 和完整 `docker-compose.yml`
- 所有对外暴露的配置项通过根目录 `.env` 集中管理，不硬编码
- 代码语言：Python（后端）、YAML（配置）、Shell（脚本）
- 每个模块写对应的单元测试，测试文件放 `tests/` 目录
- 完成后输出 `SETUP.md`，包含从零启动的完整步骤

---

## 1. 项目概述

### 1.1 背景

中国长城（CEC）边缘 AI 一体机项目：基于飞腾 D3000（端·ARM 信创）+ NVIDIA GB10 DGX Spark（边·128GB 统一内存）+ DeepSeek V4 官方 API（云），实现三层混合推理调度。

### 1.2 核心目标

| 目标 | 量化指标 |
|------|---------|
| 端/边命中率 | ≥ 68% 请求在端或边完结，不上云 |
| 云端 Token 节省 | 较全走云端降低 ≥ 70% |
| P50 首 Token 延迟 | 端 < 500ms，边 < 2s，云 < 5s |
| S3 数据隔离 | 高隐私任务 100% 不出端/边 |
| Demo Case 覆盖 | 10 个场景全部跑通 |

### 1.3 系统边界

**本系统包含：**
- 智能路由网关（基于 LiteLLM Proxy + 自定义路由插件）
- 任务复杂度分类器（`semantic-router[local]` 向量路由 + `routellm` mf 预训练路由器）
- 隐私分级过滤器（PII 识别 + 数据脱敏）
- 端侧推理后端（Ollama）
- 边侧推理后端（vLLM）
- 前端界面（Open WebUI）
- 路由决策可视化 Dashboard（FastAPI + React）
- 10 个演示用例的测试套件

**本系统不包含（当前版本）：**
- Dify 工作流平台
- RAGFlow 知识库
- 向量数据库 / Embedding 流水线
- 模型微调

---

## 2. 技术栈与版本锁定

```
Python          3.11+
Docker          24.0+
Docker Compose  2.26+
LiteLLM         最新稳定版（pip install "litellm[proxy]"）
semantic-router 最新稳定版（pip install "semantic-router[local]"）  ← v1.1 新增
routellm        最新稳定版（pip install routellm）                   ← v1.1 新增
vLLM            最新稳定版（pip install vllm）
Ollama          最新稳定版（官方安装脚本）
Open WebUI      ghcr.io/open-webui/open-webui:main
FastAPI         0.110+
Redis           7.2（语义缓存 + 状态存储）
PostgreSQL      15（LiteLLM 计费数据库）
Prometheus      2.50+（指标采集）
Grafana         10.4+（Dashboard）
```

---

## 3. 系统架构

```
┌─────────────────────────────────────────────────────┐
│                   用户接入层                          │
│  Open WebUI :3000  │  自定义 Web UI :8080            │
└─────────────────────────────┬───────────────────────┘
                              │ HTTP (OpenAI-compat)
┌─────────────────────────────▼───────────────────────┐
│              智能路由网关  :4000                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │任务分类器│ │隐私过滤器│ │Token预估 │ │语义缓存│  │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘  │
│  LiteLLM Proxy + custom routing middleware           │
└──────┬───────────────┬──────────────────┬────────────┘
       │               │                  │
  ┌────▼────┐    ┌──────▼──────┐   ┌──────▼──────┐
  │  端层   │    │   边层      │   │   云层      │
  │ Ollama  │    │   vLLM      │   │ DeepSeek    │
  │:11434   │    │   :8000     │   │  官方 API   │
  │1.5B–7B  │    │V4-Flash     │   │  V4-Pro     │
  │飞腾D3000│    │GB10 DGX     │   │  1M Context │
  └─────────┘    └─────────────┘   └─────────────┘
       │               │                  │
┌──────▼───────────────▼──────────────────▼────────────┐
│              可观测性层                               │
│  Prometheus :9090  │  Grafana :3001  │  Redis :6379  │
└───────────────────────────────────────────────────────┘
```

---

## 4. 项目目录结构

Codex 需要创建以下目录结构：

```
hybrid-router/
├── .env                          # 全局环境变量（模板见第5节）
├── .env.example                  # 不含真实密钥的示例
├── docker-compose.yml            # 完整服务编排
├── start.sh                      # 一键启动脚本
├── stop.sh                       # 停止脚本
├── SETUP.md                      # 安装说明
│
├── gateway/                      # 智能路由网关
│   ├── litellm_config.yaml       # LiteLLM 主配置
│   ├── router_middleware.py      # 自定义路由中间件
│   ├── classifier.py             # 分类器【v1.1: semantic-router + routellm】
│   ├── warmup.py                 # 路由器预热脚本【v1.1 新增】
│   ├── privacy_filter.py         # 隐私分级过滤器
│   ├── token_estimator.py        # Token 预估模块
│   ├── semantic_cache.py         # 语义缓存模块
│   └── Dockerfile
│
├── dashboard/                    # 路由决策可视化 Dashboard
│   ├── main.py                   # FastAPI 后端
│   ├── models.py                 # 数据模型
│   ├── routers/
│   │   ├── metrics.py            # 指标 API
│   │   ├── traces.py             # 路由轨迹 API
│   │   └── demo.py               # Demo Case 触发 API
│   ├── frontend/                 # React 前端
│   │   ├── src/
│   │   │   ├── App.tsx
│   │   │   ├── components/
│   │   │   │   ├── KPICards.tsx
│   │   │   │   ├── RoutingTrace.tsx
│   │   │   │   ├── TierDistribution.tsx
│   │   │   │   └── DemoRunner.tsx
│   │   │   └── pages/
│   │   │       └── Dashboard.tsx
│   │   ├── package.json
│   │   └── vite.config.ts
│   └── Dockerfile
│
├── ollama/                       # 端侧推理后端
│   ├── setup.sh                  # 模型拉取脚本
│   └── Modelfile.qwen3           # 自定义 Qwen3 模型文件（可选）
│
├── vllm/                         # 边侧推理后端
│   ├── serve.sh                  # vLLM 启动脚本
│   └── config.json               # vLLM 服务配置
│
├── monitoring/                   # 可观测性
│   ├── prometheus.yml
│   └── grafana/
│       ├── provisioning/
│       │   ├── datasources/
│       │   │   └── prometheus.yaml
│       │   └── dashboards/
│       │       └── dashboard.yaml
│       └── dashboards/
│           └── hybrid_router.json  # 预置 Grafana Dashboard
│
├── tests/                        # 测试套件
│   ├── test_classifier.py        # 分类器单元测试【v1.1: 含 SR/RouteLLM 专项测试】
│   ├── test_privacy_filter.py
│   ├── test_routing.py
│   ├── test_demo_cases.py        # 10 个 Demo Case 自动化测试
│   └── conftest.py
│
└── scripts/
    ├── seed_models.sh            # 拉取所有所需模型
    ├── run_demo.py               # 交互式 Demo 运行器
    └── benchmark.py             # 压测脚本
```

---

## 5. 环境变量（`.env`）

```bash
# ── 基础配置 ────────────────────────────────────────
PROJECT_NAME=hybrid-router
LOG_LEVEL=INFO

# ── LiteLLM 网关 ─────────────────────────────────────
LITELLM_MASTER_KEY=change-me-local-gateway-key
LITELLM_PORT=4000

# ── 端侧：Ollama ─────────────────────────────────────
OLLAMA_HOST=http://ollama:11434
OLLAMA_DEVICE_MODEL=qwen3:1.5b
# v1.1: OLLAMA_CLASSIFIER_MODEL 已移除，分类器改用 semantic-router + routellm

# ── 分类器：semantic-router + routellm ────────────────
ROUTELLM_CACHE_DIR=/app/.routellm_cache
ROUTELLM_MF_CHECKPOINT=routellm/mf-gpt-4-1106-preview-mixtral-8x7b
SEMANTIC_ROUTER_CONFIDENCE_THRESHOLD=0.75

# ── 边侧：vLLM ───────────────────────────────────────
VLLM_HOST=http://vllm:8000
VLLM_EDGE_MODEL=deepseek-ai/DeepSeek-V4-Flash
VLLM_TENSOR_PARALLEL=1
VLLM_MAX_MODEL_LEN=131072
VLLM_GPU_MEMORY_UTILIZATION=0.90

# ── 云端：DeepSeek API ────────────────────────────────
DEEPSEEK_API_KEY=your-deepseek-api-key
DEEPSEEK_API_BASE=https://api.deepseek.com/v1
DEEPSEEK_CLOUD_MODEL=deepseek-v4-pro
DEEPSEEK_FLASH_MODEL=deepseek-v4-flash

# ── 数据库 ────────────────────────────────────────────
POSTGRES_USER=litellm
POSTGRES_PASSWORD=litellm-secret-change-me
POSTGRES_DB=litellm
DATABASE_URL=postgresql://litellm:litellm-secret-change-me@postgres:5432/litellm

# ── Redis ─────────────────────────────────────────────
REDIS_URL=redis://redis:6379
SEMANTIC_CACHE_SIMILARITY_THRESHOLD=0.92
SEMANTIC_CACHE_TTL=3600

# ── Dashboard ────────────────────────────────────────
DASHBOARD_PORT=8080
DASHBOARD_SECRET=dashboard-secret-change-me

# ── 路由阈值（可在 Dashboard 热更新）────────────────────
COMPLEXITY_EDGE_THRESHOLD=MEDIUM     # SIMPLE→端, ≥MEDIUM→边
COMPLEXITY_CLOUD_THRESHOLD=RESEARCH  # ≥RESEARCH→云
CONTEXT_EDGE_THRESHOLD_TOKENS=8192   # 超过此值走边
CONTEXT_CLOUD_THRESHOLD_TOKENS=65536 # 超过此值走云
PRIVACY_CLOUD_ALLOWED_LEVELS=S2,S3  # S1 严禁上云

# ── 监控 ─────────────────────────────────────────────
PROMETHEUS_PORT=9090
GRAFANA_PORT=3001
GRAFANA_PASSWORD=admin-change-me
```

---

## 6. 核心模块详细规格

### 6.1 任务复杂度分类器（`gateway/classifier.py`）【v1.1 重写】

> **实现策略**：两级流水线。第一级用 `semantic-router[local]`（向量相似度，<5ms）做高置信度快速判断；
> 第二级用 `routellm` mf 预训练路由器（矩阵分解，ICLR 2025 验证）做强弱模型二分；
> 两级结合映射到五档复杂度等级。彻底消除自维护 Prompt、JSON 解析、LLM 重试等脆弱代码。

#### 6.1.1 复杂度等级定义（不变）

| 等级 | 英文 | 典型特征 | 默认路由 |
|------|------|---------|---------|
| 1 | SIMPLE | 问候、改写、格式转换、单句回答 | 端 |
| 2 | MEDIUM | 文件摘要、邮件草稿、简单代码、待办提取 | 端或边 |
| 3 | COMPLEX | 多步推理、多文件对比、结构化提取 | 边 |
| 4 | RESEARCH | 深度分析、多源综合、长文档问答 | 边+云协作 |
| 5 | REASONING | 数学证明、代码架构设计、超长上下文 | 云 |

#### 6.1.2 依赖安装

```bash
pip install "semantic-router[local]" routellm tiktoken
```

`semantic-router[local]` 使用 HuggingFace Encoder（`sentence-transformers/all-MiniLM-L6-v2`）
在本地计算向量，无需任何外部 API Key，完全离线运行。

`routellm` 使用 `mf`（矩阵分解）路由器，权重从 HuggingFace 自动下载并缓存。
模型路径由 `ROUTELLM_CACHE_DIR` 控制，默认 `~/.cache/routellm`。

#### 6.1.3 接口规格（不变，对外签名保持兼容）

```python
class ComplexityLevel(str, Enum):
    SIMPLE    = "SIMPLE"
    MEDIUM    = "MEDIUM"
    COMPLEX   = "COMPLEX"
    RESEARCH  = "RESEARCH"
    REASONING = "REASONING"

class ClassificationResult(BaseModel):
    level: ComplexityLevel
    confidence: float          # 0.0–1.0
    reasoning: str             # 分类依据（来源：semantic/routellm/heuristic）
    estimated_tokens: int      # 启发式预估
    cache_hit: bool
    latency_ms: float
    classifier_used: str       # "semantic_router" | "routellm" | "heuristic_fallback"

async def classify(messages: list[dict]) -> ClassificationResult:
    ...
```

#### 6.1.4 完整实现（`gateway/classifier.py`）

```python
"""
分类器 v1.1 — semantic-router[local] + routellm mf 两级流水线
"""
import hashlib, time, os
from enum import Enum
from functools import lru_cache
import tiktoken
import redis.asyncio as aioredis
from pydantic import BaseModel

# ── semantic-router 初始化 ──────────────────────────────────────────────────
from semantic_router import Route
from semantic_router.routers import SemanticRouter
from semantic_router.encoders import HuggingFaceEncoder

# ── routellm 初始化 ─────────────────────────────────────────────────────────
from routellm.controller import Controller as RouteLLMController

# ── 五档复杂度路由规则（作为 semantic-router 的 Route 对象）──────────────────
ROUTES = [
    Route(
        name="SIMPLE",
        utterances=[
            "你好", "帮我改一下这句话", "翻译成英文", "是或否",
            "Hello", "rewrite this sentence", "fix the grammar",
            "what time is it", "define this word", "spell check",
            "summarize in one sentence", "format this list",
        ],
    ),
    Route(
        name="MEDIUM",
        utterances=[
            "帮我写一封邮件", "提取会议纪要", "总结这份文档",
            "write an email", "extract action items", "summarize this document",
            "explain this code snippet", "generate a todo list",
            "draft a reply", "create a brief report",
        ],
    ),
    Route(
        name="COMPLEX",
        utterances=[
            "比较这两份文件的差异", "分析数据并生成报告", "多步骤推理",
            "compare multiple documents", "analyze data and generate report",
            "multi-step reasoning", "extract structured data from files",
            "build a comparison table", "synthesize information from sources",
        ],
    ),
    Route(
        name="RESEARCH",
        utterances=[
            "深度分析这份长报告", "综合多个来源的信息", "回答关于这本书的问题",
            "deep analysis of this report", "synthesize knowledge from multiple sources",
            "answer questions about this document", "research and fact-check",
            "review this long paper", "comprehensive literature review",
        ],
    ),
    Route(
        name="REASONING",
        utterances=[
            "证明这个数学定理", "设计系统架构", "review this entire codebase",
            "prove this theorem", "design a distributed system",
            "analyze this 200-page contract", "ultra-long context processing",
            "complex multi-agent planning", "write a research paper",
        ],
    ),
]

# ── 单例：在模块加载时初始化，复用于所有请求 ─────────────────────────────────

@lru_cache(maxsize=1)
def _get_semantic_router() -> SemanticRouter:
    encoder = HuggingFaceEncoder()   # 本地，无需 API Key
    return SemanticRouter(encoder=encoder, routes=ROUTES, auto_sync="local")

@lru_cache(maxsize=1)
def _get_routellm_controller() -> RouteLLMController:
    # mf 路由器：矩阵分解，ICLR 2025 最优配置
    # strong_model / weak_model 仅用于路由决策参考，不实际调用
    return RouteLLMController(
        routers=["mf"],
        strong_model="deepseek-v4-pro",
        weak_model="qwen3:1.5b",
        config={"mf": {"checkpoint_path": os.getenv(
            "ROUTELLM_MF_CHECKPOINT",
            "routellm/mf-gpt-4-1106-preview-mixtral-8x7b",  # HF 默认权重
        )}},
    )

@lru_cache(maxsize=1)
def _get_tokenizer():
    try:
        return tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


# ── 主分类函数 ───────────────────────────────────────────────────────────────

class ComplexityLevel(str, Enum):
    SIMPLE    = "SIMPLE"
    MEDIUM    = "MEDIUM"
    COMPLEX   = "COMPLEX"
    RESEARCH  = "RESEARCH"
    REASONING = "REASONING"

class ClassificationResult(BaseModel):
    level: ComplexityLevel
    confidence: float
    reasoning: str
    estimated_tokens: int
    cache_hit: bool
    latency_ms: float
    classifier_used: str

# 启发式 Token 预估（用于辅助决策，避免复杂任务被低估）
_COMPLEXITY_TOKEN_HINTS = {
    ComplexityLevel.SIMPLE:    200,
    ComplexityLevel.MEDIUM:    800,
    ComplexityLevel.COMPLEX:  2500,
    ComplexityLevel.RESEARCH: 6000,
    ComplexityLevel.REASONING:15000,
}

def _text_from_messages(messages: list[dict]) -> str:
    """拼接消息为纯文本（取最后 2 轮用户内容）"""
    parts = []
    for m in messages[-4:]:
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            parts.append(str(content))
    return " ".join(parts)[-2000:]  # 截断到 2000 字符

def _context_token_count(messages: list[dict]) -> int:
    enc = _get_tokenizer()
    text = _text_from_messages(messages)
    return len(enc.encode(text))

def _routellm_to_complexity(strong_win_rate: float) -> tuple[ComplexityLevel, float]:
    """
    将 routellm 的 strong_win_rate（0.0–1.0）映射到五档复杂度。
    win_rate 越高代表任务越需要强模型（越复杂）。
    """
    if strong_win_rate < 0.20:
        return ComplexityLevel.SIMPLE,    1.0 - strong_win_rate
    elif strong_win_rate < 0.40:
        return ComplexityLevel.MEDIUM,    1.0 - abs(strong_win_rate - 0.30)
    elif strong_win_rate < 0.60:
        return ComplexityLevel.COMPLEX,   1.0 - abs(strong_win_rate - 0.50)
    elif strong_win_rate < 0.80:
        return ComplexityLevel.RESEARCH,  strong_win_rate
    else:
        return ComplexityLevel.REASONING, strong_win_rate

async def classify(
    messages: list[dict],
    redis_client: aioredis.Redis | None = None,
) -> ClassificationResult:
    t0 = time.perf_counter()
    text = _text_from_messages(messages)
    ctx_tokens = _context_token_count(messages)

    # ── 缓存查询 ────────────────────────────────────────────────────────────
    cache_key = f"clf:{hashlib.sha256(text.encode()).hexdigest()[:20]}"
    if redis_client:
        cached = await redis_client.get(cache_key)
        if cached:
            import json
            result = ClassificationResult.model_validate_json(cached)
            result.cache_hit = True
            result.latency_ms = (time.perf_counter() - t0) * 1000
            return result

    # ── 超长上下文快速短路（无需路由器判断）──────────────────────────────────
    if ctx_tokens > int(os.getenv("CONTEXT_CLOUD_THRESHOLD_TOKENS", 65536)):
        level, confidence = ComplexityLevel.REASONING, 0.99
        classifier_used = "heuristic_context_length"
    else:
        # ── 第一级：semantic-router 向量匹配（<5ms）─────────────────────────
        sr = _get_semantic_router()
        sr_result = sr(text)    # 返回 RouteChoice 或 None

        if sr_result and sr_result.name and sr_result.similarity_score > 0.75:
            # 高置信度向量匹配 → 直接使用
            level = ComplexityLevel(sr_result.name)
            confidence = float(sr_result.similarity_score)
            classifier_used = "semantic_router"
        else:
            # ── 第二级：routellm mf 路由器 (~20ms，纯 CPU）──────────────────
            try:
                ctrl = _get_routellm_controller()
                strong_win_rate = ctrl.calculate_strong_win_rate(
                    prompt=text,
                    router="mf",
                )
                level, confidence = _routellm_to_complexity(strong_win_rate)
                classifier_used = "routellm_mf"
            except Exception:
                # 兜底：启发式规则（基于文本长度 + 关键词）
                level = _heuristic_classify(text, ctx_tokens)
                confidence = 0.60
                classifier_used = "heuristic_fallback"

    result = ClassificationResult(
        level=level,
        confidence=confidence,
        reasoning=f"via {classifier_used} | ctx_tokens={ctx_tokens}",
        estimated_tokens=_COMPLEXITY_TOKEN_HINTS[level],
        cache_hit=False,
        latency_ms=(time.perf_counter() - t0) * 1000,
        classifier_used=classifier_used,
    )

    # ── 写入缓存（TTL 1小时）────────────────────────────────────────────────
    if redis_client:
        await redis_client.setex(cache_key, 3600, result.model_dump_json())

    return result


def _heuristic_classify(text: str, ctx_tokens: int) -> ComplexityLevel:
    """纯规则兜底，仅在 routellm 异常时使用"""
    text_l = text.lower()
    if ctx_tokens > 32768:
        return ComplexityLevel.RESEARCH
    if any(k in text_l for k in ["证明", "theorem", "architecture", "设计系统", "codebase"]):
        return ComplexityLevel.REASONING
    if any(k in text_l for k in ["分析", "analyze", "compare", "综合", "report"]):
        return ComplexityLevel.COMPLEX
    if any(k in text_l for k in ["总结", "summarize", "email", "邮件", "code"]):
        return ComplexityLevel.MEDIUM
    return ComplexityLevel.SIMPLE
```

#### 6.1.5 路由器预热脚本（`gateway/warmup.py`）

```python
"""
在容器启动时预热两个路由器，避免第一个请求触发模型下载。
在 Dockerfile CMD 前调用：python warmup.py && litellm ...
"""
import logging
from classifier import _get_semantic_router, _get_routellm_controller, _get_tokenizer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("warmup")

log.info("预热 HuggingFace Encoder（semantic-router）...")
_get_semantic_router()("warmup query")
log.info("✓ semantic-router 就绪")

log.info("预热 routellm mf 路由器...")
ctrl = _get_routellm_controller()
ctrl.calculate_strong_win_rate("warmup", router="mf")
log.info("✓ routellm 就绪")

_get_tokenizer()
log.info("✓ tokenizer 就绪")
log.info("所有路由器预热完成，启动网关...")
```

#### 6.1.6 缓存策略（不变）

```python
cache_key = f"clf:{hashlib.sha256(text.encode()).hexdigest()[:20]}"
ttl = 3600   # 1 小时
# 命中率预期：日常场景 >40%（同类请求语义相近，SR 层在 <5ms 内命中）
```

---

### 6.2 隐私分级过滤器（`gateway/privacy_filter.py`）

#### 6.2.1 隐私等级定义

| 等级 | 含义 | 允许去向 | 处理方式 |
|------|------|---------|---------|
| S1 | 极敏感：财务凭证、人事档案、研发图纸、专利 | 端/边，绝不上公有云 | 本地处理 |
| S2 | 中敏感：客户信息、合同细节、内部报告 | 脱敏后可上云 | PII替换后转发 |
| S3 | 低敏感：公开信息、技术文档、非敏感问答 | 可直接上云 | 无处理 |

#### 6.2.2 PII 识别规则（正则 + 关键词）

```python
PII_PATTERNS = {
    "id_card":       r"\b\d{17}[\dX]\b",                    # 中国身份证
    "phone":         r"\b1[3-9]\d{9}\b",                    # 手机号
    "bank_card":     r"\b\d{16,19}\b",                      # 银行卡号
    "email":         r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "ip_address":    r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
    "salary":        r"薪[酬资]|工资|年薪|月薪",
    "financial":     r"账户|余额|转账|汇款|密码|PIN",
    "confidential":  r"机密|保密|内部|涉密|绝密",
    "patent":        r"专利|技术秘密|研发|配方|工艺",
}

S1_KEYWORDS = ["机密", "绝密", "研发图纸", "专利", "人事", "工资单", "财务报表"]
S2_KEYWORDS = ["客户", "合同", "报价", "内部", "采购", "供应商"]
```

#### 6.2.3 脱敏函数（S2 数据上云前调用）

```python
def desensitize(text: str) -> tuple[str, dict]:
    """
    Returns (desensitized_text, replacement_map)
    replacement_map 用于在响应返回时还原（如有需要）
    """
    ...

# 示例：
# 输入: "张三的身份证 320101198801011234 账户余额 50万"
# 输出: "【用户A】的身份证 【ID-0001】 账户余额 【金额-0001】"
```

#### 6.2.4 接口规格

```python
class PrivacyLevel(str, Enum):
    S1 = "S1"   # 极敏感，绝不出端/边
    S2 = "S2"   # 中敏感，脱敏后可上云
    S3 = "S3"   # 低敏感，可直接上云

class PrivacyCheckResult(BaseModel):
    level: PrivacyLevel
    detected_patterns: list[str]     # 触发的规则列表
    desensitized_text: str | None    # 仅 S2 时提供
    replacement_map: dict | None     # PII 映射表
    block_cloud: bool                # True 表示禁止上云

async def check_privacy(messages: list[dict]) -> PrivacyCheckResult:
    ...
```

---

### 6.3 路由决策引擎（`gateway/router_middleware.py`）

#### 6.3.1 路由决策矩阵

```python
def decide_route(
    classification: ClassificationResult,
    privacy: PrivacyCheckResult,
    context_tokens: int,
    gpu_queue_depth: int,      # 边侧 GPU 队列深度
    edge_available: bool,      # 边侧是否可用
) -> RouteDecision:
    """
    优先级：
    1. S1 隐私 → 强制端/边（不可被其他条件覆盖）
    2. Context > CLOUD_THRESHOLD → 云（含 V4-Pro 1M）
    3. Complexity == REASONING → 云
    4. Complexity == RESEARCH 且 context > 32K → 云（S2 先脱敏）
    5. Complexity >= MEDIUM 且 边侧可用 → 边
    6. 边侧队列 > 80% → 降级到端（简单任务）或升级到云（复杂任务）
    7. 其余 → 端
    """
```

#### 6.3.2 RouteDecision 数据模型

```python
class RouteTier(str, Enum):
    DEVICE = "device"    # 端：Ollama
    EDGE   = "edge"      # 边：vLLM
    CLOUD  = "cloud"     # 云：DeepSeek API

class RouteDecision(BaseModel):
    tier: RouteTier
    model: str                    # 实际调用的模型标识
    reason: str                   # 路由理由（展示给用户）
    privacy_applied: str | None   # 隐私处理描述
    estimated_cost_usd: float     # 预估费用
    trace_id: str                 # UUID，用于 Dashboard 追踪
```

#### 6.3.3 LiteLLM 自定义路由钩子

```python
# 实现 LiteLLM 的 CustomRouter 接口
class HybridRouter(litellm.Router):
    async def get_available_deployment(
        self,
        model: str,
        messages: list[dict],
        request_kwargs: dict | None = None,
    ) -> dict:
        # 1. 运行分类器
        # 2. 运行隐私过滤
        # 3. 执行路由决策
        # 4. 记录路由轨迹到 Redis
        # 5. 返回目标部署配置
        ...
```

---

### 6.4 语义缓存（`gateway/semantic_cache.py`）

```python
class SemanticCache:
    """
    基于向量相似度的请求缓存。
    使用轻量 Embedding 模型（Ollama: nomic-embed-text）计算请求向量，
    存储于 Redis Vector（RedisVL）。
    相似度 > 0.92 直接返回缓存，不走推理后端。
    """

    async def lookup(self, messages: list[dict]) -> CacheResult | None:
        ...

    async def store(
        self,
        messages: list[dict],
        response: str,
        tier: RouteTier,
        ttl: int = 3600,
    ) -> None:
        ...
```

> 如果当前部署环境不支持 RedisVL，降级为基于 SHA256 哈希的精确匹配缓存，功能不受影响。

---

### 6.5 LiteLLM 网关配置（`gateway/litellm_config.yaml`）

```yaml
model_list:
  # ── 端层：Ollama ──────────────────────────────────────
  - model_name: device-general
    litellm_params:
      model: ollama/qwen3:1.5b
      api_base: ${OLLAMA_HOST}
    model_info:
      tier: device
      max_tokens: 8192
      cost_per_1k_tokens: 0.0
  # 注意：v1.1 移除了 device-classifier 模型条目。
  # 分类器已改用 semantic-router[local] + routellm，不再调用 Ollama 做分类推理。

  # ── 边层：vLLM ────────────────────────────────────────
  - model_name: edge-flash
    litellm_params:
      model: openai/deepseek-ai/DeepSeek-V4-Flash
      api_base: ${VLLM_HOST}/v1
      api_key: none
    model_info:
      tier: edge
      max_tokens: 131072
      cost_per_1k_tokens: 0.0   # 自建，按算力计

  # ── 云层：DeepSeek 官方 API ────────────────────────────
  - model_name: cloud-pro
    litellm_params:
      model: deepseek/deepseek-v4-pro
      api_key: ${DEEPSEEK_API_KEY}
      api_base: ${DEEPSEEK_API_BASE}
    model_info:
      tier: cloud
      max_tokens: 1048576   # 1M context
      cost_per_1k_tokens: 0.00348   # $3.48/M output

  - model_name: cloud-flash
    litellm_params:
      model: deepseek/deepseek-v4-flash
      api_key: ${DEEPSEEK_API_KEY}
      api_base: ${DEEPSEEK_API_BASE}
    model_info:
      tier: cloud
      max_tokens: 1048576
      cost_per_1k_tokens: 0.00028   # $0.28/M output

router_settings:
  routing_strategy: custom                  # 使用 HybridRouter
  num_retries: 2
  retry_after: 5
  fallbacks:
    - device-general: [edge-flash]
    - edge-flash: [cloud-flash]
  context_window_fallbacks:
    - device-general: [edge-flash, cloud-flash]
    - edge-flash: [cloud-pro]
  timeout: 120

litellm_settings:
  drop_params: true
  set_verbose: false
  cache: true
  cache_params:
    type: redis
    host: redis
    port: 6379

general_settings:
  master_key: ${LITELLM_MASTER_KEY}
  database_url: ${DATABASE_URL}
  store_model_in_db: true
  proxy_budget_rescheduler_min_time: 60

# Prometheus 指标暴露
prometheus_port: 4001
```

---

### 6.6 可视化 Dashboard（`dashboard/`）

#### 6.6.1 后端 API（FastAPI）

```
GET  /api/metrics/summary      → KPI 汇总（命中率、节省率、P50延迟）
GET  /api/metrics/distribution → 端/边/云实时流量分布
GET  /api/traces               → 最近 N 条路由轨迹
GET  /api/traces/{trace_id}    → 单条路由详情
POST /api/demo/run             → 触发指定 Demo Case
GET  /api/demo/cases           → 获取所有 Demo Case 定义
GET  /api/health               → 三层后端健康检查
WS   /api/stream               → 实时路由事件推送（WebSocket）
```

#### 6.6.2 路由轨迹数据结构

```python
class RouteTrace(BaseModel):
    trace_id: str
    timestamp: datetime
    user_input: str            # 前 100 字，超出截断
    complexity_level: str
    privacy_level: str
    context_tokens: int
    tier: str                  # device / edge / cloud
    model_used: str
    route_reason: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_hit: bool
    privacy_desensitized: bool
    error: str | None
```

#### 6.6.3 前端页面要求

**Dashboard 主页**（`/`）包含：

1. **KPI 卡片行**（4 个）
   - 端/边命中率（目标 ≥68%，绿色达标/红色未达标）
   - 云端 Token 节省率（对比全走云的基准）
   - P50 首 Token 延迟
   - S3 数据本地率（始终 100%）

2. **实时路由分布**
   - 横向堆叠条形图（端/边/云三色，比例实时更新）
   - 下方数字标注各层请求数

3. **路由轨迹列表**
   - 最近 20 条，实时刷新（WebSocket）
   - 每行显示：层级徽章（颜色区分）、用户输入前缀、模型、延迟、费用
   - 点击展开完整详情

4. **Demo Runner 面板**
   - 10 个 Demo Case 按钮，点击触发
   - 触发后实时显示路由决策过程动画
   - 展示最终结果 + 与"全走云"的对比（延迟/费用）

5. **三层健康状态**
   - 端（Ollama）/ 边（vLLM）/ 云（DeepSeek API）在线/离线指示灯
   - 显示各层当前 P50 延迟

---

## 7. Docker Compose 完整配置

```yaml
# docker-compose.yml
version: "3.9"

services:
  # ── 数据库 ────────────────────────────────────────────
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      retries: 5

  redis:
    image: redis:7.2-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      retries: 5

  # ── 端层推理 ──────────────────────────────────────────
  ollama:
    image: ollama/ollama:latest
    volumes:
      - ollama_data:/root/.ollama
    ports:
      - "11434:11434"
    healthcheck:
      test: ["CMD-SHELL", "ollama list || exit 1"]
      interval: 30s
      retries: 3
    # GPU 支持（飞腾/ARM 环境去掉 deploy 块）
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  ollama-init:
    image: ollama/ollama:latest
    volumes:
      - ollama_data:/root/.ollama
    depends_on:
      ollama:
        condition: service_healthy
    entrypoint: ["/bin/sh", "-c"]
    command:
      - |
        ollama pull qwen3:1.5b
        ollama pull nomic-embed-text
        echo "Models ready."
        # v1.1: qwen3:0.6b 已移除（不再用 Ollama 做分类）
        # semantic-router 使用 HF sentence-transformers，routellm 权重单独下载
    restart: "no"

  # ── 边层推理（需要 NVIDIA GPU）────────────────────────
  vllm:
    image: vllm/vllm-openai:latest
    command: >
      --model ${VLLM_EDGE_MODEL}
      --tensor-parallel-size ${VLLM_TENSOR_PARALLEL}
      --max-model-len ${VLLM_MAX_MODEL_LEN}
      --gpu-memory-utilization ${VLLM_GPU_MEMORY_UTILIZATION}
      --enable-prefix-caching
      --served-model-name deepseek-v4-flash
      --trust-remote-code
    environment:
      - HF_TOKEN=${HF_TOKEN:-}
    volumes:
      - vllm_cache:/root/.cache/huggingface
    ports:
      - "8000:8000"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8000/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 120s
    profiles:
      - gpu   # 使用 --profile gpu 启动

  # ── 路由网关 ──────────────────────────────────────────
  gateway:
    build:
      context: ./gateway
      dockerfile: Dockerfile
    ports:
      - "${LITELLM_PORT}:4000"
      - "4001:4001"   # Prometheus metrics
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      ollama:
        condition: service_healthy
    volumes:
      - ./gateway/litellm_config.yaml:/app/config.yaml:ro
      - routellm_cache:/app/.routellm_cache   # v1.1: 跨重启复用 routellm 权重
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:4000/health || exit 1"]
      interval: 15s
      retries: 5
      start_period: 60s   # v1.1: 首次启动需下载 routellm 权重，给更多时间
    restart: unless-stopped

  # ── Dashboard 后端 ────────────────────────────────────
  dashboard:
    build:
      context: ./dashboard
      dockerfile: Dockerfile
    ports:
      - "${DASHBOARD_PORT}:8080"
    env_file: .env
    depends_on:
      gateway:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  # ── 前端 UI ───────────────────────────────────────────
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    ports:
      - "3000:8080"
    environment:
      - OPENAI_API_BASE_URL=http://gateway:4000/v1
      - OPENAI_API_KEY=${LITELLM_MASTER_KEY}
      - WEBUI_AUTH=false    # 演示环境关闭鉴权
    volumes:
      - open_webui_data:/app/backend/data
    depends_on:
      gateway:
        condition: service_healthy
    restart: unless-stopped

  # ── 监控 ─────────────────────────────────────────────
  prometheus:
    image: prom/prometheus:v2.50.1
    ports:
      - "${PROMETHEUS_PORT}:9090"
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.retention.time=7d"
    restart: unless-stopped

  grafana:
    image: grafana/grafana:10.4.0
    ports:
      - "${GRAFANA_PORT}:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - grafana_data:/var/lib/grafana
      - ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro
    depends_on:
      - prometheus
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
  ollama_data:
  vllm_cache:
  open_webui_data:
  grafana_data:
  routellm_cache:   # v1.1: routellm mf 权重持久化（~200MB，首次下载后跨重启复用）
```

---

## 8. Gateway Dockerfile

```dockerfile
# gateway/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl build-essential && \
    rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# v1.1：先预热路由器（下载 HF 权重），再启动 LiteLLM
# 权重缓存在 ROUTELLM_CACHE_DIR（volume 挂载时跨重启复用）
ENV ROUTELLM_CACHE_DIR=/app/.routellm_cache
CMD ["sh", "-c", "python warmup.py && python -m litellm --config /app/config.yaml --host 0.0.0.0 --port 4000"]
```

```
# gateway/requirements.txt
litellm[proxy]==1.*
# ── v1.1: 分类器核心库 ──────────────────────────────────────────────────────
semantic-router[local]   # 向量语义路由，HuggingFace Encoder，全本地无 API Key
routellm                 # ICLR 2025 预训练 mf 路由器权重
tiktoken                 # Token 计数（用于上下文长度检测）
# ── 其余依赖 ────────────────────────────────────────────────────────────────
fastapi
uvicorn[standard]
redis[hiredis]
redisvl
httpx
pydantic>=2.0
python-dotenv
prometheus-client
structlog
tenacity
```

---

## 9. Prometheus 配置

```yaml
# monitoring/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "litellm"
    static_configs:
      - targets: ["gateway:4001"]

  - job_name: "dashboard"
    static_configs:
      - targets: ["dashboard:8080"]

  - job_name: "vllm"
    static_configs:
      - targets: ["vllm:8000"]
    metrics_path: /metrics

  - job_name: "redis"
    static_configs:
      - targets: ["redis:6379"]
```

---

## 10. 10 个 Demo Case 规格

以下是完整的 Demo Case 定义，Dashboard 的 Demo Runner 需要能逐个触发并展示结果。

### Case 定义数据结构

```python
class DemoCase(BaseModel):
    id: int
    name: str
    description: str
    prompt: str                    # 实际发送给网关的用户消息
    expected_tier: RouteTier       # 期望路由层级
    expected_complexity: str       # 期望复杂度等级
    expected_privacy: str          # 期望隐私等级
    max_expected_latency_ms: int   # 最大可接受延迟
    verify_fn: str                 # 验证函数名（在 test_demo_cases.py 中实现）
```

### 10 个 Case 定义

```python
DEMO_CASES = [
    DemoCase(
        id=1,
        name="简单文字润色",
        description="日常 IM 回复改写，最简单的任务，应当全走端侧",
        prompt="帮我把这句话改得更专业一点：'那个报告我明天给你'",
        expected_tier=RouteTier.DEVICE,
        expected_complexity="SIMPLE",
        expected_privacy="S3",
        max_expected_latency_ms=800,
        verify_fn="verify_text_improved",
    ),
    DemoCase(
        id=2,
        name="会议录音转纪要",
        description="中等长度文本，提取待办事项，走端侧或边侧",
        prompt="""
以下是一段会议记录，请提取关键决策和待办事项：

张总：本次季度目标是完成 CEC 展会演示，5月28日前必须搞定。
李工：硬件部分飞腾板已到位，软件接口还缺两个模块。
王总：预算追加 20 万，用于采购 GPU 服务器。李工负责采购。
张总：下次会议定在周五上午10点，所有人必须到场。
        """,
        expected_tier=RouteTier.DEVICE,
        expected_complexity="MEDIUM",
        expected_privacy="S2",
        max_expected_latency_ms=3000,
        verify_fn="verify_meeting_minutes",
    ),
    DemoCase(
        id=3,
        name="本地文件摘要（S1隐私，不出端）",
        description="包含机密关键词，隐私等级 S1，强制走端侧，绝不上云",
        prompt="请总结以下内容（机密文件，仅供内部使用）：本公司研发的新型飞腾处理器散热方案采用液冷+风冷混合结构，热设计功耗 45W，较上一代降低 30%。核心技术参数：热阻 0.12 °C/W，最大散热功率 120W。",
        expected_tier=RouteTier.DEVICE,
        expected_complexity="MEDIUM",
        expected_privacy="S1",
        max_expected_latency_ms=5000,
        verify_fn="verify_local_only",     # 验证响应头中 tier == device
    ),
    DemoCase(
        id=4,
        name="长文档多文件综合分析",
        description="复杂分析任务，走边侧预处理 + 云端综合（S2脱敏后上云）",
        prompt="""
请对以下三份内部报告进行综合分析，指出主要风险点和改进建议：

【报告1 - 市场分析】我公司在华东市场占有率从 23% 下降至 18%，主要竞争对手为 A 公司和 B 公司。客户流失原因调研显示，60% 认为我方产品价格偏高，30% 认为交付周期过长。

【报告2 - 财务数据】Q3 营收 4.2 亿，同比下降 8%。采购成本上升 12%，导致毛利率从 35% 降至 29%。主要原因是原材料涨价和汇率波动。

【报告3 - 产品路线图】新产品 P系列计划 Q4 上市，已通过内部测试，预计可降低 15% 生产成本。目标客户定位中高端企业市场，定价策略待定。

请给出：1）核心问题分析 2）P系列产品对扭转局势的可行性评估 3）3项优先行动建议
        """,
        expected_tier=RouteTier.CLOUD,
        expected_complexity="RESEARCH",
        expected_privacy="S2",
        max_expected_latency_ms=20000,
        verify_fn="verify_comprehensive_analysis",
    ),
    DemoCase(
        id=5,
        name="企业知识库问答",
        description="基于上下文的知识检索问答，走边侧",
        prompt="""
根据以下技术文档回答问题：

【文档：开天X7技术规格】
- 处理器：飞腾 D3000，8核 ARM，主频 2.5GHz
- 内存：32GB DDR5，支持扩展至 64GB
- 存储：512GB NVMe SSD，支持 RAID 1
- 网络：2x 10GbE，1x 1GbE 管理口
- AI加速：内置 NPU，支持 INT8/FP16，算力 8 TOPS
- 功耗：最大 95W，典型负载 65W
- 工作温度：0~55°C
- 尺寸：1U 机架式，438x44x380mm

问题：开天X7最多可以配置多少内存？它的AI算力是多少？在 40°C 的环境下能正常工作吗？
        """,
        expected_tier=RouteTier.EDGE,
        expected_complexity="MEDIUM",
        expected_privacy="S3",
        max_expected_latency_ms=5000,
        verify_fn="verify_qa_accuracy",
    ),
    DemoCase(
        id=6,
        name="代码解释 / Bug 定位",
        description="中等复杂度代码问题，先走端，复杂时升级到边",
        prompt="""
以下 Python 代码运行时报错 'KeyError: model_info'，帮我找出 Bug 并修复：

```python
def route_request(messages, config):
    complexity = classify(messages)
    tier_map = {
        "SIMPLE": config["models"]["device"],
        "MEDIUM": config["models"]["edge"],
        "COMPLEX": config["models"]["cloud"],
    }
    selected = tier_map.get(complexity)
    model_name = selected["model_info"]["name"]  # 报错在这里
    return call_model(model_name, messages)
```
        """,
        expected_tier=RouteTier.DEVICE,
        expected_complexity="MEDIUM",
        expected_privacy="S3",
        max_expected_latency_ms=5000,
        verify_fn="verify_bug_fix",
    ),
    DemoCase(
        id=7,
        name="Excel 数据分析生成报告",
        description="结构化数据分析 + 报告生成，走边侧或云",
        prompt="""
以下是某产品线的月度销售数据，请进行分析并生成结构化报告：

月份 | 销售额(万) | 环比增长 | 客户数 | 退货率
1月  | 320        | -         | 156    | 2.1%
2月  | 285        | -10.9%    | 142    | 3.4%
3月  | 412        | +44.6%    | 198    | 1.8%
4月  | 398        | -3.4%     | 189    | 2.2%
5月  | 445        | +11.8%    | 215    | 1.6%

请输出：1）趋势分析 2）异常月份说明 3）Q2预测 4）改善退货率的建议
        """,
        expected_tier=RouteTier.EDGE,
        expected_complexity="COMPLEX",
        expected_privacy="S2",
        max_expected_latency_ms=10000,
        verify_fn="verify_data_report",
    ),
    DemoCase(
        id=8,
        name="多应用任务拆解：邮件+查资料+整理表格",
        description="复合任务，系统自动拆解为子任务并行执行，展示任务拆解过程",
        prompt="""
我需要你帮我完成以下三件事：
1. 起草一封给供应商的延迟付款通知邮件（原因：月底资金周转，延迟15天支付）
2. 解释一下什么是 MoE（混合专家）架构，为什么 DeepSeek V4 使用它
3. 把以下数据整理成 Markdown 表格：苹果/15元/100个、香蕉/8元/200个、橙子/12元/150个
        """,
        expected_tier=RouteTier.EDGE,
        expected_complexity="COMPLEX",
        expected_privacy="S3",
        max_expected_latency_ms=15000,
        verify_fn="verify_multi_task",
    ),
    DemoCase(
        id=9,
        name="高隐私材料总结（S1，端/边侧锁定）",
        description="包含人事+财务双重敏感信息，S1等级，绝不上公有云",
        prompt="""
请对以下人事档案进行脱敏摘要（机密，仅内部HR使用）：

员工：张某某，身份证 320101198801011234
岗位：高级工程师，薪资 38000元/月，奖金结构另附
评级：A级，晋升序列中，预计2026年Q3晋升为技术总监
绩效：连续3年优秀，2025年完成专利申报3项
家庭状况：已婚，一孩，购房贷款中
        """,
        expected_tier=RouteTier.DEVICE,
        expected_complexity="MEDIUM",
        expected_privacy="S1",
        max_expected_latency_ms=8000,
        verify_fn="verify_s1_local_only",   # 验证无任何内容发送到云 API
    ),
    DemoCase(
        id=10,
        name="超长合同标书分析（1M上下文）",
        description="大体量文档，需要云端 V4-Pro 的 1M 上下文能力，S2 脱敏后上云",
        prompt="""
以下是一份采购合同的核心条款（已脱敏），请进行法律风险评估：

【合同编号】2026-CEC-购-0042
【甲方】某电子科技有限公司（买方）
【乙方】某供应链科技有限公司（卖方）
【合同金额】人民币 1,280 万元整
【交货期】自合同签订之日起 45 个工作日内

【主要条款】
第三条（质量标准）：产品须符合 GB/T 28001-2011 标准，验收合格率不低于 99.5%
第七条（违约责任）：乙方逾期交货，每日按合同总额的 0.05% 支付违约金，最高不超过合同总额的 10%
第九条（知识产权）：乙方交付物中的全部知识产权归甲方所有，乙方不得对第三方披露
第十二条（争议解决）：争议提交【某市】仲裁委员会仲裁，适用中国法律
第十五条（不可抗力）：疫情、自然灾害等不可抗力免责，但须在 72 小时内书面通知

请分析：1）对甲方的主要风险点 2）知识产权条款是否完善 3）违约条款力度评估 4）建议修改的3个关键条款
        """,
        expected_tier=RouteTier.CLOUD,
        expected_complexity="RESEARCH",
        expected_privacy="S2",
        max_expected_latency_ms=30000,
        verify_fn="verify_legal_analysis",
    ),
]
```

---

## 11. 测试规格（`tests/test_demo_cases.py`）

```python
"""
Demo Case 自动化测试。
运行方式：pytest tests/test_demo_cases.py -v
前置条件：所有服务已通过 docker-compose up 启动
"""

import pytest
import httpx
import asyncio
from typing import Any

GATEWAY_URL = "http://localhost:4000"
DASHBOARD_URL = "http://localhost:8080"
API_KEY = "change-me-local-gateway-key"

@pytest.fixture(scope="session")
def client():
    return httpx.Client(
        base_url=GATEWAY_URL,
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=60.0,
    )

# ── 验证函数 ──────────────────────────────────────────────────────────────

def verify_local_only(response: dict, trace: dict) -> bool:
    """验证请求只走了端侧，未调用云 API"""
    return trace["tier"] == "device"

def verify_s1_local_only(response: dict, trace: dict) -> bool:
    """验证 S1 隐私数据绝对不上云"""
    assert trace["privacy_level"] == "S1"
    assert trace["tier"] in ("device", "edge")
    assert trace["tier"] != "cloud", "FAIL: S1 data went to cloud!"
    return True

def verify_classifier_used(trace: dict) -> None:
    """v1.1: 验证分类器字段存在且来源合法"""
    assert "classifier_used" in trace
    assert trace["classifier_used"] in (
        "semantic_router", "routellm_mf",
        "heuristic_context_length", "heuristic_fallback"
    ), f"Unknown classifier: {trace['classifier_used']}"

def verify_comprehensive_analysis(response: dict, trace: dict) -> bool:
    content = response["choices"][0]["message"]["content"]
    assert "风险" in content or "问题" in content
    assert "建议" in content
    verify_classifier_used(trace)
    return True

def verify_text_improved(response: dict, trace: dict) -> bool:
    content = response["choices"][0]["message"]["content"]
    assert len(content) > 5
    verify_classifier_used(trace)
    return True

def verify_meeting_minutes(response: dict, trace: dict) -> bool:
    content = response["choices"][0]["message"]["content"]
    assert any(k in content for k in ["待办", "决策", "行动", "负责", "会议"])
    verify_classifier_used(trace)
    return True

def verify_bug_fix(response: dict, trace: dict) -> bool:
    content = response["choices"][0]["message"]["content"]
    assert any(k in content for k in ["model_info", "KeyError", "fix", "修复", "问题"])
    verify_classifier_used(trace)
    return True

def verify_qa_accuracy(response: dict, trace: dict) -> bool:
    content = response["choices"][0]["message"]["content"]
    assert "64" in content or "TOPS" in content
    verify_classifier_used(trace)
    return True

def verify_data_report(response: dict, trace: dict) -> bool:
    content = response["choices"][0]["message"]["content"]
    assert any(k in content for k in ["趋势", "分析", "建议", "预测", "退货"])
    verify_classifier_used(trace)
    return True

def verify_multi_task(response: dict, trace: dict) -> bool:
    content = response["choices"][0]["message"]["content"]
    assert any(k in content for k in ["邮件", "MoE", "表格", "专家"])
    verify_classifier_used(trace)
    return True

def verify_legal_analysis(response: dict, trace: dict) -> bool:
    content = response["choices"][0]["message"]["content"]
    assert any(k in content for k in ["风险", "知识产权", "违约", "条款"])
    verify_classifier_used(trace)
    return True


@pytest.mark.parametrize("case", DEMO_CASES)
async def test_demo_case(client, case):
    """对每个 Demo Case 验证路由决策和响应质量"""
    resp = client.post("/v1/chat/completions", json={
        "model": "auto",
        "messages": [{"role": "user", "content": case.prompt}],
        "stream": False,
    })
    assert resp.status_code == 200, f"Case {case.id} failed: {resp.text}"

    trace_id = resp.headers.get("X-Trace-Id")
    trace_resp = httpx.get(f"{DASHBOARD_URL}/api/traces/{trace_id}")
    trace = trace_resp.json()

    assert trace["tier"] == case.expected_tier.value, \
        f"Case {case.id}: expected tier {case.expected_tier}, got {trace['tier']}"
    assert trace["complexity_level"] == case.expected_complexity
    assert trace["privacy_level"] == case.expected_privacy
    assert trace["latency_ms"] <= case.max_expected_latency_ms

    # v1.1: 额外验证分类器来源字段
    verify_classifier_used(trace)

    verify_fn = globals()[case.verify_fn]
    assert verify_fn(resp.json(), trace)


# v1.1 新增：分类器单元测试（不依赖完整 Docker 环境，可本地直接运行）
class TestClassifierUnit:
    """直接测试 classifier.py，不需要启动 Docker"""

    def test_semantic_router_simple(self):
        import asyncio
        from gateway.classifier import classify
        result = asyncio.run(classify([{"role": "user", "content": "你好"}]))
        assert result.level.value in ("SIMPLE", "MEDIUM")
        assert result.classifier_used in ("semantic_router", "routellm_mf", "heuristic_fallback")
        assert result.latency_ms < 500  # semantic-router 应在 <5ms，routellm <50ms

    def test_routellm_complex(self):
        import asyncio
        from gateway.classifier import classify
        result = asyncio.run(classify([{
            "role": "user",
            "content": "请对以下三份报告进行综合深度分析，指出风险点并给出系统架构设计建议"
        }]))
        assert result.level.value in ("COMPLEX", "RESEARCH", "REASONING")
        assert result.confidence > 0.5

    def test_context_length_shortcircuit(self):
        import asyncio, os
        os.environ["CONTEXT_CLOUD_THRESHOLD_TOKENS"] = "100"
        from gateway.classifier import classify
        long_text = "这是一段很长的文字。" * 200
        result = asyncio.run(classify([{"role": "user", "content": long_text}]))
        assert result.level.value == "REASONING"
        assert result.classifier_used == "heuristic_context_length"

    def test_cache_hit(self):
        import asyncio
        from gateway.classifier import classify
        msg = [{"role": "user", "content": "帮我写一封邮件感谢客户"}]
        r1 = asyncio.run(classify(msg))
        r2 = asyncio.run(classify(msg))
        # 无 Redis 时两次都是 cache_hit=False（可接受）；有 Redis 时第二次应命中
        assert r1.level == r2.level  # 结果一致即可
```

---

## 12. `start.sh` 脚本

```bash
#!/bin/bash
set -e

echo "══════════════════════════════════════════"
echo " 端-边-云混合推理调度系统  启动脚本"
echo "══════════════════════════════════════════"

# 检查 .env 文件
if [ ! -f .env ]; then
    echo "⚠️  未找到 .env 文件，正在从 .env.example 创建..."
    cp .env.example .env
    echo "✅ 请编辑 .env 填入真实的 API Key，然后重新运行 ./start.sh"
    exit 1
fi

# 检查 DeepSeek API Key
source .env
if [[ "$DEEPSEEK_API_KEY" == "your-deepseek-api-key" ]]; then
    echo "⚠️  警告：未配置 DEEPSEEK_API_KEY，云层将不可用（仅端/边模式）"
fi

# 判断是否有 GPU
GPU_MODE=""
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    echo "✅ 检测到 NVIDIA GPU，启用边侧 vLLM"
    GPU_MODE="--profile gpu"
else
    echo "⚠️  未检测到 GPU，跳过 vLLM 边侧（仅端+云模式）"
fi

# 启动基础服务
echo "▶  启动基础服务（postgres, redis）..."
docker compose up -d postgres redis

echo "⏳ 等待数据库就绪..."
sleep 5

# 启动 Ollama 并拉取模型
echo "▶  启动 Ollama（端侧推理）..."
docker compose up -d ollama
echo "⏳ 等待 Ollama 就绪并拉取模型（首次运行可能需要几分钟）..."
docker compose run --rm ollama-init

# 启动 vLLM（如有 GPU）
if [ -n "$GPU_MODE" ]; then
    echo "▶  启动 vLLM 边侧推理..."
    docker compose $GPU_MODE up -d vllm
    echo "⏳ 等待 vLLM 加载模型（首次运行可能需要 3-5 分钟）..."
fi

# 启动网关和其他服务
echo "▶  启动路由网关..."
docker compose up -d gateway

echo "⏳ 等待网关就绪..."
for i in {1..30}; do
    if curl -sf http://localhost:4000/health >/dev/null 2>&1; then
        echo "✅ 路由网关已就绪"
        break
    fi
    sleep 2
done

echo "▶  启动 Dashboard、Open WebUI、监控..."
docker compose up -d dashboard open-webui prometheus grafana

echo ""
echo "══════════════════════════════════════════"
echo " 系统启动完成！"
echo "══════════════════════════════════════════"
echo ""
echo "  🌐 Open WebUI     : http://localhost:3000"
echo "  📊 路由 Dashboard : http://localhost:8080"
echo "  🔀 LiteLLM 网关   : http://localhost:4000"
echo "  📈 Grafana 监控   : http://localhost:3001  (admin / \$GRAFANA_PASSWORD)"
echo "  🔍 Prometheus     : http://localhost:9090"
echo ""
echo "  运行 Demo Cases:  python scripts/run_demo.py"
echo "  运行测试套件:     pytest tests/ -v"
echo "  查看日志:         docker compose logs -f gateway"
echo ""
```

---

## 13. SETUP.md 要求

Codex 生成的 `SETUP.md` 必须包含：

1. **系统要求**（OS、Docker 版本、GPU 要求、内存要求）
2. **快速启动**（5步内从零到运行）
3. **配置说明**（每个 `.env` 变量的作用和推荐值）
4. **无 GPU 降级方案**（仅端+云，无边侧 vLLM）
5. **飞腾 ARM 平台适配说明**（Ollama ARM64 支持确认）
6. **10 个 Demo Case 运行方法**
7. **常见问题排查**（至少 8 个 FAQ）
8. **卸载/清理命令**

---

## 14. 关键约束与注意事项

### 14.1 不可违反的约束

1. **S1 隐私数据绝对不能发送到任何外部 API**，包括云端，这是硬性要求
2. **语义缓存必须区分隐私等级**，S1/S2 数据的缓存 key 与 S3 分开存储
3. **路由决策必须记录完整轨迹**，包括理由，可在 Dashboard 查看
4. **降级路由必须自动**，边侧不可用时自动降级到端或云，不能让请求挂起

### 14.2 飞腾 ARM 平台适配

- Ollama 支持 ARM64，直接安装，无需特殊配置
- vLLM 在飞腾 D3000 上不可用（无 NVIDIA GPU），飞腾节点只运行 Ollama
- Docker Compose 中 vLLM 服务使用 `profiles: [gpu]`，飞腾环境不启动此服务

### 14.3 性能基准（GB10 DGX Spark 边侧）

基于 GB10 128GB 统一内存的预期性能（需在目标硬件上实测后更新）：
- DeepSeek-V4-Flash（FP8 量化）：目标 ≥ 20 tokens/s decode
- Qwen3-32B（FP8）：目标 ≥ 10 tokens/s decode
- 最大并发：4 路并发请求

### 14.4 云端 API 成本控制

- 请求到云端前必须经过 Token 预估
- 单次请求预估 Token 超过 200K 时弹出确认（Dashboard 中配置）
- 每日云端 Token 总量上限通过 LiteLLM Budget 配置（默认 $10/day）

---

## 15. 验收标准

Codex 完成实现后，以下验收测试必须全部通过：

```bash
# 1. 所有服务健康
docker compose ps | grep -v "Exit"

# 2. 端侧路由验证
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer change-me-local-gateway-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "你好"}]}' \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print('PASS: tier=' + r.get('tier','?'))"

# 3. S1 隐私隔离验证
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer change-me-local-gateway-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "机密：工资单总计 120 万"}]}' \
  | python3 -c "import sys,json; r=json.load(sys.stdin); assert r.get('tier') != 'cloud', 'FAIL: S1 data went to cloud!'; print('PASS: S1 data stayed local')"

# 4. Demo Cases 全部通过
pytest tests/test_demo_cases.py -v --tb=short

# 5. Dashboard 可访问
curl -sf http://localhost:8080/api/health | python3 -c "import sys,json; r=json.load(sys.stdin); print('PASS' if r['status']=='ok' else 'FAIL')"

# 6. Grafana 预置 Dashboard 加载
curl -sf -u admin:${GRAFANA_PASSWORD} http://localhost:3001/api/dashboards/home | python3 -c "import sys,json; r=json.load(sys.stdin); print('PASS' if 'dashboard' in r else 'FAIL')"
```

---

## 16. v1.1 分类器迁移说明（给 Codex）

### 16.1 为什么换掉 LLM-as-Judge

| 问题 | 旧方案（qwen3:0.6b Ollama） | 新方案（SR + RouteLLM） |
|------|--------------------------|----------------------|
| 延迟 | 500ms–2s（等待 LLM 生成） | SR: <5ms；RouteLLM: ~20ms |
| 脆弱性 | JSON 解析失败需重试逻辑 | 返回结构化数值，无解析风险 |
| 准确度 | Prompt 工程依赖，难调优 | mf 路由器有 ICLR 2025 基准验证 |
| 资源 | 占用 Ollama 显存/CPU | 纯 Python CPU，不抢推理资源 |
| 维护 | Prompt 需随任务类型更新 | utterances 增减即可扩展路由规则 |

### 16.2 两级流水线优先级

```
请求
  │
  ▼
[L1] semantic-router 向量匹配
  ├── 置信度 > 0.75 → 直接返回（<5ms，覆盖约 50% 常规请求）
  └── 置信度 ≤ 0.75 → 进入 L2
              │
              ▼
         [L2] routellm mf 路由器
              ├── 正常返回 strong_win_rate → 映射到五档复杂度（~20ms）
              └── 异常（首次下载/网络问题）→ heuristic_fallback（纯规则，0ms）
```

### 16.3 首次启动流程（重要）

1. `docker compose up gateway` 触发 `warmup.py`
2. warmup.py 调用 `_get_semantic_router()`：下载 `all-MiniLM-L6-v2`（~90MB，HuggingFace）
3. warmup.py 调用 `_get_routellm_controller()`：下载 mf 权重（~200MB，HuggingFace）
4. 两个模型均缓存到 `routellm_cache` volume，**重启后无需重新下载**
5. `start_period: 60s` 给足首次下载时间，后续重启 <10s 可就绪

### 16.4 semantic-router utterances 定制指南

如需针对中文企业场景优化路由准确度，在 `classifier.py` 的 `ROUTES` 列表中
为每个 Route 追加中文 utterances 即可，例如：

```python
Route(
    name="RESEARCH",
    utterances=[
        # 原有英文 utterances...
        # 追加中文企业场景
        "对这份标书进行深度分析", "综合评估供应商方案",
        "审阅这份合同的法律风险", "对比多个解决方案的优劣",
        "基于多份报告给出战略建议",
    ],
),
```

更新后无需重新训练，semantic-router 在本地实时重新计算向量索引。

### 16.5 routellm 阈值调优

`_routellm_to_complexity()` 函数中的分段阈值（0.20 / 0.40 / 0.60 / 0.80）
可根据实测结果调整。建议在跑完 10 个 Demo Case 后，
用 Dashboard 的路由轨迹数据统计各复杂度等级的实际 strong_win_rate 分布，
再反向调整分段边界。

---

*文档结束。Codex：请从 `hybrid-router/` 目录开始，按本文档完整实现所有模块。实现过程中如发现规格冲突，以"不可违反的约束"（第14.1节）为最高优先级。*
