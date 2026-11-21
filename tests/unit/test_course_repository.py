import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.course_repository import course_repository
from tests.factories import make_course, make_enrollment, make_user


@pytest.mark.asyncio
async def test_course_repository_create_and_get_by_id(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = Course(
        code="CS500",
        title="Distributed Systems",
        teacher_id=teacher.id,
        min_attendance_pct=85.0,
        is_active=True,
    )
    created = await course_repository.create(db_session, course)
    assert created.id is not None

    found = await course_repository.get_by_id(db_session, created.id)
    assert found is not None
    assert found.id == created.id
    assert found.code == "CS500"
    assert found.teacher is not None
    assert found.teacher.id == teacher.id

    # Test with string UUID
    found_str = await course_repository.get_by_id(db_session, str(created.id))
    assert found_str is not None
    assert found_str.id == created.id

    # Non-existent
    missing = await course_repository.get_by_id(db_session, uuid.uuid4())
    assert missing is None

    # Invalid UUID string
    invalid = await course_repository.get_by_id(db_session, "not-a-uuid")
    assert invalid is None


@pytest.mark.asyncio
async def test_course_repository_get_by_code(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    await make_course(db_session, teacher=teacher, code="BIO101")

    # Case-insensitive lookup
    found = await course_repository.get_by_code(db_session, "bio101")
    assert found is not None
    assert found.code == "BIO101"
    assert found.teacher is not None

    found_upper = await course_repository.get_by_code(db_session, "BIO101")
    assert found_upper is not None
    assert found_upper.code == "BIO101"

    missing = await course_repository.get_by_code(db_session, "NONEXISTENT")
    assert missing is None


@pytest.mark.asyncio
async def test_course_repository_update(db_session: AsyncSession) -> None:
    course = await make_course(db_session, code="CHEM101", title="Chemistry 101")
    course.title = "Advanced Chemistry"
    course.min_attendance_pct = 90.0

    updated = await course_repository.update(db_session, course)
    assert updated.title == "Advanced Chemistry"
    assert updated.min_attendance_pct == 90.0

    found = await course_repository.get_by_id(db_session, course.id)
    assert found is not None
    assert found.title == "Advanced Chemistry"


@pytest.mark.asyncio
async def test_course_repository_list_admin(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    t1 = await make_user(db_session, role=UserRole.TEACHER)
    t2 = await make_user(db_session, role=UserRole.TEACHER)

    c1 = await make_course(db_session, teacher=t1, code="REPO_ADM_1", title="Math 1")
    c2 = await make_course(db_session, teacher=t2, code="REPO_ADM_2", title="Physics 1")

    courses, total = await course_repository.list(db_session, user=admin, page=1, size=10)
    course_ids = [c.id for c in courses]
    assert c1.id in course_ids
    assert c2.id in course_ids
    assert total >= 2


@pytest.mark.asyncio
async def test_course_repository_list_teacher(db_session: AsyncSession) -> None:
    t1 = await make_user(db_session, role=UserRole.TEACHER)
    t2 = await make_user(db_session, role=UserRole.TEACHER)

    c1 = await make_course(db_session, teacher=t1, code="REPO_TCH_1", title="My Course")
    c2 = await make_course(db_session, teacher=t2, code="REPO_TCH_2", title="Other Course")

    courses, total = await course_repository.list(db_session, user=t1, page=1, size=10)
    course_ids = [c.id for c in courses]
    assert c1.id in course_ids
    assert c2.id not in course_ids
    assert total == 1


@pytest.mark.asyncio
async def test_course_repository_list_student(db_session: AsyncSession) -> None:
    student = await make_user(db_session, role=UserRole.STUDENT)
    t = await make_user(db_session, role=UserRole.TEACHER)

    c1 = await make_course(db_session, teacher=t, code="REPO_STU_1", title="Enrolled Course")
    c2 = await make_course(db_session, teacher=t, code="REPO_STU_2", title="Not Enrolled Course")

    await make_enrollment(db_session, course=c1, student=student)

    courses, total = await course_repository.list(db_session, user=student, page=1, size=10)
    course_ids = [c.id for c in courses]
    assert c1.id in course_ids
    assert c2.id not in course_ids
    assert total == 1


@pytest.mark.asyncio
async def test_course_repository_list_filters(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    t = await make_user(db_session, role=UserRole.TEACHER)

    c_active = await make_course(
        db_session, teacher=t, code="FILTER_ACT", title="Active Alpha", is_active=True
    )
    c_inactive = await make_course(
        db_session, teacher=t, code="FILTER_INACT", title="Inactive Beta", is_active=False
    )

    # Filter active
    courses, total = await course_repository.list(
        db_session, user=admin, page=1, size=10, is_active=True
    )
    ids = [c.id for c in courses]
    assert c_active.id in ids
    assert c_inactive.id not in ids

    # Filter search by code
    courses, total = await course_repository.list(
        db_session, user=admin, page=1, size=10, search="FILTER_ACT"
    )
    ids = [c.id for c in courses]
    assert c_active.id in ids
    assert c_inactive.id not in ids

    # Filter search by title
    courses, total = await course_repository.list(
        db_session, user=admin, page=1, size=10, search="Inactive"
    )
    ids = [c.id for c in courses]
    assert c_inactive.id in ids
    assert c_active.id not in ids


@pytest.mark.asyncio
async def test_course_repository_list_unknown_role(db_session: AsyncSession) -> None:
    user = User(
        id=uuid.uuid4(),
        email="ghost@example.com",
        full_name="Ghost",
        role="guest",
        hashed_password="",
        is_active=True,
    )
    courses, total = await course_repository.list(db_session, user=user)
    assert courses == []
    assert total == 0
