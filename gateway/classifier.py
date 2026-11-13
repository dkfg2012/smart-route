from __future__ import annotations

import hashlib
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from models import ClassificationResult, ComplexityLevel
from token_estimator import count_message_tokens, text_from_messages


class ClassifierUnavailable(RuntimeError):
    pass


class SemanticRouteEngine(Protocol):
    def classify(self, text: str) -> tuple[ComplexityLevel, float, str] | None:
        ...


class RouteLLMEngine(Protocol):
    def classify(self, text: str) -> tuple[ComplexityLevel, float, str]:
        ...


@lru_cache(maxsize=1)
def _get_local_embedding_model() -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        raise ClassifierUnavailable("sentence-transformers is required for offline RouteLLM embeddings") from exc
    return SentenceTransformer(resolve_encoder_model_path())


class LocalOpenAIEmbeddingClient:
    """
    Minimal OpenAI-compatible embeddings client for RouteLLM mf.

    RouteLLM 0.2.0's mf router loads local weights but calls OpenAI for the
    prompt embedding. In offline deployments we provide embeddings from the
    same ModelScope-resolved sentence-transformer used by semantic-router and
    adapt the vector length to the mf model's configured text dimension.
    """

    class _Embeddings:
        def create(self, input: list[str], model: str) -> Any:
            del model
            encoder = _get_local_embedding_model()
            vectors = encoder.encode(input, normalize_embeddings=True)
            data = []
            for vector in vectors:
                values = list(map(float, vector))
                target_dim = int(os.getenv("ROUTELLM_TEXT_DIM", "1536"))
                if len(values) < target_dim:
                    values = values + [0.0] * (target_dim - len(values))
                elif len(values) > target_dim:
                    values = values[:target_dim]
                data.append(SimpleNamespace(embedding=values))
            return SimpleNamespace(data=data)

    def __init__(self) -> None:
        self.embeddings = self._Embeddings()


def configure_offline_routellm_embeddings() -> None:
    if os.getenv("ROUTELLM_LOCAL_EMBEDDINGS", "true").lower() not in ("1", "true", "yes"):
        return
    try:
        import routellm.routers.matrix_factorization.model as mf_model
        import routellm.routers.similarity_weighted.utils as sw_utils
    except Exception as exc:
        raise ClassifierUnavailable("cannot patch RouteLLM local embeddings") from exc
    local_client = LocalOpenAIEmbeddingClient()
    mf_model.OPENAI_CLIENT = local_client
    sw_utils.OPENAI_CLIENT = local_client


ROUTE_UTTERANCES: dict[str, list[str]] = {
    "SIMPLE": [
        "你好",
        "帮我改一下这句话",
        "翻译成英文",
        "是或否",
        "Hello",
        "rewrite this sentence",
        "fix the grammar",
        "把这句改得更自然",
        "把这句改得更自然：这个方案还行但是有点慢",
        "这句话有点生硬，帮我润色一下",
        "帮我把一句话改顺一点",
        "润色这句话",
        "优化这句话的表达",
        "让这句话更口语化",
        "把这句话改得正式一点",
        "what time is it",
        "define this word",
        "spell check",
        "summarize in one sentence",
        "format this list",
    ],
    "MEDIUM": [
        "帮我写一封邮件",
        "提取会议纪要",
        "总结这份文档",
        "write an email",
        "extract action items",
        "summarize this document",
        "explain this code snippet",
        "generate a todo list",
        "draft a reply",
        "create a brief report",
        "整理成三条行动项",
        "把这段售后反馈整理成三条行动项：客户抱怨交付慢、文档不清楚、售后响应晚",
        "把售后反馈整理成行动项",
        "根据客户反馈生成待办事项",
        "总结客户反馈并列出下一步",
        "根据反馈整理三个改进动作",
    ],
    "COMPLEX": [
        "比较这两份文件的差异",
        "分析数据并生成报告",
        "多步骤推理",
        "compare multiple documents",
        "analyze data and generate report",
        "multi-step reasoning",
        "extract structured data from files",
        "build a comparison table",
        "synthesize information from sources",
    ],
    "RESEARCH": [
        "深度分析这份长报告",
        "综合多个来源的信息",
        "回答关于这本书的问题",
        "帮我写一篇论文，百度搜索下",
        "搜索资料并写一篇论文",
        "查找资料后写研究论文",
        "百度搜索相关资料并生成论文",
        "写一篇学术论文并引用资料",
        "调研资料并写文献综述",
        "联网搜索并撰写研究报告",
        "根据公开资料完成论文初稿",
        "围绕这个主题做资料检索和论文写作",
        "deep analysis of this report",
        "synthesize knowledge from multiple sources",
        "answer questions about this document",
        "research and fact-check",
        "review this long paper",
        "comprehensive literature review",
        "search the web and write an essay",
        "gather sources and draft a research paper",
        "write an academic paper with citations",
        "审阅这份合同的法律风险",
        "基于多份报告给出战略建议",
    ],
    "REASONING": [
        "证明这个数学定理",
        "证明任意连通无环图的边数等于节点数减一",
        "证明任意连通无环图的边数等于节点数减一，并给出严谨推导",
        "给出严谨数学推导",
        "完成复杂证明题",
        "设计系统架构",
        "review this entire codebase",
        "prove this theorem",
        "prove a graph theory theorem",
        "write a rigorous mathematical proof",
        "design a distributed system",
        "analyze this 200-page contract",
        "ultra-long context processing",
        "complex multi-agent planning",
        "write a research paper",
    ],
}

