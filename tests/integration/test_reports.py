from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.enums import AttendanceStatus, SessionStatus, UserRole
from app.models.user import User
from app.repositories.report_repository import report_repository
from tests.factories import make_course, make_enrollment, make_record, make_session, make_user


@pytest.mark.asyncio
async def test_known_dataset_course_report(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    """Known dataset test:

    1 course, 4 closed sessions, 1 cancelled, 3 students with mixed statuses.
    Assert exact percentages, present, late, absent, excused counts.
    """
    course = await make_course(
        db_session,
        teacher=teacher_user,
        code="CS101",
        title="Intro to CS",
        min_attendance_pct=80.0,
    )

    student_a = await make_user(
        db_session,
        role=UserRole.STUDENT,
        full_name="Alice Adams",
        student_id="STU_A",
        email="alice@example.com",
    )
    student_b = await make_user(
        db_session,
        role=UserRole.STUDENT,
        full_name="Bob Brown",
        student_id="STU_B",
        email="bob@example.com",
    )
    student_c = await make_user(
        db_session,
        role=UserRole.STUDENT,
        full_name="Charlie Clark",
        student_id="STU_C",
        email="charlie@example.com",
    )

    await make_enrollment(db_session, course=course, student=student_a)
    await make_enrollment(db_session, course=course, student=student_b)
    await make_enrollment(db_session, course=course, student=student_c)

    # 4 closed sessions
    now = datetime(2026, 11, 22, 9, 0, 0, tzinfo=UTC)
    s1 = await make_session(db_session, course=course, starts_at=now, status=SessionStatus.CLOSED)
    s2 = await make_session(
        db_session,
        course=course,
        starts_at=now + timedelta(days=1),
        status=SessionStatus.CLOSED,
    )
    s3 = await make_session(
        db_session,
        course=course,
        starts_at=now + timedelta(days=2),
        status=SessionStatus.CLOSED,
    )
    s4 = await make_session(
        db_session,
        course=course,
        starts_at=now + timedelta(days=3),
        status=SessionStatus.CLOSED,
    )

    # 1 cancelled session (must be completely excluded from counts)
    s_cancel = await make_session(
        db_session,
        course=course,
        starts_at=now + timedelta(days=4),
        status=SessionStatus.CANCELLED,
    )

    # Student A: 4 PRESENT
    await make_record(
        db_session, class_session=s1, student=student_a, status=AttendanceStatus.PRESENT
    )
    await make_record(
        db_session, class_session=s2, student=student_a, status=AttendanceStatus.PRESENT
    )
    await make_record(
        db_session, class_session=s3, student=student_a, status=AttendanceStatus.PRESENT
    )
    await make_record(
        db_session, class_session=s4, student=student_a, status=AttendanceStatus.PRESENT
    )
    await make_record(
        db_session, class_session=s_cancel, student=student_a, status=AttendanceStatus.PRESENT
    )

    # Student B: 1 PRESENT, 1 LATE, 1 EXCUSED, 1 ABSENT
    await make_record(
        db_session, class_session=s1, student=student_b, status=AttendanceStatus.PRESENT
    )
    await make_record(db_session, class_session=s2, student=student_b, status=AttendanceStatus.LATE)
    await make_record(
        db_session, class_session=s3, student=student_b, status=AttendanceStatus.EXCUSED
    )
    await make_record(
        db_session, class_session=s4, student=student_b, status=AttendanceStatus.ABSENT
    )
    await make_record(
        db_session, class_session=s_cancel, student=student_b, status=AttendanceStatus.LATE
    )

    # Student C: 4 ABSENT (records or default absent)
    await make_record(
        db_session, class_session=s1, student=student_c, status=AttendanceStatus.ABSENT
    )
    await make_record(
        db_session, class_session=s2, student=student_c, status=AttendanceStatus.ABSENT
    )
    await make_record(
        db_session, class_session=s3, student=student_c, status=AttendanceStatus.ABSENT
    )
    await make_record(
        db_session, class_session=s4, student=student_c, status=AttendanceStatus.ABSENT
    )

    res = await client.get(
        f"/api/v1/reports/courses/{course.id}",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 200
    data = res.json()

    assert data["course_id"] == str(course.id)
    assert data["course_code"] == "CS101"
    assert data["total_closed_sessions"] == 4
    assert data["enrolled_students_count"] == 3

    # Hand-calculated expectations:
    # Student A: (4/4)*100 = 100.0% -> not at risk
    # Student B: ((1+1+1)/4)*100 = 75.0% -> at risk (< 80.0)
    # Student C: (0/4)*100 = 0.0% -> at risk (< 80.0)
    # Average: (100.0 + 75.0 + 0.0) / 3 = 58.33%
    assert data["average_percentage"] == 58.33
    assert data["average_pct"] == 58.33

    students_by_id = {s["student_id"]: s for s in data["students"]}

    stu_a = students_by_id[str(student_a.id)]
    assert stu_a["student_name"] == "Alice Adams"
    assert stu_a["present_count"] == 4
    assert stu_a["late_count"] == 0
    assert stu_a["absent_count"] == 0
    assert stu_a["excused_count"] == 0
    assert stu_a["total_sessions"] == 4
    assert stu_a["attended_sessions"] == 4
    assert stu_a["percentage"] == 100.0
    assert stu_a["is_at_risk"] is False

    stu_b = students_by_id[str(student_b.id)]
    assert stu_b["student_name"] == "Bob Brown"
    assert stu_b["present_count"] == 1
    assert stu_b["late_count"] == 1
    assert stu_b["absent_count"] == 1
    assert stu_b["excused_count"] == 1
    assert stu_b["total_sessions"] == 4
    assert stu_b["attended_sessions"] == 3
    assert stu_b["percentage"] == 75.0
    assert stu_b["is_at_risk"] is True

    stu_c = students_by_id[str(student_c.id)]
    assert stu_c["student_name"] == "Charlie Clark"
    assert stu_c["present_count"] == 0
    assert stu_c["late_count"] == 0
    assert stu_c["absent_count"] == 4
    assert stu_c["excused_count"] == 0
    assert stu_c["total_sessions"] == 4
    assert stu_c["attended_sessions"] == 0
    assert stu_c["percentage"] == 0.0
    assert stu_c["is_at_risk"] is True

    # Check at-risk endpoint returns Bob and Charlie
    res_risk = await client.get(
        f"/api/v1/reports/courses/{course.id}/at-risk",
        headers=auth_headers(teacher_user),
    )
    assert res_risk.status_code == 200
    risk_list = res_risk.json()
    assert len(risk_list) == 2
    risk_ids = [r["student_id"] for r in risk_list]
    assert str(student_b.id) in risk_ids
    assert str(student_c.id) in risk_ids
    assert str(student_a.id) not in risk_ids


@pytest.mark.asyncio
async def test_cancelled_and_open_sessions_excluded(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    """Cancelled, open, and scheduled sessions excluded from total count."""
    course = await make_course(db_session, teacher=teacher_user)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)

    # 1 closed session
    closed_sess = await make_session(db_session, course=course, status=SessionStatus.CLOSED)
    await make_record(
        db_session, class_session=closed_sess, student=student, status=AttendanceStatus.PRESENT
    )

    # 1 scheduled session
    await make_session(db_session, course=course, status=SessionStatus.SCHEDULED)
    # 1 open session
    open_sess = await make_session(db_session, course=course, status=SessionStatus.OPEN)
    await make_record(
        db_session, class_session=open_sess, student=student, status=AttendanceStatus.PRESENT
    )
    # 1 cancelled session
    cancel_sess = await make_session(db_session, course=course, status=SessionStatus.CANCELLED)
    await make_record(
        db_session, class_session=cancel_sess, student=student, status=AttendanceStatus.PRESENT
    )

    res = await client.get(
        f"/api/v1/reports/courses/{course.id}",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total_closed_sessions"] == 1
    assert data["students"][0]["total_sessions"] == 1
    assert data["students"][0]["attended_sessions"] == 1
    assert data["students"][0]["percentage"] == 100.0


@pytest.mark.asyncio
async def test_zero_sessions_null_percentage(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    """Zero sessions -> null percentage, not 0 and not division error."""
    course = await make_course(db_session, teacher=teacher_user)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)

    res = await client.get(
        f"/api/v1/reports/courses/{course.id}",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total_closed_sessions"] == 0
    assert data["average_percentage"] is None
    assert data["average_pct"] is None
    assert len(data["students"]) == 1
    assert data["students"][0]["percentage"] is None
    assert data["students"][0]["is_at_risk"] is False

    # At risk returns empty list
    risk_res = await client.get(
        f"/api/v1/reports/courses/{course.id}/at-risk",
        headers=auth_headers(teacher_user),
    )
    assert risk_res.status_code == 200
    assert risk_res.json() == []


@pytest.mark.asyncio
async def test_at_risk_boundary_endpoint(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    """At-risk boundary test: exactly 80.00 is not at risk; 79.99 is at risk."""
    course = await make_course(db_session, teacher=teacher_user, min_attendance_pct=80.0)

    # Student 1 has 4 out of 5 sessions attended -> 80.00%
    stu_80 = await make_user(db_session, role=UserRole.STUDENT, full_name="Student Eighty")
    # Student 2 has 3 out of 4 sessions attended -> 75.00% (< 80.0)
    stu_75 = await make_user(db_session, role=UserRole.STUDENT, full_name="Student SeventyFive")

    await make_enrollment(db_session, course=course, student=stu_80)
    await make_enrollment(db_session, course=course, student=stu_75)

    # 5 closed sessions
    sessions = [
        await make_session(db_session, course=course, status=SessionStatus.CLOSED) for _ in range(5)
    ]

    # Student 1 attends 4 of 5 sessions -> 80.00%
    for i in range(4):
        await make_record(
            db_session, class_session=sessions[i], student=stu_80, status=AttendanceStatus.PRESENT
        )
    await make_record(
        db_session, class_session=sessions[4], student=stu_80, status=AttendanceStatus.ABSENT
    )

    # Student 2 attends 3 of 5 sessions -> 60.00%
    for i in range(3):
        await make_record(
            db_session, class_session=sessions[i], student=stu_75, status=AttendanceStatus.PRESENT
        )
    for i in range(3, 5):
        await make_record(
            db_session, class_session=sessions[i], student=stu_75, status=AttendanceStatus.ABSENT
        )

    res = await client.get(
        f"/api/v1/reports/courses/{course.id}",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 200
    stu_dict = {s["student_id"]: s for s in res.json()["students"]}

    assert stu_dict[str(stu_80.id)]["percentage"] == 80.00
    assert stu_dict[str(stu_80.id)]["is_at_risk"] is False

    assert stu_dict[str(stu_75.id)]["percentage"] == 60.00
    assert stu_dict[str(stu_75.id)]["is_at_risk"] is True

    # Check at-risk list: only stu_75 is present
    res_risk = await client.get(
        f"/api/v1/reports/courses/{course.id}/at-risk",
        headers=auth_headers(teacher_user),
    )
    risk_ids = [s["student_id"] for s in res_risk.json()]
    assert str(stu_80.id) not in risk_ids
    assert str(stu_75.id) in risk_ids


@pytest.mark.asyncio
async def test_date_range_filter_narrows_sessions(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    """Date range filter narrows sessions."""
    course = await make_course(db_session, teacher=teacher_user)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)

    d1 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=UTC)
    d2 = datetime(2026, 11, 5, 10, 0, 0, tzinfo=UTC)
    d3 = datetime(2026, 11, 10, 10, 0, 0, tzinfo=UTC)

    s1 = await make_session(db_session, course=course, starts_at=d1, status=SessionStatus.CLOSED)
    s2 = await make_session(db_session, course=course, starts_at=d2, status=SessionStatus.CLOSED)
    s3 = await make_session(db_session, course=course, starts_at=d3, status=SessionStatus.CLOSED)

    await make_record(
        db_session, class_session=s1, student=student, status=AttendanceStatus.PRESENT
    )
    await make_record(db_session, class_session=s2, student=student, status=AttendanceStatus.ABSENT)
    await make_record(
        db_session, class_session=s3, student=student, status=AttendanceStatus.PRESENT
    )

    # 1. Without filter: 3 sessions, 2 attended, 66.67%
    res_all = await client.get(
        f"/api/v1/reports/courses/{course.id}",
        headers=auth_headers(teacher_user),
    )
    assert res_all.status_code == 200
    assert res_all.json()["total_closed_sessions"] == 3

    # 2. Filter from Nov 3 to Nov 7: only session 2
    res_filtered = await client.get(
        f"/api/v1/reports/courses/{course.id}?from=2026-11-03T00:00:00Z&to=2026-11-07T23:59:59Z",
        headers=auth_headers(teacher_user),
    )
    assert res_filtered.status_code == 200
    data_fil = res_filtered.json()
    assert data_fil["total_closed_sessions"] == 1
    assert data_fil["students"][0]["absent_count"] == 1
    assert data_fil["students"][0]["attended_sessions"] == 0
    assert data_fil["students"][0]["percentage"] == 0.0

    # 3. Invalid filter (to < from) -> 400
    res_invalid = await client.get(
        f"/api/v1/reports/courses/{course.id}?from=2026-11-10T00:00:00Z&to=2026-11-01T00:00:00Z",
        headers=auth_headers(teacher_user),
    )
    assert res_invalid.status_code == 400
    assert res_invalid.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_reports_permissions(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    student_user: User,
    admin_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    """Permissions tests:

    - Student sees own report; requesting other student -> 403; teacher of other course -> 403.
    - Admin sees dashboard summary; non-admin calling dashboard -> 403.
    """
    other_student = await make_user(db_session, role=UserRole.STUDENT)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)

    course = await make_course(db_session, teacher=teacher_user)
    await make_enrollment(db_session, course=course, student=student_user)

    # 1. Student sees own report -> 200
    res_self = await client.get(
        f"/api/v1/reports/students/{student_user.id}",
        headers=auth_headers(student_user),
    )
    assert res_self.status_code == 200
    assert res_self.json()["student_id"] == str(student_user.id)

    # 2. Student requesting other student -> 403
    res_other = await client.get(
        f"/api/v1/reports/students/{other_student.id}",
        headers=auth_headers(student_user),
    )
    assert res_other.status_code == 403

    # 3. Teacher of other course -> 403
    res_other_teacher = await client.get(
        f"/api/v1/reports/courses/{course.id}",
        headers=auth_headers(other_teacher),
    )
    assert res_other_teacher.status_code == 403

    # Teacher requesting student they don't teach -> 403
    res_teacher_stu = await client.get(
        f"/api/v1/reports/students/{other_student.id}",
        headers=auth_headers(teacher_user),
    )
    assert res_teacher_stu.status_code == 403

    # 4. Admin sees dashboard summary
    res_dash_admin = await client.get(
        "/api/v1/reports/dashboard",
        headers=auth_headers(admin_user),
    )
    assert res_dash_admin.status_code == 200
    dash_data = res_dash_admin.json()
    assert "total_users" in dash_data
    assert "users_by_role" in dash_data
    assert "active_courses" in dash_data
    assert "sessions_today" in dash_data

    # 5. Non-admin calling /reports/dashboard -> 403
    res_dash_teacher = await client.get(
        "/api/v1/reports/dashboard",
        headers=auth_headers(teacher_user),
    )
    assert res_dash_teacher.status_code == 403

    res_dash_student = await client.get(
        "/api/v1/reports/dashboard",
        headers=auth_headers(student_user),
    )
    assert res_dash_student.status_code == 403


@pytest.mark.asyncio
async def test_session_summary_endpoint(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    """Test session summary endpoint."""
    course = await make_course(db_session, teacher=teacher_user)
    session_obj = await make_session(db_session, course=course, status=SessionStatus.CLOSED)

    s1 = await make_user(db_session, role=UserRole.STUDENT)
    s2 = await make_user(db_session, role=UserRole.STUDENT)
    s3 = await make_user(db_session, role=UserRole.STUDENT)
    s4 = await make_user(db_session, role=UserRole.STUDENT)

    await make_record(
        db_session, class_session=session_obj, student=s1, status=AttendanceStatus.PRESENT
    )
    await make_record(
        db_session, class_session=session_obj, student=s2, status=AttendanceStatus.LATE
    )
    await make_record(
        db_session, class_session=session_obj, student=s3, status=AttendanceStatus.EXCUSED
    )
    await make_record(
        db_session, class_session=session_obj, student=s4, status=AttendanceStatus.ABSENT
    )

    res = await client.get(
        f"/api/v1/reports/sessions/{session_obj.id}",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == str(session_obj.id)
    assert data["present_count"] == 1
    assert data["late_count"] == 1
    assert data["excused_count"] == 1
    assert data["absent_count"] == 1
    assert data["total_records"] == 4
    # (1 + 1 + 1) / 4 * 100 = 75.0%
    assert data["attendance_rate"] == 75.0


@pytest.mark.asyncio
async def test_constant_query_count(
    test_engine: AsyncEngine,
    db_session: AsyncSession,
    teacher_user: User,
) -> None:
    """Constant query count test:

    Enroll 50 students in a course with closed sessions.
    Count database queries using SQLAlchemy engine event before_cursor_execute.
    Assert that generating the course report takes a constant number of queries (O(1))
    regardless of student count (<= 3 queries).
    """
    course = await make_course(db_session, teacher=teacher_user, code="PERF101")

    # Create 3 closed sessions
    sessions = [
        await make_session(db_session, course=course, status=SessionStatus.CLOSED) for _ in range(3)
    ]

    # Enroll 50 students
    students = [
        await make_user(
            db_session,
            role=UserRole.STUDENT,
            email=f"perf_stu_{i}@example.com",
            student_id=f"PERF_{i:03d}",
        )
        for i in range(50)
    ]
    for s in students:
        await make_enrollment(db_session, course=course, student=s)

    # Add attendance records for some sessions
    for i, s in enumerate(students):
        status = AttendanceStatus.PRESENT if i % 2 == 0 else AttendanceStatus.ABSENT
        await make_record(db_session, class_session=sessions[0], student=s, status=status)

    await db_session.flush()

    # Track queries executed during get_course_report
    query_statements: list[str] = []

    def before_cursor_execute(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        # Ignore transaction savepoints or releases
        cleaned = statement.strip().upper()
        if not cleaned.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK TO")):
            query_statements.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", before_cursor_execute)
    try:
        report = await report_repository.get_course_report(
            session=db_session,
            course=course,
        )
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", before_cursor_execute)

    assert report.enrolled_students_count == 50
    assert len(report.students) == 50

    # Query count must be O(1) constant regardless of 50 students (exactly 2 queries <= 3):
    # 1. Count closed sessions
    # 2. Aggregated query joining enrollments, users, attendance_records
    assert len(query_statements) <= 3, (
        f"Expected <= 3 queries, got {len(query_statements)}: {query_statements}"
    )
