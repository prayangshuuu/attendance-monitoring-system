"""hx-boost vs. true HTMX partial requests.

hx-boost navigations send both ``HX-Request: true`` and ``HX-Boosted: true`` and swap the
whole <body>, so they must receive the full page (layout + sidebar). Only genuine HTMX
partial requests (``HX-Request`` without ``HX-Boosted``) get the partial template.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from tests.factories import make_course, make_user
from tests.integration.test_web_admin import CSRF_TOKEN, auth_client_as

SIDEBAR_MARKER = 'id="sidebar-drawer"'
BOOSTED_HEADERS = {"HX-Request": "true", "HX-Boosted": "true"}
PARTIAL_HEADERS = {"HX-Request": "true"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_boosted_get_admin_users_returns_full_page_partial_returns_table(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN, email="boost_admin@example.com")
    await db_session.commit()
    auth_client_as(client, admin)

    full = await client.get("/admin/users")
    assert full.status_code == 200
    assert SIDEBAR_MARKER in full.text

    boosted = await client.get("/admin/users", headers=BOOSTED_HEADERS)
    assert boosted.status_code == 200
    assert SIDEBAR_MARKER in boosted.text
    assert "<html" in boosted.text.lower()

    partial = await client.get("/admin/users", headers=PARTIAL_HEADERS)
    assert partial.status_code == 200
    assert SIDEBAR_MARKER not in partial.text
    assert "<html" not in partial.text.lower()
    assert "boost_admin@example.com" in partial.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_boosted_get_renders_sidebar_for_every_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await db_session.commit()

    pages = {
        admin: ["/admin", "/admin/users", "/admin/courses", "/admin/sessions", "/admin/profile"],
        teacher: ["/teacher", "/teacher/courses", "/teacher/sessions", "/teacher/profile"],
        student: ["/student", "/student/check-in", "/student/attendance", "/student/profile"],
    }
    for user, paths in pages.items():
        auth_client_as(client, user)
        for path in paths:
            resp = await client.get(path, headers=BOOSTED_HEADERS)
            assert resp.status_code == 200, path
            assert SIDEBAR_MARKER in resp.text, path


@pytest.mark.integration
@pytest.mark.asyncio
async def test_boosted_form_post_redirects_while_partial_post_uses_hx_redirect(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    await db_session.commit()
    auth_client_as(client, admin)

    base = {"csrf_token": CSRF_TOKEN, "title": "Boosted Course", "teacher_id": str(teacher.id)}

    boosted = await client.post(
        "/admin/courses",
        data={**base, "code": "BST101"},
        headers=BOOSTED_HEADERS,
        follow_redirects=False,
    )
    assert boosted.status_code == 303
    assert boosted.headers["location"] == "/admin/courses"

    partial = await client.post(
        "/admin/courses", data={**base, "code": "BST102"}, headers=PARTIAL_HEADERS
    )
    assert partial.status_code == 200
    assert partial.headers["HX-Redirect"] == "/admin/courses"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_boosted_form_post_validation_error_renders_full_page(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Schema validation failures must re-render the page, not 500."""
    admin = await make_user(db_session, role=UserRole.ADMIN)
    await db_session.commit()
    auth_client_as(client, admin)

    resp = await client.post(
        "/admin/users",
        data={
            "csrf_token": CSRF_TOKEN,
            "email": "not-an-email",
            "full_name": "Bad Email",
            "role": "student",
            "password": "x",
            "student_id": "BAD001",
        },
        headers=BOOSTED_HEADERS,
    )
    assert resp.status_code == 422
    assert SIDEBAR_MARKER in resp.text
    assert "valid email" in resp.text

    partial = await client.post(
        "/admin/users",
        data={
            "csrf_token": CSRF_TOKEN,
            "email": "weak@example.com",
            "full_name": "Weak",
            "role": "teacher",
            "password": "short",
        },
        headers=PARTIAL_HEADERS,
    )
    assert partial.status_code == 422
    assert "HX-Trigger" in partial.headers


@pytest.mark.integration
@pytest.mark.asyncio
async def test_boosted_csv_link_forces_real_navigation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, code="CSV101")
    await db_session.commit()
    auth_client_as(client, teacher)

    url = f"/teacher/reports/{course.id}/csv"
    boosted = await client.get(url, headers=BOOSTED_HEADERS)
    assert boosted.status_code == 200
    assert boosted.headers["HX-Redirect"] == url

    plain = await client.get(url)
    assert plain.status_code == 200
    assert plain.headers["content-type"].startswith("text/csv")
