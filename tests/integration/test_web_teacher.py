from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.enums import AttendanceStatus, SessionStatus, UserRole
from app.models.user import User
from tests.factories import make_course, make_enrollment, make_record, make_session, make_user

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
async def test_teacher_dashboard_unauthenticated_redirects(client: AsyncClient) -> None:
    """Unauthenticated access to /teacher should redirect to /login."""
    response = await client.get("/teacher", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_dashboard_forbidden_for_other_roles(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Student and Admin roles accessing /teacher should receive 403 Forbidden."""
    student = await make_user(db_session, role=UserRole.STUDENT)
    admin = await make_user(db_session, role=UserRole.ADMIN)
    await db_session.commit()

    # Student access
    auth_client_as(client, student)
    response_student = await client.get("/teacher")
    assert response_student.status_code == 403

    # Admin access
    auth_client_as(client, admin)
    response_admin = await client.get("/teacher")
    assert response_admin.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_dashboard_success(client: AsyncClient, db_session: AsyncSession) -> None:
    """Teacher dashboard displays active sessions, upcoming schedule, and courses summary."""
    teacher = await make_user(db_session, role=UserRole.TEACHER, full_name="Dr. Smith")
    course = await make_course(db_session, teacher=teacher, code="CS101", title="Python 101")

    # Make one open live session and one upcoming session
    now = datetime.now(UTC)
    live_sess = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="LIVE99",
        code_expires_at=now + timedelta(minutes=15),
        starts_at=now - timedelta(minutes=10),
        ends_at=now + timedelta(minutes=50),
    )
    sched_sess = await make_session(
        db_session,
        course=course,
        status=SessionStatus.SCHEDULED,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=1),
    )
    await db_session.commit()

    auth_client_as(client, teacher)
    response = await client.get("/teacher/dashboard")
    assert response.status_code == 200
    text = response.text
    assert "Teacher Dashboard" in text
    assert "LIVE NOW" in text
    assert "LIVE99" in text
    assert "CS101" in text
    assert "Python 101" in text
    assert str(live_sess.id) in text
    assert str(sched_sess.id) in text


# ==============================================================================
# 2. Courses List & Course Detail Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_courses_list(client: AsyncClient, db_session: AsyncSession) -> None:
    """Courses endpoint lists courses assigned to teacher and supports search filtering."""
    teacher1 = await make_user(db_session, role=UserRole.TEACHER)
    teacher2 = await make_user(db_session, role=UserRole.TEACHER)

    await make_course(db_session, teacher=teacher1, code="CS101", title="Algorithms")
    await make_course(db_session, teacher=teacher1, code="CS102", title="Data Structures")
    await make_course(db_session, teacher=teacher2, code="BIO201", title="Biology")
    await db_session.commit()

    auth_client_as(client, teacher1)
    response = await client.get("/teacher/courses")
    assert response.status_code == 200
    assert "CS101" in response.text
    assert "CS102" in response.text
    assert "BIO201" not in response.text

    # Search filter
    search_resp = await client.get("/teacher/courses?search=Algorithms")
    assert search_resp.status_code == 200
    assert "CS101" in search_resp.text
    assert "CS102" not in search_resp.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_course_detail(client: AsyncClient, db_session: AsyncSession) -> None:
    """Course detail page displays roster, attendance rate, and session history."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)

    course = await make_course(db_session, teacher=teacher, code="MATH101", title="Calculus")
    other_course = await make_course(
        db_session, teacher=other_teacher, code="PHYS101", title="Physics"
    )

    student = await make_user(db_session, role=UserRole.STUDENT, full_name="Alice Student")
    await make_enrollment(db_session, course=course, student=student)
    await make_session(db_session, course=course, status=SessionStatus.CLOSED)
    await db_session.commit()

    auth_client_as(client, teacher)

    # Authorized course detail
    response = await client.get(f"/teacher/courses/{course.id}")
    assert response.status_code == 200
    assert "MATH101" in response.text
    assert "Calculus" in response.text
    assert "Alice Student" in response.text

    # Forbidden access to another teacher's course
    forbidden_resp = await client.get(f"/teacher/courses/{other_course.id}")
    assert forbidden_resp.status_code == 403


# ==============================================================================
# 3. Session Scheduling & Recurring Generation Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_schedule_session_csrf_and_creation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Scheduling a session requires CSRF and creates the session."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, code="ENG101")
    await db_session.commit()

    auth_client_as(client, teacher)

    # 1. Missing CSRF -> 403 Forbidden
    response_no_csrf = await client.post(
        "/teacher/sessions",
        data={
            "course_id": str(course.id),
            "date": "2026-11-28",
            "start_time": "09:00",
            "duration_minutes": 60,
            "late_after_minutes": 10,
        },
    )
    assert response_no_csrf.status_code == 403

    # 2. Valid with CSRF
    response_valid = await client.post(
        "/teacher/sessions",
        data={
            "csrf_token": CSRF_TOKEN,
            "course_id": str(course.id),
            "date": "2026-11-28",
            "start_time": "09:00",
            "duration_minutes": 60,
            "late_after_minutes": 10,
        },
        follow_redirects=False,
    )
    assert response_valid.status_code in (200, 303)
    if response_valid.status_code == 303:
        assert "/teacher/sessions/" in response_valid.headers["location"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_schedule_session_htmx(client: AsyncClient, db_session: AsyncSession) -> None:
    """HTMX request for session scheduling returns HX-Redirect and HX-Trigger toast."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, code="CHEM101")
    await db_session.commit()

    auth_client_as(client, teacher)

    response = await client.post(
        "/teacher/sessions",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "course_id": str(course.id),
            "date": "2026-12-01",
            "start_time": "14:00",
            "duration_minutes": 90,
            "late_after_minutes": 15,
        },
    )
    assert response.status_code == 200
    assert "HX-Redirect" in response.headers
    assert "HX-Trigger" in response.headers
    assert "toast" in response.headers["HX-Trigger"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_schedule_recurring_sessions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Generate recurring sessions across date range."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, code="HIST101")
    await db_session.commit()

    auth_client_as(client, teacher)

    response = await client.post(
        "/teacher/sessions/recurring",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "course_id": str(course.id),
            "weekday": 2,  # Wednesday
            "start_time": "10:00",
            "end_time": "11:30",
            "from_date": "2026-12-01",
            "to_date": "2026-12-21",
            "timezone": "UTC",
            "late_after_minutes": 10,
        },
        follow_redirects=False,
    )
    assert response.status_code in (200, 303)


# ==============================================================================
# 4. Live Session & Lifecycle Controls (Open, Regenerate, Close)
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_session_lifecycle(client: AsyncClient, db_session: AsyncSession) -> None:
    """Test full session lifecycle: open -> regenerate code -> close with auto-absent."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, code="ART101")
    student1 = await make_user(db_session, role=UserRole.STUDENT, full_name="Student One")
    student2 = await make_user(db_session, role=UserRole.STUDENT, full_name="Student Two")
    await make_enrollment(db_session, course=course, student=student1)
    await make_enrollment(db_session, course=course, student=student2)

    # Create scheduled session
    sess = await make_session(
        db_session,
        course=course,
        status=SessionStatus.SCHEDULED,
        starts_at=datetime.now(UTC),
        ends_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await db_session.commit()

    auth_client_as(client, teacher)

    # 1. View scheduled session page
    view_resp = await client.get(f"/teacher/sessions/{sess.id}")
    assert view_resp.status_code == 200
    assert "Open Session" in view_resp.text

    # 2. Open session
    open_resp = await client.post(
        f"/teacher/sessions/{sess.id}/open",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN},
        follow_redirects=False,
    )
    assert open_resp.status_code in (200, 303)

    # Verify session is open with code
    open_page_resp = await client.get(f"/teacher/sessions/{sess.id}")
    assert open_page_resp.status_code == 200
    assert "code-digits" in open_page_resp.text
    assert "data-expires-at" in open_page_resp.text
    assert 'hx-trigger="every 5s"' in open_page_resp.text

    # 3. Regenerate code
    regen_resp = await client.post(
        f"/teacher/sessions/{sess.id}/regenerate-code",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN},
        follow_redirects=False,
    )
    assert regen_resp.status_code in (200, 303)

    # 4. Close session -> triggers auto-absent for un-checked students
    close_resp = await client.post(
        f"/teacher/sessions/{sess.id}/close",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN},
        follow_redirects=False,
    )
    assert close_resp.status_code in (200, 303)

    # Verify closed state and auto-absent
    closed_page_resp = await client.get(f"/teacher/sessions/{sess.id}")
    assert closed_page_resp.status_code == 200
    assert "Closed" in closed_page_resp.text


# ==============================================================================
# 5. Live Roster HTMX Polling & Manual Attendance Override
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_roster_fragment_and_override(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test HTMX roster polling endpoint and manual attendance override."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, code="CS201")
    student = await make_user(db_session, role=UserRole.STUDENT, full_name="Bob Brown")
    await make_enrollment(db_session, course=course, student=student)

    sess = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="ABC789",
        code_expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    await db_session.commit()

    auth_client_as(client, teacher)

    # 1. Fetch roster fragment via HTMX
    roster_resp = await client.get(
        f"/teacher/sessions/{sess.id}/roster",
        headers={"HX-Request": "true"},
    )
    assert roster_resp.status_code == 200
    assert "Bob Brown" in roster_resp.text
    assert "Not Checked In" in roster_resp.text

    # 2. Override status to Late via HTMX POST
    override_resp = await client.post(
        f"/teacher/sessions/{sess.id}/attendance",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "student_id": str(student.id),
            "status": "late",
            "note": "Traffic delay",
        },
    )
    assert override_resp.status_code == 200
    assert "Late" in override_resp.text
    assert "HX-Trigger" in override_resp.headers
    assert "Marked as Late" in override_resp.headers["HX-Trigger"]

    # 3. Override status to Present
    present_resp = await client.post(
        f"/teacher/sessions/{sess.id}/attendance",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "student_id": str(student.id),
            "status": "present",
        },
    )
    assert present_resp.status_code == 200
    assert "Present" in present_resp.text


