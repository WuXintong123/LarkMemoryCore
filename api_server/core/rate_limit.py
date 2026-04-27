"""Token-bucket rate limiting state."""

from __future__ import annotations

import asyncio
import time
from typing import Dict, List

from .config import RATE_LIMIT_BURST, RATE_LIMIT_RPM, RATE_LIMIT_SCOPE, RATE_LIMIT_TTL_S


class TokenBucket:
    def __init__(self, tokens: float, last_refill: float, last_seen: float):
        self.tokens = tokens
        self.last_refill = last_refill
        self.last_seen = last_seen


class RateLimiter:
    def __init__(self, rpm: int, burst: int, ttl_seconds: int):
        self.rpm = rpm
        self.burst = max(burst, 1)
        self.ttl_seconds = max(ttl_seconds, 60)
        self.rate_per_second = float(rpm) / 60.0 if rpm > 0 else 0.0
        self.buckets: Dict[str, TokenBucket] = {}
        self.lock = asyncio.Lock()
        self._last_cleanup_ts = time.time()

    async def check(self, client_key: str) -> bool:
        if self.rpm <= 0:
            return True

        async with self.lock:
            now = time.time()
            self._cleanup_expired(now)

            bucket = self.buckets.get(client_key)
            if bucket is None:
                bucket = TokenBucket(
                    tokens=float(self.burst),
                    last_refill=now,
                    last_seen=now,
                )
                self.buckets[client_key] = bucket

            elapsed = max(0.0, now - bucket.last_refill)
            if elapsed > 0:
                bucket.tokens = min(
                    float(self.burst),
                    bucket.tokens + elapsed * self.rate_per_second,
                )
                bucket.last_refill = now

            bucket.last_seen = now

            if bucket.tokens < 1.0:
                return False

            bucket.tokens -= 1.0
            return True

    def _cleanup_expired(self, now: float) -> None:
        if now - self._last_cleanup_ts < 60:
            return

        expired_keys = [
            key
            for key, bucket in self.buckets.items()
            if now - bucket.last_seen > self.ttl_seconds
        ]
        for key in expired_keys:
            self.buckets.pop(key, None)
        self._last_cleanup_ts = now


def _normalize_scope_config(scope_config: str) -> List[str]:
    valid = {"api_key", "ip", "model", "path"}
    parsed = [
        segment.strip().lower()
        for segment in scope_config.split(",")
        if segment.strip()
    ]
    normalized = [segment for segment in parsed if segment in valid]
    return normalized if normalized else ["ip"]


rate_limit_scopes = _normalize_scope_config(RATE_LIMIT_SCOPE)
rate_limiter = RateLimiter(
    RATE_LIMIT_RPM,
    RATE_LIMIT_BURST,
    RATE_LIMIT_TTL_S,
)
