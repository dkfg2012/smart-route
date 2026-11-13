from __future__ import annotations

import os
import uuid
from typing import Any, Optional

from models import ClassificationResult, ComplexityLevel, PrivacyCheckResult, PrivacyLevel, RouteDecision, RouteTier
from task_policy import configured_device_model_size_b

try:
    from litellm.router import CustomRoutingStrategyBase
except Exception:  # Local unit-test environments may not have litellm installed.
    class CustomRoutingStrategyBase:  # type: ignore[no-redef]
        pass


LEVEL_ORDER = {
    ComplexityLevel.SIMPLE: 1,
    ComplexityLevel.MEDIUM: 2,
    ComplexityLevel.COMPLEX: 3,
    ComplexityLevel.RESEARCH: 4,
    ComplexityLevel.REASONING: 5,
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _model_for_tier(tier: RouteTier) -> str:
    if tier == RouteTier.DEVICE:
        return os.getenv("OLLAMA_DEVICE_MODEL", "qwen3:1.5b")
    if tier == RouteTier.EDGE:
        return os.getenv("VLLM_EDGE_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
    return os.getenv("DEEPSEEK_CLOUD_MODEL", "deepseek-v4-pro")


def _estimate_cost(tier: RouteTier, tokens: int) -> float:
    if tier != RouteTier.CLOUD:
        return 0.0
    per_1k = float(os.getenv("CLOUD_COST_PER_1K_TOKENS", "0.00348"))
    return round((tokens / 1000.0) * per_1k, 6)


def _medium_should_stay_device(classification: ClassificationResult, context_tokens: int) -> bool:
    # This is not a classifier. It is a capacity/cost routing policy for tasks
    # that have already been classified by SR/RouteLLM as MEDIUM.
    return classification.level == ComplexityLevel.MEDIUM and context_tokens <= 4096


def decide_route(
    classification: ClassificationResult,
    privacy: PrivacyCheckResult,
    context_tokens: int,
    gpu_queue_depth: int = 0,
    edge_available: bool = True,
    local_file_request: bool = False,
    local_file_synthesis: bool = False,
    local_action_request: bool = False,
) -> RouteDecision:
    cloud_threshold = _env_int("CONTEXT_CLOUD_THRESHOLD_TOKENS", 65536)
    edge_threshold = _env_int("CONTEXT_EDGE_THRESHOLD_TOKENS", 8192)
    device_context = _env_int("DEVICE_CONTEXT_TOKENS", 8192)
    device_file_model_min_b = float(os.getenv("DEVICE_FILE_MODEL_MIN_B", "14"))
    queue_limit = _env_int("EDGE_QUEUE_LIMIT", 10)
    queue_ratio = gpu_queue_depth / max(queue_limit, 1)

    privacy_applied = None

    if local_action_request:
        return RouteDecision(
            tier=RouteTier.DEVICE,
            model=_model_for_tier(RouteTier.DEVICE),
            reason="local device action must be planned/executed on device",
            privacy_applied="local_action_cloud_blocked",
            estimated_cost_usd=0.0,
            trace_id=str(uuid.uuid4()),
        )

    if local_file_request:
        device_model_size_b = configured_device_model_size_b()
        device_can_handle = (
            context_tokens <= device_context
            and (
                not local_file_synthesis
                or device_model_size_b >= device_file_model_min_b
                or os.getenv("DEVICE_CAN_RAG", "false").lower() in ("1", "true", "yes")
            )
        )
        if device_can_handle or not edge_available:
            tier = RouteTier.DEVICE
            reason = (
                "local file data stays on device; "
                f"device capacity model_size_b={device_model_size_b:g}, context_limit={device_context}"
            )
        else:
            tier = RouteTier.EDGE
            reason = (
                "local file retrieval stays off cloud; "
                "device capacity insufficient for synthesis, routes to edge"
            )
        return RouteDecision(
            tier=tier,
            model=_model_for_tier(tier),
            reason=reason,
            privacy_applied="local_file_cloud_blocked",
            estimated_cost_usd=0.0,
            trace_id=str(uuid.uuid4()),
        )

    if privacy.level == PrivacyLevel.S1 or privacy.block_cloud:
        if context_tokens > edge_threshold and edge_available:
            tier = RouteTier.EDGE
            reason = "S1 privacy blocks cloud; long local context routes to edge"
        else:
            tier = RouteTier.DEVICE
            reason = "S1 privacy blocks cloud; request stays on device"
        return RouteDecision(
            tier=tier,
            model=_model_for_tier(tier),
            reason=reason,
            privacy_applied="cloud_blocked",
            estimated_cost_usd=0.0,
            trace_id=str(uuid.uuid4()),
        )

    if privacy.level == PrivacyLevel.S2 and privacy.desensitized_text:
        privacy_applied = "desensitized_before_cloud"

    if context_tokens > cloud_threshold:
        tier = RouteTier.CLOUD
        reason = f"context tokens {context_tokens} exceed cloud threshold {cloud_threshold}"
    elif classification.level == ComplexityLevel.REASONING:
        tier = RouteTier.CLOUD
        reason = "REASONING complexity requires cloud model"
    elif classification.level == ComplexityLevel.RESEARCH and context_tokens > 32768:
        tier = RouteTier.CLOUD
        reason = "RESEARCH complexity with long context requires cloud model"
    elif edge_available and LEVEL_ORDER[classification.level] >= LEVEL_ORDER[ComplexityLevel.COMPLEX]:
        tier = RouteTier.EDGE
        reason = f"{classification.level.value} complexity routes to edge"
    elif edge_available and classification.level == ComplexityLevel.MEDIUM and not _medium_should_stay_device(classification, context_tokens):
        tier = RouteTier.EDGE
        reason = "MEDIUM task with larger context routes to edge"
    else:
        tier = RouteTier.DEVICE
        reason = "low complexity or edge unavailable; route to device"

    if tier == RouteTier.EDGE and queue_ratio > 0.8:
        if LEVEL_ORDER[classification.level] <= LEVEL_ORDER[ComplexityLevel.MEDIUM]:
            tier = RouteTier.DEVICE
            reason = "edge queue above 80%; simple or medium task downgraded to device"
        elif not privacy.block_cloud:
            tier = RouteTier.CLOUD
            reason = "edge queue above 80%; complex task upgraded to cloud"

    return RouteDecision(
        tier=tier,
        model=_model_for_tier(tier),
        reason=reason,
        privacy_applied=privacy_applied,
        estimated_cost_usd=_estimate_cost(tier, context_tokens + classification.estimated_tokens),
        trace_id=str(uuid.uuid4()),
    )


def _deployment_for_tier(model_list: list[dict[str, Any]], tier: RouteTier) -> dict[str, Any] | None:
    for deployment in model_list:
        info = deployment.get("model_info", {})
        if info.get("tier") == tier.value:
            return deployment
    fallback_name = {
        RouteTier.DEVICE: "device-general",
        RouteTier.EDGE: "edge-flash",
        RouteTier.CLOUD: "cloud-pro",
    }[tier]
    for deployment in model_list:
        if deployment.get("model_name") == fallback_name:
            return deployment
    return None


class HybridRoutingStrategy(CustomRoutingStrategyBase):
    """
    LiteLLM custom routing strategy.

    LiteLLM calls this strategy to choose a deployment from router.model_list.
    The surrounding gateway still records traces and exposes Dashboard APIs.
    """

    def __init__(self, model_list: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.model_list = model_list or []

    async def async_get_available_deployment(
        self,
        model: str,
        messages: Optional[list[dict[str, Any]]] = None,
        input: Any | None = None,
        specific_deployment: Optional[bool] = False,
        request_kwargs: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any] | None:
        from classifier import classify
        from privacy_filter import check_privacy
        from task_policy import is_local_action_request, is_local_file_request, needs_local_file_synthesis
        from token_estimator import count_message_tokens

        messages = messages or []
        request_kwargs = request_kwargs or {}
        classification = await classify(messages, request_kwargs.get("redis_client"))
        privacy = await check_privacy(messages)
        decision = decide_route(
            classification=classification,
            privacy=privacy,
            context_tokens=count_message_tokens(messages),
            gpu_queue_depth=int(request_kwargs.get("gpu_queue_depth", 0)),
            edge_available=bool(request_kwargs.get("edge_available", True)),
            local_file_request=is_local_file_request(messages),
            local_file_synthesis=needs_local_file_synthesis(messages),
            local_action_request=is_local_action_request(messages),
        )
        model_list = request_kwargs.get("model_list") or self.model_list
        deployment = _deployment_for_tier(model_list, decision.tier)
        if deployment is not None:
            deployment.setdefault("metadata", {})
            deployment["metadata"]["hybrid_route_decision"] = decision.model_dump()
        return deployment

    def get_available_deployment(
        self,
        model: str,
        messages: Optional[list[dict[str, Any]]] = None,
        input: Any | None = None,
        specific_deployment: Optional[bool] = False,
        request_kwargs: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any] | None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.async_get_available_deployment(
                    model=model,
                    messages=messages,
                    input=input,
                    specific_deployment=specific_deployment,
                    request_kwargs=request_kwargs,
                )
            )
        raise RuntimeError("Use async_get_available_deployment inside a running event loop")


HybridRouter = HybridRoutingStrategy
