from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path
from typing import Any

from models import RouteTrace


TRACE_KEY = "hybrid-router:route-traces"
MAX_IN_MEMORY = 500
_memory_traces: deque[RouteTrace] = deque(maxlen=MAX_IN_MEMORY)


def storage_snapshot_path() -> Path | None:
    if os.getenv("TRACE_FILE_DISABLED", "false").lower() in ("1", "true", "yes"):
        return None
    configured = os.getenv("TRACE_FILE")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / ".runtime" / "route-traces.jsonl"


async def make_redis_client() -> Any | None:
    try:
        import redis.asyncio as redis
    except Exception:
        return None
    try:
        client = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379"), decode_responses=True)
        await client.ping()
        return client
    except Exception:
        return None


async def store_trace(trace: RouteTrace, redis_client: Any | None = None) -> None:
    _memory_traces.appendleft(trace)
    if redis_client is None:
        snapshot = storage_snapshot_path()
        if snapshot is not None:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            with snapshot.open("a", encoding="utf-8") as handle:
                handle.write(trace.model_dump_json() + "\n")
        return
    await redis_client.lpush(TRACE_KEY, trace.model_dump_json())
    await redis_client.ltrim(TRACE_KEY, 0, int(os.getenv("TRACE_RETENTION", "500")) - 1)


async def list_traces(limit: int = 20, redis_client: Any | None = None) -> list[RouteTrace]:
    if redis_client is not None:
        raw_items = await redis_client.lrange(TRACE_KEY, 0, limit - 1)
        return [RouteTrace.model_validate_json(item) for item in raw_items]
    traces = list(_memory_traces)
    snapshot = storage_snapshot_path()
    if snapshot is not None and snapshot.exists():
        with snapshot.open("r", encoding="utf-8") as handle:
            file_items = [line.strip() for line in handle if line.strip()]
        traces.extend(RouteTrace.model_validate_json(item) for item in reversed(file_items[-MAX_IN_MEMORY:]))
    deduped: list[RouteTrace] = []
    seen: set[str] = set()
    for trace in traces:
        if trace.trace_id in seen:
            continue
        seen.add(trace.trace_id)
        deduped.append(trace)
    return deduped[:limit]


async def get_trace(trace_id: str, redis_client: Any | None = None) -> RouteTrace | None:
    for trace in await list_traces(limit=500, redis_client=redis_client):
        if trace.trace_id == trace_id:
            return trace
    return None


async def summary(redis_client: Any | None = None) -> dict[str, Any]:
    traces = await list_traces(limit=500, redis_client=redis_client)
    total = len(traces)
    if total == 0:
        return {
            "total_requests": 0,
            "local_hit_rate": 0.0,
            "cloud_token_savings": 0.0,
            "p50_latency_ms": 0.0,
            "s3_local_rate": 1.0,
        }

    local = sum(1 for trace in traces if trace.tier in ("device", "edge"))
    cloud_tokens = sum(trace.input_tokens + trace.output_tokens for trace in traces if trace.tier == "cloud")
    all_cloud_baseline = sum(trace.input_tokens + trace.output_tokens for trace in traces)
    latencies = sorted(trace.latency_ms for trace in traces)
    s3 = [trace for trace in traces if trace.privacy_level == "S3"]
    s3_local = sum(1 for trace in s3 if trace.tier in ("device", "edge", "cloud"))

    return {
        "total_requests": total,
        "local_hit_rate": local / total,
        "cloud_token_savings": 1.0 - (cloud_tokens / max(all_cloud_baseline, 1)),
        "p50_latency_ms": latencies[len(latencies) // 2],
        "s3_local_rate": s3_local / max(len(s3), 1),
    }


async def distribution(redis_client: Any | None = None) -> dict[str, int]:
    traces = await list_traces(limit=500, redis_client=redis_client)
    return {
        "device": sum(1 for trace in traces if trace.tier == "device"),
        "edge": sum(1 for trace in traces if trace.tier == "edge"),
        "cloud": sum(1 for trace in traces if trace.tier == "cloud"),
    }