# ==============================================================================
# 6. Reports & CSV Export Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_reports_and_csv_download(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test attendance reports page, at-risk warnings, and CSV export."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, code="ECON101", min_attendance_pct=80.0)

    student1 = await make_user(db_session, role=UserRole.STUDENT, full_name="Good Student")
    student2 = await make_user(db_session, role=UserRole.STUDENT, full_name="At-Risk Student")
    await make_enrollment(db_session, course=course, student=student1)
    await make_enrollment(db_session, course=course, student=student2)

    # 2 closed sessions: student1 present in both (100%), student2 present in 0 (0% - at risk)
    s1 = await make_session(db_session, course=course, status=SessionStatus.CLOSED)
    s2 = await make_session(db_session, course=course, status=SessionStatus.CLOSED)

    await make_record(
        db_session, class_session=s1, student=student1, status=AttendanceStatus.PRESENT
    )
    await make_record(
        db_session, class_session=s2, student=student1, status=AttendanceStatus.PRESENT
    )
    await make_record(
        db_session, class_session=s1, student=student2, status=AttendanceStatus.ABSENT
    )
    await make_record(
        db_session, class_session=s2, student=student2, status=AttendanceStatus.ABSENT
    )
    await db_session.commit()

    auth_client_as(client, teacher)

    # 1. Reports HTML page
    reports_resp = await client.get(f"/teacher/reports?course_id={course.id}")
    assert reports_resp.status_code == 200
    text = reports_resp.text
    assert "Attendance Reports" in text
    assert "ECON101" in text
    assert "Good Student" in text
    assert "At-Risk Student" in text
    assert "At Risk" in text

    # 2. CSV export via query param
    csv_resp = await client.get(f"/teacher/reports?course_id={course.id}&export=csv")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "attachment;" in csv_resp.headers["content-disposition"]
    assert "Good Student" in csv_resp.text
    assert "At-Risk Student" in csv_resp.text

    # 3. CSV export via direct route
    direct_csv_resp = await client.get(f"/teacher/reports/{course.id}/csv")
    assert direct_csv_resp.status_code == 200
    assert "text/csv" in direct_csv_resp.headers["content-type"]


