from app.core.exceptions import PermissionDeniedError
from app.models.course import Course
from app.models.enums import UserRole
from app.models.user import User


def ensure_can_manage_course(user: User, course: Course) -> None:
    """Ensure user is an admin or the teacher of the course."""
    if user.role == UserRole.ADMIN or course.teacher_id == user.id:
        return
    raise PermissionDeniedError(
        "You do not have permission to manage this course",
        code="PERMISSION_DENIED",
    )


def ensure_can_view_course(user: User, course: Course, is_enrolled: bool = False) -> None:
    """Ensure user is an admin, the course's teacher, or an enrolled student."""
    if user.role == UserRole.ADMIN or course.teacher_id == user.id or is_enrolled:
        return
    raise PermissionDeniedError(
        "You do not have permission to view this course",
        code="PERMISSION_DENIED",
    )