COMPLEXITY_TOKEN_HINTS = {
    ComplexityLevel.SIMPLE: 200,
    ComplexityLevel.MEDIUM: 800,
    ComplexityLevel.COMPLEX: 2500,
    ComplexityLevel.RESEARCH: 6000,
    ComplexityLevel.REASONING: 15000,
}


class SemanticRouterEngine:
    def __init__(self) -> None:
        try:
            from semantic_router import Route
            from semantic_router.encoders import HuggingFaceEncoder
            from semantic_router.routers import SemanticRouter
        except Exception as exc:
            raise ClassifierUnavailable(
                "semantic-router[local] is not installed or cannot initialize"
            ) from exc

        routes = [
            Route(name=name, utterances=utterances)
            for name, utterances in ROUTE_UTTERANCES.items()
        ]
        encoder_model = resolve_encoder_model_path()
        encoder = HuggingFaceEncoder(name=encoder_model)
        self.router = SemanticRouter(encoder=encoder, routes=routes, auto_sync="local")
        self.threshold = float(os.getenv("SEMANTIC_ROUTER_CONFIDENCE_THRESHOLD", "0.75"))

    def classify(self, text: str) -> tuple[ComplexityLevel, float, str] | None:
        route_choice = self.router(text)
        if not route_choice or not getattr(route_choice, "name", None):
            return None
        score = float(getattr(route_choice, "similarity_score", 0.0) or 0.0)
        if score < self.threshold:
            return None
        return ComplexityLevel(route_choice.name), score, "semantic_router"


class RouteLLMMFEngine:
    def __init__(self) -> None:
        try:
            from routellm.controller import Controller as RouteLLMController
        except Exception as exc:
            raise ClassifierUnavailable("routellm is not installed or cannot initialize") from exc

        self.router_name = os.getenv("ROUTELLM_ROUTER", "mf")
        configure_offline_routellm_embeddings()
        strong_model = os.getenv("ROUTELLM_STRONG_MODEL", "gpt-4-1106-preview")
        weak_model = os.getenv("ROUTELLM_WEAK_MODEL", "mixtral-8x7b-instruct-v0.1")
        default_checkpoint = {
            "mf": "routellm/mf_gpt4_augmented",
            "bert": "routellm/bert_gpt4_augmented",
            "causal_llm": "routellm/causal_llm_gpt4_augmented",
        }.get(self.router_name, "routellm/mf_gpt4_augmented")
        self.controller = RouteLLMController(
            routers=[self.router_name],
            strong_model=strong_model,
            weak_model=weak_model,
            config={
                self.router_name: {
                    "checkpoint_path": os.getenv(
                        "ROUTELLM_MF_CHECKPOINT",
                        default_checkpoint,
                    )
                }
            },
        )

    def classify(self, text: str) -> tuple[ComplexityLevel, float, str]:
        router = self.controller.routers[self.router_name]
        win_rate = float(router.calculate_strong_win_rate(text))
        level, confidence = routellm_to_complexity(win_rate)
        return level, confidence, f"routellm_{self.router_name}"


def routellm_to_complexity(strong_win_rate: float) -> tuple[ComplexityLevel, float]:
    if strong_win_rate < 0.20:
        return ComplexityLevel.SIMPLE, 1.0 - strong_win_rate
    if strong_win_rate < 0.40:
        return ComplexityLevel.MEDIUM, 1.0 - abs(strong_win_rate - 0.30)
    if strong_win_rate < 0.60:
        return ComplexityLevel.COMPLEX, 1.0 - abs(strong_win_rate - 0.50)
    if strong_win_rate < 0.80:
        return ComplexityLevel.RESEARCH, strong_win_rate
    return ComplexityLevel.REASONING, strong_win_rate


