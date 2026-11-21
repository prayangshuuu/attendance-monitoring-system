import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attendance_record import AttendanceRecord
from app.models.enums import AttendanceMethod, AttendanceStatus
from app.repositories.attendance_repository import attendance_repository
from tests.factories import make_course, make_record, make_session, make_user


@pytest.mark.asyncio
async def test_create_and_get_by_id(db_session: AsyncSession) -> None:
    session_obj = await make_session(db_session)
    student = await make_user(db_session)

    record = AttendanceRecord(
        session_id=session_obj.id,
        student_id=student.id,
        status=AttendanceStatus.PRESENT.value,
        method=AttendanceMethod.CODE.value,
    )
    created = await attendance_repository.create(db_session, record)
    assert created.id is not None

    # Fetch by UUID
    fetched = await attendance_repository.get_by_id(db_session, created.id)
    assert fetched is not None
    assert fetched.id == created.id
    # Test selectinload session and course
    assert fetched.session.id == session_obj.id
    assert fetched.session.course.id == session_obj.course_id

    # Fetch by string
    fetched_str = await attendance_repository.get_by_id(db_session, str(created.id))
    assert fetched_str is not None
    assert fetched_str.id == created.id

    # Fetch by invalid string
    assert await attendance_repository.get_by_id(db_session, "invalid-uuid") is None

    # Fetch non-existent
    assert await attendance_repository.get_by_id(db_session, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_get_by_session_and_student(db_session: AsyncSession) -> None:
    session_obj = await make_session(db_session)
    student = await make_user(db_session)

    # Not found
    assert (
        await attendance_repository.get_by_session_and_student(
            db_session, session_obj.id, student.id
        )
        is None
    )

    record = await make_record(
        db_session,
        class_session=session_obj,
        student=student,
        status=AttendanceStatus.PRESENT,
    )

    found = await attendance_repository.get_by_session_and_student(
        db_session, session_obj.id, student.id
    )
    assert found is not None
    assert found.id == record.id


@pytest.mark.asyncio
async def test_get_records_for_session(db_session: AsyncSession) -> None:
    session_obj = await make_session(db_session)
    s1 = await make_user(db_session)
    s2 = await make_user(db_session)

    assert await attendance_repository.get_records_for_session(db_session, session_obj.id) == []

    r1 = await make_record(db_session, class_session=session_obj, student=s1)
    r2 = await make_record(db_session, class_session=session_obj, student=s2)

    records = await attendance_repository.get_records_for_session(db_session, session_obj.id)
    assert len(records) == 2
    assert {r.id for r in records} == {r1.id, r2.id}


@pytest.mark.asyncio
async def test_create_many_and_update(db_session: AsyncSession) -> None:
    session_obj = await make_session(db_session)
    s1 = await make_user(db_session)
    s2 = await make_user(db_session)

    # Empty list
    assert await attendance_repository.create_many(db_session, []) == []

    records = [
        AttendanceRecord(
            session_id=session_obj.id,
            student_id=s1.id,
            status=AttendanceStatus.ABSENT.value,
            method=AttendanceMethod.MANUAL.value,
        ),
        AttendanceRecord(
            session_id=session_obj.id,
            student_id=s2.id,
            status=AttendanceStatus.ABSENT.value,
            method=AttendanceMethod.MANUAL.value,
        ),
    ]
    created = await attendance_repository.create_many(db_session, records)
    assert len(created) == 2

    # Update one record
    to_update = created[0]
    to_update.status = AttendanceStatus.EXCUSED.value
    to_update.note = "Medical excuse"
    updated = await attendance_repository.update(db_session, to_update)
    assert updated.status == AttendanceStatus.EXCUSED.value
    assert updated.note == "Medical excuse"


@pytest.mark.asyncio
async def test_list_student_records(db_session: AsyncSession) -> None:
    course1 = await make_course(db_session)
    course2 = await make_course(db_session)

    s1 = await make_session(db_session, course=course1)
    s2 = await make_session(db_session, course=course1)
    s3 = await make_session(db_session, course=course2)

    student = await make_user(db_session)
    other_student = await make_user(db_session)

    await make_record(db_session, class_session=s1, student=student)
    await make_record(db_session, class_session=s2, student=student)
    await make_record(db_session, class_session=s3, student=student)
    await make_record(db_session, class_session=s1, student=other_student)

    # List all for student
    recs, total = await attendance_repository.list_student_records(
        db_session, student_id=student.id, page=1, size=10
    )
    assert total == 3
    assert len(recs) == 3

    # Filter by course1
    recs_c1, total_c1 = await attendance_repository.list_student_records(
        db_session, student_id=student.id, course_id=course1.id, page=1, size=10
    )
    assert total_c1 == 2
    assert len(recs_c1) == 2

    # Filter by course2
    recs_c2, total_c2 = await attendance_repository.list_student_records(
        db_session, student_id=student.id, course_id=course2.id, page=1, size=10
    )
    assert total_c2 == 1
    assert len(recs_c2) == 1

    # Pagination
    recs_page, total_page = await attendance_repository.list_student_records(
        db_session, student_id=student.id, page=2, size=2
    )
    assert total_page == 3
    assert len(recs_page) == 1
