import hmac
import logging
import secrets
from collections.abc import Mapping
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import DbSession
from app.core.config import settings
from app.core.exceptions import AuthenticationError, ValidationAppError
from app.core.logging import user_id_ctx_var
from app.core.security import decode_token
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.user_repository import user_repository
from app.services.auth_service import auth_service

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def build_form_model[ModelT: BaseModel](model: type[ModelT], **data: Any) -> ModelT:
    """Instantiate a Pydantic schema from form data, mapping validation failures to AppError.

    Web handlers catch AppError to re-render forms with a message; a raw pydantic
    ValidationError would otherwise escape as a 500.
    """
    try:
        return model(**data)
    except ValidationError as exc:
        messages: list[str] = []
        for err in exc.errors():
            msg = str(err.get("msg", "Invalid value"))
            msg = msg.removeprefix("Value error, ")
            loc = ".".join(str(part) for part in err.get("loc", ()))
            messages.append(f"{loc.replace('_', ' ').capitalize()}: {msg}" if loc else msg)
        raise ValidationAppError("; ".join(messages) or "Invalid form data") from exc


class WebRedirectException(StarletteHTTPException):
    """Exception raised when an endpoint or dependency requires an HTTP redirect."""

    def __init__(
        self,
        url: str = "/login",
        status_code: int = status.HTTP_302_FOUND,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail="Redirecting",
            headers={"Location": url},
        )
        self.url = url


def generate_csrf_token() -> str:
    """Generate a cryptographically secure random CSRF token."""
    return secrets.token_urlsafe(32)


def get_or_create_csrf_token(request: Request) -> tuple[str, bool]:
    """Retrieve an existing CSRF token from cookies/state or create a new one.

    Returns:
        (csrf_token, is_new): is_new is True if a new token was generated
        and needs cookie persistence.
    """
    cookie_token = request.cookies.get("csrf_token")
    if cookie_token:
        return cookie_token, False

    if hasattr(request.state, "csrf_token") and request.state.csrf_token:
        return str(request.state.csrf_token), False

    new_token = generate_csrf_token()
    request.state.csrf_token = new_token
    return new_token, True


def queue_cookie_to_set(request: Request, cookie_kwargs: dict[str, Any]) -> None:
    """Record a cookie to be attached to the final outgoing HTTP response."""
    if not hasattr(request.state, "cookies_to_set"):
        request.state.cookies_to_set = []
    request.state.cookies_to_set.append(cookie_kwargs)


def attach_staged_cookies(request: Request, response: Response) -> Response:
    """Attach any staged cookies (e.g. from transparent refresh or CSRF) onto the response."""
    if hasattr(request.state, "cookies_to_set"):
        for cookie_kwargs in request.state.cookies_to_set:
            response.set_cookie(**cookie_kwargs)
    return response


async def verify_csrf(request: Request) -> None:
    """Verify CSRF token for non-GET/HEAD/OPTIONS web requests via Double-Submit Cookie pattern.

    Verifies that the CSRF token submitted in either 'X-CSRF-Token' header or
    form field 'csrf_token' matches the 'csrf_token' cookie.
    Raises 403 Forbidden if missing or invalid.
    """
    if request.method in SAFE_METHODS:
        return

    cookie_token = request.cookies.get("csrf_token")
    if not cookie_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token cookie missing",
        )

    # 1. Check X-CSRF-Token header
    submitted_token = request.headers.get("X-CSRF-Token") or request.headers.get("x-csrf-token")

    # 2. Check form field if not provided in header
    if not submitted_token:
        content_type = request.headers.get("content-type", "")
        if (
            "multipart/form-data" in content_type
            or "application/x-www-form-urlencoded" in content_type
        ):
            try:
                form = await request.form()
                field_val = form.get("csrf_token")
                if isinstance(field_val, str):
                    submitted_token = field_val
            except Exception as exc:
                logger.debug("Failed to read form csrf_token: %s", exc)

    if not submitted_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing from request",
        )

    if not hmac.compare_digest(submitted_token, cookie_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch",
        )


def _detach_user(db: DbSession, user: User) -> None:
    """Detach the authenticated user from the DB session.

    Services call ``session.rollback()`` on validation failures, which expires every
    instance in the session. Error re-renders then touch ``user.role`` in base.html,
    triggering an implicit async lazy-load (MissingGreenlet -> 500). A detached,
    fully-loaded user keeps its column values across rollbacks. Relationships on
    User are ``lazy="raise"``, so nothing relies on lazy loading through it.
    """
    if user in db:
        db.expunge(user)


