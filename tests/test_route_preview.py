from __future__ import annotations

import os

from fastapi.testclient import TestClient

import app as gateway
from models import (
    ClassificationResult,
    ComplexityLevel,
    PrivacyCheckResult,
    PrivacyLevel,
    RouteTier,
)
from route_preview import messages_for_model


def _classification(level: ComplexityLevel) -> ClassificationResult:
    return ClassificationResult(
        level=level,
        confidence=0.9,
        reasoning="test",
        estimated_tokens=1000,
        cache_hit=False,
        latency_ms=1.0,
        classifier_used="semantic_router",
    )


def _privacy(
    level: PrivacyLevel,
    detected: list[str] | None = None,
    desensitized: str | None = None,
) -> PrivacyCheckResult:
    return PrivacyCheckResult(
        level=level,
        detected_patterns=detected or [],
        desensitized_text=desensitized,
        block_cloud=level == PrivacyLevel.S1,
    )


def _auth_headers() -> dict[str, str]:
    key = os.getenv("LITELLM_MASTER_KEY", "change-me-local-gateway-key")
    return {"Authorization": f"Bearer {key}"}


def _client(
    monkeypatch,
    classification: ClassificationResult,
    privacy: PrivacyCheckResult,
    tokens: int = 1000,
) -> TestClient:
    async def fake_classify(messages, redis_client=None, **kwargs):
        return classification

    async def fake_privacy(messages):
        return privacy

    def fake_tokens(messages):
        return tokens

    async def forbidden_trace(*args, **kwargs):
        raise AssertionError("route preview must not write a trace")

    async def forbidden_model(*args, **kwargs):
        raise AssertionError("route preview must not call a model backend")

    monkeypatch.setenv("EDGE_AVAILABLE", "true")
    monkeypatch.setattr(gateway, "classify", fake_classify)
    monkeypatch.setattr(gateway, "check_privacy", fake_privacy)
    monkeypatch.setattr(gateway, "count_message_tokens", fake_tokens)
    monkeypatch.setattr(gateway, "store_trace", forbidden_trace)
    monkeypatch.setattr(gateway, "complete_chat", forbidden_model)
    return TestClient(gateway.app)


def _post(client: TestClient, text: str):
    return client.post(
        "/v1/route-preview",
        json={"messages": [{"role": "user", "content": text}]},
        headers=_auth_headers(),
    )


def test_reasoning_s3_targets_cloud_without_model_call(monkeypatch):
    client = _client(monkeypatch, _classification(ComplexityLevel.REASONING), _privacy(PrivacyLevel.S3))
    response = _post(client, "证明一个数学命题")

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["decision"] == "CLOUD"
    assert body["route"]["tier"] == "cloud"
    assert body["model_api_called"] is False
    assert body["destination"]["called"] is False
    assert response.headers["x-route-tier"] == "cloud"


def test_s1_long_context_routes_edge_and_never_cloud(monkeypatch):
    client = _client(
        monkeypatch,
        _classification(ComplexityLevel.REASONING),
        _privacy(PrivacyLevel.S1, detected=["confidential"]),
        tokens=20000,
    )
    response = _post(client, "机密：工资单总计 120 万")

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "EDGE"
    assert body["route"]["tier"] == "edge"
    assert body["privacy"]["block_cloud"] is True
    assert body["route"]["privacy_applied"] == "cloud_blocked"
    assert body["model_api_called"] is False


def test_s2_cloud_preview_shows_desensitized_last_user_message(monkeypatch):
    client = _client(
        monkeypatch,
        _classification(ComplexityLevel.REASONING),
        _privacy(
            PrivacyLevel.S2,
            detected=["phone"],
            desensitized="客户张三的电话 【PHONE-0001】",
        ),
    )
    response = _post(client, "客户张三的电话 13800138000")

    body = response.json()
    assert body["decision"] == "CLOUD"
    assert body["route"]["privacy_applied"] == "desensitized_before_cloud"
    assert body["privacy"]["cloud_text"] == "desensitized"
    assert "【PHONE-0001】" in body["model_input_preview"]


def test_simple_s3_stays_on_device(monkeypatch):
    client = _client(monkeypatch, _classification(ComplexityLevel.SIMPLE), _privacy(PrivacyLevel.S3))
    response = _post(client, "你好")

    body = response.json()
    assert body["decision"] == "DEVICE"
    assert body["destination"]["kind"] == "device"


def test_preview_requires_api_key(monkeypatch):
    client = _client(monkeypatch, _classification(ComplexityLevel.SIMPLE), _privacy(PrivacyLevel.S3))
    response = client.post("/v1/route-preview", json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 401


def test_messages_for_model_keeps_system_and_replaces_last_user():
    messages = [
        {"role": "system", "content": "内部使用"},
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "客户张三的电话 13800138000"},
    ]
    privacy = _privacy(PrivacyLevel.S2, detected=["phone"], desensitized="客户张三的电话 【PHONE-0001】")

    cloud_messages = messages_for_model(messages, privacy, RouteTier.CLOUD)
    assert cloud_messages[0]["content"] == "内部使用"
    assert cloud_messages[-1]["content"] == "客户张三的电话 【PHONE-0001】"
    assert messages[-1]["content"] == "客户张三的电话 13800138000"

    device_messages = messages_for_model(messages, privacy, RouteTier.DEVICE)
    assert device_messages is messages
