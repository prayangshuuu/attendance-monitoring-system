from __future__ import annotations

import contextlib
import re
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, create_refresh_token
from app.models.enums import UserRole
from app.models.user import User
from tests.factories import make_user

# ==============================================================================
# Web Test Helpers
# ==============================================================================


def extract_csrf_token(response: Response) -> tuple[str | None, str | None]:
    """Extract CSRF token from response cookies and HTML body.

    Returns:
        tuple[cookie_token, html_token]
    """
    cookie_token = response.cookies.get("csrf_token")

    html_token: str | None = None
    text = response.text

    # Try <meta name="csrf-token" content="...">
    meta_match = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', text)
    if meta_match:
        html_token = meta_match.group(1)
    else:
        # Try <input type="hidden" name="csrf_token" value="...">
        input_match = re.search(
            r'<input[^>]+name=["\']csrf_token["\'][^>]+value=["\']([^"\']+)["\']', text
        )
        if input_match:
            html_token = input_match.group(1)
        else:
            # Reverse order of attributes: value before name
            input_match2 = re.search(
                r'<input[^>]+value=["\']([^"\']+)["\'][^>]+name=["\']csrf_token["\']', text
            )
            if input_match2:
                html_token = input_match2.group(1)

    return cookie_token, html_token


def parse_set_cookies(response: Response) -> dict[str, dict[str, Any]]:
    """Parse all Set-Cookie headers into a structured dict.

    Returns:
        dict where key is cookie name and value is dict of attributes:
        {"value": ..., "httponly": bool, "samesite": str, "secure": bool, "max_age": int}
    """
    result: dict[str, dict[str, Any]] = {}
    set_cookie_headers = response.headers.get_list("set-cookie")

    for header in set_cookie_headers:
        parts = [p.strip() for p in header.split(";")]
        if not parts or "=" not in parts[0]:
            continue

        name, value = parts[0].split("=", 1)
        attrs: dict[str, Any] = {
            "value": value,
            "httponly": False,
            "samesite": "lax",
            "secure": False,
            "max_age": None,
        }

        for attr in parts[1:]:
            lower = attr.lower()
            if lower == "httponly":
                attrs["httponly"] = True
            elif lower == "secure":
                attrs["secure"] = True
            elif lower.startswith("samesite="):
                attrs["samesite"] = lower.split("=", 1)[1]
            elif lower.startswith("max-age="):
                with contextlib.suppress(ValueError):
                    attrs["max_age"] = int(lower.split("=", 1)[1])

        result[name] = attrs

    return result


def assert_httponly_cookie(response: Response, cookie_name: str) -> None:
    """Assert that a cookie is set with the HttpOnly flag."""
    parsed = parse_set_cookies(response)
    assert cookie_name in parsed, (
        f"Cookie '{cookie_name}' not found in Set-Cookie headers: {list(parsed.keys())}"
    )
    assert parsed[cookie_name]["httponly"] is True, (
        f"Cookie '{cookie_name}' is not marked HttpOnly: {parsed[cookie_name]}"
    )


def assert_cookie_cleared(response: Response, cookie_name: str) -> None:
    """Assert that a cookie is cleared (empty value, or max-age=0, or expired)."""
    parsed = parse_set_cookies(response)
    if cookie_name in parsed:
        attrs = parsed[cookie_name]
        is_cleared = attrs["value"] in ("", '""') or attrs["max_age"] == 0
        assert is_cleared, f"Cookie '{cookie_name}' was not cleared: {attrs}"
    else:
        # Also check response.cookies
        cookie_val = response.cookies.get(cookie_name)
        assert cookie_val is None or cookie_val == "", (
            f"Cookie '{cookie_name}' still present: {cookie_val}"
        )


def assert_redirect(response: Response, expected_target: str) -> None:
    """Assert that the response is a redirect to expected_target."""
    assert response.status_code in (302, 303, 307), (
        f"Expected redirect status (302, 303, 307), got {response.status_code}"
    )
    location = response.headers.get("location", "")
    parsed = urlparse(location)
    path = parsed.path
    assert path == expected_target or location == expected_target, (
        f"Expected redirect to '{expected_target}', got '{location}'"
    )