async def get_optional_web_user(
    request: Request,
    response: Response,
    db: DbSession,
) -> User | None:
    """Authenticate web user from cookies with transparent access token refresh.

    Returns the authenticated User or None if unauthenticated.
    """
    # 1. Try existing access token
    access_token = request.cookies.get("access_token")
    if access_token:
        try:
            payload = decode_token(access_token, expected_type="access")
            user_id = payload.get("sub")
            if user_id:
                user = await user_repository.get_by_id(db, user_id)
                if user and user.is_active:
                    _detach_user(db, user)
                    user_id_ctx_var.set(str(user.id))
                    request.state.user = user
                    return user
        except AuthenticationError:
            # Token expired or invalid - fall through to refresh_token
            pass
        except Exception as exc:
            logger.debug("Failed to resolve web user from access token: %s", exc)

    # 2. Attempt transparent refresh using refresh_token cookie
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        try:
            tokens = await auth_service.refresh(session=db, refresh_token=refresh_token)
            payload = decode_token(tokens.access_token, expected_type="access")
            user_id = payload.get("sub")
            if user_id:
                user = await user_repository.get_by_id(db, user_id)
                if user and user.is_active:
                    _detach_user(db, user)
                    user_id_ctx_var.set(str(user.id))
                    request.state.user = user

                    # Transparently set new access_token cookie
                    secure = settings.ENVIRONMENT == "production"
                    acc_params: dict[str, Any] = {
                        "key": "access_token",
                        "value": tokens.access_token,
                        "httponly": True,
                        "samesite": "lax",
                        "secure": secure,
                        "max_age": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                        "path": "/",
                    }
                    response.set_cookie(**acc_params)
                    queue_cookie_to_set(request, acc_params)

                    if tokens.refresh_token:
                        ref_params: dict[str, Any] = {
                            "key": "refresh_token",
                            "value": tokens.refresh_token,
                            "httponly": True,
                            "samesite": "lax",
                            "secure": secure,
                            "max_age": settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
                            "path": "/",
                        }
                        response.set_cookie(**ref_params)
                        queue_cookie_to_set(request, ref_params)

                    return user
        except AuthenticationError:
            pass
        except Exception as exc:
            logger.debug("Failed to refresh web user tokens: %s", exc)

    return None


async def get_current_web_user(
    request: Request,
    response: Response,
    db: DbSession,
) -> User:
    """Dependency that returns the current authenticated User.

    Redirects unauthenticated browser requests to /login (or HX-Redirect: /login for HTMX).
    """
    user = await get_optional_web_user(request=request, response=response, db=db)
    if not user:
        if request.headers.get("HX-Request") == "true":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"HX-Redirect": "/login"},
                detail="Not authenticated",
            )
        raise WebRedirectException(url="/login", status_code=status.HTTP_302_FOUND)
    return user


async def require_web_admin(
    user: Annotated[User, Depends(get_current_web_user)],
) -> User:
    """Dependency that requires the current user to have the 'admin' role."""
    if user.role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted for your role",
        )
    return user


async def require_web_teacher(
    user: Annotated[User, Depends(get_current_web_user)],
) -> User:
    """Dependency that requires the current user to have the 'teacher' role."""
    if user.role != UserRole.TEACHER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted for your role",
        )
    return user


async def require_web_student(
    user: Annotated[User, Depends(get_current_web_user)],
) -> User:
    """Dependency that requires the current user to have the 'student' role."""
    if user.role != UserRole.STUDENT.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted for your role",
        )
    return user


def pagination_query(**filters: object) -> str:
    """URL-encode active list filters (no leading '?', no 'page') for pagination links.

    Empty/None values are dropped so links stay clean, e.g. ``q=ann&role=student``.
    Templates append ``page=N`` to it so filters survive page changes.
    """
    return urlencode({k: str(v) for k, v in filters.items() if v not in (None, "")})


def htmx_download_redirect(request: Request) -> Response | None:
    """For file downloads reached via htmx (e.g. a boosted CSV link), force a real navigation.

    htmx would otherwise swap the CSV body into the page. Returning HX-Redirect to the same
    URL makes the browser perform a normal GET, which honours Content-Disposition.
    Returns None for normal browser requests, which should receive the file directly.
    """
    if request.headers.get("HX-Request") != "true":
        return None
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return Response(status_code=status.HTTP_200_OK, headers={"HX-Redirect": target})


def is_partial_request(request: Request) -> bool:
    """True for HTMX partial requests; False for full loads and hx-boost navigations."""
    return (
        request.headers.get("HX-Request") == "true" and request.headers.get("HX-Boosted") != "true"
    )


def render_template(
    request: Request,
    template_name: str,
    context: dict[str, Any] | None = None,
    partial_template: str | None = None,
    status_code: int = status.HTTP_200_OK,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """Render a Jinja2 template with automatic context injection and HTMX partial support.

    - Automatically attaches 'user', 'request', and 'csrf_token' to context.
    - Inspects 'request.headers.get("HX-Request") == "true"': if it's an HTMX partial request
      and partial_template is provided, renders the partial template.
      Otherwise renders template_name.
    - Ensures CSRF cookie is set and any staged cookies are attached.
    """
    ctx: dict[str, Any] = dict(context) if context is not None else {}
    ctx["request"] = request

    if "user" not in ctx:
        ctx["user"] = getattr(request.state, "user", None)

    csrf_token, is_new = get_or_create_csrf_token(request)
    if "csrf_token" not in ctx:
        ctx["csrf_token"] = csrf_token

    # Boosted navigations (hx-boost) also send HX-Request; they need the full page
    # (layout + sidebar), so only true partial requests get the partial template.
    is_htmx = is_partial_request(request)
    chosen_template = partial_template if (is_htmx and partial_template) else template_name

    response = templates.TemplateResponse(
        request=request,
        name=chosen_template,
        context=ctx,
        status_code=status_code,
        headers=headers,
    )

    # Set csrf_token cookie if new or mismatch
    if is_new or request.cookies.get("csrf_token") != csrf_token:
        response.set_cookie(
            key="csrf_token",
            value=csrf_token,
            httponly=False,
            samesite="lax",
            secure=settings.ENVIRONMENT == "production",
            max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
            path="/",
        )

    # Attach any staged cookies (e.g. from transparent token refresh)
    attach_staged_cookies(request, response)

    return response
