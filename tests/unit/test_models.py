import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.attendance_record import AttendanceRecord
from app.models.class_session import ClassSession
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.enums import AttendanceMethod, AttendanceStatus, SessionStatus, UserRole
from app.models.user import User
from tests.factories import make_course, make_enrollment, make_record, make_session, make_user


async def test_create_and_read_entities_with_relationships(db_session: AsyncSession) -> None:
    """Create one of each entity and read back with explicit selectinload."""
    # 1. Create entities
    teacher = await make_user(
        db_session,
        email="prof.alan@university.edu",
        full_name="Alan Turing",
        role=UserRole.TEACHER,
    )
    course = await make_course(
        db_session,
        teacher=teacher,
        code="CS501",
        title="Theoretical Computer Science",
        min_attendance_pct=85.0,
    )
    student = await make_user(
        db_session,
        email="ada.lovelace@university.edu",
        full_name="Ada Lovelace",
        role=UserRole.STUDENT,
        student_id="STU_ADA_001",
    )
    enrollment = await make_enrollment(
        db_session,
        course=course,
        student=student,
    )
    now = datetime.now(UTC)
    session_obj = await make_session(
        db_session,
        course=course,
        starts_at=now,
        ends_at=now + timedelta(hours=2),
        status=SessionStatus.OPEN,
        checkin_code="K9X2P1",
        late_after_minutes=15,
    )
    marker = await make_user(
        db_session,
        email="admin.grace@university.edu",
        full_name="Grace Hopper",
        role=UserRole.ADMIN,
    )
    record = await make_record(
        db_session,
        class_session=session_obj,
        student=student,
        status=AttendanceStatus.PRESENT,
        method=AttendanceMethod.CODE,
        marker=marker,
        note="Checked in with passcode",
    )

    # 2. Query back with selectinload
    # Query Course with teacher, enrollments, sessions
    stmt_course = (
        select(Course)
        .where(Course.id == course.id)
        .options(
            selectinload(Course.teacher),
            selectinload(Course.enrollments).selectinload(Enrollment.student),
            selectinload(Course.sessions).selectinload(ClassSession.attendance_records),
        )
    )
    res_course = await db_session.execute(stmt_course)
    loaded_course = res_course.scalar_one()

    assert loaded_course.code == "CS501"
    assert loaded_course.teacher.email == "prof.alan@university.edu"
    assert len(loaded_course.enrollments) == 1
    assert loaded_course.enrollments[0].student.full_name == "Ada Lovelace"
    assert len(loaded_course.sessions) == 1
    assert loaded_course.sessions[0].checkin_code == "K9X2P1"

    # Query ClassSession with course, attendance_records -> student and marker
    stmt_session = (
        select(ClassSession)
        .where(ClassSession.id == session_obj.id)
        .options(
            selectinload(ClassSession.course),
            selectinload(ClassSession.attendance_records).selectinload(AttendanceRecord.student),
            selectinload(ClassSession.attendance_records).selectinload(AttendanceRecord.marker),
        )
    )
    res_session = await db_session.execute(stmt_session)
    loaded_session = res_session.scalar_one()

    assert loaded_session.course.id == course.id
    assert len(loaded_session.attendance_records) == 1
    loaded_record = loaded_session.attendance_records[0]
    assert loaded_record.student.id == student.id
    assert loaded_record.marker is not None
    assert loaded_record.marker.id == marker.id
    assert loaded_record.status == AttendanceStatus.PRESENT
    assert loaded_record.method == AttendanceMethod.CODE

    # Query User (teacher) with courses_taught
    stmt_teacher = (
        select(User).where(User.id == teacher.id).options(selectinload(User.courses_taught))
    )
    res_teacher = await db_session.execute(stmt_teacher)
    loaded_teacher = res_teacher.scalar_one()
    assert len(loaded_teacher.courses_taught) == 1
    assert loaded_teacher.courses_taught[0].id == course.id

    # Query User (student) with enrollments and attendance_records
    stmt_student = (
        select(User)
        .where(User.id == student.id)
        .options(
            selectinload(User.enrollments),
            selectinload(User.attendance_records),
        )
    )
    res_student = await db_session.execute(stmt_student)
    loaded_student = res_student.scalar_one()
    assert len(loaded_student.enrollments) == 1
    assert loaded_student.enrollments[0].course_id == course.id
    assert len(loaded_student.attendance_records) == 1
    assert loaded_student.attendance_records[0].id == record.id

    # Query User (marker) with marked_attendance_records
    stmt_marker = (
        select(User)
        .where(User.id == marker.id)
        .options(selectinload(User.marked_attendance_records))
    )
    res_marker = await db_session.execute(stmt_marker)
    loaded_marker = res_marker.scalar_one()
    assert len(loaded_marker.marked_attendance_records) == 1
    assert loaded_marker.marked_attendance_records[0].id == record.id

    # Verify __repr__ on all models
    assert f"<User id={teacher.id}" in repr(teacher)
    assert f"<Course id={course.id}" in repr(course)
    assert f"<Enrollment id={enrollment.id}" in repr(enrollment)
    assert f"<ClassSession id={session_obj.id}" in repr(session_obj)
    assert f"<AttendanceRecord id={record.id}" in repr(record)


