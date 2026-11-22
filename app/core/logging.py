from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings

request_id_ctx_var: ContextVar[str] = ContextVar("request_id", default="")
user_id_ctx_var: ContextVar[str] = ContextVar("user_id", default="")

audit_logger: logging.Logger = logging.getLogger("audit")

SENSITIVE_FIELD_NAMES: set[str] = {
    "password",
    "hashed_password",
    "current_password",
    "new_password",
    "access_token",
    "refresh_token",
    "token",
    "jwt",
    "secret",
    "checkin_code",
    "check_in_code",
    "passcode",
    "code",
}

_BEARER_PATTERN = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9\-._~+/]+=*")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(?<!status_)(?<!error_)(password|hashed_password|current_password|new_password|"
    r"access_token|refresh_token|token|jwt|secret|checkin_code|check_in_code|passcode|code)\b"
    r'(\s*[:=]\s*["\']?)([^"\'\s&,]+)(["\']?)'
)


def mask_string(value: str) -> str:
    """Masks sensitive values (passwords, tokens, codes) in string representations."""
    masked = _BEARER_PATTERN.sub(r"\1******", value)
    return _KEY_VALUE_PATTERN.sub(r"\1\2******\4", masked)


def mask_sensitive_value(key: str, val: Any) -> Any:
    """Recursively masks sensitive values in dictionaries and lists."""
    key_lower = key.lower()
    if (
        key_lower in SENSITIVE_FIELD_NAMES
        and not key_lower.endswith("_status_code")
        and key_lower != "status_code"
    ):
        return "******"

    if isinstance(val, dict):
        return {str(k): mask_sensitive_value(str(k), v) for k, v in val.items()}
    if isinstance(val, list):
        return [
            mask_sensitive_value(key, item) if isinstance(item, (dict, list)) else item
            for item in val
        ]
    if isinstance(val, str):
        return mask_string(val)
    return val


class SensitiveDataFilter(logging.Filter):
    """Filters/masks sensitive data from log records before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if (
                isinstance(record.args, tuple)
                and len(record.args) == 1
                and isinstance(record.args[0], dict)
            ):
                record.args = (
                    {str(k): mask_sensitive_value(str(k), v) for k, v in record.args[0].items()},
                )
            elif isinstance(record.args, dict):
                record.args = {
                    str(k): mask_sensitive_value(str(k), v) for k, v in record.args.items()
                }
            try:
                formatted = record.getMessage()
                record.msg = mask_string(formatted)
                record.args = ()
            except Exception:
                pass
        elif isinstance(record.msg, str):
            record.msg = mask_string(record.msg)
        elif isinstance(record.msg, dict):
            record.msg = {str(k): mask_sensitive_value(str(k), v) for k, v in record.msg.items()}

        # Mask sensitive attributes if added to record directly or via extra
        for attr in list(record.__dict__.keys()):
            attr_lower = attr.lower()
            if attr_lower in SENSITIVE_FIELD_NAMES and attr_lower != "status_code":
                record.__dict__[attr] = "******"

        return True


class JsonFormatter(logging.Formatter):
    """JSON log formatter for production environment."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        message = record.getMessage()

        request_id = getattr(record, "request_id", None) or request_id_ctx_var.get() or ""
        user_id = getattr(record, "user_id", None) or user_id_ctx_var.get() or ""

        path = getattr(record, "path", None)
        method = getattr(record, "method", None)
        status = getattr(record, "status", None)
        duration_ms = getattr(record, "duration_ms", None)

        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": request_id,
            "user_id": user_id,
            "path": path,
            "method": method,
            "status": status,
            "duration_ms": duration_ms,
        }

        # Include audit or extra custom fields if present
        for key in (
            "actor_id",
            "target_student_id",
            "target_user_id",
            "session_id",
            "old_status",
            "new_status",
            "old_role",
            "new_role",
            "action",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


COLORS: dict[int, str] = {
    logging.DEBUG: "\033[36m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}
RESET = "\033[0m"


class DevelopmentFormatter(logging.Formatter):
    """Readable colored log formatter for development environment."""

    def format(self, record: logging.LogRecord) -> str:
        color = COLORS.get(record.levelno, "")
        reset = RESET if color else ""
        asctime = self.formatTime(record, datefmt="%Y-%m-%d %H:%M:%S")

        req_id = getattr(record, "request_id", None) or request_id_ctx_var.get()
        user_id = getattr(record, "user_id", None) or user_id_ctx_var.get()

        context_items: list[str] = []
        if req_id:
            context_items.append(f"req_id={req_id}")
        if user_id:
            context_items.append(f"user_id={user_id}")
        context_str = f" [{' | '.join(context_items)}]" if context_items else ""

        message = record.getMessage()
        formatted = (
            f"{asctime} {color}{record.levelname:<8}{reset} [{record.name}]{context_str} {message}"
        )
        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)
        return formatted


def setup_logging() -> None:
    """Configures application-wide logging handlers and formatters."""
    settings = get_settings()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(SensitiveDataFilter())

    if settings.ENVIRONMENT == "production":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(DevelopmentFormatter())

    root_logger.addHandler(handler)
