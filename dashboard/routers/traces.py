from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

GATEWAY_DIR = Path(__file__).resolve().parents[2] / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

from storage import get_trace, list_traces  # noqa: E402

router = APIRouter(prefix="/api/traces", tags=["traces"])
DEFAULT_TRACE_LIMIT = 200
MAX_TRACE_LIMIT = 500


@router.get("")
async def traces(request: Request, limit: int = DEFAULT_TRACE_LIMIT):
    redis = getattr(request.app.state, "redis", None)
    safe_limit = max(1, min(limit, MAX_TRACE_LIMIT))
    return [trace.model_dump(mode="json") for trace in await list_traces(safe_limit, redis)]


@router.get("/{trace_id}")
async def trace_detail(request: Request, trace_id: str):
    trace = await get_trace(trace_id, getattr(request.app.state, "redis", None))
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return trace.model_dump(mode="json")
