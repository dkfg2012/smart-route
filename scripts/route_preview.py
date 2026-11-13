#!/usr/bin/env python3
"""Run the hybrid routing pipeline without calling any model backend.

This script mirrors ``POST /v1/route-preview``: classification, privacy
filtering, token estimation, local-capability policy and the device/edge/cloud
decision all run for real; then the result is printed and the process exits.
No Ollama, vLLM or cloud API is contacted.

Usage:
    python scripts/route_preview.py "帮我写一段研发计划"
    python scripts/route_preview.py --system "内部使用" "客户张三的电话 13800138000"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "gateway"))

from classifier import ClassifierUnavailable, classify  # noqa: E402
from privacy_filter import check_privacy  # noqa: E402
from route_preview import build_route_preview  # noqa: E402
from router_middleware import decide_route  # noqa: E402
from task_policy import (  # noqa: E402
    is_local_action_request,
    is_local_file_request,
    needs_local_file_synthesis,
)
from token_estimator import count_message_tokens  # noqa: E402


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes")


async def _preview(messages: list[dict[str, str]], edge_available: bool, gpu_queue_depth: int) -> dict:
    classification = await classify(messages)
    privacy = await check_privacy(messages)
    context_tokens = count_message_tokens(messages)
    local_action_request = is_local_action_request(messages)
    local_file_request = is_local_file_request(messages)
    local_file_synthesis = needs_local_file_synthesis(messages)
    decision = decide_route(
        classification,
        privacy,
        context_tokens,
        gpu_queue_depth,
        edge_available,
        local_file_request=local_file_request,
        local_file_synthesis=local_file_synthesis,
        local_action_request=local_action_request,
    )
    return build_route_preview(
        messages,
        classification,
        privacy,
        decision,
        context_tokens,
        edge_available=edge_available,
        gpu_queue_depth=gpu_queue_depth,
        local_action_request=local_action_request,
        local_file_request=local_file_request,
        local_file_synthesis=local_file_synthesis,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run the hybrid router and print the device/edge/cloud decision.",
    )
    parser.add_argument("prompt", nargs="+", help="user prompt to route")
    parser.add_argument("--system", default=None, help="optional system message")
    parser.add_argument(
        "--edge-unavailable",
        action="store_true",
        help="simulate EDGE_AVAILABLE=false",
    )
    parser.add_argument(
        "--gpu-queue-depth",
        type=int,
        default=int(os.getenv("GPU_QUEUE_DEPTH", "0")),
        help="simulate edge queue depth (default: env GPU_QUEUE_DEPTH)",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON indent")
    args = parser.parse_args()

    messages: list[dict[str, str]] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": " ".join(args.prompt)})

    edge_available = _env_bool("EDGE_AVAILABLE", True) and not args.edge_unavailable

    try:
        preview = asyncio.run(_preview(messages, edge_available, args.gpu_queue_depth))
    except ClassifierUnavailable as exc:
        print(f"classifier unavailable: {exc}", file=sys.stderr)
        print(
            "Install gateway requirements and let the classifier load its local models, "
            "or run the gateway inside Docker Compose.",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    print(json.dumps(preview, ensure_ascii=False, indent=args.indent))


if __name__ == "__main__":
    main()
