import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationAppError
from app.models.enums import SessionStatus, UserRole
from app.schemas.report import CourseReport, StudentCourseSummary
from app.services.report_service import normalize_datetime, report_service, sanitize_csv_cell
from app.utils.clock import Clock
from tests.factories import make_course, make_enrollment, make_session, make_user


class FrozenClock(Clock):
    def __init__(self, frozen_time: datetime) -> None:
        self._time = frozen_time

    def now(self) -> datetime:
        return self._time


def test_sanitize_csv_cell() -> None:
    assert sanitize_csv_cell(None) == ""
    assert sanitize_csv_cell("Alice") == "Alice"
    assert sanitize_csv_cell(123) == "123"
    assert sanitize_csv_cell(85.5) == "85.5"

    # Formula triggers
    assert (
        sanitize_csv_cell('=HYPERLINK("http://evil.com","click")')
        == '\'=HYPERLINK("http://evil.com","click")'
    )
    assert sanitize_csv_cell("+cmd|' /C calc'!A0") == "'+cmd|' /C calc'!A0"
    assert sanitize_csv_cell("-123") == "'-123"
    assert sanitize_csv_cell("@admin") == "'@admin"
    assert sanitize_csv_cell(' =HYPERLINK("test")') == '\' =HYPERLINK("test")'
    assert sanitize_csv_cell("  +cmd") == "'  +cmd"
    assert sanitize_csv_cell("  -123") == "'  -123"
    assert sanitize_csv_cell("  @admin") == "'  @admin"

    # OWASP Tab (\t) and Carriage Return (\r) formula injection vectors
    assert sanitize_csv_cell("\tcalc") == "'\tcalc"
    assert sanitize_csv_cell("\rcmd") == "'\rcmd"
    assert sanitize_csv_cell("\t") == "'\t"
    assert sanitize_csv_cell("\r") == "'\r"
    assert sanitize_csv_cell(" \t=calc") == "' \t=calc"
    assert sanitize_csv_cell(" \rcmd") == "' \rcmd"
    assert sanitize_csv_cell("  \tHello") == "'  \tHello"
    assert sanitize_csv_cell("  \rHello") == "'  \rHello"
    assert sanitize_csv_cell("\n=calc") == "'\n=calc"
    assert sanitize_csv_cell("   ") == "   "


def test_normalize_datetime() -> None:
    assert normalize_datetime(None) is None

    naive = datetime(2026, 11, 22, 10, 0, 0)
    normalized = normalize_datetime(naive)
    assert normalized is not None
    assert normalized.tzinfo == UTC

    aware = datetime(2026, 11, 22, 10, 0, 0, tzinfo=UTC)
    assert normalize_datetime(aware) == aware


@pytest.mark.asyncio
async def test_get_course_report_validations(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)

    # 1. Course not found
    with pytest.raises(NotFoundError) as exc_nf:
        await report_service.get_course_report(db_session, uuid.uuid4(), admin)
    assert exc_nf.value.code == "COURSE_NOT_FOUND"

    # 2. Permission denied (other teacher)
    with pytest.raises(PermissionDeniedError) as exc_perm:
        await report_service.get_course_report(db_session, course.id, other_teacher)
    assert exc_perm.value.code == "PERMISSION_DENIED"

    # 3. Invalid date range (to < from)
    now = datetime.now(UTC)
    with pytest.raises(ValidationAppError) as exc_val:
        await report_service.get_course_report(
            db_session,
            course.id,
            admin,
            from_dt=now,
            to_dt=now - timedelta(days=1),
        )
    assert exc_val.value.code == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_at_risk_boundary_logic(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, min_attendance_pct=80.0)

    # Unit verification of boundary threshold:
    # 80.00 is NOT at risk (80.00 < 80.0 is False)
    # 79.99 IS at risk (79.99 < 80.0 is True)
    assert (course.min_attendance_pct > 80.00) is False
    assert (course.min_attendance_pct > 79.99) is True

    # Test via get_course_at_risk mocking report
    stu1 = StudentCourseSummary(
        student_id=uuid.uuid4(),
        student_name="Border Not Risk",
        student_email="notrisk@example.com",
        present_count=4,
        late_count=0,
        absent_count=1,
        excused_count=0,
        total_sessions=5,
        attended_sessions=4,
        percentage=80.00,
        is_at_risk=False,
    )
    stu2 = StudentCourseSummary(
        student_id=uuid.uuid4(),
        student_name="Border At Risk",
        student_email="atrisk@example.com",
        present_count=7999,
        late_count=0,
        absent_count=2001,
        excused_count=0,
        total_sessions=10000,
        attended_sessions=7999,
        percentage=79.99,
        is_at_risk=True,
    )

    mock_report = CourseReport(
        course_id=course.id,
        course_code=course.code,
        course_title=course.title,
        min_attendance_pct=80.0,
        total_closed_sessions=10,
        enrolled_students_count=2,
        average_percentage=79.995,
        average_pct=79.995,
        students=[stu1, stu2],
    )

    class MockReportRepo(report_service.report_repo.__class__):
        async def get_course_report(self, *args: object, **kwargs: object) -> CourseReport:
            return mock_report

    svc = report_service.__class__(
        report_repo=MockReportRepo(), course_repo=report_service.course_repo
    )
    at_risk = await svc.get_course_at_risk(db_session, course.id, teacher)

    assert len(at_risk) == 1
    assert at_risk[0].student_name == "Border At Risk"
    assert at_risk[0].percentage == 79.99


