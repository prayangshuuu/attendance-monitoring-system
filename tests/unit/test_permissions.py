import uuid

import pytest

from app.core.exceptions import PermissionDeniedError
from app.models.course import Course
from app.models.enums import UserRole
from app.models.user import User
from app.services.permissions import ensure_can_manage_course, ensure_can_view_course


def test_ensure_can_manage_course_admin() -> None:
    admin = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        full_name="Admin",
        role=UserRole.ADMIN,
        hashed_password="",
        is_active=True,
    )
    course = Course(
        id=uuid.uuid4(),
        code="CS101",
        title="Intro CS",
        teacher_id=uuid.uuid4(),
        min_attendance_pct=80.0,
        is_active=True,
    )
    # Should not raise
    ensure_can_manage_course(admin, course)


def test_ensure_can_manage_course_teacher_owner() -> None:
    teacher_id = uuid.uuid4()
    teacher = User(
        id=teacher_id,
        email="teacher@example.com",
        full_name="Teacher",
        role=UserRole.TEACHER,
        hashed_password="",
        is_active=True,
    )
    course = Course(
        id=uuid.uuid4(),
        code="CS101",
        title="Intro CS",
        teacher_id=teacher_id,
        min_attendance_pct=80.0,
        is_active=True,
    )
    # Should not raise
    ensure_can_manage_course(teacher, course)


def test_ensure_can_manage_course_teacher_other() -> None:
    teacher = User(
        id=uuid.uuid4(),
        email="teacher2@example.com",
        full_name="Other Teacher",
        role=UserRole.TEACHER,
        hashed_password="",
        is_active=True,
    )
    course = Course(
        id=uuid.uuid4(),
        code="CS101",
        title="Intro CS",
        teacher_id=uuid.uuid4(),
        min_attendance_pct=80.0,
        is_active=True,
    )
    with pytest.raises(PermissionDeniedError) as exc_info:
        ensure_can_manage_course(teacher, course)
    assert exc_info.value.status_code == 403


def test_ensure_can_manage_course_student() -> None:
    student = User(
        id=uuid.uuid4(),
        email="student@example.com",
        full_name="Student",
        role=UserRole.STUDENT,
        hashed_password="",
        is_active=True,
    )
    course = Course(
        id=uuid.uuid4(),
        code="CS101",
        title="Intro CS",
        teacher_id=uuid.uuid4(),
        min_attendance_pct=80.0,
        is_active=True,
    )
    with pytest.raises(PermissionDeniedError) as exc_info:
        ensure_can_manage_course(student, course)
    assert exc_info.value.status_code == 403


def test_ensure_can_view_course_admin() -> None:
    admin = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        full_name="Admin",
        role=UserRole.ADMIN,
        hashed_password="",
        is_active=True,
    )
    course = Course(
        id=uuid.uuid4(),
        code="CS101",
        title="Intro CS",
        teacher_id=uuid.uuid4(),
        min_attendance_pct=80.0,
        is_active=True,
    )
    ensure_can_view_course(admin, course, is_enrolled=False)


def test_ensure_can_view_course_teacher_owner() -> None:
    teacher_id = uuid.uuid4()
    teacher = User(
        id=teacher_id,
        email="teacher@example.com",
        full_name="Teacher",
        role=UserRole.TEACHER,
        hashed_password="",
        is_active=True,
    )
    course = Course(
        id=uuid.uuid4(),
        code="CS101",
        title="Intro CS",
        teacher_id=teacher_id,
        min_attendance_pct=80.0,
        is_active=True,
    )
    ensure_can_view_course(teacher, course, is_enrolled=False)


def test_ensure_can_view_course_student_enrolled() -> None:
    student = User(
        id=uuid.uuid4(),
        email="student@example.com",
        full_name="Student",
        role=UserRole.STUDENT,
        hashed_password="",
        is_active=True,
    )
    course = Course(
        id=uuid.uuid4(),
        code="CS101",
        title="Intro CS",
        teacher_id=uuid.uuid4(),
        min_attendance_pct=80.0,
        is_active=True,
    )
    ensure_can_view_course(student, course, is_enrolled=True)


def test_ensure_can_view_course_student_not_enrolled() -> None:
    student = User(
        id=uuid.uuid4(),
        email="student@example.com",
        full_name="Student",
        role=UserRole.STUDENT,
        hashed_password="",
        is_active=True,
    )
    course = Course(
        id=uuid.uuid4(),
        code="CS101",
        title="Intro CS",
        teacher_id=uuid.uuid4(),
        min_attendance_pct=80.0,
        is_active=True,
    )
    with pytest.raises(PermissionDeniedError) as exc_info:
        ensure_can_view_course(student, course, is_enrolled=False)
    assert exc_info.value.status_code == 403


def test_ensure_can_view_course_teacher_other() -> None:
    teacher = User(
        id=uuid.uuid4(),
        email="other@example.com",
        full_name="Other Teacher",
        role=UserRole.TEACHER,
        hashed_password="",
        is_active=True,
    )
    course = Course(
        id=uuid.uuid4(),
        code="CS101",
        title="Intro CS",
        teacher_id=uuid.uuid4(),
        min_attendance_pct=80.0,
        is_active=True,
    )
    with pytest.raises(PermissionDeniedError) as exc_info:
        ensure_can_view_course(teacher, course, is_enrolled=False)
    assert exc_info.value.status_code == 403
