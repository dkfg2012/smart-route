from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from models import CacheResult, RouteTier
from privacy_filter import check_privacy
from token_estimator import text_from_messages


class SemanticCache:
    def __init__(self, redis_client: Any | None = None) -> None:
        self.redis = redis_client
        self.ttl = int(os.getenv("SEMANTIC_CACHE_TTL", "3600"))

    async def _key(self, messages: list[dict[str, Any]]) -> str:
        privacy = await check_privacy(messages)
        text = text_from_messages(messages, user_only=True)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"semantic-cache:{privacy.level.value}:{digest}"

    async def lookup(self, messages: list[dict[str, Any]]) -> CacheResult | None:
        if self.redis is None:
            return None
        key = await self._key(messages)
        raw = await self.redis.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        return CacheResult(
            response=data["response"],
            tier=RouteTier(data["tier"]),
            similarity=1.0,
            cache_key=key,
        )

    async def store(
        self,
        messages: list[dict[str, Any]],
        response: dict[str, Any],
        tier: RouteTier,
        ttl: int | None = None,
    ) -> None:
        if self.redis is None:
            return
        key = await self._key(messages)
        payload = json.dumps({"response": response, "tier": tier.value}, ensure_ascii=False)
        await self.redis.setex(key, ttl or self.ttl, payload)

