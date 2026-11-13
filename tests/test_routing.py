from __future__ import annotations

from models import ClassificationResult, ComplexityLevel, PrivacyCheckResult, PrivacyLevel, RouteTier
from router_middleware import decide_route


def cls(level: ComplexityLevel) -> ClassificationResult:
    return ClassificationResult(
        level=level,
        confidence=0.9,
        reasoning="test",
        estimated_tokens=1000,
        cache_hit=False,
        latency_ms=1,
        classifier_used="semantic_router",
    )


def privacy(level: PrivacyLevel) -> PrivacyCheckResult:
    return PrivacyCheckResult(level=level, detected_patterns=[], block_cloud=level == PrivacyLevel.S1)


def test_s1_never_routes_cloud():
    decision = decide_route(cls(ComplexityLevel.REASONING), privacy(PrivacyLevel.S1), 1000, edge_available=True)
    assert decision.tier != RouteTier.CLOUD


def test_reasoning_routes_cloud_for_s3():
    decision = decide_route(cls(ComplexityLevel.REASONING), privacy(PrivacyLevel.S3), 1000, edge_available=True)
    assert decision.tier == RouteTier.CLOUD


def test_complex_routes_edge():
    decision = decide_route(cls(ComplexityLevel.COMPLEX), privacy(PrivacyLevel.S3), 1000, edge_available=True)
    assert decision.tier == RouteTier.EDGE


def test_edge_queue_moves_complex_to_cloud():
    decision = decide_route(cls(ComplexityLevel.COMPLEX), privacy(PrivacyLevel.S3), 1000, gpu_queue_depth=9, edge_available=True)
    assert decision.tier == RouteTier.CLOUD


def test_local_file_search_stays_device_for_retrieval():
    decision = decide_route(
        cls(ComplexityLevel.COMPLEX),
        privacy(PrivacyLevel.S3),
        1000,
        edge_available=True,
        local_file_request=True,
        local_file_synthesis=False,
    )
    assert decision.tier == RouteTier.DEVICE
    assert decision.privacy_applied == "local_file_cloud_blocked"


def test_local_file_synthesis_can_route_edge_when_device_small(monkeypatch):
    monkeypatch.setenv("DEVICE_MODEL_SIZE_B", "1.5")
    decision = decide_route(
        cls(ComplexityLevel.COMPLEX),
        privacy(PrivacyLevel.S3),
        1000,
        edge_available=True,
        local_file_request=True,
        local_file_synthesis=True,
    )
    assert decision.tier == RouteTier.EDGE
    assert decision.privacy_applied == "local_file_cloud_blocked"


def test_local_file_synthesis_stays_device_when_device_large(monkeypatch):
    monkeypatch.setenv("DEVICE_MODEL_SIZE_B", "30")
    decision = decide_route(
        cls(ComplexityLevel.REASONING),
        privacy(PrivacyLevel.S3),
        1000,
        edge_available=True,
        local_file_request=True,
        local_file_synthesis=True,
    )
    assert decision.tier == RouteTier.DEVICE
    assert decision.privacy_applied == "local_file_cloud_blocked"


def test_local_action_stays_device_even_if_complex():
    decision = decide_route(
        cls(ComplexityLevel.COMPLEX),
        privacy(PrivacyLevel.S3),
        1000,
        edge_available=True,
        local_action_request=True,
    )
    assert decision.tier == RouteTier.DEVICE
    assert decision.privacy_applied == "local_action_cloud_blocked"
