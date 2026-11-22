import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import IntegrityError
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

    # Create user with student_id=None (branch 35->43)
    teacher_user = await service.create_user(
        db_session,
        UserCreate(
            email="service_teacher@example.com",
            full_name="Service Teacher",
            role=UserRole.TEACHER,
            password="Password123",
            student_id=None,
        ),
    )
    assert teacher_user.id is not None
    assert teacher_user.student_id is None
    assert teacher_user.role == UserRole.TEACHER.value


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

    # Search with empty / whitespace string
    page_empty_search = await user_service.list_users(db_session, page=1, size=10, search="   ")
    assert page_empty_search.total >= 2

    # Search with query term
    page_search = await user_service.list_users(db_session, page=1, size=10, search="list1")
    assert page_search.total == 1
    assert page_search.items[0].email == "list1@example.com"


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

    # Role change to non-student (branch 127->128)
    updated_role = await user_service.update_user(
        db_session,
        user.id,
        UserUpdate(role=UserRole.TEACHER, student_id=None),
    )
    assert updated_role.role == UserRole.TEACHER.value
    assert updated_role.student_id is None

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


@pytest.mark.asyncio
async def test_user_service_import_csv_empty(db_session: AsyncSession) -> None:
    for empty_input in ("", b"", "email,full_name,role,student_id,password\n"):
        with pytest.raises(ValidationAppError) as exc_info:
            await user_service.import_users_csv(db_session, empty_input)
        assert exc_info.value.code == "CSV_EMPTY_ERROR"
        assert exc_info.value.details["errors"][0]["field"] == "file"
        msg = exc_info.value.details["errors"][0]["message"]
        assert msg == "CSV file contains no user data rows"


@pytest.mark.asyncio
async def test_user_service_import_csv_invalid_encoding(db_session: AsyncSession) -> None:
    with pytest.raises(ValidationAppError) as exc_info:
        await user_service.import_users_csv(db_session, b"\xff\xfe\x00")
    assert exc_info.value.code == "CSV_IMPORT_ERROR"
    assert exc_info.value.details["errors"][0]["field"] == "file"


@pytest.mark.asyncio
async def test_user_service_import_csv_missing_headers(db_session: AsyncSession) -> None:
    csv_text = "email,full_name,role\ntest@example.com,Test,student"
    with pytest.raises(ValidationAppError) as exc_info:
        await user_service.import_users_csv(db_session, csv_text)
    assert exc_info.value.code == "CSV_IMPORT_ERROR"
    assert exc_info.value.details["errors"][0]["field"] == "header"


@pytest.mark.asyncio
async def test_user_service_import_csv_validation_errors(db_session: AsyncSession) -> None:
    existing = await make_user(
        db_session,
        email="existing_csv@example.com",
        student_id="STU_EXISTING",
    )
    csv_text = (
        "email,full_name,role,student_id,password\n"
        ",Empty Email,student,STU_EMPTY,Password123\n"
        "bademail,Bad Email,student,STU_1,Password123\n"
        "s2@example.com,,student,STU_2,Password123\n"
        "s3@example.com,Valid Name,invalidrole,STU_3,Password123\n"
        "s4@example.com,Student No ID,student,,Password123\n"
        "t1@example.com,Teacher With ID,teacher,STU_TEACHER,Password123\n"
        "s5@example.com,Weak Pwd,student,STU_5,weak\n"
        "dup@example.com,Dup 1,student,STU_DUP_A,Password123\n"
        "dup@example.com,Dup 2,student,STU_DUP_B,Password123\n"
        "s6@example.com,Dup Stu 1,student,STU_SAME,Password123\n"
        "s7@example.com,Dup Stu 2,student,STU_SAME,Password123\n"
        f"{existing.email},DB Dup Email,student,STU_UNIQUE_A,Password123\n"
        f"diff@example.com,DB Dup Stu,student,{existing.student_id},Password123\n"
    )

    with pytest.raises(ValidationAppError) as exc_info:
        await user_service.import_users_csv(db_session, csv_text)

    errs = exc_info.value.details["errors"]
    assert len(errs) >= 10


@pytest.mark.asyncio
async def test_user_service_import_csv_success(db_session: AsyncSession) -> None:
    csv_text = (
        "email,full_name,role,student_id,password\n"
        "csv_s1@example.com,CSV Student One,student,STU_CSV_1,Password123\n"
        "csv_t1@example.com,CSV Teacher One,teacher,,Password123\n"
        "csv_a1@example.com,CSV Admin One,admin,,Password123\n"
    )
    users = await user_service.import_users_csv(db_session, csv_text)
    assert len(users) == 3
    assert users[0].role == UserRole.STUDENT.value
    assert users[0].student_id == "STU_CSV_1"
    assert users[1].role == UserRole.TEACHER.value
    assert users[1].student_id is None
    assert users[2].role == UserRole.ADMIN.value


@pytest.mark.asyncio
async def test_create_user_integrity_error_rolls_back_and_raises_conflict(
    db_session: AsyncSession,
) -> None:
    data = UserCreate(
        email="conflict_user@example.com",
        full_name="Conflict User",
        role=UserRole.STUDENT,
        password="Password123!",
        student_id="STU_CONF_1",
    )
    with (
        patch.object(
            db_session,
            "commit",
            AsyncMock(side_effect=IntegrityError("conflict", params={}, orig=Exception())),
        ),
        patch.object(db_session, "rollback", AsyncMock()) as mock_rollback,
    ):
        with pytest.raises(ConflictError) as exc:
            await user_service.create_user(db_session, data)
        assert exc.value.code == "USER_EXISTS"
        assert exc.value.status_code == 409
        mock_rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_user_integrity_error_rolls_back_and_raises_conflict(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, role=UserRole.STUDENT)
    update_data = UserUpdate(full_name="New Name")
    with (
        patch.object(
            db_session,
            "commit",
            AsyncMock(side_effect=IntegrityError("conflict", params={}, orig=Exception())),
        ),
        patch.object(db_session, "rollback", AsyncMock()) as mock_rollback,
    ):
        with pytest.raises(ConflictError) as exc:
            await user_service.update_user(db_session, user.id, update_data)
        assert exc.value.code == "USER_CONFLICT"
        assert exc.value.status_code == 409
        mock_rollback.assert_awaited_once()