# ==============================================================================
# 7. Profile & Password Change Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_profile_and_password_change(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test viewing profile and changing teacher password."""
    teacher = await make_user(
        db_session,
        role=UserRole.TEACHER,
        email="prof_smith@example.com",
        password="OldPassword123!",
    )
    await db_session.commit()

    auth_client_as(client, teacher)

    # 1. View profile
    profile_resp = await client.get("/teacher/profile")
    assert profile_resp.status_code == 200
    assert "prof_smith@example.com" in profile_resp.text
    assert "Change Password" in profile_resp.text

    # 2. Change password with mismatched confirmation
    mismatch_resp = await client.post(
        "/teacher/profile/password",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "old_password": "OldPassword123!",
            "new_password": "NewPassword123!",
            "confirm_password": "DifferentPassword123!",
        },
    )
    assert mismatch_resp.status_code == 422
    assert "do not match" in mismatch_resp.text

    # 3. Successful password change
    success_resp = await client.post(
        "/teacher/profile/password",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "old_password": "OldPassword123!",
            "new_password": "NewPassword123!",
            "confirm_password": "NewPassword123!",
        },
    )
    assert success_resp.status_code == 200
    assert "changed successfully" in success_resp.text


# ==============================================================================
# 8. Live Shortcut Navigation Test
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_live_shortcut(client: AsyncClient, db_session: AsyncSession) -> None:
    """Test /teacher/live navigation shortcut."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    await db_session.commit()

    auth_client_as(client, teacher)

    # Without active session -> redirects to /teacher/sessions
    resp_no_active = await client.get("/teacher/live", follow_redirects=False)
    assert resp_no_active.status_code == 302
    assert resp_no_active.headers["location"] == "/teacher/sessions"

    # With active live session -> redirects directly to /teacher/sessions/{id}
    live_sess = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="SHORT1",
        code_expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    await db_session.commit()

    resp_active = await client.get("/teacher/live", follow_redirects=False)
    assert resp_active.status_code == 302
    assert resp_active.headers["location"] == f"/teacher/sessions/{live_sess.id}"


# ==============================================================================
# 9. Extended Validation & Edge Cases Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_schedule_session_validation_errors(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test validation errors when scheduling sessions."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    await db_session.commit()

    auth_client_as(client, teacher)

    # 1. Missing course_id
    resp_no_course = await client.post(
        "/teacher/sessions",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN, "date": "2026-11-28", "start_time": "09:00"},
    )
    assert resp_no_course.status_code == 422

    # 2. Invalid start time
    resp_invalid_time = await client.post(
        "/teacher/sessions",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "course_id": str(course.id),
            "date": "2026-11-28",
            "start_time": "invalid_time",
        },
    )
    assert resp_invalid_time.status_code == 422

    # 3. End time before start time
    resp_end_before = await client.post(
        "/teacher/sessions",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "course_id": str(course.id),
            "date": "2026-11-28",
            "start_time": "10:00",
            "end_time": "09:00",
        },
    )
    assert resp_end_before.status_code == 422

    # 4. HTMX error response
    resp_htmx_err = await client.post(
        "/teacher/sessions",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "course_id": str(course.id),
            "date": "2026-11-28",
            "start_time": "10:00",
            "end_time": "09:00",
        },
    )
    assert resp_htmx_err.status_code == 422
    assert "HX-Trigger" in resp_htmx_err.headers


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_schedule_recurring_validation_errors(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test validation errors for recurring session generation."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    await db_session.commit()

    auth_client_as(client, teacher)

    # Missing course
    resp = await client.post(
        "/teacher/sessions/recurring",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN, "weekday": 0},
    )
    assert resp.status_code == 422

    # Invalid dates
    resp_invalid = await client.post(
        "/teacher/sessions/recurring",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "course_id": str(course.id),
            "weekday": 0,
            "start_time": "10:00",
            "end_time": "11:00",
            "from_date": "not_a_date",
            "to_date": "not_a_date",
        },
    )
    assert resp_invalid.status_code == 422

    # HTMX error
    resp_htmx = await client.post(
        "/teacher/sessions/recurring",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "course_id": str(course.id),
            "weekday": 0,
            "start_time": "10:00",
            "end_time": "11:00",
            "from_date": "not_a_date",
            "to_date": "not_a_date",
        },
    )
    assert resp_htmx.status_code == 422
    assert "HX-Trigger" in resp_htmx.headers


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_live_session_not_found_and_forbidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Live session page returns 404 for missing session and 403 for another teacher's session."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_course = await make_course(db_session, teacher=other_teacher)
    other_sess = await make_session(db_session, course=other_course)
    await db_session.commit()

    auth_client_as(client, teacher)

    # 404 Not Found
    resp_404 = await client.get(f"/teacher/sessions/{UUID('00000000-0000-0000-0000-000000000000')}")
    assert resp_404.status_code == 404

    # 403 Forbidden
    resp_403 = await client.get(f"/teacher/sessions/{other_sess.id}")
    assert resp_403.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_controls_errors_and_htmx_handling(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test error handling in open, close, and regenerate code endpoints."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    # Closed session cannot be regenerated
    closed_sess = await make_session(db_session, course=course, status=SessionStatus.CLOSED)
    await db_session.commit()

    auth_client_as(client, teacher)

    # Trying to regenerate code on closed session via HTMX returns 400
    resp_regen = await client.post(
        f"/teacher/sessions/{closed_sess.id}/regenerate-code",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN},
    )
    assert resp_regen.status_code == 400
    assert "HX-Trigger" in resp_regen.headers

    # Trying to close an already closed session via HTMX returns 400
    resp_close = await client.post(
        f"/teacher/sessions/{closed_sess.id}/close",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN},
    )
    assert resp_close.status_code == 400

    # Non-HTMX open on closed session raises 409 Conflict
    resp_open = await client.post(
        f"/teacher/sessions/{closed_sess.id}/open",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN},
    )
    assert resp_open.status_code in (400, 409)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_attendance_override_non_htmx_and_validation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test non-HTMX attendance override and validation error on invalid status."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)
    sess = await make_session(db_session, course=course, status=SessionStatus.OPEN)
    await db_session.commit()

    auth_client_as(client, teacher)

    # 1. Invalid status value -> 400
    resp_invalid_st = await client.post(
        f"/teacher/sessions/{sess.id}/attendance",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN, "student_id": str(student.id), "status": "invalid_status"},
    )
    assert resp_invalid_st.status_code == 400

    # 2. Non-HTMX valid post -> redirects 303
    resp_valid = await client.post(
        f"/teacher/sessions/{sess.id}/attendance",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN, "student_id": str(student.id), "status": "excused"},
        follow_redirects=False,
    )
    assert resp_valid.status_code == 303
    assert f"/teacher/sessions/{sess.id}" in resp_valid.headers["location"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_reports_access_forbidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Reports endpoint returns 403 when requesting another teacher's course."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_course = await make_course(db_session, teacher=other_teacher)
    await db_session.commit()

    auth_client_as(client, teacher)
    resp = await client.get(f"/teacher/reports?course_id={other_course.id}")
    assert resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_profile_password_wrong_old_and_htmx(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Password change fails with incorrect current password and supports HTMX."""
    teacher = await make_user(
        db_session,
        role=UserRole.TEACHER,
        password="CorrectPassword123!",
    )
    await db_session.commit()

    auth_client_as(client, teacher)

    # 1. Incorrect old password
    resp_wrong = await client.post(
        "/teacher/profile/password",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "old_password": "WrongPassword123!",
            "new_password": "NewValidPass123!",
            "confirm_password": "NewValidPass123!",
        },
    )
    assert resp_wrong.status_code == 422
    assert "incorrect" in resp_wrong.text.lower()

    # 2. HTMX successful password change
    resp_htmx = await client.post(
        "/teacher/profile/password",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "old_password": "CorrectPassword123!",
            "new_password": "BrandNewPass123!",
            "confirm_password": "BrandNewPass123!",
        },
    )
    assert resp_htmx.status_code == 200
    assert "HX-Trigger" in resp_htmx.headers
    assert "changed successfully" in resp_htmx.text


