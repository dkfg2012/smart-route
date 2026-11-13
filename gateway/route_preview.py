from __future__ import annotations

from typing import Any

from model_client import _endpoint_for_tier
from models import ClassificationResult, PrivacyCheckResult, RouteDecision, RouteTier
from task_policy import configured_device_model_size_b
from token_estimator import text_from_messages


def messages_for_model(
    messages: list[dict[str, Any]],
    privacy_result: PrivacyCheckResult,
    tier: RouteTier,
) -> list[dict[str, Any]]:
    """Return the messages that would be sent to the chosen backend.

    This mirrors the gateway behaviour: only cloud traffic swaps in the
    desensitized text, and only the last user message is replaced. System and
    assistant messages are intentionally left untouched to reflect what the
    real request would look like.
    """
    if tier != RouteTier.CLOUD or not privacy_result.desensitized_text:
        return messages
    copied = [dict(message) for message in messages]
    for message in reversed(copied):
        if message.get("role") == "user":
            message["content"] = privacy_result.desensitized_text
            break
    return copied


def _destination(tier: RouteTier, model: str) -> dict[str, Any]:
    """Describe the backend endpoint without contacting it."""
    try:
        base_url, _api_key, backend_model = _endpoint_for_tier(tier)
    except Exception:
        return {
            "kind": tier.value,
            "base_url": None,
            "endpoint": None,
            "model": model,
            "called": False,
        }
    return {
        "kind": tier.value,
        "base_url": base_url,
        "endpoint": base_url.rstrip("/") + "/chat/completions",
        "model": backend_model,
        "called": False,
    }


def _privacy_payload(privacy: PrivacyCheckResult, decision: RouteDecision) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "level": privacy.level.value,
        "block_cloud": privacy.block_cloud,
        "detected_patterns": list(privacy.detected_patterns),
        "replacement_count": len(privacy.replacement_map or {}),
    }
    if privacy.desensitized_text:
        payload["desensitized_text"] = privacy.desensitized_text[:1000]
    if privacy.replacement_map:
        payload["replacement_map"] = dict(list(privacy.replacement_map.items())[:50])
    if decision.tier == RouteTier.CLOUD:
        payload["cloud_text"] = (
            "desensitized" if privacy.desensitized_text else "original"
        )
    else:
        payload["cloud_text"] = "not_sent_to_cloud"
    return payload


def build_route_preview(
    messages: list[dict[str, Any]],
    classification: ClassificationResult,
    privacy: PrivacyCheckResult,
    decision: RouteDecision,
    context_tokens: int,
    *,
    edge_available: bool,
    gpu_queue_depth: int,
    local_action_request: bool,
    local_file_request: bool,
    local_file_synthesis: bool,
) -> dict[str, Any]:
    """Build the response of the dry-run endpoint.

    Everything up to ``decide_route`` has already run; this function only
    shapes the result. It never calls a model backend, the semantic cache or
    the trace store.
    """
    tier = decision.tier
    model_messages = messages_for_model(messages, privacy, tier)

    return {
        "dry_run": True,
        "trace_id": decision.trace_id,
        "decision": tier.value.upper(),
        "route": {
            "tier": tier.value,
            "model": decision.model,
            "reason": decision.reason,
            "privacy_applied": decision.privacy_applied,
            "estimated_cost_usd": decision.estimated_cost_usd,
        },
        "destination": _destination(tier, decision.model),
        "classification": {
            "level": classification.level.value,
            "confidence": classification.confidence,
            "reasoning": classification.reasoning,
            "estimated_tokens": classification.estimated_tokens,
            "latency_ms": classification.latency_ms,
            "classifier_used": classification.classifier_used,
            "cache_hit": classification.cache_hit,
        },
        "privacy": _privacy_payload(privacy, decision),
        "context": {
            "tokens": context_tokens,
            "edge_available": edge_available,
            "gpu_queue_depth": gpu_queue_depth,
            "local_action_request": local_action_request,
            "local_file_request": local_file_request,
            "local_file_synthesis": local_file_synthesis,
            "device_model_size_b": configured_device_model_size_b(),
        },
        "model_input_preview": text_from_messages(model_messages)[:300],
        "model_api_called": False,
        "note": (
            "Routing pipeline ran end-to-end (classifier, privacy, token estimate, "
            "task policy, route decision). No device, edge or cloud model API was "
            "called and no semantic-cache or trace entry was written."
        ),
    }
