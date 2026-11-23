"""Regression tests for web UI fixes: login spinner, HTML error pages, CSP-safe markup."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from tests.factories import make_user
from tests.integration.test_web_admin import auth_client_as

TEMPLATES_DIR = Path("app/templates")
HTML = {"Accept": "text/html,application/xhtml+xml"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_login_spinner_is_an_htmx_indicator(client: AsyncClient) -> None:
    """The sign-in spinner must only show during a request (it spun forever before)."""
    resp = await client.get("/login")
    assert resp.status_code == 200
    indicator = r'class="[^"]*(htmx-indicator[^"]*spinner|spinner[^"]*htmx-indicator)'
    assert re.search(indicator, resp.text)
    assert "includeIndicatorStyles" in resp.text  # htmx's CSP-blocked inline styles disabled
    assert "hx-disabled-elt" in resp.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_browser_404_renders_html_error_page(client: AsyncClient) -> None:
    resp = await client.get("/definitely-not-a-page", headers=HTML)
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_404_stays_json(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/definitely-not-a-route", headers=HTML)
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_wrong_role_gets_html_403_with_layout(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    student = await make_user(db_session, role=UserRole.STUDENT, email="ui_reg_student@example.com")
    await db_session.commit()
    auth_client_as(client, student)

    resp = await client.get("/admin/users", headers=HTML)
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("text/html")


def test_templates_have_no_inline_styles_or_handlers() -> None:
    """Strict CSP (style-src/script-src 'self') blocks inline styles and on*= handlers."""
    offenders: list[str] = []
    pattern = re.compile(r'\sstyle="|\son[a-z]+="')
    for path in TEMPLATES_DIR.rglob("*.html"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}")
    assert not offenders, "CSP-unsafe markup:\n" + "\n".join(offenders)
