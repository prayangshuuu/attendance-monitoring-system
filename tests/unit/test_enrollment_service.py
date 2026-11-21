import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationAppError
from app.models.enums import UserRole
from app.services.enrollment_service import enrollment_service
from tests.factories import make_course, make_enrollment, make_user


@pytest.mark.asyncio
async def test_enroll_students_success(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)

    s1 = await make_user(db_session, role=UserRole.STUDENT)
    s2 = await make_user(db_session, role=UserRole.STUDENT)

    # Initial enrollment
    res = await enrollment_service.enroll_students(
        db_session, course.id, [s1.id, s2.id], current_user=teacher
    )
    assert len(res.enrolled) == 2
    assert len(res.skipped) == 0

    # Re-enrolling s1, plus s3 (should skip s1, enroll s3)
    s3 = await make_user(db_session, role=UserRole.STUDENT)
    res2 = await enrollment_service.enroll_students(
        db_session, course.id, [s1.id, s3.id], current_user=admin
    )
    assert res2.enrolled == [s3.id]
    assert res2.skipped == [s1.id]


@pytest.mark.asyncio
async def test_enroll_students_empty_list(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)

    res = await enrollment_service.enroll_students(db_session, course.id, [], current_user=teacher)
    assert res.enrolled == []
    assert res.skipped == []


@pytest.mark.asyncio
async def test_enroll_students_course_not_found(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    with pytest.raises(NotFoundError):
        await enrollment_service.enroll_students(
            db_session, uuid.uuid4(), [uuid.uuid4()], current_user=admin
        )


@pytest.mark.asyncio
async def test_enroll_students_permission_denied(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    student = await make_user(db_session, role=UserRole.STUDENT)

    with pytest.raises(PermissionDeniedError):
        await enrollment_service.enroll_students(
            db_session, course.id, [student.id], current_user=other_teacher
        )

    with pytest.raises(PermissionDeniedError):
        await enrollment_service.enroll_students(
            db_session, course.id, [student.id], current_user=student
        )


@pytest.mark.asyncio
async def test_enroll_students_invalid_student_id(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)

    # 1. Non-existent student
    with pytest.raises(ValidationAppError) as exc1:
        await enrollment_service.enroll_students(
            db_session, course.id, [uuid.uuid4()], current_user=teacher
        )
    assert exc1.value.code == "USER_NOT_FOUND"

    # 2. Inactive student
    inactive_student = await make_user(db_session, role=UserRole.STUDENT, is_active=False)
    with pytest.raises(ValidationAppError) as exc2:
        await enrollment_service.enroll_students(
            db_session, course.id, [inactive_student.id], current_user=teacher
        )
    assert exc2.value.code == "USER_INACTIVE"

    # 3. Not a student (teacher role)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    with pytest.raises(ValidationAppError) as exc3:
        await enrollment_service.enroll_students(
            db_session, course.id, [other_teacher.id], current_user=teacher
        )
    assert exc3.value.code == "INVALID_ROLE"


@pytest.mark.asyncio
async def test_unenroll_success_and_failures(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)

    # 1. Non-existent course
    with pytest.raises(NotFoundError):
        await enrollment_service.unenroll(
            db_session, uuid.uuid4(), student.id, current_user=teacher
        )

    # 2. Permission denied
    other_student = await make_user(db_session, role=UserRole.STUDENT)
    with pytest.raises(PermissionDeniedError):
        await enrollment_service.unenroll(
            db_session, course.id, student.id, current_user=other_student
        )

    # 3. Non-existent enrollment
    not_enrolled = await make_user(db_session, role=UserRole.STUDENT)
    with pytest.raises(NotFoundError):
        await enrollment_service.unenroll(
            db_session, course.id, not_enrolled.id, current_user=teacher
        )

    # 4. Success
    await enrollment_service.unenroll(db_session, course.id, student.id, current_user=teacher)
    is_enrolled = await enrollment_service.enrollment_repo.is_enrolled(
        db_session, course.id, student.id
    )
    assert is_enrolled is False


@pytest.mark.asyncio
async def test_list_course_students_service(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)

    # Success
    page = await enrollment_service.list_course_students(
        db_session, course.id, current_user=teacher, page=1, size=10
    )
    assert page.total == 1
    assert page.items[0].id == student.id

    # Non-existent course
    with pytest.raises(NotFoundError):
        await enrollment_service.list_course_students(
            db_session, uuid.uuid4(), current_user=teacher, page=1, size=10
        )

    # Permission denied
    other = await make_user(db_session, role=UserRole.STUDENT)
    with pytest.raises(PermissionDeniedError):
        await enrollment_service.list_course_students(
            db_session, course.id, current_user=other, page=1, size=10
        )
