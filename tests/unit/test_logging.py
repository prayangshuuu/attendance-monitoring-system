from __future__ import annotations

import json
import logging
from unittest.mock import patch

from app.core.config import Settings
from app.core.logging import (
    DevelopmentFormatter,
    JsonFormatter,
    SensitiveDataFilter,
    audit_logger,
    mask_sensitive_value,
    mask_string,
    request_id_ctx_var,
    setup_logging,
    user_id_ctx_var,
)


def test_audit_logger_instance() -> None:
    assert isinstance(audit_logger, logging.Logger)
    assert audit_logger.name == "audit"


def test_mask_string_passwords_and_tokens() -> None:
    raw = "User password=MySecret123! and access_token=eyJh.b.c and token: 'tok_xyz'"
    masked = mask_string(raw)
    assert "MySecret123!" not in masked
    assert "eyJh.b.c" not in masked
    assert "tok_xyz" not in masked
    assert "password=******" in masked
    assert "access_token=******" in masked
    assert "token: '******'" in masked


def test_mask_string_bearer_and_checkin_codes() -> None:
    raw = "Authorization: Bearer secret-jwt-payload, code=123456, checkin_code: 654321"
    masked = mask_string(raw)
    assert "secret-jwt-payload" not in masked
    assert "123456" not in masked
    assert "654321" not in masked
    assert "Bearer ******" in masked
    assert "code=******" in masked
    assert "checkin_code: ******" in masked


def test_mask_string_does_not_mask_status_code_or_error_code() -> None:
    raw = "Request finished with status_code=200 and error_code=VALIDATION_ERROR"
    masked = mask_string(raw)
    assert "status_code=200" in masked
    assert "error_code=VALIDATION_ERROR" in masked


def test_mask_sensitive_value_dict_and_list() -> None:
    data = {
        "password": "plain-password",
        "nested": {
            "token": "sensitive-token",
            "normal": "hello",
            "list_data": [{"checkin_code": "999999"}, "safe"],
        },
        "status_code": 200,
    }
    masked = mask_sensitive_value("", data)
    assert masked["password"] == "******"
    assert masked["nested"]["token"] == "******"
    assert masked["nested"]["normal"] == "hello"
    assert masked["nested"]["list_data"][0]["checkin_code"] == "******"
    assert masked["nested"]["list_data"][1] == "safe"
    assert masked["status_code"] == 200


def test_sensitive_data_filter_masks_log_record() -> None:
    filter_obj = SensitiveDataFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Login failed for password=%s and token=%s",
        args=("secret1", "token2"),
        exc_info=None,
    )
    filter_obj.filter(record)
    formatted = record.getMessage()
    assert "secret1" not in formatted
    assert "token2" not in formatted
    assert "password=******" in formatted
    assert "token=******" in formatted


def test_sensitive_data_filter_masks_extra_attributes() -> None:
    filter_obj = SensitiveDataFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Some event",
        args=(),
        exc_info=None,
    )
    record.password = "my_raw_password"
    record.status_code = 400
    filter_obj.filter(record)
    assert record.password == "******"
    assert record.status_code == 400


def test_sensitive_data_filter_dict_message_and_args() -> None:
    filter_obj = SensitiveDataFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg={"password": "raw", "token": "abc"},
        args=(),
        exc_info=None,
    )
    filter_obj.filter(record)
    assert isinstance(record.msg, dict)
    assert record.msg["password"] == "******"
    assert record.msg["token"] == "******"

    dict_args_record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Data: %(password)s",
        args=({"password": "raw"},),
        exc_info=None,
    )
    filter_obj.filter(dict_args_record)
    assert "raw" not in dict_args_record.getMessage()


def test_json_formatter_production() -> None:
    req_token = request_id_ctx_var.set("req-12345")
    user_token = user_id_ctx_var.set("user-67890")
    try:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="app.access",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Request completed",
            args=(),
            exc_info=None,
        )
        record.method = "GET"
        record.path = "/api/v1/health"
        record.status = 200
        record.duration_ms = 12.34
        record.actor_id = "admin-1"

        line = formatter.format(record)
        data = json.loads(line)

        assert "timestamp" in data
        assert data["level"] == "INFO"
        assert data["logger"] == "app.access"
        assert data["message"] == "Request completed"
        assert data["request_id"] == "req-12345"
        assert data["user_id"] == "user-67890"
        assert data["path"] == "/api/v1/health"
        assert data["method"] == "GET"
        assert data["status"] == 200
        assert data["duration_ms"] == 12.34
        assert data["actor_id"] == "admin-1"
    finally:
        request_id_ctx_var.reset(req_token)
        user_id_ctx_var.reset(user_token)


def test_json_formatter_with_exception() -> None:
    formatter = JsonFormatter()
    try:
        raise ValueError("test exception message")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="app.main",
            level=logging.ERROR,
            pathname="test.py",
            lineno=20,
            msg="Exception caught",
            args=(),
            exc_info=sys.exc_info(),
        )
        line = formatter.format(record)
        data = json.loads(line)
        assert data["level"] == "ERROR"
        assert "exception" in data
        assert "test exception message" in data["exception"]


def test_development_formatter() -> None:
    req_token = request_id_ctx_var.set("req-dev")
    user_token = user_id_ctx_var.set("user-dev")
    try:
        formatter = DevelopmentFormatter()
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Hello dev",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "INFO" in formatted
        assert "req_id=req-dev" in formatted
        assert "user_id=user-dev" in formatted
        assert "Hello dev" in formatted
    finally:
        request_id_ctx_var.reset(req_token)
        user_id_ctx_var.reset(user_token)


def test_setup_logging_dev_and_prod() -> None:
    with patch("app.core.logging.get_settings", return_value=Settings(ENVIRONMENT="development")):
        setup_logging()
        root = logging.getLogger()
        assert len(root.handlers) >= 1
        assert isinstance(root.handlers[0].formatter, DevelopmentFormatter)

    with patch("app.core.logging.get_settings", return_value=Settings(ENVIRONMENT="production")):
        setup_logging()
        root = logging.getLogger()
        assert len(root.handlers) >= 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
