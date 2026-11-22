from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AttendanceStatus, SessionStatus, UserRole
from app.repositories.report_repository import report_repository
from app.utils.clock import Clock
from tests.factories import make_course, make_enrollment, make_record, make_session, make_user


class FrozenClock(Clock):
    def __init__(self, frozen_time: datetime) -> None:
        self._time = frozen_time

    def now(self) -> datetime:
        return self._time


@pytest.mark.asyncio
async def test_get_course_report_empty(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    report = await report_repository.get_course_report(db_session, course)
    assert report.course_id == course.id
    assert report.total_closed_sessions == 0
    assert report.enrolled_students_count == 0
    assert report.average_percentage is None
    assert report.average_pct is None
    assert len(report.students) == 0


@pytest.mark.asyncio
async def test_get_course_report_zero_sessions_with_students(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)

    report = await report_repository.get_course_report(db_session, course)
    assert report.total_closed_sessions == 0
    assert report.enrolled_students_count == 1
    assert report.students[0].percentage is None
    assert report.students[0].is_at_risk is False
    assert report.average_percentage is None


@pytest.mark.asyncio
async def test_get_session_summary_empty(db_session: AsyncSession) -> None:
    course = await make_course(db_session)
    session_obj = await make_session(db_session, course=course, status=SessionStatus.CLOSED)

    summary = await report_repository.get_session_summary(db_session, session_obj)
    assert summary.session_id == session_obj.id
    assert summary.total_records == 0
    assert summary.attendance_rate is None

    # With records
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_record(
        db_session, class_session=session_obj, student=student, status=AttendanceStatus.PRESENT
    )
    summary_with_rec = await report_repository.get_session_summary(db_session, session_obj)
    assert summary_with_rec.total_records == 1
    assert summary_with_rec.attendance_rate == 100.0


@pytest.mark.asyncio
async def test_get_student_report_not_enrolled(db_session: AsyncSession) -> None:
    student = await make_user(db_session, role=UserRole.STUDENT)
    report = await report_repository.get_student_report(db_session, student)
    assert report is not None
    assert report.student_id == student.id
    assert len(report.courses) == 0


@pytest.mark.asyncio
async def test_get_student_report_unrelated_teacher(db_session: AsyncSession) -> None:
    student = await make_user(db_session, role=UserRole.STUDENT)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    report = await report_repository.get_student_report(
        db_session, student, teacher_id=other_teacher.id
    )
    assert report is None


@pytest.mark.asyncio
async def test_get_student_report_success(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher, min_attendance_pct=80.0)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)

    sess1 = await make_session(db_session, course=course, status=SessionStatus.CLOSED)
    sess2 = await make_session(db_session, course=course, status=SessionStatus.CLOSED)

    await make_record(
        db_session, class_session=sess1, student=student, status=AttendanceStatus.PRESENT
    )
    await make_record(
        db_session, class_session=sess2, student=student, status=AttendanceStatus.ABSENT
    )

    # Course with 0 closed sessions
    course_zero = await make_course(db_session, teacher=teacher, min_attendance_pct=80.0)
    await make_enrollment(db_session, course=course_zero, student=student)

    report = await report_repository.get_student_report(db_session, student, teacher_id=teacher.id)
    assert report is not None
    assert len(report.courses) == 2
    c_zero = next(c for c in report.courses if c.course_id == course_zero.id)
    assert c_zero.total_sessions == 0
    assert c_zero.percentage is None
    assert c_zero.is_at_risk is False

    c_item = next(c for c in report.courses if c.course_id == course.id)
    assert c_item.course_id == course.id
    assert c_item.total_sessions == 2
    assert c_item.present_count == 1
    assert c_item.absent_count == 1
    assert c_item.percentage == 50.0
    assert c_item.is_at_risk is True


@pytest.mark.asyncio
async def test_get_dashboard_summary(db_session: AsyncSession) -> None:
    now = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)
    clock = FrozenClock(now)

    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)
    course = await make_course(db_session, teacher=teacher, is_active=True)

    # Session today (closed)
    sess_today = await make_session(
        db_session,
        course=course,
        starts_at=now.replace(hour=9, minute=0),
        status=SessionStatus.CLOSED,
    )
    # Session yesterday
    await make_session(
        db_session,
        course=course,
        starts_at=now - timedelta(days=1),
        status=SessionStatus.CLOSED,
    )
    # Session today (cancelled)
    await make_session(
        db_session,
        course=course,
        starts_at=now.replace(hour=14, minute=0),
        status=SessionStatus.CANCELLED,
    )

    await make_record(
        db_session,
        class_session=sess_today,
        student=student,
        status=AttendanceStatus.PRESENT,
    )

    summary = await report_repository.get_dashboard_summary(db_session, clock)
    assert summary.total_users >= 2
    assert summary.users_by_role["teacher"] >= 1
    assert summary.users_by_role["student"] >= 1
    assert summary.active_courses >= 1
    assert summary.sessions_today == 1  # Cancelled excluded
    assert summary.attendance_rate_today == 100.0
