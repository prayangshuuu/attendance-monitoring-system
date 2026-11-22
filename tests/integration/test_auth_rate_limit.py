from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.rate_limiter import in_memory_rate_limiter
from tests.factories import make_user


@pytest.mark.asyncio
async def test_rate_limiter_reset_specific_key() -> None:
    key1 = "login:user1@example.com"
    key2 = "login:user2@example.com"

    for _ in range(5):
        await in_memory_rate_limiter.is_allowed(key1, max_requests=5, window_seconds=60)
        await in_memory_rate_limiter.is_allowed(key2, max_requests=5, window_seconds=60)

    assert await in_memory_rate_limiter.is_allowed(key1, max_requests=5, window_seconds=60) is False
    assert await in_memory_rate_limiter.is_allowed(key2, max_requests=5, window_seconds=60) is False

    # Reset only key1
    await in_memory_rate_limiter.reset(key1)

    # key1 is allowed now; key2 remains blocked
    assert await in_memory_rate_limiter.is_allowed(key1, max_requests=5, window_seconds=60) is True
    assert await in_memory_rate_limiter.is_allowed(key2, max_requests=5, window_seconds=60) is False

    # Reset all
    await in_memory_rate_limiter.reset()
    assert await in_memory_rate_limiter.is_allowed(key2, max_requests=5, window_seconds=60) is True


@pytest.mark.asyncio
async def test_login_rate_limiting_5_failed_attempts(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, password="CorrectPassword123!")
    email = user.email

    # Clean any previous attempts
    await in_memory_rate_limiter.reset(f"login:{email.lower()}")

    # 5 failed attempts with wrong password
    for _ in range(5):
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "WrongPassword!"},
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "INVALID_CREDENTIALS"

    # 6th attempt should be blocked by rate limiter with 429
    blocked_res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "WrongPassword!"},
    )
    assert blocked_res.status_code == 429
    err = blocked_res.json()["error"]
    assert err["code"] == "TOO_MANY_FAILED_LOGINS"
    assert "Too many failed login attempts" in err["message"]

    # Even with correct password, still blocked until reset / window expiry
    correct_blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "CorrectPassword123!"},
    )
    assert correct_blocked.status_code == 429
    assert correct_blocked.json()["error"]["code"] == "TOO_MANY_FAILED_LOGINS"

    # Reset limiter for this email
    await in_memory_rate_limiter.reset(f"login:{email.lower()}")

    # Now login succeeds and resets counter
    success_res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "CorrectPassword123!"},
    )
    assert success_res.status_code == 200
    assert "access_token" in success_res.json()


@pytest.mark.asyncio
async def test_successful_login_resets_failed_attempts(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, password="ValidPassword123!")
    email = user.email

    await in_memory_rate_limiter.reset(f"login:{email.lower()}")

    # 3 failed attempts
    for _ in range(3):
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "WrongPassword!"},
        )
        assert res.status_code == 401

    # 4th attempt succeeds
    success_res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "ValidPassword123!"},
    )
    assert success_res.status_code == 200

    # Because reset was called on success, another 4 failed attempts are allowed (doesn't hit 5)
    for _ in range(4):
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "WrongPassword!"},
        )
        assert res.status_code == 401
