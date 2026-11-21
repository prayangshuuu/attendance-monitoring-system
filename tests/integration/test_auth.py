from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, create_refresh_token
from tests.factories import make_user


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session, password="studentpassword123")
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "studentpassword123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session, password="studentpassword123")
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "WrongPassword123!"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "INVALID_CREDENTIALS"
    assert data["error"]["message"] == "Invalid email or password"


@pytest.mark.asyncio
async def test_login_unknown_email(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "SomePassword123!"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "INVALID_CREDENTIALS"
    assert data["error"]["message"] == "Invalid email or password"


@pytest.mark.asyncio
async def test_login_inactive_user(client: AsyncClient, db_session: AsyncSession) -> None:
    inactive_user = await make_user(
        db_session,
        email="inactive@example.com",
        password="password123",
        is_active=False,
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": inactive_user.email, "password": "password123"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "USER_INACTIVE"
    assert data["error"]["message"] == "User account is inactive"


@pytest.mark.asyncio
async def test_me_with_valid_token(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session)
    token = create_access_token(sub=user.id, role=user.role)
    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(user.id)
    assert data["email"] == user.email
    assert data["full_name"] == user.full_name
    assert data["role"] == user.role
    assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_me_without_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_me_with_basic_auth_fails(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me", headers={"Authorization": "Basic 12345"})
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_me_with_garbage_token(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-valid-token"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_me_with_expired_token(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    expired_token = create_access_token(
        sub=user.id,
        role=user.role,
        expires_delta=timedelta(seconds=-1),
    )
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_me_with_refresh_token_fails(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    refresh_token = create_refresh_token(sub=user.id)
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "INVALID_TOKEN_TYPE"


@pytest.mark.asyncio
async def test_refresh_token_flow_success(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    refresh_token = create_refresh_token(sub=user.id)
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    # Rotated refresh token is a new token
    assert data["refresh_token"] != refresh_token


@pytest.mark.asyncio
async def test_refresh_with_access_token_fails(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session)
    access_token = create_access_token(sub=user.id, role=user.role)
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": access_token},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "INVALID_TOKEN_TYPE"


@pytest.mark.asyncio
async def test_refresh_with_inactive_user_fails(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, is_active=False)
    refresh_token = create_refresh_token(sub=user.id)

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "USER_INACTIVE"


@pytest.mark.asyncio
async def test_refresh_with_garbage_token_fails(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "garbage.token.data"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_validation_error_handler(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "not-an-email"},  # missing password and invalid email
    )
    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "errors" in data["error"]["details"]


@pytest.mark.asyncio
async def test_http_exception_handler(client: AsyncClient) -> None:
    response = await client.get("/api/v1/non-existent-route")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "HTTP_ERROR"
    assert data["error"]["message"] == "Not Found"
