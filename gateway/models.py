from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ComplexityLevel(str, Enum):
    SIMPLE = "SIMPLE"
    MEDIUM = "MEDIUM"
    COMPLEX = "COMPLEX"
    RESEARCH = "RESEARCH"
    REASONING = "REASONING"


class PrivacyLevel(str, Enum):
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"


class RouteTier(str, Enum):
    DEVICE = "device"
    EDGE = "edge"
    CLOUD = "cloud"


class ClassificationResult(BaseModel):
    level: ComplexityLevel
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    estimated_tokens: int
    cache_hit: bool = False
    latency_ms: float
    classifier_used: str


class PrivacyCheckResult(BaseModel):
    level: PrivacyLevel
    detected_patterns: list[str]
    desensitized_text: str | None = None
    replacement_map: dict[str, str] | None = None
    block_cloud: bool


class RouteDecision(BaseModel):
    tier: RouteTier
    model: str
    reason: str
    privacy_applied: str | None
    estimated_cost_usd: float
    trace_id: str


class RouteTrace(BaseModel):
    trace_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_input: str
    complexity_level: str
    privacy_level: str
    context_tokens: int
    tier: str
    model_used: str
    route_reason: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_hit: bool
    privacy_desensitized: bool
    error: str | None = None
    classifier_used: str
    request: dict[str, Any] = Field(default_factory=dict)
    response_preview: str | None = None


class DemoCase(BaseModel):
    id: int
    name: str
    description: str
    prompt: str
    expected_tier: RouteTier
    expected_complexity: str
    expected_privacy: str
    max_expected_latency_ms: int
    verify_fn: str


class CacheResult(BaseModel):
    response: dict[str, Any]
    tier: RouteTier
    similarity: float
    cache_key: str

