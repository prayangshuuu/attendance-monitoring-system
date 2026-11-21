import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationAppError,
)
from app.models.enums import UserRole
from app.schemas.course import CourseCreate, CourseUpdate
from app.services.course_service import course_service
from tests.factories import make_course, make_enrollment, make_user


@pytest.mark.asyncio
async def test_create_course_success(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    data = CourseCreate(
        code="cs201",
        title="Data Structures",
        teacher_id=teacher.id,
        min_attendance_pct=85.0,
    )
    course = await course_service.create_course(db_session, data)
    assert course.id is not None
    assert course.code == "CS201"
    assert course.title == "Data Structures"
    assert course.teacher_id == teacher.id
    assert course.teacher is not None
    assert course.teacher.id == teacher.id


@pytest.mark.asyncio
async def test_create_course_invalid_teacher_nonexistent(db_session: AsyncSession) -> None:
    data = CourseCreate(
        code="CS202",
        title="Algorithms",
        teacher_id=uuid.uuid4(),
    )
    with pytest.raises(ValidationAppError) as exc_info:
        await course_service.create_course(db_session, data)
    assert exc_info.value.code == "INVALID_TEACHER"


@pytest.mark.asyncio
async def test_create_course_invalid_teacher_inactive(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER, is_active=False)
    data = CourseCreate(
        code="CS203",
        title="Operating Systems",
        teacher_id=teacher.id,
    )
    with pytest.raises(ValidationAppError) as exc_info:
        await course_service.create_course(db_session, data)
    assert exc_info.value.code == "INVALID_TEACHER"


@pytest.mark.asyncio
async def test_create_course_invalid_teacher_wrong_role(db_session: AsyncSession) -> None:
    student = await make_user(db_session, role=UserRole.STUDENT)
    data = CourseCreate(
        code="CS204",
        title="Networks",
        teacher_id=student.id,
    )
    with pytest.raises(ValidationAppError) as exc_info:
        await course_service.create_course(db_session, data)
    assert exc_info.value.code == "INVALID_TEACHER"


@pytest.mark.asyncio
async def test_create_course_duplicate_code(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    await make_course(db_session, teacher=teacher, code="CS205")

    data = CourseCreate(
        code="cs205",
        title="Another CS",
        teacher_id=teacher.id,
    )
    with pytest.raises(ConflictError) as exc_info:
        await course_service.create_course(db_session, data)
    assert exc_info.value.code == "COURSE_CODE_EXISTS"


@pytest.mark.asyncio
async def test_get_course_admin_and_teacher_and_enrolled_student(
    db_session: AsyncSession,
) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)
    other_student = await make_user(db_session, role=UserRole.STUDENT)

    course = await make_course(db_session, teacher=teacher)
    await make_enrollment(db_session, course=course, student=student)

    # Admin can get
    res_admin = await course_service.get_course(db_session, course.id, admin)
    assert res_admin.id == course.id

    # Teacher owner can get
    res_teacher = await course_service.get_course(db_session, course.id, teacher)
    assert res_teacher.id == course.id

    # Enrolled student can get
    res_student = await course_service.get_course(db_session, course.id, student)
    assert res_student.id == course.id

    # Non-enrolled student gets 403
    with pytest.raises(PermissionDeniedError):
        await course_service.get_course(db_session, course.id, other_student)

    # Other teacher gets 403
    with pytest.raises(PermissionDeniedError):
        await course_service.get_course(db_session, course.id, other_teacher)

    # Missing course gets 404
    with pytest.raises(NotFoundError):
        await course_service.get_course(db_session, uuid.uuid4(), admin)


@pytest.mark.asyncio
async def test_list_courses_service(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    t = await make_user(db_session, role=UserRole.TEACHER)
    await make_course(db_session, teacher=t, code="LST_SRV_1")
    await make_course(db_session, teacher=t, code="LST_SRV_2")

    page = await course_service.list_courses(db_session, current_user=admin, page=1, size=10)
    assert page.total >= 2
    assert len(page.items) >= 2


@pytest.mark.asyncio
async def test_update_course_success_and_validations(db_session: AsyncSession) -> None:
    t1 = await make_user(db_session, role=UserRole.TEACHER)
    t2 = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=t1, code="UPD101", title="Original Title")

    # Update title, attendance, is_active, teacher
    update_data = CourseUpdate(
        title="Updated Title",
        teacher_id=t2.id,
        min_attendance_pct=90.0,
        is_active=False,
    )
    updated = await course_service.update_course(db_session, course.id, update_data)
    assert updated.title == "Updated Title"
    assert updated.teacher_id == t2.id
    assert updated.min_attendance_pct == 90.0
    assert updated.is_active is False

    # Update code
    update_code = CourseUpdate(code="UPD102")
    updated_code = await course_service.update_course(db_session, course.id, update_code)
    assert updated_code.code == "UPD102"

    # Same code update is allowed
    same_code = CourseUpdate(code="UPD102")
    same_res = await course_service.update_course(db_session, course.id, same_code)
    assert same_res.code == "UPD102"

    # Conflict on another course's code
    await make_course(db_session, teacher=t1, code="EXISTING_CODE")
    with pytest.raises(ConflictError):
        await course_service.update_course(
            db_session, course.id, CourseUpdate(code="EXISTING_CODE")
        )

    # Invalid teacher on update
    with pytest.raises(ValidationAppError):
        await course_service.update_course(
            db_session, course.id, CourseUpdate(teacher_id=uuid.uuid4())
        )

    # Non-existent course
    with pytest.raises(NotFoundError):
        await course_service.update_course(
            db_session, uuid.uuid4(), CourseUpdate(title="Nonexistent")
        )


@pytest.mark.asyncio
async def test_deactivate_course(db_session: AsyncSession) -> None:
    course = await make_course(db_session, is_active=True)
    assert course.is_active is True

    await course_service.deactivate_course(db_session, course.id)

    found = await course_service.course_repo.get_by_id(db_session, course.id)
    assert found is not None
    assert found.is_active is False

    # Non-existent course
    with pytest.raises(NotFoundError):
        await course_service.deactivate_course(db_session, uuid.uuid4())
