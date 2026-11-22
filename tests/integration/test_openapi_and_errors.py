from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import app, create_app
from app.schemas.common import ErrorDetail, ErrorResponse


def test_error_response_schema() -> None:
    detail = ErrorDetail(code="TEST_CODE", message="Test message", details={"field": "value"})
    res = ErrorResponse(error=detail)
    d = res.model_dump()
    assert d["error"]["code"] == "TEST_CODE"
    assert d["error"]["message"] == "Test message"
    assert d["error"]["details"]["field"] == "value"


@pytest.mark.asyncio
async def test_validation_error_format(client: AsyncClient) -> None:
    # Invalid email and missing password
    response = await client.post("/api/v1/auth/login", json={"email": "bad_email"})
    assert response.status_code == 422
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert data["error"]["message"] == "Validation error"
    assert "errors" in data["error"]["details"]


@pytest.mark.asyncio
async def test_unhandled_exception_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        with (
            patch("app.api.v1.auth.auth_service.authenticate", side_effect=RuntimeError("Boom!")),
            caplog.at_level(logging.ERROR, logger="app.main"),
        ):
            response = await test_client.post(
                "/api/v1/auth/login",
                json={"email": "test@example.com", "password": "password"},
            )
            assert response.status_code == 500
            data = response.json()
            assert data["error"]["code"] == "INTERNAL_SERVER_ERROR"
            assert data["error"]["message"] == "An unexpected error occurred"
            assert data["error"]["details"] == {}
            # Assert no sensitive trace or internal error info leaked to response
            assert "Boom!" not in response.text
            assert "traceback" not in response.text.lower()

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert any("An unexpected error occurred" in r.getMessage() for r in errors)


@pytest.mark.asyncio
async def test_docs_enabled_in_development(client: AsyncClient) -> None:
    docs_res = await client.get("/docs")
    assert docs_res.status_code == 200

    openapi_res = await client.get("/openapi.json")
    assert openapi_res.status_code == 200
    schema = openapi_res.json()
    assert "paths" in schema
    assert "components" in schema


@pytest.mark.asyncio
async def test_docs_disabled_in_production() -> None:
    prod_settings = Settings(
        ENVIRONMENT="production",
        JWT_SECRET_KEY="super-secret-key-that-is-at-least-32-chars-long",
        FIRST_ADMIN_PASSWORD="StrongProdPassword123!",
    )
    with (
        patch("app.main.get_settings", return_value=prod_settings),
        patch("app.core.config.get_settings", return_value=prod_settings),
        patch("app.core.middleware.get_settings", return_value=prod_settings),
    ):
        prod_app = create_app()
        assert prod_app.docs_url is None
        assert prod_app.redoc_url is None
        assert prod_app.openapi_url is None
        transport = ASGITransport(app=prod_app)
        async with AsyncClient(transport=transport, base_url="http://test") as prod_client:
            docs_res = await prod_client.get("/docs")
            assert docs_res.status_code == 404

            redoc_res = await prod_client.get("/redoc")
            assert redoc_res.status_code == 404

            openapi_res = await prod_client.get("/openapi.json")
            assert openapi_res.status_code == 404
