from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.deps import DbSession
from app.core.config import settings
from app.core.exceptions import AppError, AuthenticationError
from app.core.security import decode_token
from app.models.enums import UserRole
from app.web.deps import (
    attach_staged_cookies,
    get_optional_web_user,
    get_or_create_csrf_token,
    is_partial_request,
    render_template,
    verify_csrf,
)

router = APIRouter(tags=["web-auth"])


def get_portal_redirect_url(role: str | UserRole) -> str:
    """Return the designated portal URL based on user role."""
    role_str = role.value if isinstance(role, UserRole) else str(role)
    if role_str == UserRole.ADMIN.value:
        return "/admin"
    if role_str == UserRole.TEACHER.value:
        return "/teacher"
    if role_str == UserRole.STUDENT.value:
        return "/student"
    return "/login"


@router.get("/", response_class=RedirectResponse)
async def index(
    request: Request,
    response: Response,
    db: DbSession,
) -> RedirectResponse:
    """Root endpoint: checks authentication and redirects to the role portal or /login."""
    user = await get_optional_web_user(request=request, response=response, db=db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    portal_url = get_portal_redirect_url(user.role)
    redirect_resp = RedirectResponse(url=portal_url, status_code=status.HTTP_302_FOUND)
    attach_staged_cookies(request, redirect_resp)
    return redirect_resp


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    response: Response,
    db: DbSession,
) -> Response:
    """Render the login page or redirect to role portal if already authenticated."""
    user = await get_optional_web_user(request=request, response=response, db=db)
    if user:
        portal_url = get_portal_redirect_url(user.role)
        redirect_resp = RedirectResponse(url=portal_url, status_code=status.HTTP_302_FOUND)
        attach_staged_cookies(request, redirect_resp)
        return redirect_resp

    return render_template(
        request=request,
        template_name="auth/login.html",
        context={"title": "Sign In"},
        partial_template="auth/partials/login_form.html",
    )


@router.post("/login")
async def login_post(
    request: Request,
    db: DbSession,
    csrf_dep: None = Depends(verify_csrf),
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> Response:
    """Handle user login submission, setting cookies on success or rendering errors on failure."""
    from app.services.auth_service import auth_service

    email_clean = email.strip()
    if not email_clean or not password:
        return render_template(
            request=request,
            template_name="auth/login.html",
            context={
                "error": "Email and password are required.",
                "email": email_clean,
                "title": "Sign In",
            },
            partial_template="auth/partials/login_form.html",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    try:
        token_response = await auth_service.authenticate_user(
            session=db,
            email=email_clean,
            password=password,
        )
    except AuthenticationError as exc:
        return render_template(
            request=request,
            template_name="auth/login.html",
            context={
                "error": exc.message,
                "email": email_clean,
                "title": "Sign In",
            },
            partial_template="auth/partials/login_form.html",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    except AppError as exc:
        status_code = (
            exc.status_code if exc.status_code in (401, 422, 429) else status.HTTP_401_UNAUTHORIZED
        )
        return render_template(
            request=request,
            template_name="auth/login.html",
            context={
                "error": exc.message,
                "email": email_clean,
                "title": "Sign In",
            },
            partial_template="auth/partials/login_form.html",
            status_code=status_code,
        )

    # Authentication succeeded: determine role portal
    payload = decode_token(token_response.access_token, expected_type="access")
    user_role = payload.get("role", "")
    portal_url = get_portal_redirect_url(user_role)

    is_htmx = is_partial_request(request)
    login_response: Response
    if is_htmx:
        login_response = Response(
            status_code=status.HTTP_200_OK,
            headers={"HX-Redirect": portal_url},
        )
    else:
        login_response = RedirectResponse(
            url=portal_url,
            status_code=status.HTTP_302_FOUND,
        )

    secure_cookie = settings.ENVIRONMENT == "production"

    login_response.set_cookie(
        key="access_token",
        value=token_response.access_token,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    login_response.set_cookie(
        key="refresh_token",
        value=token_response.refresh_token,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        path="/",
    )

    csrf_token, _ = get_or_create_csrf_token(request)
    login_response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        samesite="lax",
        secure=secure_cookie,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        path="/",
    )

    return login_response


@router.post("/logout")
async def logout_post(
    request: Request,
    csrf_dep: None = Depends(verify_csrf),
) -> Response:
    """Clear authentication cookies and redirect to /login."""
    is_htmx = is_partial_request(request)
    logout_response: Response
    if is_htmx:
        logout_response = Response(
            status_code=status.HTTP_200_OK,
            headers={"HX-Redirect": "/login"},
        )
    else:
        logout_response = RedirectResponse(
            url="/login",
            status_code=status.HTTP_302_FOUND,
        )

    logout_response.delete_cookie(key="access_token", path="/")
    logout_response.delete_cookie(key="refresh_token", path="/")
    logout_response.delete_cookie(key="csrf_token", path="/")
    return logout_response


@router.get("/logout")
async def logout_get(
    request: Request,
) -> Response:
    """Clear authentication cookies and redirect to /login on direct navigation."""
    is_htmx = is_partial_request(request)
    logout_response: Response
    if is_htmx:
        logout_response = Response(
            status_code=status.HTTP_200_OK,
            headers={"HX-Redirect": "/login"},
        )
    else:
        logout_response = RedirectResponse(
            url="/login",
            status_code=status.HTTP_302_FOUND,
        )

    logout_response.delete_cookie(key="access_token", path="/")
    logout_response.delete_cookie(key="refresh_token", path="/")
    logout_response.delete_cookie(key="csrf_token", path="/")
    return logout_response
