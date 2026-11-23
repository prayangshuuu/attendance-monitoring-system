from app.web.auth import router as auth_router
from app.web.deps import (
    get_current_web_user,
    get_optional_web_user,
    render_template,
    require_web_admin,
    require_web_student,
    require_web_teacher,
    templates,
    verify_csrf,
)
from app.web.student import router as student_router
from app.web.teacher import router as teacher_router

__all__ = [
    "auth_router",
    "get_current_web_user",
    "get_optional_web_user",
    "render_template",
    "require_web_admin",
    "require_web_student",
    "require_web_teacher",
    "student_router",
    "teacher_router",
    "templates",
    "verify_csrf",
]
