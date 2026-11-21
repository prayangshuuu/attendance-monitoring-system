import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.core.security import verify_password
from app.models.enums import UserRole
from app.schemas.user import PasswordChange, UserCreate, UserUpdate
from app.services.user_service import UserService, user_service
from tests.factories import make_user


@pytest.mark.asyncio
async def test_user_service_create_user(db_session: AsyncSession) -> None:
    service = UserService()
    data = UserCreate(
        email="service_stu@example.com",
        full_name="Service Student",
        role=UserRole.STUDENT,
        password="Password123",
        student_id="STU_SVC_1",
    )
    user = await service.create_user(db_session, data)
    assert user.id is not None
    assert user.email == "service_stu@example.com"
    assert verify_password("Password123", user.hashed_password)
    assert user.role == UserRole.STUDENT.value

    # Duplicate email
    with pytest.raises(ConflictError) as exc_info:
        await service.create_user(
            db_session,
            UserCreate(
                email="service_stu@example.com",
                full_name="Duplicate Email",
                role=UserRole.STUDENT,
                password="Password123",
                student_id="STU_SVC_2",
            ),
        )
    assert exc_info.value.code == "USER_EMAIL_EXISTS"

    # Duplicate student_id
    with pytest.raises(ConflictError) as exc_info:
        await service.create_user(
            db_session,
            UserCreate(
                email="different_email@example.com",
                full_name="Duplicate Student ID",
                role=UserRole.STUDENT,
                password="Password123",
                student_id="STU_SVC_1",
            ),
        )
    assert exc_info.value.code == "STUDENT_ID_EXISTS"


@pytest.mark.asyncio
async def test_user_service_get_by_id(db_session: AsyncSession) -> None:
    user = await make_user(db_session, email="findme@example.com")
    found = await user_service.get_user_by_id(db_session, user.id)
    assert found.id == user.id

    with pytest.raises(NotFoundError) as exc_info:
        await user_service.get_user_by_id(db_session, uuid.uuid4())
    assert exc_info.value.code == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_user_service_list_users(db_session: AsyncSession) -> None:
    await make_user(db_session, email="list1@example.com", role=UserRole.STUDENT)
    await make_user(db_session, email="list2@example.com", role=UserRole.TEACHER)

    page = await user_service.list_users(db_session, page=1, size=10)
    assert page.total >= 2
    assert len(page.items) >= 2
    assert page.page == 1
    assert page.size == 10


@pytest.mark.asyncio
async def test_user_service_update_user(db_session: AsyncSession) -> None:
    user = await make_user(
        db_session,
        email="orig@example.com",
        full_name="Original Name",
        role=UserRole.STUDENT,
        student_id="STU_ORIG",
    )
    await make_user(
        db_session,
        email="other@example.com",
        student_id="STU_OTHER",
    )

    # Valid update
    updated = await user_service.update_user(
        db_session,
        user.id,
        UserUpdate(full_name="Updated Name", student_id="STU_NEW_ID"),
    )
    assert updated.full_name == "Updated Name"
    assert updated.student_id == "STU_NEW_ID"

    # Same email update (no-op conflict)
    updated_same = await user_service.update_user(
        db_session,
        user.id,
        UserUpdate(email="orig@example.com"),
    )
    assert updated_same.email == "orig@example.com"

    # New unique email and is_active update
    updated_new_email = await user_service.update_user(
        db_session,
        user.id,
        UserUpdate(email="brand_new_unique@example.com", is_active=False),
    )
    assert updated_new_email.email == "brand_new_unique@example.com"
    assert updated_new_email.is_active is False

    # Conflicting email
    with pytest.raises(ConflictError) as exc_info:
        await user_service.update_user(
            db_session,
            user.id,
            UserUpdate(email="other@example.com"),
        )
    assert exc_info.value.code == "USER_EMAIL_EXISTS"

    # Conflicting student_id
    with pytest.raises(ConflictError) as exc_info:
        await user_service.update_user(
            db_session,
            user.id,
            UserUpdate(student_id="STU_OTHER"),
        )
    assert exc_info.value.code == "STUDENT_ID_EXISTS"

    # Teacher cannot have student_id
    teacher = await make_user(
        db_session,
        email="teacher_up@example.com",
        role=UserRole.TEACHER,
    )
    with pytest.raises(ValidationAppError) as exc_info:
        await user_service.update_user(
            db_session,
            teacher.id,
            UserUpdate(student_id="STU_INVALID"),
        )
    assert exc_info.value.code == "INVALID_STUDENT_ID"

    # Student changed to teacher clears student_id
    converted = await user_service.update_user(
        db_session,
        user.id,
        UserUpdate(role=UserRole.TEACHER),
    )
    assert converted.role == UserRole.TEACHER.value
    assert converted.student_id is None

    # Change to student without student_id triggers service validation error
    with pytest.raises(ValidationAppError) as exc_info:
        await user_service.update_user(
            db_session,
            converted.id,
            UserUpdate.model_construct(role=UserRole.STUDENT, student_id=None),
        )
    assert exc_info.value.code == "STUDENT_ID_REQUIRED"


@pytest.mark.asyncio
async def test_user_service_deactivate_user(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    target = await make_user(db_session, role=UserRole.STUDENT, is_active=True)

    # Admin cannot deactivate self
    with pytest.raises(ValidationAppError) as exc_info:
        await user_service.deactivate_user(db_session, user_id=admin.id, admin_id=admin.id)
    assert exc_info.value.code == "SELF_DEACTIVATION_FORBIDDEN"

    # Admin deactivates target
    deactivated = await user_service.deactivate_user(
        db_session, user_id=target.id, admin_id=admin.id
    )
    assert deactivated.is_active is False


@pytest.mark.asyncio
async def test_user_service_change_password(db_session: AsyncSession) -> None:
    user = await make_user(db_session, password="OldPassword123")

    # Wrong old password
    with pytest.raises(ValidationAppError) as exc_info:
        await user_service.change_password(
            db_session,
            user.id,
            PasswordChange(old_password="WrongOldPassword1", new_password="NewPassword123"),
        )
    assert exc_info.value.code == "INVALID_PASSWORD"

    # Correct old password
    await user_service.change_password(
        db_session,
        user.id,
        PasswordChange(old_password="OldPassword123", new_password="NewPassword123"),
    )
    refetched = await user_service.get_user_by_id(db_session, user.id)
    assert verify_password("NewPassword123", refetched.hashed_password)
