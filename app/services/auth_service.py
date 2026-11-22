from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, AuthenticationError
from app.core.logging import user_id_ctx_var
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.repositories.user_repository import UserRepository, user_repository
from app.schemas.auth import TokenResponse
from app.utils.rate_limiter import RateLimiter, in_memory_rate_limiter


class AuthService:
    def __init__(
        self,
        user_repo: UserRepository | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.user_repo = user_repo or user_repository
        self.rate_limiter = rate_limiter or in_memory_rate_limiter

    async def authenticate(
        self,
        session: AsyncSession,
        email: str,
        password: str,
    ) -> TokenResponse:
        normalized_email = email.lower().strip()
        key = f"login:{normalized_email}"
        if not await self.rate_limiter.is_allowed(key, max_requests=5, window_seconds=15 * 60):
            raise AppError(
                status_code=429,
                code="TOO_MANY_FAILED_LOGINS",
                message="Too many failed login attempts. Please try again later.",
            )

        user = await self.user_repo.get_by_email(session, normalized_email)
        if not user or not verify_password(password, user.hashed_password):
            raise AuthenticationError("Invalid email or password", code="INVALID_CREDENTIALS")

        if not user.is_active:
            raise AuthenticationError("User account is inactive", code="USER_INACTIVE")

        await self.rate_limiter.reset(key)
        user_id_ctx_var.set(str(user.id))

        access_token = create_access_token(sub=user.id, role=user.role)
        refresh_token = create_refresh_token(sub=user.id)
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
        )

    async def refresh(
        self,
        session: AsyncSession,
        refresh_token: str,
    ) -> TokenResponse:
        payload = decode_token(refresh_token, expected_type="refresh")
        user_id = payload.get("sub")
        if not user_id:
            raise AuthenticationError("Invalid token subject", code="INVALID_TOKEN")

        user = await self.user_repo.get_by_id(session, user_id)
        if not user:
            raise AuthenticationError("User not found", code="USER_NOT_FOUND")

        if not user.is_active:
            raise AuthenticationError("User account is inactive", code="USER_INACTIVE")

        user_id_ctx_var.set(str(user.id))
        access_token = create_access_token(sub=user.id, role=user.role)
        new_refresh_token = create_refresh_token(sub=user.id)
        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
        )


auth_service = AuthService()
