from typing import Any

from fastapi import Query
from pydantic import BaseModel, Field


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    size: int


class PaginationParams(BaseModel):
    page: int = Query(1, ge=1, description="Page number")
    size: int = Query(20, ge=1, le=100, description="Items per page")


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail

    model_config = {
        "json_schema_extra": {
            "example": {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Resource not found",
                    "details": {},
                }
            }
        }
    }
