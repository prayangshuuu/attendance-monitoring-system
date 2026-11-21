import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError
from app.core.security import create_refresh_token
from app.models.enums import UserRole
from app.services.auth_service import auth_service
from tests.factories import make_user


@pytest.mark.asyncio
async def test_auth_service_authenticate_success(db_session: AsyncSession) -> None:
    await make_user(
        db_session,
        email="svc_auth@example.com",
        password="MySecretPassword1!",
        role=UserRole.STUDENT,
        is_active=True,
    )
    tokens = await auth_service.authenticate(
        db_session, "svc_auth@example.com", "MySecretPassword1!"
    )
    assert tokens.access_token is not None
    assert tokens.refresh_token is not None
    assert tokens.token_type == "bearer"


@pytest.mark.asyncio
async def test_auth_service_authenticate_wrong_password(db_session: AsyncSession) -> None:
    await make_user(
        db_session,
        email="svc_wrong_pwd@example.com",
        password="CorrectPassword1!",
        role=UserRole.TEACHER,
    )
    with pytest.raises(AuthenticationError) as exc_info:
        await auth_service.authenticate(db_session, "svc_wrong_pwd@example.com", "WrongPassword!")
    assert exc_info.value.code == "INVALID_CREDENTIALS"
    assert exc_info.value.message == "Invalid email or password"


@pytest.mark.asyncio
async def test_auth_service_authenticate_unknown_email(db_session: AsyncSession) -> None:
    with pytest.raises(AuthenticationError) as exc_info:
        await auth_service.authenticate(db_session, "nonexistent@example.com", "SomePassword!")
    assert exc_info.value.code == "INVALID_CREDENTIALS"
    assert exc_info.value.message == "Invalid email or password"


@pytest.mark.asyncio
async def test_auth_service_authenticate_inactive_user(db_session: AsyncSession) -> None:
    await make_user(
        db_session,
        email="svc_inactive@example.com",
        password="MyPassword1!",
        is_active=False,
    )
    with pytest.raises(AuthenticationError) as exc_info:
        await auth_service.authenticate(db_session, "svc_inactive@example.com", "MyPassword1!")
    assert exc_info.value.code == "USER_INACTIVE"


@pytest.mark.asyncio
async def test_auth_service_refresh_success(db_session: AsyncSession) -> None:
    user = await make_user(db_session, is_active=True)
    refresh_token = create_refresh_token(sub=user.id)

    tokens = await auth_service.refresh(db_session, refresh_token)
    assert tokens.access_token is not None
    assert tokens.refresh_token is not None
    assert tokens.refresh_token != refresh_token


@pytest.mark.asyncio
async def test_auth_service_refresh_user_not_found(db_session: AsyncSession) -> None:
    non_existent_id = uuid.uuid4()
    refresh_token = create_refresh_token(sub=non_existent_id)

    with pytest.raises(AuthenticationError) as exc_info:
        await auth_service.refresh(db_session, refresh_token)
    assert exc_info.value.code == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_auth_service_refresh_user_inactive(db_session: AsyncSession) -> None:
    user = await make_user(db_session, is_active=False)
    refresh_token = create_refresh_token(sub=user.id)

    with pytest.raises(AuthenticationError) as exc_info:
        await auth_service.refresh(db_session, refresh_token)
    assert exc_info.value.code == "USER_INACTIVE"


@pytest.mark.asyncio
async def test_auth_service_refresh_missing_sub(db_session: AsyncSession) -> None:
    with patch("app.services.auth_service.decode_token", return_value={"type": "refresh"}):
        with pytest.raises(AuthenticationError) as exc_info:
            await auth_service.refresh(db_session, "dummy_token")
        assert exc_info.value.code == "INVALID_TOKEN"
