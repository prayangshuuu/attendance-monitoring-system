import asyncio
import time
from collections import defaultdict
from typing import Protocol, runtime_checkable

from app.core.exceptions import AppError


@runtime_checkable
class RateLimiter(Protocol):
    async def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool: ...

    async def reset(self, key: str | None = None) -> None: ...


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - window_seconds
            timestamps = [ts for ts in self._requests[key] if ts > cutoff]
            if len(timestamps) >= max_requests:
                self._requests[key] = timestamps
                return False
            timestamps.append(now)
            self._requests[key] = timestamps
            return True

    async def check(
        self,
        key: str,
        max_requests: int = 5,
        window_seconds: int = 60,
    ) -> None:
        allowed = await self.is_allowed(key, max_requests, window_seconds)
        if not allowed:
            raise AppError(
                message="Too many check-in attempts. Please try again later.",
                code="TOO_MANY_ATTEMPTS",
                status_code=429,
            )

    async def reset(self, key: str | None = None) -> None:
        async with self._lock:
            if key is not None:
                self._requests.pop(key, None)
            else:
                self._requests.clear()


in_memory_rate_limiter = InMemoryRateLimiter()


def get_rate_limiter() -> RateLimiter:
    return in_memory_rate_limiter
