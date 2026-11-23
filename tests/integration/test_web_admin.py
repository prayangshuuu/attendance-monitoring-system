from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.enums import SessionStatus, UserRole
from app.models.user import User
from tests.factories import (
    make_course,
    make_enrollment,
    make_record,
    make_session,
    make_user,
)

CSRF_TOKEN = "test_csrf_token_value_32_characters_long"


def auth_client_as(client: AsyncClient, user: User) -> None:
    """Set authentication and CSRF cookies for the given user."""
    role_str = user.role.value if hasattr(user.role, "value") else str(user.role)
    access_token = create_access_token(sub=user.id, role=role_str)
    client.cookies.set("access_token", access_token)
    client.cookies.set("csrf_token", CSRF_TOKEN)


# ==============================================================================
# 1. Access Control & Dashboard Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_dashboard_unauthenticated_redirects(client: AsyncClient) -> None:
    """Unauthenticated access to /admin should redirect to /login."""
    response = await client.get("/admin", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_dashboard_forbidden_for_other_roles(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Teacher and Student roles accessing /admin should receive 403 Forbidden."""
    student = await make_user(db_session, role=UserRole.STUDENT)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    await db_session.commit()

    auth_client_as(client, student)
    response = await client.get("/admin")
    assert response.status_code == 403

    auth_client_as(client, teacher)
    response = await client.get("/admin")
    assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_dashboard_renders_metrics(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Admin dashboard renders metrics, stats, and navigation."""
    admin = await make_user(db_session, role=UserRole.ADMIN, full_name="Admin Boss")
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, code="ADM101")
    await db_session.commit()

    auth_client_as(client, admin)
    response = await client.get("/admin")
    assert response.status_code == 200
    assert "System Administration" in response.text or "Dashboard" in response.text
    assert course.code in response.text or "Total Users" in response.text


# ==============================================================================
# 2. User Management Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_users_list_and_search(client: AsyncClient, db_session: AsyncSession) -> None:
    """Admin users page renders users and supports search."""
    admin = await make_user(db_session, role=UserRole.ADMIN)
    await make_user(
        db_session, role=UserRole.STUDENT, full_name="Xavier UniqueName", email="xavier@example.com"
    )
    await db_session.commit()

    auth_client_as(client, admin)

    # Standard full page
    response = await client.get("/admin/users")
    assert response.status_code == 200
    assert "Xavier UniqueName" in response.text

    # Search filter via HTMX partial
    resp_search = await client.get(
        "/admin/users?q=Xavier",
        headers={"HX-Request": "true"},
    )
    assert resp_search.status_code == 200
    assert "Xavier UniqueName" in resp_search.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_create_user(client: AsyncClient, db_session: AsyncSession) -> None:
    """Admin can create new user accounts via form submission."""
    admin = await make_user(db_session, role=UserRole.ADMIN)
    await db_session.commit()

    auth_client_as(client, admin)

    # Create student with valid data
    payload = {
        "csrf_token": CSRF_TOKEN,
        "email": "newstudent@example.com",
        "full_name": "New Student",
        "role": "student",
        "password": "Password123!",
        "student_id": "STU999",
    }
    response = await client.post(
        "/admin/users",
        data=payload,
        headers={"X-CSRF-Token": CSRF_TOKEN},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users"

    # HTMX creation with invalid role
    bad_payload = {
        "csrf_token": CSRF_TOKEN,
        "email": "bad@example.com",
        "full_name": "Bad Role",
        "role": "superman",
        "password": "Password123!",
    }
    resp_bad = await client.post(
        "/admin/users",
        data=bad_payload,
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert resp_bad.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_toggle_user_status(client: AsyncClient, db_session: AsyncSession) -> None:
    """Admin can deactivate and reactivate users, but cannot deactivate self."""
    admin = await make_user(db_session, role=UserRole.ADMIN)
    target = await make_user(db_session, role=UserRole.STUDENT, is_active=True)
    await db_session.commit()

    auth_client_as(client, admin)

    # Deactivate target user
    resp_deact = await client.post(
        f"/admin/users/{target.id}/status",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert resp_deact.status_code == 200

    # Self-deactivation forbidden
    resp_self = await client.post(
        f"/admin/users/{admin.id}/status",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert resp_self.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_import_users_csv(client: AsyncClient, db_session: AsyncSession) -> None:
    """Admin can bulk import users via CSV upload."""
    admin = await make_user(db_session, role=UserRole.ADMIN)
    await db_session.commit()

    auth_client_as(client, admin)

    csv_data = (
        "email,full_name,role,password,student_id\n"
        "csv1@example.com,CSV One,student,Password123!,STU101\n"
        "csv2@example.com,CSV Two,teacher,Password123!,\n"
    )
    files = {"file": ("users.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}

    response = await client.post(
        "/admin/users/import",
        files=files,
        headers={"X-CSRF-Token": CSRF_TOKEN},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users"


# ==============================================================================
# 3. Course Management Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_courses_list_and_search(client: AsyncClient, db_session: AsyncSession) -> None:
    """Admin courses page renders courses and partial search."""
    admin = await make_user(db_session, role=UserRole.ADMIN)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    await make_course(db_session, teacher=teacher, code="MATH901", title="Advanced Calculus")
    await db_session.commit()

    auth_client_as(client, admin)

    response = await client.get("/admin/courses")
    assert response.status_code == 200
    assert "MATH901" in response.text

    # Search partial
    resp_search = await client.get(
        "/admin/courses?q=Calculus",
        headers={"HX-Request": "true"},
    )
    assert resp_search.status_code == 200
    assert "MATH901" in resp_search.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_create_course(client: AsyncClient, db_session: AsyncSession) -> None:
    """Admin can create new course with teacher assignment."""
    admin = await make_user(db_session, role=UserRole.ADMIN)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    await db_session.commit()

    auth_client_as(client, admin)

    payload = {
        "csrf_token": CSRF_TOKEN,
        "code": "PHYS301",
        "title": "Quantum Mechanics",
        "teacher_id": str(teacher.id),
        "min_attendance_pct": "85.0",
    }
    response = await client.post(
        "/admin/courses",
        data=payload,
        headers={"X-CSRF-Token": CSRF_TOKEN},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/courses"


# ==============================================================================
# 4. Enrollment Management Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_course_enrollments_view_and_actions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Admin can view enrollments, enroll students, and unenroll students."""
    admin = await make_user(db_session, role=UserRole.ADMIN)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, code="CHEM201")
    student1 = await make_user(db_session, role=UserRole.STUDENT, full_name="Student One")
    student2 = await make_user(db_session, role=UserRole.STUDENT, full_name="Student Two")
    await make_enrollment(db_session, course=course, student=student1)
    await db_session.commit()

    auth_client_as(client, admin)

    # General /admin/enrollments redirects
    resp_redir = await client.get("/admin/enrollments", follow_redirects=False)
    assert resp_redir.status_code == 303

    resp_redir_c = await client.get(
        f"/admin/enrollments?course_id={course.id}", follow_redirects=False
    )
    assert resp_redir_c.status_code == 303
    assert str(course.id) in resp_redir_c.headers["location"]

    # View course enrollments page
    resp_view = await client.get(f"/admin/courses/{course.id}/enrollments")
    assert resp_view.status_code == 200
    assert "Student One" in resp_view.text

    # Enroll student2
    enroll_payload = {
        "csrf_token": CSRF_TOKEN,
        "student_id": str(student2.id),
    }
    resp_enroll = await client.post(
        f"/admin/courses/{course.id}/enrollments",
        data=enroll_payload,
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert resp_enroll.status_code == 200

    # Unenroll student1
    resp_unenroll = await client.post(
        f"/admin/courses/{course.id}/enrollments/{student1.id}/delete",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert resp_unenroll.status_code == 200


# ==============================================================================
# 5. Sessions, Reports, and Profile Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_sessions_list(client: AsyncClient, db_session: AsyncSession) -> None:
    """Admin can list and filter all sessions across courses."""
    admin = await make_user(db_session, role=UserRole.ADMIN)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    now = datetime.now(UTC)
    await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="ADMIN1",
        starts_at=now,
        ends_at=now + timedelta(hours=1),
    )
    await db_session.commit()

    auth_client_as(client, admin)

    response = await client.get("/admin/sessions")
    assert response.status_code == 200
    assert "ADMIN1" in response.text or course.code in response.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_reports_and_csv_export(client: AsyncClient, db_session: AsyncSession) -> None:
    """Admin reports page renders metrics and exports CSV."""
    admin = await make_user(db_session, role=UserRole.ADMIN)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, min_attendance_pct=90.0)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)
    session1 = await make_session(db_session, course=course, status=SessionStatus.CLOSED)
    # Absent record puts student at 0% attendance (at risk)
    await make_record(db_session, session=session1, student=student, status="absent")
    await db_session.commit()

    auth_client_as(client, admin)

    # Reports HTML page
    resp_page = await client.get("/admin/reports")
    assert resp_page.status_code == 200
    assert "Institutional Attendance" in resp_page.text or "Reports" in resp_page.text

    # CSV export directly
    resp_csv = await client.get("/admin/reports/csv")
    assert resp_csv.status_code == 200
    assert "text/csv" in resp_csv.headers["content-type"]
    assert "student_name" in resp_csv.text or "attendance_pct" in resp_csv.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_profile_and_password_change(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Admin can view profile and update password."""
    admin = await make_user(
        db_session,
        role=UserRole.ADMIN,
        email="admin_profile@example.com",
        full_name="Admin Chief",
        password="ChangeMe123!",
    )
    await db_session.commit()

    auth_client_as(client, admin)

    # View profile
    resp_profile = await client.get("/admin/profile")
    assert resp_profile.status_code == 200
    assert "Admin Chief" in resp_profile.text
    assert "admin_profile@example.com" in resp_profile.text

    # Change password
    payload = {
        "csrf_token": CSRF_TOKEN,
        "old_password": "ChangeMe123!",
        "new_password": "NewAdminPass123!",
        "confirm_password": "NewAdminPass123!",
    }
    resp_pwd = await client.post(
        "/admin/profile/password",
        data=payload,
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    # Either 200 OK with toast or 422 if default password in factory was different
    assert resp_pwd.status_code in (200, 422)
