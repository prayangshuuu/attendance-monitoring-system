import warnings
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
from pwdlib import PasswordHash

from app.core.config import settings
from app.core.exceptions import AuthenticationError

if hasattr(jwt, "InsecureKeyLengthWarning"):
    warnings.filterwarnings("ignore", category=jwt.InsecureKeyLengthWarning)

pwd_context = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    sub: str | UUID,
    role: str,
    expires_delta: timedelta | None = None,
) -> str:
    now = datetime.now(UTC)
    if expires_delta is not None:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode: dict[str, Any] = {
        "sub": str(sub),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": expire,
        "jti": str(uuid4()),
    }
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(
    sub: str | UUID,
    expires_delta: timedelta | None = None,
) -> str:
    now = datetime.now(UTC)
    if expires_delta is not None:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode: dict[str, Any] = {
        "sub": str(sub),
        "type": "refresh",
        "iat": now,
        "exp": expire,
        "jti": str(uuid4()),
    }
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as e:
        raise AuthenticationError("Token has expired", code="TOKEN_EXPIRED") from e
    except jwt.InvalidTokenError as e:
        raise AuthenticationError("Could not validate credentials", code="INVALID_TOKEN") from e

    if expected_type is not None:
        token_type = payload.get("type")
        if token_type != expected_type:
            raise AuthenticationError(
                f"Invalid token type: expected '{expected_type}', got '{token_type}'",
                code="INVALID_TOKEN_TYPE",
            )

    if not payload.get("sub"):
        raise AuthenticationError("Token missing subject claim", code="INVALID_TOKEN")

    return payload
