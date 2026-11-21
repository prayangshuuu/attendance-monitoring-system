import uuid
from unittest.mock import patch

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import create_access_token
from app.models.enums import UserRole
from tests.factories import make_user


@pytest.mark.asyncio
async def test_get_current_user_no_credentials(db_session: AsyncSession) -> None:
    with pytest.raises(AuthenticationError) as exc_info:
        await get_current_user(db_session, None)
    assert exc_info.value.code == "NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_get_current_user_invalid_scheme(db_session: AsyncSession) -> None:
    credentials = HTTPAuthorizationCredentials(scheme="Basic", credentials="some_token")
    with pytest.raises(AuthenticationError) as exc_info:
        await get_current_user(db_session, credentials)
    assert exc_info.value.code == "INVALID_AUTH_SCHEME"


@pytest.mark.asyncio
async def test_get_current_user_missing_sub_in_payload(db_session: AsyncSession) -> None:
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    with patch("app.api.deps.decode_token", return_value={"type": "access"}):
        with pytest.raises(AuthenticationError) as exc_info:
            await get_current_user(db_session, credentials)
        assert exc_info.value.code == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_get_current_user_user_not_found(db_session: AsyncSession) -> None:
    non_existent_id = uuid.uuid4()
    token = create_access_token(sub=non_existent_id, role=UserRole.STUDENT)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(AuthenticationError) as exc_info:
        await get_current_user(db_session, credentials)
    assert exc_info.value.code == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_current_user_inactive_user(db_session: AsyncSession) -> None:
    user = await make_user(db_session, is_active=False)
    token = create_access_token(sub=user.id, role=user.role)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(AuthenticationError) as exc_info:
        await get_current_user(db_session, credentials)
    assert exc_info.value.code == "USER_INACTIVE"


@pytest.mark.asyncio
async def test_get_current_user_success(db_session: AsyncSession) -> None:
    user = await make_user(db_session, is_active=True)
    token = create_access_token(sub=user.id, role=user.role)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    current_user = await get_current_user(db_session, credentials)
    assert current_user.id == user.id


@pytest.mark.asyncio
async def test_require_roles_checker(db_session: AsyncSession) -> None:
    student = await make_user(db_session, role=UserRole.STUDENT)
    admin = await make_user(db_session, role=UserRole.ADMIN)

    admin_guard = require_roles(UserRole.ADMIN)

    with pytest.raises(PermissionDeniedError) as exc_info:
        await admin_guard(student)
    assert exc_info.value.code == "PERMISSION_DENIED"

    allowed = await admin_guard(admin)
    assert allowed.id == admin.id
