import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.class_session import ClassSession
from app.models.course import Course
from app.models.enums import SessionStatus, UserRole
from app.models.user import User
from app.utils.rate_limiter import in_memory_rate_limiter
from tests.factories import make_course, make_enrollment, make_session, make_user


@dataclass
class MatrixContext:
    admin: User
    teacher_owner: User
    teacher_other: User
    student_enrolled: User
    student_other: User
    course: Course
    scheduled_session: ClassSession
    open_session: ClassSession
    headers: dict[str, dict[str, str]]


@pytest.fixture(autouse=True)
async def reset_rate_limiter() -> None:
    await in_memory_rate_limiter.reset()


@pytest.fixture
def matrix_users() -> None:
    pass


@pytest.fixture
async def matrix_context(
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> MatrixContext:
    admin = await make_user(
        db_session,
        email="matrix_admin@example.com",
        role=UserRole.ADMIN,
    )
    teacher_owner = await make_user(
        db_session,
        email="matrix_teacher_owner@example.com",
        role=UserRole.TEACHER,
    )
    teacher_other = await make_user(
        db_session,
        email="matrix_teacher_other@example.com",
        role=UserRole.TEACHER,
    )
    student_enrolled = await make_user(
        db_session,
        email="matrix_student_enrolled@example.com",
        role=UserRole.STUDENT,
        student_id="STU_MAT1",
    )
    student_other = await make_user(
        db_session,
        email="matrix_student_other@example.com",
        role=UserRole.STUDENT,
        student_id="STU_MAT2",
    )

    course = await make_course(
        db_session,
        teacher=teacher_owner,
        code=f"MTX_{uuid.uuid4().hex[:6].upper()}",
        min_attendance_pct=80.0,
    )
    await make_enrollment(db_session, course=course, student=student_enrolled)

    base_time = datetime(2027, 3, 1, 9, 0, tzinfo=UTC)
    scheduled_session = await make_session(
        db_session,
        course=course,
        starts_at=base_time,
        ends_at=base_time + timedelta(hours=1),
        status=SessionStatus.SCHEDULED.value,
        late_after_minutes=10,
    )
    open_session = await make_session(
        db_session,
        course=course,
        starts_at=base_time + timedelta(days=1),
        ends_at=base_time + timedelta(days=1, hours=1),
        status=SessionStatus.OPEN.value,
        checkin_code="MTX123",
        code_expires_at=base_time + timedelta(days=1, minutes=30),
        late_after_minutes=10,
    )

    headers = {
        "admin": auth_headers(admin),
        "teacher_owner": auth_headers(teacher_owner),
        "teacher_other": auth_headers(teacher_other),
        "student_enrolled": auth_headers(student_enrolled),
        "student_other": auth_headers(student_other),
        "anonymous": {},
    }

    return MatrixContext(
        admin=admin,
        teacher_owner=teacher_owner,
        teacher_other=teacher_other,
        student_enrolled=student_enrolled,
        student_other=student_other,
        course=course,
        scheduled_session=scheduled_session,
        open_session=open_session,
        headers=headers,
    )


PERMISSION_MATRIX: dict[tuple[str, str], dict[str, int]] = {
    ("GET", "/api/v1/users"): {
        "admin": 200,
        "teacher_owner": 403,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("POST", "/api/v1/users"): {
        "admin": 201,
        "teacher_owner": 403,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("POST", "/api/v1/courses"): {
        "admin": 201,
        "teacher_owner": 403,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("GET", "/api/v1/courses/{id}"): {
        "admin": 200,
        "teacher_owner": 200,
        "teacher_other": 403,
        "student_enrolled": 200,
        "student_other": 403,
        "anonymous": 401,
    },
    ("PATCH", "/api/v1/courses/{id}"): {
        "admin": 200,
        "teacher_owner": 403,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("DELETE", "/api/v1/courses/{id}"): {
        "admin": 204,
        "teacher_owner": 403,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("POST", "/api/v1/courses/{id}/enrollments"): {
        "admin": 200,
        "teacher_owner": 200,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("GET", "/api/v1/courses/{id}/students"): {
        "admin": 200,
        "teacher_owner": 200,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("GET", "/api/v1/courses/{id}/sessions"): {
        "admin": 200,
        "teacher_owner": 200,
        "teacher_other": 403,
        "student_enrolled": 200,
        "student_other": 403,
        "anonymous": 401,
    },
    ("POST", "/api/v1/courses/{id}/sessions"): {
        "admin": 201,
        "teacher_owner": 201,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("POST", "/api/v1/sessions/{id}/open"): {
        "admin": 200,
        "teacher_owner": 200,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("POST", "/api/v1/sessions/{id}/close"): {
        "admin": 200,
        "teacher_owner": 200,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("GET", "/api/v1/sessions/{id}/attendance"): {
        "admin": 200,
        "teacher_owner": 200,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("PUT", "/api/v1/sessions/{id}/attendance"): {
        "admin": 200,
        "teacher_owner": 200,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("GET", "/api/v1/reports/courses/{id}"): {
        "admin": 200,
        "teacher_owner": 200,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
    ("GET", "/api/v1/reports/dashboard"): {
        "admin": 200,
        "teacher_owner": 403,
        "teacher_other": 403,
        "student_enrolled": 403,
        "student_other": 403,
        "anonymous": 401,
    },
}

PARAM_CASES = [
    (method, endpoint_template, role, expected_status)
    for (method, endpoint_template), role_statuses in PERMISSION_MATRIX.items()
    for role, expected_status in role_statuses.items()
]


def resolve_endpoint_request(
    method: str,
    endpoint_template: str,
    ctx: MatrixContext,
) -> tuple[str, dict[str, Any] | None]:
    """Resolve endpoint URL template and return appropriate valid test payload."""
    if endpoint_template == "/api/v1/users":
        if method == "GET":
            return "/api/v1/users", None
        return "/api/v1/users", {
            "email": f"matrix_create_{uuid.uuid4().hex[:8]}@example.com",
            "password": "Password123!",
            "full_name": "Matrix New User",
            "role": "student",
            "student_id": f"STU_{uuid.uuid4().hex[:6]}",
        }

    if endpoint_template == "/api/v1/courses":
        return "/api/v1/courses", {
            "code": f"MTRX_{uuid.uuid4().hex[:6].upper()}",
            "title": "Matrix Test Course",
            "teacher_id": str(ctx.teacher_owner.id),
        }

    if endpoint_template == "/api/v1/courses/{id}":
        url = f"/api/v1/courses/{ctx.course.id}"
        if method == "GET":
            return url, None
        if method == "PATCH":
            return url, {"title": "Updated Course Title"}
        if method == "DELETE":
            return url, None

    if endpoint_template == "/api/v1/courses/{id}/enrollments":
        return f"/api/v1/courses/{ctx.course.id}/enrollments", {
            "student_ids": [str(ctx.student_other.id)],
        }

    if endpoint_template == "/api/v1/courses/{id}/students":
        return f"/api/v1/courses/{ctx.course.id}/students", None

    if endpoint_template == "/api/v1/courses/{id}/sessions":
        if method == "GET":
            return f"/api/v1/courses/{ctx.course.id}/sessions", None
        # Unique timestamp far in the future avoiding overlap
        return f"/api/v1/courses/{ctx.course.id}/sessions", {
            "starts_at": "2029-01-01T09:00:00Z",
            "ends_at": "2029-01-01T10:00:00Z",
            "late_after_minutes": 10,
        }

    if endpoint_template == "/api/v1/sessions/{id}/open":
        return f"/api/v1/sessions/{ctx.scheduled_session.id}/open", None

    if endpoint_template == "/api/v1/sessions/{id}/close":
        return f"/api/v1/sessions/{ctx.open_session.id}/close", None

    if endpoint_template == "/api/v1/sessions/{id}/attendance":
        if method == "GET":
            return f"/api/v1/sessions/{ctx.open_session.id}/attendance", None
        return f"/api/v1/sessions/{ctx.open_session.id}/attendance", {
            "items": [{"student_id": str(ctx.student_enrolled.id), "status": "present"}],
        }

    if endpoint_template == "/api/v1/reports/courses/{id}":
        return f"/api/v1/reports/courses/{ctx.course.id}", None

    if endpoint_template == "/api/v1/reports/dashboard":
        return "/api/v1/reports/dashboard", None

    raise ValueError(f"Unrecognized endpoint template: {method} {endpoint_template}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "endpoint_template", "role", "expected_status"),
    PARAM_CASES,
    ids=[f"{m} {ep}-{r}" for m, ep, r, _ in PARAM_CASES],
)
async def test_endpoint_permission_matrix(
    client: AsyncClient,
    matrix_context: MatrixContext,
    method: str,
    endpoint_template: str,
    role: str,
    expected_status: int,
) -> None:
    """Parametrized permission matrix test asserting access control per role."""
    url, payload = resolve_endpoint_request(method, endpoint_template, matrix_context)
    headers = matrix_context.headers[role]

    response = await client.request(
        method=method,
        url=url,
        headers=headers,
        json=payload,
    )

    assert response.status_code == expected_status, (
        f"Assertion failure on {method} {endpoint_template} for role '{role}'. "
        f"Expected status {expected_status}, received {response.status_code}. "
        f"Response body: {response.text}"
    )
