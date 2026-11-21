import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationAppError,
)
from app.models.enums import SessionStatus, UserRole
from app.schemas.session import (
    RecurringSessionCreate,
    SessionCreate,
    SessionUpdate,
)
from app.services.session_service import session_service
from tests.factories import make_course, make_enrollment, make_session, make_user


@pytest.mark.asyncio
async def test_create_session_success_and_errors(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    now = datetime.now(UTC)

    payload = SessionCreate(
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=2),
        late_after_minutes=15,
    )

    # 1. Course not found
    with pytest.raises(NotFoundError) as exc:
        await session_service.create_session(db_session, uuid.uuid4(), teacher, payload)
    assert exc.value.code == "COURSE_NOT_FOUND"

    # 2. Permission denied (other teacher)
    with pytest.raises(PermissionDeniedError):
        await session_service.create_session(db_session, course.id, other_teacher, payload)

    # 3. Success (teacher)
    session_read = await session_service.create_session(db_session, course.id, teacher, payload)
    assert session_read.course_id == course.id
    assert session_read.status == SessionStatus.SCHEDULED
    assert session_read.late_after_minutes == 15

    # 4. Overlap conflict
    with pytest.raises(ConflictError) as exc2:
        await session_service.create_session(db_session, course.id, teacher, payload)
    assert exc2.value.code == "SESSION_OVERLAP"


@pytest.mark.asyncio
async def test_create_recurring_sessions(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)

    payload = RecurringSessionCreate(
        weekday=0,  # Monday
        start_time=time(10, 0),
        end_time=time(11, 30),
        from_date=date(2026, 3, 20),
        to_date=date(2026, 4, 10),
        timezone="Europe/London",  # DST change on March 29, 2026!
        late_after_minutes=10,
    )

    # 1. Course not found
    with pytest.raises(NotFoundError):
        await session_service.create_recurring(db_session, uuid.uuid4(), teacher, payload)

    # 2. Permission denied
    with pytest.raises(PermissionDeniedError):
        await session_service.create_recurring(db_session, course.id, other_teacher, payload)

    # 3. Create recurring (Mondays: March 23, March 30, April 6)
    res = await session_service.create_recurring(db_session, course.id, teacher, payload)
    assert len(res.created) == 3
    assert res.skipped == 0

    # Verify DST transition in London:
    # March 23 (GMT, UTC+0): 10:00 local is 10:00 UTC
    assert res.created[0].starts_at == datetime(2026, 3, 23, 10, 0, tzinfo=UTC)
    # March 30 (BST, UTC+1): 10:00 local is 09:00 UTC
    assert res.created[1].starts_at == datetime(2026, 3, 30, 9, 0, tzinfo=UTC)
    # April 6 (BST, UTC+1): 10:00 local is 09:00 UTC
    assert res.created[2].starts_at == datetime(2026, 4, 6, 9, 0, tzinfo=UTC)

    # 4. Calling again skips all 3 overlapping sessions
    res2 = await session_service.create_recurring(db_session, course.id, teacher, payload)
    assert len(res2.created) == 0
    assert res2.skipped == 3