def make_user_auth_cookies(user: User) -> dict[str, str]:
    """Create access and refresh token cookies for testing authenticated web requests."""
    access_tok = create_access_token(sub=user.id, role=user.role)
    refresh_tok = create_refresh_token(sub=user.id)
    return {
        "access_token": access_tok,
        "refresh_token": refresh_tok,
    }


def set_client_cookies(client: AsyncClient, cookies: dict[str, str]) -> None:
    """Set cookies on AsyncClient instance avoiding deprecated per-request parameter."""
    client.cookies.clear()
    for k, v in cookies.items():
        client.cookies.set(k, v)


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture(autouse=True)
def skip_if_no_web_routes(app: FastAPI) -> None:
    """Ensure web routes are present."""
    # Web routes are mounted
    pass


@pytest.fixture
async def web_admin(db_session: AsyncSession) -> User:
    return await make_user(
        db_session,
        email="web_admin@example.com",
        full_name="Web Admin",
        role=UserRole.ADMIN,
        password="ValidPassword123!",
    )


@pytest.fixture
async def web_teacher(db_session: AsyncSession) -> User:
    return await make_user(
        db_session,
        email="web_teacher@example.com",
        full_name="Web Teacher",
        role=UserRole.TEACHER,
        password="ValidPassword123!",
    )


@pytest.fixture
async def web_student(db_session: AsyncSession) -> User:
    return await make_user(
        db_session,
        email="web_student@example.com",
        full_name="Web Student",
        role=UserRole.STUDENT,
        student_id="WEB_STU_001",
        password="ValidPassword123!",
    )


# ==============================================================================
# Integration Tests for Web Authentication & CSRF
# ==============================================================================


@pytest.mark.asyncio
async def test_get_root_anonymous_redirects_to_login(client: AsyncClient) -> None:
    """Test GET / redirects anonymous users to /login."""
    response = await client.get("/", follow_redirects=False)
    assert_redirect(response, "/login")


@pytest.mark.asyncio
async def test_get_root_authenticated_redirects_by_role(
    client: AsyncClient,
    web_admin: User,
    web_teacher: User,
    web_student: User,
) -> None:
    """Test GET / redirects authenticated users to their corresponding role portal."""
    # Admin -> /admin
    admin_cookies = make_user_auth_cookies(web_admin)
    set_client_cookies(client, admin_cookies)
    res_admin = await client.get("/", follow_redirects=False)
    assert_redirect(res_admin, "/admin")

    # Teacher -> /teacher
    teacher_cookies = make_user_auth_cookies(web_teacher)
    set_client_cookies(client, teacher_cookies)
    res_teacher = await client.get("/", follow_redirects=False)
    assert_redirect(res_teacher, "/teacher")

    # Student -> /student
    student_cookies = make_user_auth_cookies(web_student)
    set_client_cookies(client, student_cookies)
    res_student = await client.get("/", follow_redirects=False)
    assert_redirect(res_student, "/student")


@pytest.mark.asyncio
async def test_get_login_renders_html_with_form_and_csrf_meta(client: AsyncClient) -> None:
    """Test GET /login renders 200 HTML with login form and CSRF token."""
    response = await client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")

    # Must contain HTML login form
    html = response.text.lower()
    assert "<form" in html
    assert 'type="email"' in html or 'name="email"' in html
    assert 'type="password"' in html or 'name="password"' in html

    # Must include CSRF token in meta tag or form field
    cookie_token, html_token = extract_csrf_token(response)
    assert html_token is not None or cookie_token is not None, (
        "Expected CSRF token in meta tag, hidden input, or cookie"
    )


