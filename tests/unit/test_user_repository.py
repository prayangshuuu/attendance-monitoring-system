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
