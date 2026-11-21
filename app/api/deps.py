from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import decode_token
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.user_repository import user_repository
from app.utils.clock import Clock, get_clock

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


def require_roles(
    *roles: UserRole | str,
) -> Callable[..., Coroutine[Any, Any, User]]:
    allowed_roles = {r.value if isinstance(r, UserRole) else str(r) for r in roles}

    async def role_checker(
        current_user: CurrentUser,
    ) -> User:
        if current_user.role not in allowed_roles:
            raise PermissionDeniedError(
                f"Operation not permitted for role: {current_user.role}",
                code="PERMISSION_DENIED",
            )
        return current_user

    return role_checker


AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
TeacherOrAdmin = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.TEACHER))]
ClockDep = Annotated[Clock, Depends(get_clock)]
