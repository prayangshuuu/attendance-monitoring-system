from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.enums import UserRole
from app.schemas.common import Page, PaginationParams
from app.schemas.user import PasswordChange, UserCreate, UserRead, UserUpdate


def test_page_schema() -> None:
    page = Page[str](items=["item1", "item2"], total=2, page=1, size=20)
    assert page.items == ["item1", "item2"]
    assert page.total == 2
    assert page.page == 1
    assert page.size == 20


def test_pagination_params_schema() -> None:
    params = PaginationParams()
    assert params.page == 1
    assert params.size == 20

    custom = PaginationParams(page=2, size=50)
    assert custom.page == 2
    assert custom.size == 50

    with pytest.raises(ValidationError):
        PaginationParams(page=0, size=20)

    with pytest.raises(ValidationError):
        PaginationParams(page=1, size=101)


def test_user_read_schema() -> None:
    user_id = uuid4()
    now = datetime.now(UTC)
    read = UserRead(
        id=user_id,
        email="test@example.com",
        full_name="Test User",
        role=UserRole.STUDENT,
        student_id="STU123",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    assert read.id == user_id
    assert read.email == "test@example.com"
    assert read.role == UserRole.STUDENT
    assert "hashed_password" not in read.model_dump()


def test_user_create_validation() -> None:
    # Valid student
    u = UserCreate(
        email="Student@Example.Com",
        full_name="Student One",
        role=UserRole.STUDENT,
        password="ValidPassword1",
        student_id="STU1001",
    )
    assert u.email == "student@example.com"
    assert u.student_id == "STU1001"

    # Valid teacher (no student_id)
    t = UserCreate(
        email="teacher@example.com",
        full_name="Teacher One",
        role=UserRole.TEACHER,
        password="ValidPassword1",
    )
    assert t.student_id is None

    # Student missing student_id
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="stu@example.com",
            full_name="Student",
            role=UserRole.STUDENT,
            password="ValidPassword1",
            student_id=None,
        )
    assert "student_id is required" in str(exc_info.value)

    # Student with empty string student_id
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="stu@example.com",
            full_name="Student",
            role=UserRole.STUDENT,
            password="ValidPassword1",
            student_id="   ",
        )
    assert "student_id is required" in str(exc_info.value)

    # Teacher with student_id forbidden
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="teacher@example.com",
            full_name="Teacher",
            role=UserRole.TEACHER,
            password="ValidPassword1",
            student_id="STU123",
        )
    assert "student_id must be None" in str(exc_info.value)

    # Password complexity: no digit
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="test@example.com",
            full_name="Test",
            role=UserRole.STUDENT,
            password="passwordonly",
            student_id="STU1",
        )
    assert "at least one letter and one number" in str(exc_info.value)

    # Password complexity: too short
    with pytest.raises(ValidationError):
        UserCreate(
            email="test@example.com",
            full_name="Test",
            role=UserRole.STUDENT,
            password="p1",
            student_id="STU1",
        )


def test_user_update_validation() -> None:
    # Partial updates
    u1 = UserUpdate(full_name="New Name")
    assert u1.full_name == "New Name"
    assert u1.email is None

    # Email normalization
    u2 = UserUpdate(email="NEW@EXAMPLE.COM")
    assert u2.email == "new@example.com"

    u2_none = UserUpdate(email=None)
    assert u2_none.email is None

    # Valid student update
    u3 = UserUpdate(role=UserRole.STUDENT, student_id="STU_NEW")
    assert u3.role == UserRole.STUDENT
    assert u3.student_id == "STU_NEW"

    # Role student without student_id
    with pytest.raises(ValidationError) as exc_info:
        UserUpdate(role=UserRole.STUDENT, student_id=None)
    assert "student_id is required" in str(exc_info.value)

    # Role teacher with student_id
    with pytest.raises(ValidationError) as exc_info:
        UserUpdate(role=UserRole.TEACHER, student_id="STU123")
    assert "student_id must be None" in str(exc_info.value)


def test_password_change_validation() -> None:
    # Valid
    p = PasswordChange(old_password="OldPassword1", new_password="NewPassword2")
    assert p.old_password == "OldPassword1"
    assert p.new_password == "NewPassword2"

    # Invalid new password
    with pytest.raises(ValidationError) as exc_info:
        PasswordChange(old_password="OldPassword1", new_password="weak")
    assert "at least one letter and one number" in str(exc_info.value)


def test_user_full_name_whitespace_validation() -> None:
    # Leading/trailing whitespace should be stripped
    u = UserCreate(
        email="test@example.com",
        full_name="  Jane Doe  ",
        role=UserRole.STUDENT,
        password="Password123!",
        student_id="STU1001",
    )
    assert u.full_name == "Jane Doe"

    # Whitespace-only full_name should be rejected
    with pytest.raises(ValidationError) as exc:
        UserCreate(
            email="test@example.com",
            full_name="   ",
            role=UserRole.STUDENT,
            password="Password123!",
            student_id="STU1001",
        )
    assert "full_name cannot be empty or whitespace only" in str(exc.value)

    # UserUpdate whitespace-only full_name should be rejected
    with pytest.raises(ValidationError) as exc_up:
        UserUpdate(full_name="   ")
    assert "full_name cannot be empty or whitespace only" in str(exc_up.value)

    # UserUpdate valid full_name with whitespace should be stripped
    u_up = UserUpdate(full_name="  Updated Name  ")
    assert u_up.full_name == "Updated Name"

    u_up_none = UserUpdate(full_name=None)
    assert u_up_none.full_name is None