@lru_cache(maxsize=1)
def _get_semantic_router() -> SemanticRouterEngine:
    return SemanticRouterEngine()


@lru_cache(maxsize=1)
def _get_routellm_controller() -> RouteLLMMFEngine:
    return RouteLLMMFEngine()


def _cache_key(text: str) -> str:
    return f"clf:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:20]}"


async def _read_cache(redis_client: Any, key: str) -> ClassificationResult | None:
    if redis_client is None:
        return None
    raw = await redis_client.get(key)
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return ClassificationResult.model_validate_json(raw)


async def _write_cache(redis_client: Any, key: str, result: ClassificationResult) -> None:
    if redis_client is None:
        return
    ttl = int(os.getenv("SEMANTIC_CACHE_TTL", "3600"))
    await redis_client.setex(key, ttl, result.model_dump_json())


async def classify(
    messages: list[dict[str, Any]],
    redis_client: Any | None = None,
    semantic_engine: SemanticRouteEngine | None = None,
    routellm_engine: RouteLLMEngine | None = None,
) -> ClassificationResult:
    started = time.perf_counter()
    text = text_from_messages(messages, user_only=True)[-4000:]
    if not text.strip():
        raise ClassifierUnavailable("cannot classify an empty user prompt")

    context_tokens = count_message_tokens(messages)
    key = _cache_key(text)
    cached = await _read_cache(redis_client, key)
    if cached:
        cached.cache_hit = True
        cached.latency_ms = (time.perf_counter() - started) * 1000
        return cached

    errors: list[str] = []

    semantic = semantic_engine
    if semantic is None:
        try:
            semantic = _get_semantic_router()
        except Exception as exc:
            errors.append(f"semantic_router unavailable: {exc}")

    if semantic is not None:
        try:
            routed = semantic.classify(text)
        except Exception as exc:
            routed = None
            errors.append(f"semantic_router failed: {exc}")
        if routed is not None:
            level, confidence, used = routed
            result = ClassificationResult(
                level=level,
                confidence=confidence,
                reasoning=f"via {used} | ctx_tokens={context_tokens}",
                estimated_tokens=COMPLEXITY_TOKEN_HINTS[level],
                cache_hit=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                classifier_used=used,
            )
            await _write_cache(redis_client, key, result)
            return result

    route_llm = routellm_engine
    if route_llm is None:
        try:
            route_llm = _get_routellm_controller()
        except Exception as exc:
            errors.append(f"routellm unavailable: {exc}")

    if route_llm is not None:
        try:
            level, confidence, used = route_llm.classify(text)
            result = ClassificationResult(
                level=level,
                confidence=confidence,
                reasoning=f"via {used} | ctx_tokens={context_tokens}",
                estimated_tokens=COMPLEXITY_TOKEN_HINTS[level],
                cache_hit=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                classifier_used=used,
            )
            await _write_cache(redis_client, key, result)
            return result
        except Exception as exc:
            errors.append(f"routellm failed: {exc}")

    raise ClassifierUnavailable("; ".join(errors) or "no classifier engine available")


def dependency_status() -> dict[str, Any]:
    status: dict[str, Any] = {}
    for name, factory in {
        "semantic_router": _get_semantic_router,
        "routellm_mf": _get_routellm_controller,
    }.items():
        try:
            factory()
            status[name] = {"ok": True}
        except Exception as exc:
            status[name] = {"ok": False, "error": str(exc)}
    return status


def dependency_status_json() -> str:
    return json.dumps(dependency_status(), ensure_ascii=False)


def resolve_encoder_model_path() -> str:
    configured = os.getenv(
        "SEMANTIC_ROUTER_ENCODER_MODEL",
        "sentence-transformers/all-MiniLM-L6-v2",
    )
    if Path(configured).exists():
        return configured

    if os.getenv("USE_MODELSCOPE", "true").lower() not in ("1", "true", "yes"):
        return configured

    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except Exception:
        return configured

    cache_dir = os.getenv("MODELSCOPE_CACHE_DIR")
    try:
        return snapshot_download(
            configured,
            cache_dir=cache_dir,
            revision=os.getenv("MODELSCOPE_REVISION", "master"),
        )
    except Exception:
        return configured
