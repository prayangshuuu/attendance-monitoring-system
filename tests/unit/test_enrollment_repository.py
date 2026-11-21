import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enrollment import Enrollment
from app.models.enums import UserRole
from app.repositories.enrollment_repository import enrollment_repository
from tests.factories import make_course, make_enrollment, make_user


@pytest.mark.asyncio
async def test_enrollment_repository_create_and_get(db_session: AsyncSession) -> None:
    course = await make_course(db_session)
    student = await make_user(db_session, role=UserRole.STUDENT)

    enrollment = Enrollment(course_id=course.id, student_id=student.id)
    created = await enrollment_repository.create_enrollments(db_session, [enrollment])
    assert len(created) == 1
    assert created[0].id is not None

    found = await enrollment_repository.get_enrollment(db_session, course.id, student.id)
    assert found is not None
    assert found.course_id == course.id
    assert found.student_id == student.id

    # Non-existent
    missing = await enrollment_repository.get_enrollment(db_session, course.id, uuid.uuid4())
    assert missing is None


@pytest.mark.asyncio
async def test_enrollment_repository_is_enrolled(db_session: AsyncSession) -> None:
    course = await make_course(db_session)
    student = await make_user(db_session, role=UserRole.STUDENT)
    other = await make_user(db_session, role=UserRole.STUDENT)

    await make_enrollment(db_session, course=course, student=student)

    assert await enrollment_repository.is_enrolled(db_session, course.id, student.id) is True
    assert await enrollment_repository.is_enrolled(db_session, course.id, other.id) is False


@pytest.mark.asyncio
async def test_enrollment_repository_get_enrolled_student_ids(db_session: AsyncSession) -> None:
    course = await make_course(db_session)
    s1 = await make_user(db_session, role=UserRole.STUDENT)
    s2 = await make_user(db_session, role=UserRole.STUDENT)
    s3 = await make_user(db_session, role=UserRole.STUDENT)

    await make_enrollment(db_session, course=course, student=s1)
    await make_enrollment(db_session, course=course, student=s2)

    enrolled = await enrollment_repository.get_enrolled_student_ids(
        db_session, course.id, [s1.id, s2.id, s3.id]
    )
    assert enrolled == {s1.id, s2.id}

    # Empty list
    empty = await enrollment_repository.get_enrolled_student_ids(db_session, course.id, [])
    assert empty == set()


@pytest.mark.asyncio
async def test_enrollment_repository_delete(db_session: AsyncSession) -> None:
    course = await make_course(db_session)
    student = await make_user(db_session, role=UserRole.STUDENT)

    await make_enrollment(db_session, course=course, student=student)
    assert await enrollment_repository.is_enrolled(db_session, course.id, student.id) is True

    deleted = await enrollment_repository.delete_enrollment(db_session, course.id, student.id)
    assert deleted is True

    assert await enrollment_repository.is_enrolled(db_session, course.id, student.id) is False

    # Deleting again returns False
    deleted_again = await enrollment_repository.delete_enrollment(db_session, course.id, student.id)
    assert deleted_again is False


@pytest.mark.asyncio
async def test_enrollment_repository_list_course_students(db_session: AsyncSession) -> None:
    course = await make_course(db_session)
    s1 = await make_user(db_session, role=UserRole.STUDENT, full_name="Alice")
    s2 = await make_user(db_session, role=UserRole.STUDENT, full_name="Bob")

    await make_enrollment(db_session, course=course, student=s1)
    await make_enrollment(db_session, course=course, student=s2)

    students, total = await enrollment_repository.list_course_students(
        db_session, course.id, page=1, size=10
    )
    assert total == 2
    student_ids = [s.id for s in students]
    assert s1.id in student_ids
    assert s2.id in student_ids
