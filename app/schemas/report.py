from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import SessionStatus


class StudentCourseSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    student_id: UUID
    student_name: str
    student_email: str
    student_code: str | None = None
    present_count: int
    late_count: int
    absent_count: int
    excused_count: int
    total_sessions: int
    attended_sessions: int
    percentage: float | None = None
    is_at_risk: bool


class CourseReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    course_id: UUID
    course_code: str
    course_title: str
    min_attendance_pct: float
    total_closed_sessions: int
    enrolled_students_count: int
    average_percentage: float | None = None
    average_pct: float | None = None
    students: list[StudentCourseSummary]


class AtRiskStudent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    student_id: UUID
    student_name: str
    student_email: str
    student_code: str | None = None
    percentage: float
    min_attendance_pct: float
    is_at_risk: bool = True


class SessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: UUID
    course_id: UUID
    starts_at: datetime
    ends_at: datetime
    status: SessionStatus
    present_count: int
    late_count: int
    absent_count: int
    excused_count: int
    total_records: int
    attendance_rate: float | None = None


class StudentReportItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    course_id: UUID
    course_code: str
    course_title: str
    min_attendance_pct: float
    present_count: int
    late_count: int
    absent_count: int
    excused_count: int
    total_sessions: int
    attended_sessions: int = 0
    percentage: float | None = None
    is_at_risk: bool


class StudentFullReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    student_id: UUID
    student_name: str
    student_email: str
    courses: list[StudentReportItem]


class DashboardSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_users: int
    users_by_role: dict[str, int]
    active_courses: int
    sessions_today: int
    attendance_rate_today: float | None = None
