from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.course import (
    CourseCreate,
    CourseRead,
    CourseTeacherSummary,
    CourseUpdate,
)


def test_course_teacher_summary_schema() -> None:
    teacher_id = uuid4()
    summary = CourseTeacherSummary(
        id=teacher_id,
        email="teacher@example.com",
        full_name="Prof. Turing",
    )
    assert summary.id == teacher_id
    assert summary.email == "teacher@example.com"
    assert summary.full_name == "Prof. Turing"


def test_course_create_schema_success() -> None:
    teacher_id = uuid4()
    course = CourseCreate(
        code="cs101",
        title="  Introduction to Computer Science  ",
        teacher_id=teacher_id,
        min_attendance_pct=75.5,
        is_active=True,
    )
    assert course.code == "CS101"  # uppercase normalized
    assert course.title == "Introduction to Computer Science"  # stripped
    assert course.teacher_id == teacher_id
    assert course.min_attendance_pct == 75.5
    assert course.is_active is True


def test_course_create_defaults() -> None:
    teacher_id = uuid4()
    course = CourseCreate(
        code="math201",
        title="Calculus",
        teacher_id=teacher_id,
    )
    assert course.min_attendance_pct == 80.0
    assert course.is_active is True


def test_course_create_validation_errors() -> None:
    teacher_id = uuid4()

    # Empty code
    with pytest.raises(ValidationError):
        CourseCreate(code="", title="Math", teacher_id=teacher_id)

    # Empty title
    with pytest.raises(ValidationError):
        CourseCreate(code="MATH101", title="   ", teacher_id=teacher_id)

    # Negative attendance pct
    with pytest.raises(ValidationError):
        CourseCreate(code="MATH101", title="Math", teacher_id=teacher_id, min_attendance_pct=-5.0)

    # Over 100 attendance pct
    with pytest.raises(ValidationError):
        CourseCreate(code="MATH101", title="Math", teacher_id=teacher_id, min_attendance_pct=105.0)


def test_course_update_schema() -> None:
    update = CourseUpdate(
        code="cs102",
        title="  Data Structures  ",
        min_attendance_pct=85.0,
        is_active=False,
    )
    assert update.code == "CS102"
    assert update.title == "Data Structures"
    assert update.min_attendance_pct == 85.0
    assert update.is_active is False

    # Partial update
    partial = CourseUpdate(title="Algorithms")
    assert partial.title == "Algorithms"
    assert partial.code is None
    assert partial.teacher_id is None
    assert partial.min_attendance_pct is None
    assert partial.is_active is None

    # Explicit None
    explicit_none = CourseUpdate(code=None, title=None)
    assert explicit_none.code is None
    assert explicit_none.title is None


def test_course_update_validation_errors() -> None:
    with pytest.raises(ValidationError):
        CourseUpdate(code="")

    with pytest.raises(ValidationError):
        CourseUpdate(title="   ")

    with pytest.raises(ValidationError):
        CourseUpdate(min_attendance_pct=-1.0)

    with pytest.raises(ValidationError):
        CourseUpdate(min_attendance_pct=101.0)


def test_course_read_schema() -> None:
    c_id = uuid4()
    t_id = uuid4()
    now = datetime.now(UTC)
    read = CourseRead(
        id=c_id,
        code="CS101",
        title="Intro",
        teacher_id=t_id,
        min_attendance_pct=80.0,
        is_active=True,
        teacher=CourseTeacherSummary(id=t_id, email="prof@uni.edu", full_name="Prof"),
        created_at=now,
        updated_at=now,
    )
    assert read.id == c_id
    assert read.code == "CS101"
    assert read.teacher is not None
    assert read.teacher.email == "prof@uni.edu"