# ==============================================================================
# 10. Comprehensive Path Coverage Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_sessions_filters_and_pagination(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test sessions list page with course and status filters, and pagination."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    c1 = await make_course(db_session, teacher=teacher, code="C1")
    c2 = await make_course(db_session, teacher=teacher, code="C2")

    await make_session(db_session, course=c1, status=SessionStatus.SCHEDULED)
    await make_session(db_session, course=c2, status=SessionStatus.CLOSED)
    await db_session.commit()

    auth_client_as(client, teacher)

    # Filter by course
    resp_c1 = await client.get(f"/teacher/sessions?course_id={c1.id}")
    assert resp_c1.status_code == 200
    assert "C1" in resp_c1.text

    # Filter by status
    resp_st = await client.get("/teacher/sessions?status=closed")
    assert resp_st.status_code == 200
    assert "C2" in resp_st.text

    # Pagination page 1
    resp_page = await client.get("/teacher/sessions?page=1&size=1")
    assert resp_page.status_code == 200


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_schedule_session_with_iso_strings(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test scheduling session providing starts_at and ends_at as ISO strings."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    await db_session.commit()

    auth_client_as(client, teacher)

    now = datetime.now(UTC) + timedelta(days=2)
    s_iso = now.isoformat()
    e_iso = (now + timedelta(hours=2)).isoformat()

    resp = await client.post(
        "/teacher/sessions",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={
            "csrf_token": CSRF_TOKEN,
            "course_id": str(course.id),
            "starts_at": s_iso,
            "ends_at": e_iso,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_controls_non_htmx_and_roster_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test non-HTMX flows for open/close/regenerate and roster 404."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    sess = await make_session(db_session, course=course, status=SessionStatus.SCHEDULED)
    await db_session.commit()

    auth_client_as(client, teacher)

    # 1. Non-HTMX Open
    open_resp = await client.post(
        f"/teacher/sessions/{sess.id}/open",
        headers={"X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN},
        follow_redirects=False,
    )
    assert open_resp.status_code == 303

    # 2. HTMX Regenerate
    regen_resp = await client.post(
        f"/teacher/sessions/{sess.id}/regenerate-code",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN},
    )
    assert regen_resp.status_code == 200

    # 3. HTMX Close
    close_resp = await client.post(
        f"/teacher/sessions/{sess.id}/close",
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
        data={"csrf_token": CSRF_TOKEN},
    )
    assert close_resp.status_code == 200

    # 4. Roster 404
    roster_404 = await client.get(
        f"/teacher/sessions/{UUID('00000000-0000-0000-0000-000000000000')}/roster"
    )
    assert roster_404.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_teacher_reports_filters_and_empty(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test reports page with date filters, empty course list, and direct CSV with dates."""
    teacher_no_courses = await make_user(db_session, role=UserRole.TEACHER)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    await db_session.commit()

    # 1. Teacher with no courses
    auth_client_as(client, teacher_no_courses)
    resp_empty = await client.get("/teacher/reports")
    assert resp_empty.status_code == 200
    assert "No course selected" in resp_empty.text

    # 2. Teacher with date filters
    auth_client_as(client, teacher)
    resp_dates = await client.get(
        f"/teacher/reports?course_id={course.id}&from_dt=2026-01-01&to_dt=2026-12-31"
    )
    assert resp_dates.status_code == 200

    # 3. Direct CSV with date filters
    resp_csv_dates = await client.get(
        f"/teacher/reports/{course.id}/csv?from_dt=2026-01-01&to_dt=2026-12-31"
    )
    assert resp_csv_dates.status_code == 200
    assert "text/csv" in resp_csv_dates.headers["content-type"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_all_teacher_portal_pages_render_and_sidebar(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Verify all 8 Teacher Portal pages exist, return 200, and include the persistent sidebar."""
    teacher = await make_user(db_session, role=UserRole.TEACHER, full_name="Prof. Portal Architect")
    course = await make_course(
        db_session, teacher=teacher, code="ARCH404", title="Portal Architecture"
    )
    student = await make_user(db_session, role=UserRole.STUDENT, full_name="Student Test")
    await make_enrollment(db_session, course=course, student=student)
    session_obj = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="PORTAL",
        code_expires_at=datetime.now(UTC) + timedelta(minutes=20),
    )
    await db_session.commit()

    auth_client_as(client, teacher)

    teacher_urls = [
        "/teacher",
        "/teacher/courses",
        f"/teacher/courses/{course.id}",
        "/teacher/sessions",
        f"/teacher/sessions/{session_obj.id}",
        "/teacher/live",
        "/teacher/reports",
        "/teacher/profile",
    ]

    for url in teacher_urls:
        resp = await client.get(url, follow_redirects=True)
        assert resp.status_code == 200, f"Expected 200 for {url}, got {resp.status_code}"
        assert "sidebar-drawer" in resp.text, f"Missing persistent sidebar on {url}"
        assert "AttendSys" in resp.text, f"Missing brand banner on {url}"
        assert "app.css" in resp.text, f"Missing design system stylesheet on {url}"
