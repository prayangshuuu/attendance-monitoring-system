from pathlib import Path

import jinja2
import pytest


@pytest.fixture
def jinja_env() -> jinja2.Environment:
    template_dir = Path(__file__).resolve().parent.parent.parent / "app" / "templates"
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        autoescape=jinja2.select_autoescape(["html", "xml"]),
    )
    return env


def test_static_assets_exist() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    htmx_file = root / "app" / "static" / "js" / "htmx.min.js"
    css_file = root / "app" / "static" / "css" / "app.css"
    app_js_file = root / "app" / "static" / "js" / "app.js"

    assert htmx_file.is_file(), "htmx.min.js must exist"
    assert htmx_file.stat().st_size > 10000, "htmx.min.js should be minified HTMX 2.x"

    assert css_file.is_file(), "app.css must exist"
    css_content = css_file.read_text(encoding="utf-8")
    assert "--color-present: #16a34a" in css_content
    assert "--color-late: #d97706" in css_content
    assert "--color-absent: #dc2626" in css_content
    assert "--color-excused: #2563eb" in css_content
    # Light-only theme: no dark-mode override, native controls pinned light.
    assert "@media (prefers-color-scheme: dark)" not in css_content
    assert "color-scheme: light" in css_content
    # HTMX indicator rules must live in app.css (CSP blocks htmx's injected styles).
    assert ".htmx-indicator" in css_content
    assert ".htmx-request .htmx-indicator" in css_content
    assert "@media (max-width: 639px)" in css_content
    assert "data-label" in css_content

    assert app_js_file.is_file(), "app.js must exist"
    js_content = app_js_file.read_text(encoding="utf-8")
    assert "allowEval = false" in js_content
    assert "htmx:configRequest" in js_content
    assert "X-CSRF-Token" in js_content
    assert "htmx:beforeSwap" in js_content, "4xx form/validation responses must be swapped"
    assert "initDrawer" in js_content
    assert "showToast" in js_content
    assert "countdown:expired" in js_content


def test_base_template_unauthenticated(jinja_env: jinja2.Environment) -> None:
    template = jinja_env.get_template("base.html")
    rendered = template.render(user=None, csrf_token="test-csrf-123")

    assert '<meta name="csrf-token" content="test-csrf-123">' in rendered
    assert '<link rel="stylesheet" href="/static/css/app.css">' in rendered
    assert '<script src="/static/js/htmx.min.js"></script>' in rendered
    assert '<script src="/static/js/app.js" defer></script>' in rendered
    assert 'class="standalone-wrapper"' in rendered
    assert 'id="toast-container"' in rendered


@pytest.mark.parametrize(
    ("role", "expected_links"),
    [
        (
            "admin",
            [
                "/admin",
                "/admin/users",
                "/admin/courses",
                "/admin/enrollments",
                "/admin/sessions",
                "/admin/reports",
                "/admin/profile",
            ],
        ),
        (
            "teacher",
            [
                "/teacher",
                "/teacher/courses",
                "/teacher/sessions",
                "/teacher/live",
                "/teacher/reports",
                "/teacher/profile",
            ],
        ),
        (
            "student",
            [
                "/student",
                "/student/check-in",
                "/student/attendance",
                "/student/courses",
                "/student/profile",
            ],
        ),
    ],
)
def test_base_template_authenticated_roles(
    jinja_env: jinja2.Environment, role: str, expected_links: list[str]
) -> None:
    class MockUser:
        def __init__(self, role: str) -> None:
            self.role = role
            self.full_name = f"Test {role.title()}"
            self.email = f"{role}@example.com"

    template = jinja_env.get_template("base.html")
    rendered = template.render(user=MockUser(role), csrf_token="token-xyz")

    assert 'id="sidebar-drawer"' in rendered
    assert 'id="drawer-backdrop"' in rendered
    assert 'id="mobile-menu-btn"' in rendered
    assert f"Test {role.title()}" in rendered
    assert f'<span class="brand-badge">{role}</span>' in rendered
    assert 'action="/logout"' in rendered

    for link in expected_links:
        assert f'href="{link}"' in rendered


