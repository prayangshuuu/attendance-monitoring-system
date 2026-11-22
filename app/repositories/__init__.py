from app.repositories.attendance_repository import AttendanceRepository, attendance_repository
from app.repositories.course_repository import CourseRepository, course_repository
from app.repositories.enrollment_repository import EnrollmentRepository, enrollment_repository
from app.repositories.report_repository import ReportRepository, report_repository
from app.repositories.session_repository import SessionRepository, session_repository
from app.repositories.user_repository import UserRepository, user_repository

__all__ = [
    "AttendanceRepository",
    "CourseRepository",
    "EnrollmentRepository",
    "ReportRepository",
    "SessionRepository",
    "UserRepository",
    "attendance_repository",
    "course_repository",
    "enrollment_repository",
    "report_repository",
    "session_repository",
    "user_repository",
]
