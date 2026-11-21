from typing import Any


class AppError(Exception):
    def __init__(
        self,
        message: str = "An error occurred",
        code: str = "BAD_REQUEST",
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details: dict[str, Any] = details if details is not None else {}


class NotFoundError(AppError):
    def __init__(
        self,
        message: str = "Resource not found",
        code: str = "NOT_FOUND",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=404, details=details)


class ConflictError(AppError):
    def __init__(
        self,
        message: str = "Resource already exists",
        code: str = "CONFLICT",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=409, details=details)


class PermissionDeniedError(AppError):
    def __init__(
        self,
        message: str = "Permission denied",
        code: str = "PERMISSION_DENIED",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=403, details=details)


class AuthenticationError(AppError):
    def __init__(
        self,
        message: str = "Authentication failed",
        code: str = "AUTHENTICATION_FAILED",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=401, details=details)


class ValidationAppError(AppError):
    def __init__(
        self,
        message: str = "Validation failed",
        code: str = "VALIDATION_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=400, details=details)
