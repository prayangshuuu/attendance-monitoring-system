from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import create_access_token
from app.main import create_app
from tests.factories import make_user


@pytest.mark.asyncio
async def test_request_id_generated_when_not_provided(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    req_id = response.headers.get("x-request-id")
    assert req_id is not None
    assert len(req_id) >= 16


@pytest.mark.asyncio
async def test_request_id_preserved_when_provided(client: AsyncClient) -> None:
    custom_id = "custom-test-request-id-12345"
    response = await client.get("/health", headers={"X-Request-ID": custom_id})
    assert response.status_code == 200
    assert response.headers.get("x-request-id") == custom_id


@pytest.mark.asyncio
async def test_security_headers_in_development(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    # HSTS should NOT be present in development
    assert "strict-transport-security" not in response.headers


@pytest.mark.asyncio
async def test_security_headers_in_production() -> None:
    prod_settings = Settings(ENVIRONMENT="production")
    with (
        patch("app.main.get_settings", return_value=prod_settings),
        patch("app.core.middleware.get_settings", return_value=prod_settings),
    ):
        prod_app = create_app()
        transport = ASGITransport(app=prod_app)
        async with AsyncClient(transport=transport, base_url="http://test") as prod_client:
            response = await prod_client.get("/api/v1/health-non-existent")
            assert response.headers.get("x-content-type-options") == "nosniff"
            assert response.headers.get("x-frame-options") == "DENY"
            assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
            assert (
                response.headers.get("strict-transport-security")
                == "max-age=31536000; includeSubDomains"
            )


@pytest.mark.asyncio
async def test_cors_middleware_headers(client: AsyncClient) -> None:
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type, X-Request-ID",
    }
    response = await client.options("/api/v1/auth/login", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert response.headers.get("access-control-allow-credentials") == "true"

    # Actual request with Origin should expose X-Request-ID
    get_response = await client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert get_response.status_code == 200
    assert get_response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    exposed = get_response.headers.get("access-control-expose-headers", "")
    assert "x-request-id" in exposed.lower()

    # Preflight from disallowed origin should not be granted access
    disallowed_headers = {
        "Origin": "http://unauthorized-origin.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type",
    }
    disallowed_response = await client.options("/api/v1/auth/login", headers=disallowed_headers)
    assert disallowed_response.headers.get("access-control-allow-origin") is None


@pytest.mark.asyncio
async def test_access_log_middleware_logs_request(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.access"):
        response = await client.get("/health")
        assert response.status_code == 200

    access_records = [r for r in caplog.records if r.name == "app.access"]
    assert len(access_records) >= 1
    rec = access_records[0]
    assert rec.method == "GET"
    assert rec.path == "/health"
    assert rec.status == 200
    assert rec.duration_ms >= 0


@pytest.mark.asyncio
async def test_access_log_middleware_captures_authenticated_user_id(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = await make_user(db_session)
    token = create_access_token(sub=user.id, role=user.role)

    with caplog.at_level(logging.INFO, logger="app.access"):
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    access_records = [r for r in caplog.records if r.name == "app.access"]
    assert len(access_records) >= 1
    rec = access_records[-1]
    assert rec.user_id == str(user.id)


@pytest.mark.asyncio
async def test_login_request_logs_do_not_contain_password_or_tokens(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_password = "SuperSecretPassword123!"
    user = await make_user(db_session, password=raw_password)

    with caplog.at_level(logging.DEBUG):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": raw_password},
        )
        assert response.status_code == 200
        data = response.json()
        access_token = data["access_token"]
        refresh_token = data["refresh_token"]

    all_logs = " ".join(r.getMessage() for r in caplog.records)
    assert raw_password not in all_logs
    assert access_token not in all_logs
    assert refresh_token not in all_logs