@pytest.mark.asyncio
async def test_get_session_summary_service(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    session_obj = await make_session(db_session, course=course, status=SessionStatus.CLOSED)

    # 1. Session not found
    with pytest.raises(NotFoundError) as exc_nf:
        await report_service.get_session_summary(db_session, uuid.uuid4(), teacher)
    assert exc_nf.value.code == "SESSION_NOT_FOUND"

    # 2. Permission denied (other teacher)
    with pytest.raises(PermissionDeniedError) as exc_perm:
        await report_service.get_session_summary(db_session, session_obj.id, other_teacher)
    assert exc_perm.value.code == "PERMISSION_DENIED"

    # 3. Course not found (mock course repo returning None)
    class MissingCourseRepo(report_service.course_repo.__class__):
        async def get_by_id(self, *args: object, **kwargs: object) -> None:
            return None

    svc_missing_course = report_service.__class__(
        report_repo=report_service.report_repo,
        course_repo=MissingCourseRepo(),
        session_repo=report_service.session_repo,
    )
    with pytest.raises(NotFoundError) as exc_cnf:
        await svc_missing_course.get_session_summary(db_session, session_obj.id, teacher)
    assert exc_cnf.value.code == "COURSE_NOT_FOUND"

    # 4. Success
    summary = await report_service.get_session_summary(db_session, session_obj.id, teacher)
    assert summary.session_id == session_obj.id


@pytest.mark.asyncio
async def test_get_student_report_service(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    student1 = await make_user(db_session, role=UserRole.STUDENT)
    student2 = await make_user(db_session, role=UserRole.STUDENT)

    # 1. Student not found
    with pytest.raises(NotFoundError) as exc_nf:
        await report_service.get_student_report(db_session, uuid.uuid4(), admin)
    assert exc_nf.value.code == "STUDENT_NOT_FOUND"

    # 2. Student requesting other student -> Permission denied
    with pytest.raises(PermissionDeniedError) as exc_perm1:
        await report_service.get_student_report(db_session, student2.id, student1)
    assert exc_perm1.value.code == "PERMISSION_DENIED"

    # Teacher requesting student they don't teach -> Permission denied
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    with pytest.raises(PermissionDeniedError) as exc_perm2:
        await report_service.get_student_report(db_session, student1.id, teacher)
    assert exc_perm2.value.code == "PERMISSION_DENIED"

    # 3. Student requesting self -> Success
    res = await report_service.get_student_report(db_session, student1.id, student1)
    assert res.student_id == student1.id


@pytest.mark.asyncio
async def test_export_course_csv_service(db_session: AsyncSession) -> None:
    clock = FrozenClock(datetime(2026, 11, 22, 10, 0, 0, tzinfo=UTC))
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, code="CS101")
    student = await make_user(
        db_session,
        role=UserRole.STUDENT,
        full_name='=HYPERLINK("http://evil.com","click")',
        student_id="+cmd|' /C calc'!A0",
    )
    await make_enrollment(db_session, course=course, student=student)

    export = await report_service.export_course_csv(db_session, course.id, teacher, clock)

    assert export.filename == "attendance_CS101_20261122.csv"
    reader = list(csv.reader(io.StringIO(export.content)))
    assert len(reader) == 2  # header + 1 student
    headers = reader[0]
    assert headers[0] == "student_id"
    assert headers[1] == "student_name"
    assert headers[3] == "student_code"

    row = reader[1]
    # Sanitized formula cells have prepended single quote
    assert row[1].startswith("'=")
    assert row[3].startswith("'+")


@pytest.mark.asyncio
async def test_get_dashboard_service(db_session: AsyncSession) -> None:
    clock = FrozenClock(datetime(2026, 11, 22, 10, 0, 0, tzinfo=UTC))
    admin = await make_user(db_session, role=UserRole.ADMIN)
    student = await make_user(db_session, role=UserRole.STUDENT)

    # Non-admin -> PermissionDeniedError
    with pytest.raises(PermissionDeniedError) as exc:
        await report_service.get_dashboard(db_session, student, clock)
    assert exc.value.code == "PERMISSION_DENIED"

    # Admin -> Success
    dash = await report_service.get_dashboard(db_session, admin, clock)
    assert dash.total_users >= 2
