from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Request

GATEWAY_DIR = Path(__file__).resolve().parents[2] / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

from storage import distribution, summary  # noqa: E402

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("")
async def metrics_root(request: Request):
    redis = getattr(request.app.state, "redis", None)
    return {
        "summary": await summary(redis),
        "distribution": await distribution(redis),
    }


@router.get("/summary")
async def metrics_summary(request: Request):
    return await summary(getattr(request.app.state, "redis", None))


@router.get("/distribution")
async def metrics_distribution(request: Request):
    return await distribution(getattr(request.app.state, "redis", None))
