from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from prometheus_client import Counter, Histogram, generate_latest
from prometheus_client import CONTENT_TYPE_LATEST

from classifier import ClassifierUnavailable, _get_routellm_controller, _get_semantic_router, classify
from model_client import complete_chat
from models import RouteTrace
from privacy_filter import check_privacy
from route_preview import build_route_preview, messages_for_model
from router_middleware import decide_route
from semantic_cache import SemanticCache
from storage import distribution, list_traces, make_redis_client, store_trace, summary
from task_policy import is_local_action_request, is_local_file_request, needs_local_file_synthesis
from token_estimator import count_message_tokens, text_from_messages


REQUESTS = Counter("hybrid_router_requests_total", "Total gateway requests", ["tier"])
LATENCY = Histogram("hybrid_router_request_latency_ms", "Gateway latency", ["tier"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = await make_redis_client()
    app.state.classifier_status = {}
    for name, factory in {
        "semantic_router": _get_semantic_router,
        "routellm_mf": _get_routellm_controller,
    }.items():
        try:
            factory()
            app.state.classifier_status[name] = "ready"
        except Exception as exc:
            app.state.classifier_status[name] = f"error: {exc}"
    yield
    if app.state.redis is not None:
        await app.state.redis.aclose()


app = FastAPI(title="Hybrid Router Gateway", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_auth(authorization: str | None) -> None:
    expected = os.getenv("LITELLM_MASTER_KEY", "change-me-local-gateway-key")
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid API key")


async def _plan_route(
    messages: list[dict[str, Any]],
    redis_client: Any,
) -> tuple[Any, Any, int, Any, dict[str, Any]]:
    """Run the control plane shared by chat completions and route preview.

    Classification, privacy checks, token estimation, local-capability policy
    and the device/edge/cloud decision all run for real. No model backend is
    touched here.
    """
    try:
        classification = await classify(messages, redis_client=redis_client)
    except ClassifierUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    privacy = await check_privacy(messages)
    context_tokens = count_message_tokens(messages)
    edge_available = os.getenv("EDGE_AVAILABLE", "true").lower() in ("1", "true", "yes")
    gpu_queue_depth = int(os.getenv("GPU_QUEUE_DEPTH", "0"))
    local_action_request = is_local_action_request(messages)
    local_file_request = is_local_file_request(messages)
    local_file_synthesis = needs_local_file_synthesis(messages)
    decision = decide_route(
        classification,
        privacy,
        context_tokens,
        gpu_queue_depth,
        edge_available,
        local_file_request=local_file_request,
        local_file_synthesis=local_file_synthesis,
        local_action_request=local_action_request,
    )
    signals = {
        "edge_available": edge_available,
        "gpu_queue_depth": gpu_queue_depth,
        "local_action_request": local_action_request,
        "local_file_request": local_file_request,
        "local_file_synthesis": local_file_synthesis,
    }
    return classification, privacy, context_tokens, decision, signals


def _streaming_response(response_data: dict[str, Any]) -> StreamingResponse:
    import json

    content = response_data["choices"][0]["message"]["content"]
    chunk_base = {
        "id": response_data["id"],
        "object": "chat.completion.chunk",
        "created": response_data["created"],
        "model": response_data["model"],
    }

    async def event_stream():
        first = {
            **chunk_base,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"

        for index in range(0, len(content), 64):
            chunk = {
                **chunk_base,
                "choices": [
                    {"index": 0, "delta": {"content": content[index : index + 64]}, "finish_reason": None}
                ],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

        done = {
            **chunk_base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "classifier": getattr(app.state, "classifier_status", {}),
        "redis": bool(getattr(app.state, "redis", None)),
        "model_backend_mode": os.getenv("MODEL_BACKEND_MODE", "mock"),
    }


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/models")
async def list_models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(authorization)
    created = int(time.time())
    model_ids = [
        "hybrid-auto",
        os.getenv("OLLAMA_DEVICE_MODEL", "qwen3:1.5b"),
        os.getenv("VLLM_EDGE_MODEL", "deepseek-ai/DeepSeek-V4-Flash"),
        os.getenv("DEEPSEEK_CLOUD_MODEL", "deepseek-v4-pro"),
    ]
    return {
        "object": "list",
        "data": [
            {"id": model_id, "object": "model", "created": created, "owned_by": "hybrid-router"}
            for model_id in dict.fromkeys(model_ids)
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    _check_auth(authorization)
    payload = await request.json()
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages must be a list")

    started = time.perf_counter()
    redis_client = getattr(app.state, "redis", None)
    classification, privacy, context_tokens, decision, _signals = await _plan_route(messages, redis_client)

    cache = SemanticCache(redis_client)
    cached = await cache.lookup(messages)
    if cached is not None:
        total_latency = (time.perf_counter() - started) * 1000
        cached.response["trace_id"] = decision.trace_id
        cached.response["tier"] = cached.tier.value
        usage = cached.response.get("usage", {})
        trace = RouteTrace(
            trace_id=decision.trace_id,
            user_input=text_from_messages(messages, user_only=True)[:100],
            complexity_level=classification.level.value,
            privacy_level=privacy.level.value,
            context_tokens=context_tokens,
            tier=cached.tier.value,
            model_used=decision.model,
            route_reason=f"semantic cache hit after decision: {decision.reason}",
            latency_ms=total_latency,
            input_tokens=int(usage.get("prompt_tokens", context_tokens)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            cost_usd=0.0,
            cache_hit=True,
            privacy_desensitized=bool(decision.privacy_applied),
            classifier_used=classification.classifier_used,
            request={"model": payload.get("model", "auto")},
            response_preview=cached.response["choices"][0]["message"]["content"][:160],
        )
        await store_trace(trace, redis_client)
        if payload.get("stream") is True:
            return _streaming_response(cached.response)
        return Response(
            content=cached.response if isinstance(cached.response, str) else __import__("json").dumps(cached.response, ensure_ascii=False),
            media_type="application/json",
            headers={
                "X-Trace-Id": decision.trace_id,
                "X-Route-Tier": cached.tier.value,
                "X-Classifier-Used": classification.classifier_used,
            },
        )

    model_messages = messages_for_model(messages, privacy, decision.tier)
    response_data = await complete_chat(decision, model_messages, decision.trace_id)
    total_latency = (time.perf_counter() - started) * 1000
    REQUESTS.labels(decision.tier.value).inc()
    LATENCY.labels(decision.tier.value).observe(total_latency)

    usage = response_data.get("usage", {})
    content = response_data["choices"][0]["message"]["content"]
    trace = RouteTrace(
        trace_id=decision.trace_id,
        user_input=text_from_messages(messages, user_only=True)[:100],
        complexity_level=classification.level.value,
        privacy_level=privacy.level.value,
        context_tokens=context_tokens,
        tier=decision.tier.value,
        model_used=decision.model,
        route_reason=decision.reason,
        latency_ms=total_latency,
        input_tokens=int(usage.get("prompt_tokens", context_tokens)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        cost_usd=decision.estimated_cost_usd,
        cache_hit=False,
        privacy_desensitized=bool(decision.privacy_applied),
        classifier_used=classification.classifier_used,
        request={"model": payload.get("model", "auto")},
        response_preview=content[:160],
    )
    await store_trace(trace, redis_client)
    await cache.store(messages, response_data, decision.tier)

    import json

    if payload.get("stream") is True:
        return _streaming_response(response_data)

    return Response(
        content=json.dumps(response_data, ensure_ascii=False),
        media_type="application/json",
        headers={
            "X-Trace-Id": decision.trace_id,
            "X-Route-Tier": decision.tier.value,
            "X-Classifier-Used": classification.classifier_used,
        },
    )


@app.post("/v1/route-preview")
async def route_preview(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    """Dry run the routing pipeline and report the decision.

    The real classifier, privacy filter, token estimator, task policy and
    ``decide_route`` all run. The chosen model backend is never called, and no
    semantic-cache or trace entry is written, so this endpoint is safe to use
    for probing what the gateway would do with a given input.
    """
    _check_auth(authorization)
    payload = await request.json()
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages must be a list")

    started = time.perf_counter()
    classification, privacy, context_tokens, decision, signals = await _plan_route(
        messages,
        getattr(app.state, "redis", None),
    )
    preview = build_route_preview(
        messages,
        classification,
        privacy,
        decision,
        context_tokens,
        **signals,
    )
    preview["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)

    import json

    return Response(
        content=json.dumps(preview, ensure_ascii=False),
        media_type="application/json",
        headers={
            "X-Trace-Id": decision.trace_id,
            "X-Route-Tier": decision.tier.value,
            "X-Classifier-Used": classification.classifier_used,
        },
    )


@app.get("/debug/traces")
async def debug_traces(limit: int = 20) -> list[dict[str, Any]]:
    return [trace.model_dump(mode="json") for trace in await list_traces(limit, getattr(app.state, "redis", None))]


@app.get("/debug/summary")
async def debug_summary() -> dict[str, Any]:
    redis_client = getattr(app.state, "redis", None)
    return {"summary": await summary(redis_client), "distribution": await distribution(redis_client)}
