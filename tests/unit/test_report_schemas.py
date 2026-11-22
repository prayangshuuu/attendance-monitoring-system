import uuid
from datetime import UTC, datetime

from app.models.enums import SessionStatus
from app.schemas.report import (
    AtRiskStudent,
    CourseReport,
    DashboardSummary,
    SessionSummary,
    StudentCourseSummary,
    StudentFullReport,
    StudentReportItem,
)


def test_student_course_summary_schema() -> None:
    stu_id = uuid.uuid4()
    item = StudentCourseSummary(
        student_id=stu_id,
        student_name="Test Student",
        student_email="student@example.com",
        student_code="STU001",
        present_count=4,
        late_count=1,
        absent_count=0,
        excused_count=0,
        total_sessions=5,
        attended_sessions=5,
        percentage=100.0,
        is_at_risk=False,
    )
    assert item.student_id == stu_id
    assert item.percentage == 100.0
    assert item.is_at_risk is False


def test_course_report_schema() -> None:
    cid = uuid.uuid4()
    report = CourseReport(
        course_id=cid,
        course_code="CS101",
        course_title="Computer Science I",
        min_attendance_pct=80.0,
        total_closed_sessions=4,
        enrolled_students_count=0,
        average_percentage=None,
        average_pct=None,
        students=[],
    )
    assert report.course_code == "CS101"
    assert report.total_closed_sessions == 4
    assert report.average_percentage is None
    assert report.average_pct is None


def test_at_risk_student_schema() -> None:
    sid = uuid.uuid4()
    at_risk = AtRiskStudent(
        student_id=sid,
        student_name="At Risk Student",
        student_email="risk@example.com",
        percentage=75.0,
        min_attendance_pct=80.0,
    )
    assert at_risk.percentage == 75.0
    assert at_risk.is_at_risk is True


def test_session_summary_schema() -> None:
    sid = uuid.uuid4()
    cid = uuid.uuid4()
    now = datetime.now(UTC)
    summary = SessionSummary(
        session_id=sid,
        course_id=cid,
        starts_at=now,
        ends_at=now,
        status=SessionStatus.CLOSED,
        present_count=10,
        late_count=2,
        absent_count=3,
        excused_count=1,
        total_records=16,
        attendance_rate=81.25,
    )
    assert summary.session_id == sid
    assert summary.attendance_rate == 81.25


def test_student_full_report_schema() -> None:
    sid = uuid.uuid4()
    cid = uuid.uuid4()
    report = StudentFullReport(
        student_id=sid,
        student_name="Student",
        student_email="student@example.com",
        courses=[
            StudentReportItem(
                course_id=cid,
                course_code="CS101",
                course_title="Intro",
                min_attendance_pct=80.0,
                present_count=2,
                late_count=0,
                absent_count=1,
                excused_count=0,
                total_sessions=3,
                attended_sessions=2,
                percentage=66.67,
                is_at_risk=True,
            )
        ],
    )
    assert len(report.courses) == 1
    assert report.courses[0].percentage == 66.67
    assert report.courses[0].is_at_risk is True


def test_dashboard_summary_schema() -> None:
    dash = DashboardSummary(
        total_users=10,
        users_by_role={"admin": 1, "teacher": 2, "student": 7},
        active_courses=3,
        sessions_today=2,
        attendance_rate_today=85.0,
    )
    assert dash.total_users == 10
    assert dash.attendance_rate_today == 85.0
