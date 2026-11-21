from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User
from app.repositories.user_repository import user_repository

http_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(http_bearer)],
) -> User:
    if credentials is None:
        raise AuthenticationError("Not authenticated", code="NOT_AUTHENTICATED")
    if credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Invalid authentication scheme", code="INVALID_AUTH_SCHEME")

    payload = decode_token(credentials.credentials, expected_type="access")
    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Invalid token subject", code="INVALID_TOKEN")

    user = await user_repository.get_by_id(db, user_id)
    if not user:
        raise AuthenticationError("User not found", code="USER_NOT_FOUND")

    if not user.is_active:
        raise AuthenticationError("User account is inactive", code="USER_INACTIVE")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
