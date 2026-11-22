import pytest

from app.core.config import Settings, get_settings


def test_settings_load_defaults() -> None:
    settings = Settings()
    assert settings.APP_NAME == "Attendance Monitoring System"
    assert settings.ENVIRONMENT == "development" or settings.ENVIRONMENT == "testing"
    assert settings.JWT_ALGORITHM == "HS256"
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 30
    assert settings.REFRESH_TOKEN_EXPIRE_DAYS == 7
    assert settings.CHECKIN_CODE_TTL_MINUTES == 15
    assert settings.FIRST_ADMIN_EMAIL == "admin@example.com"
    assert settings.FIRST_ADMIN_PASSWORD == "ChangeMe123!"


def test_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Custom Attendance Service")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "45")
    monkeypatch.setenv("FIRST_ADMIN_EMAIL", "superadmin@school.edu")

    settings = Settings()
    assert settings.APP_NAME == "Custom Attendance Service"
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 45
    assert settings.FIRST_ADMIN_EMAIL == "superadmin@school.edu"


def test_cors_origins_parsing() -> None:
    # Comma-separated string
    s1 = Settings(CORS_ORIGINS="http://localhost:3000,http://example.com")  # type: ignore[arg-type]
    assert s1.CORS_ORIGINS == ["http://localhost:3000", "http://example.com"]

    # JSON formatted list string
    s2 = Settings(CORS_ORIGINS='["http://localhost:3000", "http://localhost:8080"]')  # type: ignore[arg-type]
    assert s2.CORS_ORIGINS == ["http://localhost:3000", "http://localhost:8080"]

    # Native Python list
    s3 = Settings(CORS_ORIGINS=["http://localhost:3000"])
    assert s3.CORS_ORIGINS == ["http://localhost:3000"]

    # Empty string or invalid
    s4 = Settings(CORS_ORIGINS="")  # type: ignore[arg-type]
    assert s4.CORS_ORIGINS == []

    s5 = Settings(CORS_ORIGINS=123)  # type: ignore[arg-type]
    assert s5.CORS_ORIGINS == []

    # Malformed JSON falling back to comma split
    s6 = Settings(CORS_ORIGINS="[malformed, json]")  # type: ignore[arg-type]
    assert s6.CORS_ORIGINS == ["[malformed", "json]"]


def test_get_settings_cached() -> None:
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_production_settings_valid() -> None:
    settings = Settings(
        ENVIRONMENT="production",
        JWT_SECRET_KEY="a-very-strong-production-secret-key-that-is-long-enough",
        FIRST_ADMIN_PASSWORD="StrongSuperSecretPassword2026!#",
    )
    assert settings.ENVIRONMENT == "production"
    assert settings.JWT_SECRET_KEY == "a-very-strong-production-secret-key-that-is-long-enough"
    assert settings.FIRST_ADMIN_PASSWORD == "StrongSuperSecretPassword2026!#"


@pytest.mark.parametrize(
    "insecure_jwt",
    [
        "short-secret",
        "change-me-long-enough-production-secret-key-12345",
        "a-long-enough-secret-key-with-placeholder-value",
        "CHANGE-ME-uppercase-long-enough-secret-key-12345",
        "PLACEHOLDER-uppercase-long-enough-secret-key-123",
    ],
)
def test_production_settings_insecure_jwt_rejected(insecure_jwt: str) -> None:
    with pytest.raises(
        ValueError,
        match="JWT_SECRET_KEY must have length >= 32",
    ):
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY=insecure_jwt,
            FIRST_ADMIN_PASSWORD="StrongSuperSecretPassword2026!#",
        )


@pytest.mark.parametrize(
    "insecure_pwd",
    [
        "ChangeMe123!",
        "admin",
        "password",
        "12345678",
    ],
)
def test_production_settings_insecure_admin_password_rejected(insecure_pwd: str) -> None:
    with pytest.raises(ValueError, match="FIRST_ADMIN_PASSWORD cannot be default/insecure"):
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="a-very-strong-production-secret-key-that-is-long-enough",
            FIRST_ADMIN_PASSWORD=insecure_pwd,
        )
