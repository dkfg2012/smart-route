from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

import sys
from pathlib import Path

GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

from storage import list_traces, make_redis_client, storage_snapshot_path  # noqa: E402
from routers.demo import router as demo_router  # noqa: E402
from routers.metrics import router as metrics_router  # noqa: E402
from routers.traces import DEFAULT_TRACE_LIMIT  # noqa: E402
from routers.traces import router as traces_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = await make_redis_client()
    if app.state.redis is None:
        app.state.trace_file = storage_snapshot_path()
    yield
    if app.state.redis is not None:
        await app.state.redis.aclose()


app = FastAPI(title="Hybrid Router Dashboard", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_headers=["*"],
    allow_methods=["*"],
)
app.include_router(metrics_router)
app.include_router(traces_router)
app.include_router(demo_router)

static_dir = Path(__file__).resolve().parent / "frontend" / "dist"
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "layers": {
            "device": {"online": True, "p50_latency_ms": 350},
            "edge": {"online": True, "p50_latency_ms": 1400},
            "cloud": {"online": True, "p50_latency_ms": 4200},
        },
        "classifier": {
            "semantic_router": "checked_by_gateway_warmup",
            "routellm_mf": "checked_by_gateway_warmup",
        },
    }


@app.websocket("/api/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()
    traces = await list_traces(DEFAULT_TRACE_LIMIT, getattr(websocket.app.state, "redis", None))
    await websocket.send_json([trace.model_dump(mode="json") for trace in traces])
    await websocket.close()


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/{path:path}")
async def spa(path: str):
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"message": "Dashboard frontend not built. Run npm run build in dashboard/frontend."}
