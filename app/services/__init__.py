from app.services.auth_service import AuthService, auth_service
from app.services.course_service import CourseService, course_service
from app.services.enrollment_service import EnrollmentService, enrollment_service
from app.services.permissions import ensure_can_manage_course, ensure_can_view_course
from app.services.session_service import SessionService, session_service
from app.services.user_service import UserService, user_service

__all__ = [
    "AuthService",
    "CourseService",
    "EnrollmentService",
    "SessionService",
    "UserService",
    "auth_service",
    "course_service",
    "enrollment_service",
    "ensure_can_manage_course",
    "ensure_can_view_course",
    "session_service",
    "user_service",
]
