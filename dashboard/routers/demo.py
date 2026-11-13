from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

GATEWAY_DIR = Path(__file__).resolve().parents[2] / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

from demo_cases import DEMO_CASES, get_demo_case  # noqa: E402

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.get("/cases")
async def cases():
    return [case.model_dump(mode="json") for case in DEMO_CASES]


@router.post("/run")
async def run_demo(payload: dict[str, Any]):
    case = get_demo_case(int(payload.get("id", 0)))
    if case is None:
        raise HTTPException(status_code=404, detail="demo case not found")
    try:
        import httpx
    except Exception as exc:
        raise HTTPException(status_code=500, detail="httpx is required") from exc

    gateway_url = os.getenv("GATEWAY_URL", "http://gateway:4000")
    api_key = os.getenv("LITELLM_MASTER_KEY", "change-me-local-gateway-key")
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{gateway_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": "auto", "messages": [{"role": "user", "content": case.prompt}]},
        )
    return {
        "case": case.model_dump(mode="json"),
        "status_code": response.status_code,
        "trace_id": response.headers.get("X-Trace-Id"),
        "response": response.json(),
    }


@router.get("/models")
async def models():
    try:
        import httpx
    except Exception as exc:
        raise HTTPException(status_code=500, detail="httpx is required") from exc

    gateway_url = os.getenv("GATEWAY_URL", "http://gateway:4000")
    api_key = os.getenv("LITELLM_MASTER_KEY", "change-me-local-gateway-key")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{gateway_url.rstrip('/')}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


@router.post("/chat")
async def chat(payload: dict[str, Any]):
    try:
        import httpx
    except Exception as exc:
        raise HTTPException(status_code=500, detail="httpx is required") from exc

    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages must be a list")

    gateway_url = os.getenv("GATEWAY_URL", "http://gateway:4000")
    api_key = os.getenv("LITELLM_MASTER_KEY", "change-me-local-gateway-key")
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            f"{gateway_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": payload.get("model", "hybrid-auto"),
                "messages": messages,
                "stream": False,
            },
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return {
        "trace_id": response.headers.get("X-Trace-Id"),
        "tier": response.headers.get("X-Route-Tier"),
        "classifier": response.headers.get("X-Classifier-Used"),
        "response": response.json(),
    }
