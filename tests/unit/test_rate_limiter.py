import asyncio
import time

import pytest

from app.core.exceptions import AppError
from app.utils.rate_limiter import InMemoryRateLimiter, RateLimiter, get_rate_limiter


def test_rate_limiter_protocol_conformance() -> None:
    limiter = InMemoryRateLimiter()
    assert isinstance(limiter, RateLimiter)


def test_get_rate_limiter_singleton() -> None:
    limiter = get_rate_limiter()
    assert isinstance(limiter, RateLimiter)
    assert limiter is get_rate_limiter()


@pytest.mark.asyncio
async def test_is_allowed_basic_and_window_sliding() -> None:
    limiter = InMemoryRateLimiter()
    key = "student1:session1"

    # Up to 3 requests allowed in 10-second window
    assert await limiter.is_allowed(key, max_requests=3, window_seconds=10) is True
    assert await limiter.is_allowed(key, max_requests=3, window_seconds=10) is True
    assert await limiter.is_allowed(key, max_requests=3, window_seconds=10) is True
    # 4th request rejected
    assert await limiter.is_allowed(key, max_requests=3, window_seconds=10) is False

    # Different key is independent
    other_key = "student2:session1"
    assert await limiter.is_allowed(other_key, max_requests=3, window_seconds=10) is True


@pytest.mark.asyncio
async def test_sliding_window_expiration() -> None:
    limiter = InMemoryRateLimiter()
    key = "student1:session2"

    # Insert old timestamps manually
    now = time.monotonic()
    limiter._requests[key] = [now - 15, now - 12]

    # Window of 10s -> old timestamps are purged, so new requests are allowed
    assert await limiter.is_allowed(key, max_requests=2, window_seconds=10) is True
    assert await limiter.is_allowed(key, max_requests=2, window_seconds=10) is True
    assert await limiter.is_allowed(key, max_requests=2, window_seconds=10) is False


@pytest.mark.asyncio
async def test_check_raises_app_error_on_limit_exceeded() -> None:
    limiter = InMemoryRateLimiter()
    key = "student1:session3"

    for _ in range(5):
        await limiter.check(key, max_requests=5, window_seconds=60)

    with pytest.raises(AppError) as exc_info:
        await limiter.check(key, max_requests=5, window_seconds=60)

    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "TOO_MANY_ATTEMPTS"
    assert exc_info.value.message == "Too many check-in attempts. Please try again later."


@pytest.mark.asyncio
async def test_reset_clears_requests() -> None:
    limiter = InMemoryRateLimiter()
    key = "student1:session4"

    for _ in range(5):
        await limiter.is_allowed(key, max_requests=5, window_seconds=60)

    assert await limiter.is_allowed(key, max_requests=5, window_seconds=60) is False

    await limiter.reset()
    assert await limiter.is_allowed(key, max_requests=5, window_seconds=60) is True


@pytest.mark.asyncio
async def test_concurrent_access() -> None:
    limiter = InMemoryRateLimiter()
    key = "student1:session5"

    async def hit() -> bool:
        return await limiter.is_allowed(key, max_requests=10, window_seconds=60)

    results = await asyncio.gather(*[hit() for _ in range(15)])
    assert results.count(True) == 10
    assert results.count(False) == 5