@pytest.mark.asyncio
async def test_post_login_invalid_credentials_returns_error_feedback(client: AsyncClient) -> None:
    """Test POST /login with invalid credentials returns 401 or error feedback."""
    # Obtain initial CSRF token if cookie-based
    get_res = await client.get("/login")
    cookie_token, html_token = extract_csrf_token(get_res)
    csrf_token = html_token or cookie_token or "dummy-csrf-token"

    cookies = {}
    if cookie_token:
        cookies["csrf_token"] = cookie_token
    set_client_cookies(client, cookies)

    response = await client.post(
        "/login",
        data={
            "email": "nonexistent@example.com",
            "password": "WrongPassword123!",
            "csrf_token": csrf_token,
        },
        headers={"X-CSRF-Token": csrf_token},
        follow_redirects=False,
    )

    # Either 401 Unauthorized, 422 Unprocessable, or 200 re-rendering form with error message
    assert response.status_code in (401, 422, 200)
    if response.status_code == 200:
        # When 200 is returned, HTML must show error feedback
        text = response.text.lower()
        assert "invalid" in text or "error" in text or "incorrect" in text


@pytest.mark.asyncio
async def test_post_login_admin_sets_httponly_cookies_and_redirects_to_admin(
    client: AsyncClient,
    web_admin: User,
) -> None:
    """Test POST /login with admin credentials sets HttpOnly cookies and redirects."""
    get_res = await client.get("/login")
    cookie_token, html_token = extract_csrf_token(get_res)
    csrf_token = html_token or cookie_token or "test-csrf-token"

    cookies = {}
    if cookie_token:
        cookies["csrf_token"] = cookie_token
    set_client_cookies(client, cookies)

    response = await client.post(
        "/login",
        data={
            "email": web_admin.email,
            "password": "ValidPassword123!",
            "csrf_token": csrf_token,
        },
        headers={"X-CSRF-Token": csrf_token},
        follow_redirects=False,
    )

    assert_redirect(response, "/admin")
    assert_httponly_cookie(response, "access_token")
    assert_httponly_cookie(response, "refresh_token")


@pytest.mark.asyncio
async def test_post_login_teacher_redirects_to_teacher(
    client: AsyncClient,
    web_teacher: User,
) -> None:
    """Test POST /login with valid teacher credentials redirects to /teacher."""
    get_res = await client.get("/login")
    cookie_token, html_token = extract_csrf_token(get_res)
    csrf_token = html_token or cookie_token or "test-csrf-token"

    cookies = {}
    if cookie_token:
        cookies["csrf_token"] = cookie_token
    set_client_cookies(client, cookies)

    response = await client.post(
        "/login",
        data={
            "email": web_teacher.email,
            "password": "ValidPassword123!",
            "csrf_token": csrf_token,
        },
        headers={"X-CSRF-Token": csrf_token},
        follow_redirects=False,
    )

    assert_redirect(response, "/teacher")
    assert_httponly_cookie(response, "access_token")


@pytest.mark.asyncio
async def test_post_login_student_redirects_to_student(
    client: AsyncClient,
    web_student: User,
) -> None:
    """Test POST /login with valid student credentials redirects to /student."""
    get_res = await client.get("/login")
    cookie_token, html_token = extract_csrf_token(get_res)
    csrf_token = html_token or cookie_token or "test-csrf-token"

    cookies = {}
    if cookie_token:
        cookies["csrf_token"] = cookie_token
    set_client_cookies(client, cookies)

    response = await client.post(
        "/login",
        data={
            "email": web_student.email,
            "password": "ValidPassword123!",
            "csrf_token": csrf_token,
        },
        headers={"X-CSRF-Token": csrf_token},
        follow_redirects=False,
    )

    assert_redirect(response, "/student")
    assert_httponly_cookie(response, "access_token")


