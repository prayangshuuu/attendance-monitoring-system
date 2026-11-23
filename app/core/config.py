import json
from functools import lru_cache
from typing import Any, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Attendance Monitoring System"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/attendance"
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_POOL_RECYCLE: int = 1800
    JWT_SECRET_KEY: str = "change-me-32-byte-long-secret-key-attendance-system"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    CHECKIN_CODE_TTL_MINUTES: int = 15
    CORS_ORIGINS: list[str] | str = ["http://localhost:3000"]
    FIRST_ADMIN_EMAIL: str = "admin@example.com"
    FIRST_ADMIN_PASSWORD: str = "ChangeMe123!"
    COOKIE_SECURE: bool = False
    CSRF_SECRET_KEY: str = "change-me-csrf-secret-key-attendance-system"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return []
            if stripped.startswith("[") and stripped.endswith("]"):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass
            return [item.strip() for item in stripped.split(",") if item.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(item).strip() for item in v if str(item).strip()]
        return []

    @model_validator(mode="after")
    def validate_production_security(self) -> Self:
        if self.ENVIRONMENT == "production":
            jwt_secret = self.JWT_SECRET_KEY.lower()
            if (
                len(self.JWT_SECRET_KEY) < 32
                or "change-me" in jwt_secret
                or "placeholder" in jwt_secret
            ):
                raise ValueError(
                    "JWT_SECRET_KEY must have length >= 32 and cannot contain "
                    "'change-me' or 'placeholder' in production"
                )
            insecure_passwords = {"ChangeMe123!", "admin", "password", "12345678"}
            if self.FIRST_ADMIN_PASSWORD in insecure_passwords:
                raise ValueError("FIRST_ADMIN_PASSWORD cannot be default/insecure in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
