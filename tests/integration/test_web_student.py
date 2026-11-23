from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.enums import AttendanceMethod, AttendanceStatus, SessionStatus, UserRole
from app.models.user import User
from tests.factories import make_course, make_enrollment, make_record, make_session, make_user

CSRF_TOKEN = "test_csrf_token_student_32_characters"


def auth_client_as(client: AsyncClient, user: User) -> None:
    """Set authentication and CSRF cookies for the given user."""
    role_str = user.role.value if hasattr(user.role, "value") else str(user.role)
    access_token = create_access_token(sub=user.id, role=role_str)
    client.cookies.set("access_token", access_token)
    client.cookies.set("csrf_token", CSRF_TOKEN)


# ==============================================================================
# 1. Access Control & Authorization Tests
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_portal_unauthenticated_redirects(client: AsyncClient) -> None:
    """Unauthenticated browser request to /student redirects to /login."""
    response = await client.get("/student", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_portal_unauthenticated_htmx(client: AsyncClient) -> None:
    """Unauthenticated HTMX request returns 401 with HX-Redirect header."""
    response = await client.get("/student", headers={"HX-Request": "true"})
    assert response.status_code in (200, 401)
    assert response.headers.get("HX-Redirect") == "/login"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_portal_forbidden_for_teacher_and_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Teacher and Admin roles accessing /student should receive 403 Forbidden."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    admin = await make_user(db_session, role=UserRole.ADMIN)
    await db_session.commit()

    # Teacher access
    auth_client_as(client, teacher)
    res_teacher = await client.get("/student")
    assert res_teacher.status_code == 403

    # Admin access
    auth_client_as(client, admin)
    res_admin = await client.get("/student")
    assert res_admin.status_code == 403


# ==============================================================================
# 2. Student Dashboard Tests (GET /student & GET /student/dashboard)
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_dashboard_empty_enrollments(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Dashboard handles students with no course enrollments gracefully."""
    student = await make_user(db_session, role=UserRole.STUDENT, full_name="New Student")
    await db_session.commit()

    auth_client_as(client, student)
    response = await client.get("/student/dashboard")
    assert response.status_code == 200
    assert "Welcome back, New Student" in response.text
    assert "No Enrolled Courses" in response.text
    assert "Overall Attendance" in response.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_dashboard_at_risk_warning_and_active_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Dashboard displays at-risk warning when below threshold and highlights active sessions."""
    teacher = await make_user(db_session, role=UserRole.TEACHER, full_name="Prof. Alan")
    student = await make_user(db_session, role=UserRole.STUDENT, full_name="Bob Smith")

    course_risk = await make_course(
        db_session, teacher=teacher, code="CS101", title="Algorithms", min_attendance_pct=80.0
    )
    course_good = await make_course(
        db_session, teacher=teacher, code="CS102", title="Databases", min_attendance_pct=75.0
    )
    await make_enrollment(db_session, course=course_risk, student=student)
    await make_enrollment(db_session, course=course_good, student=student)

    now = datetime.now(UTC)

    # In CS101: 2 closed sessions, student was absent in both -> 0% attendance (At Risk!)
    s1 = await make_session(
        db_session,
        course=course_risk,
        status=SessionStatus.CLOSED,
        starts_at=now - timedelta(days=2),
        ends_at=now - timedelta(days=2, hours=-1),
    )
    s2 = await make_session(
        db_session,
        course=course_risk,
        status=SessionStatus.CLOSED,
        starts_at=now - timedelta(days=1),
        ends_at=now - timedelta(days=1, hours=-1),
    )
    await make_record(db_session, session=s1, student=student, status=AttendanceStatus.ABSENT)
    await make_record(db_session, session=s2, student=student, status=AttendanceStatus.ABSENT)

    # In CS102: 1 closed session, student was present -> 100% attendance (Good Standing)
    s3 = await make_session(
        db_session,
        course=course_good,
        status=SessionStatus.CLOSED,
        starts_at=now - timedelta(days=1),
        ends_at=now - timedelta(days=1, hours=-1),
    )
    await make_record(db_session, session=s3, student=student, status=AttendanceStatus.PRESENT)

    # Open active session in CS101 for quick check-in
    await make_session(
        db_session,
        course=course_risk,
        status=SessionStatus.OPEN,
        checkin_code="LIVE01",
        code_expires_at=now + timedelta(minutes=15),
        starts_at=now - timedelta(minutes=5),
        ends_at=now + timedelta(minutes=45),
    )
    await db_session.commit()

    auth_client_as(client, student)
    response = await client.get("/student")
    assert response.status_code == 200

    # At-risk warning checks
    assert "Attendance Risk Warning" in response.text
    assert "CS101" in response.text
    assert "At Risk" in response.text
    assert "Good Standing" in response.text

    # Active session quick check-in banner
    assert "LIVE SESSION OPEN" in response.text
    assert "CS101" in response.text


# ==============================================================================
# 3. Check-in Page & Submission Tests (GET & POST /student/checkin)
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_checkin_page_get(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /student/checkin and /student/check-in both render check-in form."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)
    course = await make_course(db_session, teacher=teacher, code="MATH101")
    await make_enrollment(db_session, course=course, student=student)

    now = datetime.now(UTC)
    await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="MATH99",
        code_expires_at=now + timedelta(minutes=10),
        starts_at=now - timedelta(minutes=2),
        ends_at=now + timedelta(minutes=40),
    )
    await db_session.commit()

    auth_client_as(client, student)

    # Test /student/checkin
    res1 = await client.get("/student/checkin")
    assert res1.status_code == 200
    assert "Class Check-in" in res1.text
    assert "MATH101" in res1.text
    assert 'name="code"' in res1.text
    assert "code-slots-helper" in res1.text
    assert "sidebar-drawer" in res1.text
    assert "is-active" in res1.text

    # Test alias /student/check-in
    res2 = await client.get("/student/check-in")
    assert res2.status_code == 200
    assert "Class Check-in" in res2.text
    assert "MATH101" in res2.text
    assert 'name="code"' in res2.text
    assert "code-slots-helper" in res2.text
    assert "sidebar-drawer" in res2.text
    assert "is-active" in res2.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_checkin_post_success_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /student/checkin before late threshold records status 'present'."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)
    course = await make_course(
        db_session, teacher=teacher, code="ENG201", title="English Literature"
    )
    await make_enrollment(db_session, course=course, student=student)

    now = datetime.now(UTC)
    sess = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="ENG123",
        late_after_minutes=15,
        code_expires_at=now + timedelta(minutes=12),
        starts_at=now - timedelta(minutes=5),  # 5 min in < 15 min -> Present
        ends_at=now + timedelta(minutes=45),
    )
    await db_session.commit()

    auth_client_as(client, student)

    # Submit via HTMX
    response = await client.post(
        "/student/checkin",
        data={"code": "ENG123", "session_id": str(sess.id), "csrf_token": CSRF_TOKEN},
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert response.status_code == 200
    assert "Marked Present" in response.text
    assert "ENG201" in response.text
    assert "HX-Trigger" in response.headers
    trigger_data = json.loads(response.headers["HX-Trigger"])
    assert trigger_data["toast"]["type"] == "success"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_checkin_post_success_late(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /student/checkin after late threshold records status 'late'."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)
    course = await make_course(db_session, teacher=teacher, code="PHYS301")
    await make_enrollment(db_session, course=course, student=student)

    now = datetime.now(UTC)
    sess = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="PHYS77",
        late_after_minutes=5,
        code_expires_at=now + timedelta(minutes=10),
        starts_at=now - timedelta(minutes=12),  # 12 min in > 5 min -> Late
        ends_at=now + timedelta(minutes=40),
    )
    await db_session.commit()

    auth_client_as(client, student)

    response = await client.post(
        "/student/checkin",
        data={"code": "phys77", "session_id": str(sess.id), "csrf_token": CSRF_TOKEN},
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert response.status_code == 200
    assert "Marked Late" in response.text
    trigger_data = json.loads(response.headers["HX-Trigger"])
    assert trigger_data["toast"]["type"] == "warning"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_checkin_post_auto_resolve_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Omitting session_id auto-resolves the active session for the enrolled student."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)
    course = await make_course(db_session, teacher=teacher, code="BIO101")
    await make_enrollment(db_session, course=course, student=student)

    now = datetime.now(UTC)
    await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="BIO789",
        code_expires_at=now + timedelta(minutes=15),
        starts_at=now - timedelta(minutes=2),
        ends_at=now + timedelta(minutes=45),
    )
    await db_session.commit()

    auth_client_as(client, student)

    # session_id is omitted
    response = await client.post(
        "/student/check-in",
        data={"code": "BIO789", "csrf_token": CSRF_TOKEN},
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert response.status_code == 200
    assert "Marked Present" in response.text
    assert "BIO101" in response.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_checkin_post_already_checked_in(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Submitting code again when already checked in returns 422 with conflict message."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)
    course = await make_course(db_session, teacher=teacher, code="CHEM101")
    await make_enrollment(db_session, course=course, student=student)

    now = datetime.now(UTC)
    sess = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="CHEM99",
        code_expires_at=now + timedelta(minutes=15),
        starts_at=now - timedelta(minutes=2),
        ends_at=now + timedelta(minutes=45),
    )
    # Already recorded
    await make_record(db_session, session=sess, student=student, status=AttendanceStatus.PRESENT)
    await db_session.commit()

    auth_client_as(client, student)

    response = await client.post(
        "/student/checkin",
        data={"code": "CHEM99", "session_id": str(sess.id), "csrf_token": CSRF_TOKEN},
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert response.status_code == 422
    assert "already checked in" in response.text.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_checkin_post_invalid_and_expired_codes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Submitting invalid code or expired code returns 422 with clear feedback."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)
    course = await make_course(db_session, teacher=teacher, code="ART101")
    await make_enrollment(db_session, course=course, student=student)

    now = datetime.now(UTC)
    sess_valid = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="ART123",
        code_expires_at=now + timedelta(minutes=10),
        starts_at=now - timedelta(minutes=2),
        ends_at=now + timedelta(minutes=45),
    )
    sess_expired = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="ARTOLD",
        code_expires_at=now - timedelta(minutes=5),  # Expired!
        starts_at=now - timedelta(minutes=25),
        ends_at=now + timedelta(minutes=20),
    )
    await db_session.commit()

    auth_client_as(client, student)

    # 1. Invalid code
    res_invalid = await client.post(
        "/student/checkin",
        data={"code": "WRONG1", "session_id": str(sess_valid.id), "csrf_token": CSRF_TOKEN},
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert res_invalid.status_code == 422
    assert "invalid check-in code" in res_invalid.text.lower()

    # 2. Expired code
    res_expired = await client.post(
        "/student/checkin",
        data={"code": "ARTOLD", "session_id": str(sess_expired.id), "csrf_token": CSRF_TOKEN},
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert res_expired.status_code == 422
    assert "expired" in res_expired.text.lower()

    # 3. Code with invalid length
    res_short = await client.post(
        "/student/checkin",
        data={"code": "AB12", "session_id": str(sess_valid.id), "csrf_token": CSRF_TOKEN},
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert res_short.status_code == 422
    assert "exactly 6 characters" in res_short.text.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_checkin_csrf_protection(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Missing or mismatching CSRF token on POST /student/checkin returns 403 Forbidden."""
    student = await make_user(db_session, role=UserRole.STUDENT)
    await db_session.commit()

    auth_client_as(client, student)

    # Post with wrong CSRF token
    response = await client.post(
        "/student/checkin",
        data={"code": "ABC123", "csrf_token": "wrong_csrf_token"},
        headers={"X-CSRF-Token": "wrong_csrf_token"},
    )
    assert response.status_code == 403


# ==============================================================================
# 4. Attendance History Tests (GET /student/attendance)
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_attendance_history_table_and_filtering(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /student/attendance displays records with status badges and filters."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)
    c1 = await make_course(db_session, teacher=teacher, code="HIST101", title="World History")
    c2 = await make_course(db_session, teacher=teacher, code="HIST201", title="European History")
    await make_enrollment(db_session, course=c1, student=student)
    await make_enrollment(db_session, course=c2, student=student)

    now = datetime.now(UTC)
    s1 = await make_session(
        db_session,
        course=c1,
        status=SessionStatus.CLOSED,
        starts_at=now - timedelta(days=3),
        ends_at=now - timedelta(days=3, hours=-1),
    )
    s2 = await make_session(
        db_session,
        course=c1,
        status=SessionStatus.CLOSED,
        starts_at=now - timedelta(days=2),
        ends_at=now - timedelta(days=2, hours=-1),
    )
    s3 = await make_session(
        db_session,
        course=c2,
        status=SessionStatus.CLOSED,
        starts_at=now - timedelta(days=1),
        ends_at=now - timedelta(days=1, hours=-1),
    )

    await make_record(
        db_session,
        session=s1,
        student=student,
        status=AttendanceStatus.PRESENT,
        method=AttendanceMethod.CODE,
    )
    await make_record(
        db_session,
        session=s2,
        student=student,
        status=AttendanceStatus.LATE,
        method=AttendanceMethod.CODE,
    )
    await make_record(
        db_session,
        session=s3,
        student=student,
        status=AttendanceStatus.ABSENT,
        method=AttendanceMethod.MANUAL,
    )
    await db_session.commit()

    auth_client_as(client, student)

    # 1. View all attendance records
    res_all = await client.get("/student/attendance")
    assert res_all.status_code == 200
    assert "My Attendance History" in res_all.text
    assert "HIST101" in res_all.text
    assert "HIST201" in res_all.text
    assert "Present" in res_all.text
    assert "Late" in res_all.text
    assert "Absent" in res_all.text

    # 2. Filter by HIST101 course
    res_filtered = await client.get(f"/student/attendance?course_id={c1.id}")
    assert res_filtered.status_code == 200
    assert "HIST101" in res_filtered.text
    # HIST201 should not appear in table body for c1 filter
    assert "European History" not in res_filtered.text


# ==============================================================================
# 5. Enrolled Courses Page Tests (GET /student/courses)
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_courses_page(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /student/courses displays enrolled courses with teacher name and stats."""
    teacher = await make_user(
        db_session, role=UserRole.TEACHER, full_name="Dr. Jane Watson", email="jwatson@example.com"
    )
    student = await make_user(db_session, role=UserRole.STUDENT)
    course = await make_course(
        db_session,
        teacher=teacher,
        code="CS50",
        title="Intro to CS",
        min_attendance_pct=85.0,
    )
    await make_enrollment(db_session, course=course, student=student)

    now = datetime.now(UTC)
    s = await make_session(
        db_session,
        course=course,
        status=SessionStatus.CLOSED,
        starts_at=now - timedelta(days=1),
        ends_at=now - timedelta(days=1, hours=-1),
    )
    await make_record(db_session, session=s, student=student, status=AttendanceStatus.PRESENT)
    await db_session.commit()

    auth_client_as(client, student)

    response = await client.get("/student/courses")
    assert response.status_code == 200
    assert "My Enrolled Courses" in response.text
    assert "CS50" in response.text
    assert "Intro to CS" in response.text
    assert "Dr. Jane Watson" in response.text
    assert "85.0%" in response.text  # Min required
    assert "Good Standing" in response.text


# ==============================================================================
# 6. Profile & Password Change Tests (GET & POST /student/profile)
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_profile_and_password_change(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /student/profile displays student info, student ID badge, and updates password."""
    student = await make_user(
        db_session,
        role=UserRole.STUDENT,
        full_name="Samantha Ray",
        email="samantha@example.com",
        student_id="STU_998877",
        password="OldPassword123!",
    )
    await db_session.commit()

    auth_client_as(client, student)

    # 1. Profile view
    res_profile = await client.get("/student/profile")
    assert res_profile.status_code == 200
    assert "Samantha Ray" in res_profile.text
    assert "STU_998877" in res_profile.text
    assert "Student ID Number" in res_profile.text

    # 2. Password mismatch validation error
    res_mismatch = await client.post(
        "/student/profile/password",
        data={
            "old_password": "OldPassword123!",
            "new_password": "NewPassword123!",
            "confirm_password": "DifferentPassword123!",
            "csrf_token": CSRF_TOKEN,
        },
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert res_mismatch.status_code == 422
    assert "do not match" in res_mismatch.text.lower()

    # 3. Successful password change
    res_success = await client.post(
        "/student/profile/password",
        data={
            "old_password": "OldPassword123!",
            "new_password": "BrandNewPassword123!",
            "confirm_password": "BrandNewPassword123!",
            "csrf_token": CSRF_TOKEN,
        },
        headers={"HX-Request": "true", "X-CSRF-Token": CSRF_TOKEN},
    )
    assert res_success.status_code == 200
    assert "changed successfully" in res_success.text.lower()
    assert "HX-Trigger" in res_success.headers


# ==============================================================================
# 7. End-to-End QA Verification: All Student Portal Pages & Unified Design
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_portal_all_pages_qa_verification(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Verify all student portal routes return 200 OK, have persistent sidebar,

    use the unified light theme design tokens, and include polished components.
    """
    teacher = await make_user(db_session, role=UserRole.TEACHER, full_name="Prof. QA")
    student = await make_user(
        db_session,
        role=UserRole.STUDENT,
        full_name="QA Student",
        student_id="STU-QA-2026",
    )
    course = await make_course(
        db_session,
        teacher=teacher,
        code="QA101",
        title="Quality Engineering",
        min_attendance_pct=80.0,
    )
    await make_enrollment(db_session, course=course, student=student)

    now = datetime.now(UTC)
    # 1 closed session
    closed_s = await make_session(
        db_session,
        course=course,
        status=SessionStatus.CLOSED,
        starts_at=now - timedelta(days=1),
        ends_at=now - timedelta(days=1, hours=-1),
    )
    await make_record(
        db_session,
        session=closed_s,
        student=student,
        status=AttendanceStatus.PRESENT,
        method=AttendanceMethod.CODE,
    )

    # 1 active session with check-in code and countdown expiration
    await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN,
        checkin_code="QACODE",
        code_expires_at=now + timedelta(minutes=14),
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(minutes=45),
    )
    await db_session.commit()

    auth_client_as(client, student)

    urls_to_test = [
        ("/student", "Welcome back, QA Student"),
        ("/student/", "Welcome back, QA Student"),
        ("/student/dashboard", "Welcome back, QA Student"),
        ("/student/check-in", "Class Check-in"),
        ("/student/checkin", "Class Check-in"),
        ("/student/attendance", "My Attendance History"),
        ("/student/courses", "My Enrolled Courses"),
        ("/student/profile", "Student Profile"),
    ]

    for url, expected_text in urls_to_test:
        res = await client.get(url)
        assert res.status_code == 200, f"URL {url} failed with status {res.status_code}"
        assert expected_text in res.text, f"URL {url} missing expected text '{expected_text}'"
        assert 'id="sidebar-drawer"' in res.text, f"URL {url} missing persistent sidebar"
        assert 'class="sidebar"' in res.text, f"URL {url} missing sidebar class"
        assert 'class="nav-link' in res.text, f"URL {url} missing navigation links"
        assert "is-active" in res.text, f"URL {url} missing active navigation indicator"
        assert "/static/css/app.css" in res.text, f"URL {url} missing app.css stylesheet"

    # Specific component assertions:
    # 1. Check-in code input formatting and 6-char slot helper
    res_checkin = await client.get("/student/check-in")
    assert "code-input-lg" in res_checkin.text
    assert "code-slots-helper" in res_checkin.text
    assert "countdown-badge" in res_checkin.text
    assert "pulse-indicator" in res_checkin.text

    # 2. Courses progress bar with threshold indicator marker
    res_courses = await client.get("/student/courses")
    assert "progress-threshold-marker" in res_courses.text
    assert "Required Min" in res_courses.text

    # 3. Profile Student ID badge and copy button
    res_profile = await client.get("/student/profile")
    assert "student-badge" in res_profile.text
    assert "STU-QA-2026" in res_profile.text
    assert "copy-id-btn" in res_profile.text
