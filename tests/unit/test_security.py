import uuid
from datetime import timedelta

import pytest

from app.core.exceptions import AuthenticationError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_and_verify_roundtrip() -> None:
    password = "SuperSecretPassword123!"
    hashed = hash_password(password)

    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


def test_token_encode_and_decode_access() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(sub=user_id, role="admin")

    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == str(user_id)
    assert payload["role"] == "admin"
    assert payload["type"] == "access"
    assert "iat" in payload
    assert "exp" in payload
    assert "jti" in payload


def test_token_encode_and_decode_refresh() -> None:
    user_id = uuid.uuid4()
    token = create_refresh_token(sub=user_id)

    payload = decode_token(token, expected_type="refresh")
    assert payload["sub"] == str(user_id)
    assert payload["type"] == "refresh"
    assert "iat" in payload
    assert "exp" in payload
    assert "jti" in payload

    # Also test with custom expires_delta
    custom_token = create_refresh_token(sub=user_id, expires_delta=timedelta(days=2))
    custom_payload = decode_token(custom_token, expected_type="refresh")
    assert custom_payload["sub"] == str(user_id)


def test_expired_token_rejected() -> None:
    user_id = uuid.uuid4()
    expired_token = create_access_token(
        sub=user_id,
        role="student",
        expires_delta=timedelta(seconds=-10),
    )

    with pytest.raises(AuthenticationError) as exc_info:
        decode_token(expired_token)

    assert exc_info.value.code == "TOKEN_EXPIRED"
    assert exc_info.value.status_code == 401


def test_refresh_token_rejected_where_access_expected() -> None:
    user_id = uuid.uuid4()
    refresh_token = create_refresh_token(sub=user_id)

    with pytest.raises(AuthenticationError) as exc_info:
        decode_token(refresh_token, expected_type="access")

    assert exc_info.value.code == "INVALID_TOKEN_TYPE"
    assert exc_info.value.status_code == 401


def test_access_token_rejected_where_refresh_expected() -> None:
    user_id = uuid.uuid4()
    access_token = create_access_token(sub=user_id, role="teacher")

    with pytest.raises(AuthenticationError) as exc_info:
        decode_token(access_token, expected_type="refresh")

    assert exc_info.value.code == "INVALID_TOKEN_TYPE"
    assert exc_info.value.status_code == 401


def test_decode_garbage_token_rejected() -> None:
    with pytest.raises(AuthenticationError) as exc_info:
        decode_token("not.a.valid.jwt.token")

    assert exc_info.value.code == "INVALID_TOKEN"
    assert exc_info.value.status_code == 401


def test_token_missing_sub_rejected() -> None:
    import jwt

    from app.core.config import settings

    token = jwt.encode(
        {"role": "admin", "type": "access"},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(AuthenticationError) as exc_info:
        decode_token(token)

    assert exc_info.value.code == "INVALID_TOKEN"