@pytest.mark.asyncio
async def test_get_session_and_permissions(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    student_enrolled = await make_user(db_session, role=UserRole.STUDENT)
    student_not_enrolled = await make_user(db_session, role=UserRole.STUDENT)
    admin = await make_user(db_session, role=UserRole.ADMIN)

    course = await make_course(db_session, teacher=teacher)
    await make_enrollment(db_session, course=course, student=student_enrolled)

    s = await make_session(
        db_session,
        course=course,
        status=SessionStatus.SCHEDULED.value,
    )

    # 1. Session not found
    with pytest.raises(NotFoundError) as exc:
        await session_service.get_session(db_session, uuid.uuid4(), teacher)
    assert exc.value.code == "SESSION_NOT_FOUND"

    # 2. Non-course teacher -> 403
    with pytest.raises(PermissionDeniedError):
        await session_service.get_session(db_session, s.id, other_teacher)

    # 3. Non-enrolled student -> 403
    with pytest.raises(PermissionDeniedError):
        await session_service.get_session(db_session, s.id, student_not_enrolled)

    # 4. Enrolled student -> succeeds
    res_student = await session_service.get_session(db_session, s.id, student_enrolled)
    assert res_student.id == s.id

    # 5. Course teacher -> succeeds
    res_teacher = await session_service.get_session(db_session, s.id, teacher)
    assert res_teacher.id == s.id

    # 6. Admin -> succeeds
    res_admin = await session_service.get_session(db_session, s.id, admin)
    assert res_admin.id == s.id


@pytest.mark.asyncio
async def test_list_course_sessions(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT)

    course = await make_course(db_session, teacher=teacher)
    await make_enrollment(db_session, course=course, student=student)

    t1 = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    await make_session(
        db_session,
        course=course,
        starts_at=t1,
        ends_at=t1 + timedelta(hours=1),
        status=SessionStatus.SCHEDULED.value,
    )

    # 1. Course not found
    with pytest.raises(NotFoundError):
        await session_service.list_course_sessions(db_session, uuid.uuid4(), teacher)

    # 2. Permission denied (other teacher)
    with pytest.raises(PermissionDeniedError):
        await session_service.list_course_sessions(db_session, course.id, other_teacher)

    # 3. Student view
    res_student = await session_service.list_course_sessions(db_session, course.id, student)
    assert res_student.total == 1

    # 4. Teacher view
    res_teacher = await session_service.list_course_sessions(db_session, course.id, teacher)
    assert res_teacher.total == 1


@pytest.mark.asyncio
async def test_update_session(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)

    t1 = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    s1 = await make_session(
        db_session,
        course=course,
        starts_at=t1,
        ends_at=t1 + timedelta(hours=1),
        status=SessionStatus.SCHEDULED.value,
    )
    t2 = datetime(2026, 11, 25, 14, 0, tzinfo=UTC)
    await make_session(
        db_session,
        course=course,
        starts_at=t2,
        ends_at=t2 + timedelta(hours=1),
        status=SessionStatus.SCHEDULED.value,
    )

    # 1. Session not found
    with pytest.raises(NotFoundError):
        await session_service.update_session(
            db_session, uuid.uuid4(), teacher, SessionUpdate(late_after_minutes=5)
        )

    # 2. Permission denied
    with pytest.raises(PermissionDeniedError):
        await session_service.update_session(
            db_session, s1.id, other_teacher, SessionUpdate(late_after_minutes=5)
        )

    # 3. Not SCHEDULED -> ConflictError
    s1.status = SessionStatus.OPEN.value
    await db_session.flush()
    with pytest.raises(ConflictError) as exc:
        await session_service.update_session(
            db_session, s1.id, teacher, SessionUpdate(late_after_minutes=5)
        )
    assert exc.value.code == "INVALID_SESSION_STATUS"

    s1.status = SessionStatus.SCHEDULED.value
    await db_session.flush()

    # 4. Update ends_at <= starts_at
    with pytest.raises(ValidationAppError) as exc2:
        await session_service.update_session(
            db_session, s1.id, teacher, SessionUpdate(ends_at=t1 - timedelta(hours=1))
        )
    assert exc2.value.code == "VALIDATION_ERROR"

    # 5. Overlap with s2
    with pytest.raises(ConflictError) as exc3:
        await session_service.update_session(
            db_session,
            s1.id,
            teacher,
            SessionUpdate(starts_at=t2, ends_at=t2 + timedelta(hours=1)),
        )
    assert exc3.value.code == "SESSION_OVERLAP"

    # 6. Valid update
    updated = await session_service.update_session(
        db_session,
        s1.id,
        teacher,
        SessionUpdate(
            starts_at=t1 + timedelta(minutes=30),
            ends_at=t1 + timedelta(hours=1, minutes=30),
            late_after_minutes=20,
        ),
    )
    assert updated.late_after_minutes == 20
    assert updated.starts_at == t1 + timedelta(minutes=30)