def test_components_macros(jinja_env: jinja2.Environment) -> None:
    tmpl = jinja_env.from_string("""
    {% import "components/components.html" as ui %}
    {{ ui.button(text="Save", variant="primary", icon="check") }}
    {{ ui.field(
        name="email",
        label="Email Address",
        type="email",
        value="test@example.com",
        required=true,
        error="Invalid email"
    ) }}
    {{ ui.field(
        name="role",
        label="Role",
        type="select",
        options=[("admin", "Admin"), ("student", "Student")],
        value="student"
    ) }}
    {{ ui.field(name="active", label="Active User", type="checkbox", value="true") }}
    {{ ui.badge(text="Present", variant="present") }}
    {{ ui.stat_card(
        title="Total Students",
        value="150",
        icon="users",
        change="+12%",
        change_type="up"
    ) }}
    {{ ui.alert(message="Operation successful", variant="success", title="Success") }}
    {{ ui.pagination(current_page=2, total_pages=5, total_items=50, base_url="/users") }}
    {{ ui.empty_state(
        title="No Records Found",
        description="Add a new entry to get started.",
        action_text="Create",
        action_url="/create"
    ) }}
    {{ ui.table(
        headers=["Name", "Email"],
        rows=[["Alice", "alice@example.com"], ["Bob", "bob@example.com"]]
    ) }}
    """)
    rendered = tmpl.render()

    assert "btn btn-primary" in rendered
    assert "lucide-check" in rendered
    assert 'name="email"' in rendered
    assert "Invalid email" in rendered
    assert 'aria-invalid="true"' in rendered
    assert "<select" in rendered
    assert 'value="student"' in rendered
    assert 'type="checkbox"' in rendered
    assert "badge badge-present" in rendered
    assert "Total Students" in rendered
    assert "150" in rendered
    assert "alert alert-success" in rendered
    assert "Page <strong>2</strong> of <strong>5</strong>" in rendered
    assert "No Records Found" in rendered
    assert 'data-label="Name"' in rendered
    assert 'data-label="Email"' in rendered


def test_icons_macro(jinja_env: jinja2.Environment) -> None:
    icons_to_test = [
        "home",
        "users",
        "user",
        "book",
        "calendar",
        "clock",
        "check",
        "check-circle",
        "x",
        "x-circle",
        "alert",
        "alert-triangle",
        "download",
        "key",
        "log-out",
        "menu",
        "info",
        "chevron-left",
        "chevron-right",
    ]
    tmpl = jinja_env.from_string("""
    {% import "components/icons.html" as icons %}
    {% for name in icon_names %}
      <div id="{{ name }}">{{ icons.icon(name, size=24) }}</div>
    {% endfor %}
    """)
    rendered = tmpl.render(icon_names=icons_to_test)

    for name in icons_to_test:
        assert f'<div id="{name}">' in rendered
        assert "<svg" in rendered


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 500])
def test_error_templates(jinja_env: jinja2.Environment, status_code: int) -> None:
    template = jinja_env.get_template(f"errors/{status_code}.html")
    # Test unauthenticated rendering
    rendered_anon = template.render(user=None, csrf_token="anon-csrf")
    assert f'<div class="error-code">{status_code}</div>' in rendered_anon
    assert 'class="standalone-wrapper"' in rendered_anon

    # Test authenticated rendering
    class MockUser:
        role = "admin"
        full_name = "Admin User"
        email = "admin@example.com"

    rendered_auth = template.render(user=MockUser(), csrf_token="auth-csrf")
    assert f'<div class="error-code">{status_code}</div>' in rendered_auth
    assert 'id="sidebar-drawer"' in rendered_auth


def test_sidebar_active_state_checkin(jinja_env: jinja2.Environment) -> None:
    class MockUser:
        role = "student"
        full_name = "Student User"
        email = "student@example.com"

    template = jinja_env.get_template("base.html")

    class MockRequestDash:
        class url:  # noqa: N801 - mimics request.url attribute
            path = "/student/check-in"

    class MockRequestNoDash:
        class url:  # noqa: N801 - mimics request.url attribute
            path = "/student/checkin"

    rendered_dash = template.render(user=MockUser(), csrf_token="csrf", request=MockRequestDash)
    assert 'href="/student/check-in" class="nav-link is-active"' in rendered_dash

    rendered_nodash = template.render(user=MockUser(), csrf_token="csrf", request=MockRequestNoDash)
    assert 'href="/student/check-in" class="nav-link is-active"' in rendered_nodash
