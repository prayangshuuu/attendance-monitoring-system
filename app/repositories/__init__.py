from app.repositories.course_repository import CourseRepository, course_repository
from app.repositories.enrollment_repository import EnrollmentRepository, enrollment_repository
from app.repositories.user_repository import UserRepository, user_repository

__all__ = [
    "CourseRepository",
    "EnrollmentRepository",
    "UserRepository",
    "course_repository",
    "enrollment_repository",
    "user_repository",
]