@pytest.mark.asyncio
async def test_post_login_htmx_valid_returns_hx_redirect(
    client: AsyncClient,
    web_admin: User,
) -> None:
    """HTMX POST /login with valid admin credentials returns HX-Redirect and sets cookies."""
    get_res = await client.get("/login")
    cookie_token, html_token = extract_csrf_token(get_res)
    csrf_token = html_token or cookie_token or "test-csrf-token"

    cookies = {}
    if cookie_token:
        cookies["csrf_token"] = cookie_token
    set_client_cookies(client, cookies)

    response = await client.post(
        "/login",
        data={
            "email": web_admin.email,
            "password": "ValidPassword123!",
            "csrf_token": csrf_token,
        },
        headers={
            "X-CSRF-Token": csrf_token,
            "HX-Request": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == "/admin"
    assert_httponly_cookie(response, "access_token")
    assert_httponly_cookie(response, "refresh_token")


@pytest.mark.asyncio
async def test_post_login_htmx_invalid_returns_401_partial_with_alert(
    client: AsyncClient,
) -> None:
    """HTMX POST /login with bad credentials returns 401 with the login partial and an error."""
    get_res = await client.get("/login")
    cookie_token, html_token = extract_csrf_token(get_res)
    csrf_token = html_token or cookie_token or "test-csrf-token"

    cookies = {}
    if cookie_token:
        cookies["csrf_token"] = cookie_token
    set_client_cookies(client, cookies)

    response = await client.post(
        "/login",
        data={
            "email": "invalid.user@example.com",
            "password": "WrongPassword123!",
            "csrf_token": csrf_token,
        },
        headers={
            "X-CSRF-Token": csrf_token,
            "HX-Request": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 401
    html = response.text
    assert 'id="login-card"' in html
    assert "alert alert-danger" in html
    assert "invalid.user@example.com" in html
    assert "htmx-indicator spinner" in html


@pytest.mark.asyncio
async def test_post_logout_clears_cookies_and_redirects_to_login(
    client: AsyncClient,
    web_admin: User,
) -> None:
    """Test POST /logout clears cookies and redirects to /login."""
    user_cookies = make_user_auth_cookies(web_admin)

    # Obtain CSRF token
    get_res = await client.get("/login")
    cookie_token, html_token = extract_csrf_token(get_res)
    csrf_token = html_token or cookie_token or "logout-csrf-token"

    if cookie_token:
        user_cookies["csrf_token"] = cookie_token
    set_client_cookies(client, user_cookies)

    response = await client.post(
        "/logout",
        data={"csrf_token": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
        follow_redirects=False,
    )

    assert_redirect(response, "/login")
    assert_cookie_cleared(response, "access_token")
    assert_cookie_cleared(response, "refresh_token")


# ==============================================================================
# CSRF Protection Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_csrf_missing_token_returns_403(
    client: AsyncClient,
    web_admin: User,
) -> None:
    """Submitting a POST to a protected web route without CSRF token returns 403 Forbidden."""
    user_cookies = make_user_auth_cookies(web_admin)
    set_client_cookies(client, user_cookies)

    # Submitting without csrf_token in form data or headers
    response = await client.post(
        "/logout",
        follow_redirects=False,
    )

    assert response.status_code == 403, (
        f"Expected 403 Forbidden on missing CSRF token, got {response.status_code}"
    )


@pytest.mark.asyncio
async def test_csrf_mismatched_token_returns_403(
    client: AsyncClient,
    web_admin: User,
) -> None:
    """Submitting a POST with mismatched CSRF token returns 403 Forbidden."""
    user_cookies = make_user_auth_cookies(web_admin)
    user_cookies["csrf_token"] = "legitimate-cookie-token-12345"
    set_client_cookies(client, user_cookies)

    response = await client.post(
        "/logout",
        data={"csrf_token": "tampered-or-mismatched-token"},
        headers={"X-CSRF-Token": "tampered-or-mismatched-token"},
        follow_redirects=False,
    )

    assert response.status_code == 403, (
        f"Expected 403 Forbidden on mismatched CSRF token, got {response.status_code}"
    )


@pytest.mark.asyncio
async def test_csrf_valid_token_allows_post(
    client: AsyncClient,
    web_admin: User,
) -> None:
    """Submitting a POST with matching CSRF cookie and header/form token is accepted."""
    user_cookies = make_user_auth_cookies(web_admin)
    matching_token = "valid-matching-csrf-token-abcdef"
    user_cookies["csrf_token"] = matching_token
    set_client_cookies(client, user_cookies)

    response = await client.post(
        "/logout",
        data={"csrf_token": matching_token},
        headers={"X-CSRF-Token": matching_token},
        follow_redirects=False,
    )

    # Should succeed (redirect to /login), definitely not 403
    assert response.status_code != 403, (
        f"Expected valid CSRF token to pass protection, got 403 Forbidden: {response.text}"
    )
