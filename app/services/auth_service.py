from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.repositories.user_repository import UserRepository, user_repository
from app.schemas.auth import TokenResponse


class AuthService:
    def __init__(self, user_repo: UserRepository | None = None) -> None:
        self.user_repo = user_repo or user_repository

    async def authenticate(
        self,
        session: AsyncSession,
        email: str,
        password: str,
    ) -> TokenResponse:
        user = await self.user_repo.get_by_email(session, email)
        if not user or not verify_password(password, user.hashed_password):
            raise AuthenticationError("Invalid email or password", code="INVALID_CREDENTIALS")

        if not user.is_active:
            raise AuthenticationError("User account is inactive", code="USER_INACTIVE")

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

        access_token = create_access_token(sub=user.id, role=user.role)
        new_refresh_token = create_refresh_token(sub=user.id)
        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
        )


auth_service = AuthService()
