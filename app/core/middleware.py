from __future__ import annotations

import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.logging import request_id_ctx_var, user_id_ctx_var

access_logger = logging.getLogger("app.access")


class RequestIDMiddleware:
    """Reads incoming X-Request-ID or generates a UUID, storing it in ContextVar."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = ""
        for name, value in scope.get("headers", []):
            if name.lower() == b"x-request-id":
                request_id = value.decode("latin-1").strip()
                break

        if not request_id:
            request_id = uuid.uuid4().hex

        req_id_token = request_id_ctx_var.set(request_id)
        user_id_token = user_id_ctx_var.set("")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Avoid duplicate headers
                headers = [h for h in headers if h[0].lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_ctx_var.reset(req_id_token)
            user_id_ctx_var.reset(user_id_token)


class AccessLogMiddleware:
    """Logs one structured access log entry per HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            method = scope.get("method", "")
            path = scope.get("path", "")
            req_id = request_id_ctx_var.get()
            uid = user_id_ctx_var.get()

            access_logger.info(
                "%s %s %s %.2fms",
                method,
                path,
                status_code,
                duration_ms,
                extra={
                    "method": method,
                    "path": path,
                    "status": status_code,
                    "duration_ms": duration_ms,
                    "request_id": req_id,
                    "user_id": uid,
                },
            )


class SecurityHeadersMiddleware:
    """Applies security headers (nosniff, DENY, referrer-policy, HSTS) to responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.settings = get_settings()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing_names = {
                    b"x-content-type-options",
                    b"x-frame-options",
                    b"referrer-policy",
                    b"strict-transport-security",
                }
                headers = [h for h in headers if h[0].lower() not in existing_names]

                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))

                if self.settings.ENVIRONMENT == "production":
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )

                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
