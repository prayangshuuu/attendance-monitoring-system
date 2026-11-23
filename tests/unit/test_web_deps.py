from datetime import timedelta
from typing import Any

import pytest
from fastapi import HTTPException, Request, Response, status

from app.core.security import create_access_token, create_refresh_token
from app.models.enums import UserRole
from app.web.deps import (
    WebRedirectException,
    attach_staged_cookies,
    generate_csrf_token,
    get_current_web_user,
    get_optional_web_user,
    get_or_create_csrf_token,
    queue_cookie_to_set,
    render_template,
    require_web_admin,
    require_web_student,
    require_web_teacher,
    verify_csrf,
)
from tests.factories import make_user


def build_request(
    method: str = "GET",
    path: str = "/",
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> Request:
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))

    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": raw_headers,
    }
    req = Request(scope)
    if cookies:
        req._cookies = cookies
    return req


@pytest.mark.unit
def test_generate_and_get_or_create_csrf_token() -> None:
    token = generate_csrf_token()
    assert isinstance(token, str)
    assert len(token) >= 32

    # Request without cookie or state creates a new token
    req1 = build_request()
    token1, is_new1 = get_or_create_csrf_token(req1)
    assert is_new1 is True
    assert len(token1) >= 32
    assert req1.state.csrf_token == token1

    # Second call returns existing state token without marking it new
    token1_again, is_new1_again = get_or_create_csrf_token(req1)
    assert is_new1_again is False
    assert token1_again == token1

    # Request with existing cookie returns existing cookie token
    req2 = build_request(cookies={"csrf_token": "existing_csrf_cookie"})
    token2, is_new2 = get_or_create_csrf_token(req2)
    assert is_new2 is False
    assert token2 == "existing_csrf_cookie"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_csrf_safe_methods() -> None:
    for method in ("GET", "HEAD", "OPTIONS"):
        req = build_request(method=method)
        # Should not raise any exception even with no cookies or headers
        await verify_csrf(req)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_csrf_missing_cookie() -> None:
    req = build_request(method="POST")
    with pytest.raises(HTTPException) as exc_info:
        await verify_csrf(req)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "cookie missing" in exc_info.value.detail.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_csrf_missing_submission() -> None:
    req = build_request(method="POST", cookies={"csrf_token": "valid_token"})
    with pytest.raises(HTTPException) as exc_info:
        await verify_csrf(req)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "missing from request" in exc_info.value.detail.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_csrf_mismatch() -> None:
    req = build_request(
        method="POST",
        headers={"X-CSRF-Token": "wrong_token"},
        cookies={"csrf_token": "valid_token"},
    )
    with pytest.raises(HTTPException) as exc_info:
        await verify_csrf(req)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "mismatch" in exc_info.value.detail.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_csrf_valid_header() -> None:
    token = "secure_token_12345"
    req = build_request(
        method="POST",
        headers={"X-CSRF-Token": token},
        cookies={"csrf_token": token},
    )
    await verify_csrf(req)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_csrf_valid_form() -> None:
    token = "form_csrf_token_xyz"
    req = build_request(
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
        cookies={"csrf_token": token},
    )
    req._form = {"csrf_token": token}  # simulate Starlette parsed form
    await verify_csrf(req)


@pytest.mark.unit
def test_render_template_context_and_htmx() -> None:
    req = build_request(headers={"HX-Request": "false"})
    resp = render_template(
        request=req,
        template_name="auth/login.html",
        context={"custom_var": "hello"},
        partial_template="auth/partials/login_form.html",
    )
    assert resp.status_code == 200
    assert "csrf_token" in resp.headers.get("set-cookie", "")

    # HTMX request renders partial
    req_htmx = build_request(headers={"HX-Request": "true"})
    resp_htmx = render_template(
        request=req_htmx,
        template_name="auth/login.html",
        partial_template="auth/partials/login_form.html",
    )
    assert resp_htmx.status_code == 200


@pytest.mark.unit
def test_queue_and_attach_staged_cookies() -> None:
    req = build_request()
    queue_cookie_to_set(req, {"key": "test_cookie", "value": "test_value"})
    assert len(req.state.cookies_to_set) == 1

    resp = Response("OK")
    attach_staged_cookies(req, resp)
    assert "test_cookie=test_value" in resp.headers.get("set-cookie", "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_optional_web_user_valid_access_token(db_session: Any) -> None:
    user = await make_user(db_session, role=UserRole.ADMIN)
    access_token = create_access_token(sub=user.id, role=user.role)

    req = build_request(cookies={"access_token": access_token})
    resp = Response()
    found_user = await get_optional_web_user(request=req, response=resp, db=db_session)
    assert found_user is not None
    assert found_user.id == user.id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_optional_web_user_transparent_refresh(db_session: Any) -> None:
    user = await make_user(db_session, role=UserRole.TEACHER)
    # Expired access token (negative delta)
    expired_access_token = create_access_token(
        sub=user.id, role=user.role, expires_delta=timedelta(minutes=-5)
    )
    valid_refresh_token = create_refresh_token(sub=user.id)

    req = build_request(
        cookies={
            "access_token": expired_access_token,
            "refresh_token": valid_refresh_token,
        }
    )
    resp = Response()
    found_user = await get_optional_web_user(request=req, response=resp, db=db_session)
    assert found_user is not None
    assert found_user.id == user.id

    # Verify new access_token was set on response and staged on request
    assert "access_token" in resp.headers.get("set-cookie", "")
    assert hasattr(req.state, "cookies_to_set")
    assert any(c["key"] == "access_token" for c in req.state.cookies_to_set)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_current_web_user_unauthenticated(db_session: Any) -> None:
    # Standard browser request raises WebRedirectException (status 302)
    req = build_request()
    resp = Response()
    with pytest.raises(WebRedirectException) as exc_info:
        await get_current_web_user(request=req, response=resp, db=db_session)
    assert exc_info.value.status_code == status.HTTP_302_FOUND
    assert exc_info.value.headers.get("Location") == "/login"

    # HTMX request raises HTTPException with HX-Redirect header
    req_htmx = build_request(headers={"HX-Request": "true"})
    with pytest.raises(HTTPException) as exc_htmx_info:
        await get_current_web_user(request=req_htmx, response=resp, db=db_session)
    assert exc_htmx_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_htmx_info.value.headers.get("HX-Redirect") == "/login"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_role_guards(db_session: Any) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)

    # Admin guard
    assert (await require_web_admin(admin)).id == admin.id
    with pytest.raises(HTTPException) as exc1:
        await require_web_admin(teacher)
    assert exc1.value.status_code == status.HTTP_403_FORBIDDEN

    # Teacher guard
    assert (await require_web_teacher(teacher)).id == teacher.id
    with pytest.raises(HTTPException) as exc2:
        await require_web_teacher(student)
    assert exc2.value.status_code == status.HTTP_403_FORBIDDEN

    # Student guard
    assert (await require_web_student(student)).id == student.id
    with pytest.raises(HTTPException) as exc3:
        await require_web_student(admin)
    assert exc3.value.status_code == status.HTTP_403_FORBIDDEN
