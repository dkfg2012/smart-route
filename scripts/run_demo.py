#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "gateway"))

from demo_cases import DEMO_CASES  # noqa: E402


def post_json(url: str, payload: dict, api_key: str) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default=os.getenv("GATEWAY_URL", "http://localhost:4000"))
    parser.add_argument("--case", type=int, default=0)
    args = parser.parse_args()

    api_key = os.getenv("LITELLM_MASTER_KEY", "change-me-local-gateway-key")
    cases = [case for case in DEMO_CASES if args.case in (0, case.id)]
    for case in cases:
        print(f"\n[{case.id}] {case.name}")
        result = post_json(
            f"{args.gateway.rstrip('/')}/v1/chat/completions",
            {"model": "auto", "messages": [{"role": "user", "content": case.prompt}]},
            api_key,
        )
        print(json.dumps({
            "tier": result.get("tier"),
            "trace_id": result.get("trace_id"),
            "content": result["choices"][0]["message"]["content"][:240],
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

