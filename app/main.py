import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import setup_logging
from app.core.middleware import (
    AccessLogMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from app.db.session import engine, get_db
from app.schemas.common import ErrorResponse
from scripts.create_admin import bootstrap_admin

settings = get_settings()
logger = logging.getLogger("app.main")

HTTP_STATUS_CODES: dict[int, str] = {}

STANDARD_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Bad Request"},
    401: {"model": ErrorResponse, "description": "Unauthorized"},
    403: {"model": ErrorResponse, "description": "Forbidden"},
    404: {"model": ErrorResponse, "description": "Not Found"},
    422: {"model": ErrorResponse, "description": "Validation Error"},
    429: {"model": ErrorResponse, "description": "Too Many Requests"},
    500: {"model": ErrorResponse, "description": "Internal Server Error"},
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await bootstrap_admin()
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    setup_logging()
    current_settings = get_settings()
    is_production = current_settings.ENVIRONMENT == "production"

    app = FastAPI(
        title=current_settings.APP_NAME,
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
        responses=STANDARD_RESPONSES,
    )

    # Middlewares order: CORS -> RequestID -> AccessLog -> SecurityHeaders
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=current_settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Validation error",
                    "details": {"errors": jsonable_encoder(exc.errors())},
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": HTTP_STATUS_CODES.get(exc.status_code, "HTTP_ERROR"),
                    "message": str(exc.detail),
                    "details": {},
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("An unexpected error occurred: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected error occurred",
                    "details": {},
                }
            },
        )

    @app.get("/health")
    async def health_check(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, str]:
        db_status = "ok"
        try:
            await db.execute(text("SELECT 1"))
        except Exception:
            db_status = "error"
        return {"status": "ok", "database": db_status}

    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()
