from app.core.exceptions import (
    AppError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationAppError,
)


def test_app_error_defaults() -> None:
    err = AppError("something went wrong")
    assert err.message == "something went wrong"
    assert err.code == "BAD_REQUEST"
    assert err.status_code == 400
    assert err.details == {}


def test_app_error_custom_details() -> None:
    err = AppError("bad", code="CUSTOM_CODE", status_code=418, details={"field": "reason"})
    assert err.message == "bad"
    assert err.code == "CUSTOM_CODE"
    assert err.status_code == 418
    assert err.details == {"field": "reason"}


def test_not_found_error() -> None:
    err = NotFoundError("Item not found")
    assert err.message == "Item not found"
    assert err.code == "NOT_FOUND"
    assert err.status_code == 404
    assert err.details == {}


def test_conflict_error() -> None:
    err = ConflictError("Email already in use")
    assert err.message == "Email already in use"
    assert err.code == "CONFLICT"
    assert err.status_code == 409


def test_permission_denied_error() -> None:
    err = PermissionDeniedError("Admin access required")
    assert err.message == "Admin access required"
    assert err.code == "PERMISSION_DENIED"
    assert err.status_code == 403


def test_authentication_error() -> None:
    err = AuthenticationError("Invalid password")
    assert err.message == "Invalid password"
    assert err.code == "AUTHENTICATION_FAILED"
    assert err.status_code == 401


def test_validation_app_error() -> None:
    err = ValidationAppError("Field missing")
    assert err.message == "Field missing"
    assert err.code == "VALIDATION_ERROR"
    assert err.status_code == 400
