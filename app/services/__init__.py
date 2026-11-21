from app.services.auth_service import AuthService, auth_service
from app.services.course_service import CourseService, course_service
from app.services.enrollment_service import EnrollmentService, enrollment_service
from app.services.permissions import ensure_can_manage_course, ensure_can_view_course
from app.services.user_service import UserService, user_service

__all__ = [
    "AuthService",
    "CourseService",
    "EnrollmentService",
    "UserService",
    "auth_service",
    "course_service",
    "enrollment_service",
    "ensure_can_manage_course",
    "ensure_can_view_course",
    "user_service",
]
