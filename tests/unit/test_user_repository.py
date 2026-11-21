import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User
from app.repositories.user_repository import UserRepository, user_repository


@pytest.mark.asyncio
async def test_user_repository_create_and_get_by_id(db_session: AsyncSession) -> None:
    user = User(
        email="repo_user@example.com",
        full_name="Repo User",
        hashed_password="some_hashed_password",
        role=UserRole.STUDENT,
        is_active=True,
    )
    created = await user_repository.create(db_session, user)
    assert created.id is not None

    found = await user_repository.get_by_id(db_session, created.id)
    assert found is not None
    assert found.id == created.id
    assert found.email == "repo_user@example.com"

    # Also test with str(UUID)
    found_str = await user_repository.get_by_id(db_session, str(created.id))
    assert found_str is not None
    assert found_str.id == created.id

    # Non-existent UUID
    missing = await user_repository.get_by_id(db_session, uuid.uuid4())
    assert missing is None

    # Invalid UUID string
    invalid = await user_repository.get_by_id(db_session, "not-a-valid-uuid")
    assert invalid is None


@pytest.mark.asyncio
async def test_user_repository_get_by_email_case_insensitive(db_session: AsyncSession) -> None:
    user = User(
        email="case_test@example.com",
        full_name="Case User",
        hashed_password="hashed_password",
        role=UserRole.TEACHER,
        is_active=True,
    )
    await UserRepository.create(db_session, user)

    found_lower = await UserRepository.get_by_email(db_session, "case_test@example.com")
    assert found_lower is not None
    assert found_lower.email == "case_test@example.com"

    found_upper = await UserRepository.get_by_email(db_session, "CASE_TEST@EXAMPLE.COM")
    assert found_upper is not None
    assert found_upper.email == "case_test@example.com"

    found_mixed = await UserRepository.get_by_email(db_session, "  Case_Test@Example.Com  ")
    assert found_mixed is not None
    assert found_mixed.email == "case_test@example.com"

    missing = await UserRepository.get_by_email(db_session, "nobody@example.com")
    assert missing is None


@pytest.mark.asyncio
async def test_user_repository_get_by_student_id(db_session: AsyncSession) -> None:
    user = User(
        email="stu_repo@example.com",
        full_name="Student Repo",
        hashed_password="hashed_password",
        role=UserRole.STUDENT,
        student_id="STU1001",
        is_active=True,
    )
    await user_repository.create(db_session, user)

    found = await user_repository.get_by_student_id(db_session, "STU1001")
    assert found is not None
    assert found.id == user.id

    # Case-insensitive
    found_lower = await user_repository.get_by_student_id(db_session, "stu1001")
    assert found_lower is not None
    assert found_lower.id == user.id

    # Stripped
    found_stripped = await user_repository.get_by_student_id(db_session, "  STU1001  ")
    assert found_stripped is not None
    assert found_stripped.id == user.id

    # Non-existent
    missing = await user_repository.get_by_student_id(db_session, "UNKNOWN")
    assert missing is None


@pytest.mark.asyncio
async def test_user_repository_update(db_session: AsyncSession) -> None:
    user = User(
        email="update_repo@example.com",
        full_name="Before Update",
        hashed_password="hashed_password",
        role=UserRole.STUDENT,
        student_id="STU1002",
        is_active=True,
    )
    await user_repository.create(db_session, user)

    user.full_name = "After Update"
    user.is_active = False
    updated = await user_repository.update(db_session, user)
    assert updated.full_name == "After Update"
    assert updated.is_active is False

    refetched = await user_repository.get_by_id(db_session, user.id)
    assert refetched is not None
    assert refetched.full_name == "After Update"
    assert refetched.is_active is False


@pytest.mark.asyncio
async def test_user_repository_create_many(db_session: AsyncSession) -> None:
    users = [
        User(
            email=f"bulk_{i}@example.com",
            full_name=f"Bulk User {i}",
            hashed_password="hashed_password",
            role=UserRole.STUDENT,
            student_id=f"STUBULK_{i}",
            is_active=True,
        )
        for i in range(3)
    ]
    created = await user_repository.create_many(db_session, users)
    assert len(created) == 3
    for u in created:
        assert u.id is not None
        found = await user_repository.get_by_id(db_session, u.id)
        assert found is not None


@pytest.mark.asyncio
async def test_user_repository_list_and_filters(db_session: AsyncSession) -> None:
    u1 = User(
        email="alice@example.com",
        full_name="Alice Smith",
        hashed_password="pwd",
        role=UserRole.STUDENT,
        student_id="STU_A1",
        is_active=True,
    )
    u2 = User(
        email="bob@example.com",
        full_name="Bob Jones",
        hashed_password="pwd",
        role=UserRole.STUDENT,
        student_id="STU_B2",
        is_active=False,
    )
    u3 = User(
        email="carol@example.com",
        full_name="Carol White",
        hashed_password="pwd",
        role=UserRole.TEACHER,
        student_id=None,
        is_active=True,
    )
    u4 = User(
        email="david@example.com",
        full_name="David Black",
        hashed_password="pwd",
        role=UserRole.ADMIN,
        student_id=None,
        is_active=True,
    )
    await user_repository.create_many(db_session, [u1, u2, u3, u4])

    # Default list
    items, total = await user_repository.list(db_session, page=1, size=10)
    assert total >= 4
    assert len(items) >= 4

    # Pagination: size=2, page=1
    items_p1, total_p1 = await user_repository.list(db_session, page=1, size=2)
    assert len(items_p1) == 2
    assert total_p1 >= 4

    # Pagination: size=2, page=2
    items_p2, total_p2 = await user_repository.list(db_session, page=2, size=2)
    assert len(items_p2) == 2
    assert total_p2 == total_p1
    assert items_p1[0].id != items_p2[0].id

    # Filter by role
    teachers, teacher_total = await user_repository.list(
        db_session, role=UserRole.TEACHER, page=1, size=10
    )
    assert all(u.role == UserRole.TEACHER.value for u in teachers)
    assert teacher_total >= 1

    # Filter by is_active=False
    inactive, inactive_total = await user_repository.list(
        db_session, is_active=False, page=1, size=10
    )
    assert all(u.is_active is False for u in inactive)
    assert inactive_total >= 1

    # Search by full_name
    search_name, search_total = await user_repository.list(
        db_session, search="Alice", page=1, size=10
    )
    assert any(u.full_name == "Alice Smith" for u in search_name)
    assert search_total >= 1

    # Search by email
    search_email, _ = await user_repository.list(
        db_session, search="carol@example", page=1, size=10
    )
    assert any(u.email == "carol@example.com" for u in search_email)

    # Search by student_id
    search_stu, _ = await user_repository.list(db_session, search="STU_B2", page=1, size=10)
    assert any(u.student_id == "STU_B2" for u in search_stu)

    # Search with no matches
    no_match, no_match_total = await user_repository.list(
        db_session, search="NonExistentTermXYZ", page=1, size=10
    )
    assert len(no_match) == 0
    assert no_match_total == 0