async def test_lazy_raise_on_unloaded_relationships(db_session: AsyncSession) -> None:
    """Verify accessing unloaded relationships triggers InvalidRequestError due to lazy='raise'."""
    course = await make_course(db_session)
    await db_session.flush()

    # Expunge all to clear the session identity map so objects are not cached
    db_session.expunge_all()

    stmt = select(Course).where(Course.id == course.id)
    res = await db_session.execute(stmt)
    unloaded_course = res.scalar_one()

    with pytest.raises(
        InvalidRequestError, match=r"'Course\.teacher' is not available due to lazy='raise'"
    ):
        _ = unloaded_course.teacher

    with pytest.raises(
        InvalidRequestError, match=r"'Course\.enrollments' is not available due to lazy='raise'"
    ):
        _ = unloaded_course.enrollments

    with pytest.raises(
        InvalidRequestError, match=r"'Course\.sessions' is not available due to lazy='raise'"
    ):
        _ = unloaded_course.sessions


async def test_duplicate_email_raises_integrity_error(db_session: AsyncSession) -> None:
    """Duplicate email violates unique constraint -> IntegrityError."""
    await make_user(db_session, email="unique@example.com")

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_user(db_session, email="unique@example.com")


async def test_duplicate_student_id_raises_integrity_error(db_session: AsyncSession) -> None:
    """Duplicate student_id violates unique constraint -> IntegrityError."""
    await make_user(db_session, student_id="STU_DUP_123")

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_user(db_session, student_id="STU_DUP_123")


async def test_duplicate_course_code_raises_integrity_error(db_session: AsyncSession) -> None:
    """Duplicate course code violates unique constraint -> IntegrityError."""
    await make_course(db_session, code="MATH101")

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_course(db_session, code="MATH101")


async def test_duplicate_enrollment_raises_integrity_error(db_session: AsyncSession) -> None:
    """Duplicate (course_id, student_id) enrollment -> IntegrityError."""
    course = await make_course(db_session)
    student = await make_user(db_session, role=UserRole.STUDENT)

    await make_enrollment(db_session, course=course, student=student)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_enrollment(db_session, course=course, student=student)


async def test_duplicate_attendance_record_raises_integrity_error(
    db_session: AsyncSession,
) -> None:
    """Duplicate (session_id, student_id) attendance record -> IntegrityError."""
    session_obj = await make_session(db_session)
    student = await make_user(db_session, role=UserRole.STUDENT)

    await make_record(db_session, class_session=session_obj, student=student)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_record(db_session, class_session=session_obj, student=student)


