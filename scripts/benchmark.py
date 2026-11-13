#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import time
from urllib import request


def call(gateway: str, api_key: str, prompt: str) -> float:
    started = time.perf_counter()
    payload = json.dumps({"model": "auto", "messages": [{"role": "user", "content": prompt}]}).encode()
    req = request.Request(
        f"{gateway.rstrip('/')}/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=60) as resp:
        resp.read()
    return (time.perf_counter() - started) * 1000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default="http://localhost:4000")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    api_key = os.getenv("LITELLM_MASTER_KEY", "change-me-local-gateway-key")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        latencies = list(pool.map(
            lambda _: call(args.gateway, api_key, "帮我把这句话改得更专业一点：明天给你报告"),
            range(args.requests),
        ))
    print(json.dumps({
        "requests": args.requests,
        "concurrency": args.concurrency,
        "p50_ms": statistics.median(latencies),
        "max_ms": max(latencies),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