async def test_session_ends_at_before_starts_at_raises_integrity_error(
    db_session: AsyncSession,
) -> None:
    """ends_at <= starts_at violates check constraint -> IntegrityError."""
    course = await make_course(db_session)
    now = datetime.now(UTC)

    # Case 1: ends_at == starts_at
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_session(db_session, course=course, starts_at=now, ends_at=now)

    # Case 2: ends_at < starts_at
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_session(
                db_session,
                course=course,
                starts_at=now,
                ends_at=now - timedelta(minutes=15),
            )


async def test_user_invalid_role_check_constraint(db_session: AsyncSession) -> None:
    """Invalid role outside admin, teacher, student violates check constraint."""
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_user(db_session, role="superuser")


async def test_course_invalid_min_attendance_pct_check_constraint(
    db_session: AsyncSession,
) -> None:
    """min_attendance_pct < 0 or > 100 violates check constraint."""
    # Negative
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_course(db_session, min_attendance_pct=-1.0)

    # Greater than 100
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_course(db_session, min_attendance_pct=100.5)


async def test_session_negative_late_after_minutes_check_constraint(
    db_session: AsyncSession,
) -> None:
    """late_after_minutes < 0 violates check constraint."""
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_session(db_session, late_after_minutes=-1)


async def test_session_invalid_status_check_constraint(db_session: AsyncSession) -> None:
    """status not in scheduled, open, closed, cancelled violates check constraint."""
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_session(db_session, status="finished")


async def test_attendance_record_invalid_status_check_constraint(
    db_session: AsyncSession,
) -> None:
    """status not in present, late, absent, excused violates check constraint."""
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_record(db_session, status="sick")


async def test_attendance_record_invalid_method_check_constraint(
    db_session: AsyncSession,
) -> None:
    """method not in code, manual violates check constraint."""
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_record(db_session, method="biometric")


async def test_foreign_key_constraints_enforced(db_session: AsyncSession) -> None:
    """Nonexistent foreign key targets raise IntegrityError when foreign keys are enabled."""
    non_existent_id = uuid.uuid4()

    # Course with non-existent teacher
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_course(db_session, teacher_id=non_existent_id)

    # Enrollment with non-existent course
    student = await make_user(db_session, role=UserRole.STUDENT)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_enrollment(db_session, student=student, course_id=non_existent_id)

    # ClassSession with non-existent course
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_session(db_session, course_id=non_existent_id)

    # AttendanceRecord with non-existent session
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await make_record(db_session, student=student, session_id=non_existent_id)


async def test_cascade_delete_course_and_session(db_session: AsyncSession) -> None:
    """Deleting a course cascades deletion to sessions and enrollments, and sessions to records."""
    course = await make_course(db_session)
    student = await make_user(db_session, role=UserRole.STUDENT)
    enrollment = await make_enrollment(db_session, course=course, student=student)
    session_obj = await make_session(db_session, course=course)
    record = await make_record(db_session, class_session=session_obj, student=student)

    # Delete course
    await db_session.delete(course)
    await db_session.flush()

    # Verify enrollments, sessions, and records are cascaded deleted
    enrollment_check = await db_session.execute(
        select(Enrollment).where(Enrollment.id == enrollment.id)
    )
    assert enrollment_check.scalar_one_or_none() is None

    session_check = await db_session.execute(
        select(ClassSession).where(ClassSession.id == session_obj.id)
    )
    assert session_check.scalar_one_or_none() is None

    record_check = await db_session.execute(
        select(AttendanceRecord).where(AttendanceRecord.id == record.id)
    )
    assert record_check.scalar_one_or_none() is None


async def test_teacher_fk_ondelete_restrict(db_session: AsyncSession) -> None:
    """Deleting a teacher with existing courses should fail due to RESTRICT ondelete."""
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    await make_course(db_session, teacher=teacher)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.delete(teacher)
            await db_session.flush()
