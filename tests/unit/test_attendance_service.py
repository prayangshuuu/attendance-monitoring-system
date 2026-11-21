import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationAppError,
)
from app.models.attendance_record import AttendanceRecord
from app.models.enums import AttendanceMethod, AttendanceStatus, SessionStatus, UserRole
from app.models.user import User
from app.repositories.session_repository import session_repository
from app.schemas.attendance import ManualMarkItem
from app.services.attendance_service import attendance_service
from app.services.session_service import session_service
from tests.factories import make_course, make_enrollment, make_record, make_session, make_user


class FrozenClock:
    def __init__(self, frozen_time: datetime) -> None:
        self._time = frozen_time

    def now(self) -> datetime:
        return self._time


# ---------------------------------------------------------------------------
# check_in Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_in_order_of_validations(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    student = await make_user(db_session, role=UserRole.STUDENT)
    other_student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)

    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    clock = FrozenClock(now)

    # 1. SESSION_NOT_FOUND (404)
    with pytest.raises(NotFoundError) as exc:
        await attendance_service.check_in(db_session, student, uuid.uuid4(), "ABC123", clock=clock)
    assert exc.value.code == "SESSION_NOT_FOUND"
    assert exc.value.status_code == 404

    # Create a scheduled session (not open)
    session_obj = await make_session(
        db_session,
        course=course,
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        status=SessionStatus.SCHEDULED.value,
        checkin_code="K7M9P2",
        code_expires_at=now + timedelta(minutes=15),
    )

    # 2. NOT_ENROLLED (403) comes before SESSION_NOT_OPEN
    with pytest.raises(PermissionDeniedError) as exc:
        await attendance_service.check_in(
            db_session, other_student, session_obj.id, "K7M9P2", clock=clock
        )
    assert exc.value.code == "NOT_ENROLLED"
    assert exc.value.status_code == 403

    # 3. SESSION_NOT_OPEN (409)
    with pytest.raises(ConflictError) as exc:
        await attendance_service.check_in(
            db_session, student, session_obj.id, "K7M9P2", clock=clock
        )
    assert exc.value.code == "SESSION_NOT_OPEN"
    assert exc.value.status_code == 409

    # Now open session
    session_obj.status = SessionStatus.OPEN.value
    await db_session.flush()

    # 4. INVALID_CODE (400)
    with pytest.raises(ValidationAppError) as exc:
        await attendance_service.check_in(
            db_session, student, session_obj.id, "WRONG1", clock=clock
        )
    assert exc.value.code == "INVALID_CODE"
    assert exc.value.status_code == 400

    # 5. CODE_EXPIRED (400) - expired code check
    future_clock = FrozenClock(now + timedelta(minutes=20))
    with pytest.raises(ValidationAppError) as exc:
        await attendance_service.check_in(
            db_session, student, session_obj.id, "k7m9p2", clock=future_clock
        )
    assert exc.value.code == "CODE_EXPIRED"
    assert exc.value.status_code == 400

    # Successful check-in (case-insensitive code test)
    record = await attendance_service.check_in(
        db_session, student, session_obj.id, "k7m9p2", clock=clock
    )
    assert record.status == AttendanceStatus.PRESENT.value
    assert record.method == AttendanceMethod.CODE.value
    assert record.checked_in_at is not None and record.checked_in_at.replace(tzinfo=UTC) == now

    # 6. ALREADY_CHECKED_IN (409)
    with pytest.raises(ConflictError) as exc:
        await attendance_service.check_in(
            db_session, student, session_obj.id, "K7M9P2", clock=clock
        )
    assert exc.value.code == "ALREADY_CHECKED_IN"
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_check_in_late_status_calculation(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)

    session_start = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    session_obj = await make_session(
        db_session,
        course=course,
        starts_at=session_start,
        ends_at=session_start + timedelta(hours=2),
        status=SessionStatus.OPEN.value,
        checkin_code="ABCDEF",
        code_expires_at=session_start + timedelta(minutes=30),
        late_after_minutes=10,
    )

    # 11 minutes after starts_at -> past 10 min late threshold -> LATE
    late_time = session_start + timedelta(minutes=11)
    clock = FrozenClock(late_time)

    record = await attendance_service.check_in(
        db_session, student, session_obj.id, "ABCDEF", clock=clock
    )
    assert record.status == AttendanceStatus.LATE.value
    assert record.method == AttendanceMethod.CODE.value


@pytest.mark.asyncio
async def test_check_in_race_safety_integrity_error(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)

    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    session_obj = await make_session(
        db_session,
        course=course,
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        status=SessionStatus.OPEN.value,
        checkin_code="RACE99",
        code_expires_at=now + timedelta(minutes=30),
    )

    # Simulate race condition where commit raises IntegrityError
    with patch.object(
        db_session, "commit", side_effect=IntegrityError("duplicate", None, Exception())
    ):
        with pytest.raises(ConflictError) as exc:
            await attendance_service.check_in(
                db_session, student, session_obj.id, "RACE99", clock=FrozenClock(now)
            )
        assert exc.value.code == "ALREADY_CHECKED_IN"
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_check_in_concurrent_gather() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            teacher = await make_user(s, role=UserRole.TEACHER)
            student = await make_user(s, role=UserRole.STUDENT)
            course = await make_course(s, teacher=teacher)
            await make_enrollment(s, course=course, student=student)
            now = datetime.now(UTC)
            session_obj = await make_session(
                s,
                course=course,
                starts_at=now,
                ends_at=now + timedelta(hours=2),
                status=SessionStatus.OPEN.value,
                checkin_code="CONC01",
                code_expires_at=now + timedelta(minutes=15),
            )
            session_id = session_obj.id
            await s.commit()

        async def do_checkin() -> AttendanceRecord:
            async with AsyncSession(engine, expire_on_commit=False) as s:
                st = await s.get(User, student.id)
                assert st is not None
                return await attendance_service.check_in(s, st, session_id, "CONC01")

        results = await asyncio.gather(do_checkin(), do_checkin(), return_exceptions=True)
        successes = [r for r in results if isinstance(r, AttendanceRecord)]
        conflicts = [
            r for r in results if isinstance(r, ConflictError) and r.code == "ALREADY_CHECKED_IN"
        ]
        assert len(successes) == 1
        assert len(conflicts) == 1

        async with AsyncSession(engine) as s:
            recs = await attendance_service.attendance_repo.get_records_for_session(s, session_id)
            assert len(recs) == 1
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# bulk_mark Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_mark_validations_and_upsert(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    s1 = await make_user(db_session, role=UserRole.STUDENT)
    s2 = await make_user(db_session, role=UserRole.STUDENT)
    unenrolled = await make_user(db_session, role=UserRole.STUDENT)

    await make_enrollment(db_session, course=course, student=s1)
    await make_enrollment(db_session, course=course, student=s2)

    session_obj = await make_session(db_session, course=course)

    # Session not found
    with pytest.raises(NotFoundError) as exc:
        await attendance_service.bulk_mark(db_session, teacher, uuid.uuid4(), [])
    assert exc.value.code == "SESSION_NOT_FOUND"

    # Course not found
    with patch.object(attendance_service.course_repo, "get_by_id", AsyncMock(return_value=None)):
        with pytest.raises(NotFoundError) as exc:
            await attendance_service.bulk_mark(db_session, teacher, session_obj.id, [])
        assert exc.value.code == "COURSE_NOT_FOUND"

    # Permission denied
    with pytest.raises(PermissionDeniedError) as exc:
        await attendance_service.bulk_mark(db_session, other_teacher, session_obj.id, [])
    assert exc.value.code == "PERMISSION_DENIED"

    # Empty items
    assert await attendance_service.bulk_mark(db_session, teacher, session_obj.id, []) == []

    # Student not enrolled
    with pytest.raises(ValidationAppError) as exc:
        await attendance_service.bulk_mark(
            db_session,
            teacher,
            session_obj.id,
            [ManualMarkItem(student_id=unenrolled.id, status=AttendanceStatus.PRESENT)],
        )
    assert exc.value.code == "STUDENT_NOT_ENROLLED"

    # Initial mark: s1 present, s2 absent
    items = [
        ManualMarkItem(student_id=s1.id, status=AttendanceStatus.PRESENT, note="Here"),
        ManualMarkItem(student_id=s2.id, status=AttendanceStatus.ABSENT),
    ]
    records = await attendance_service.bulk_mark(db_session, teacher, session_obj.id, items)
    assert len(records) == 2

    # Second mark (upsert): change s2 to EXCUSED
    items_update = [
        ManualMarkItem(student_id=s2.id, status=AttendanceStatus.EXCUSED, note="Excused note")
    ]
    updated_records = await attendance_service.bulk_mark(
        db_session, teacher, session_obj.id, items_update
    )
    assert len(updated_records) == 1
    assert updated_records[0].status == AttendanceStatus.EXCUSED.value
    assert updated_records[0].marked_by == teacher.id
    assert updated_records[0].method == AttendanceMethod.MANUAL.value


# ---------------------------------------------------------------------------
# update_record Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_record(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)
    session_obj = await make_session(db_session, course=course)

    record = await make_record(
        db_session,
        class_session=session_obj,
        student=student,
        status=AttendanceStatus.ABSENT,
    )

    # Record not found
    with pytest.raises(NotFoundError) as exc:
        await attendance_service.update_record(
            db_session, teacher, uuid.uuid4(), AttendanceStatus.PRESENT
        )
    assert exc.value.code == "ATTENDANCE_RECORD_NOT_FOUND"

    # Permission denied
    with pytest.raises(PermissionDeniedError) as exc:
        await attendance_service.update_record(
            db_session, other_teacher, record.id, AttendanceStatus.PRESENT
        )
    assert exc.value.code == "PERMISSION_DENIED"

    # Close session to verify override after close
    session_obj.status = SessionStatus.CLOSED.value
    await db_session.flush()

    # Success update (override after close works and sets method=manual)
    updated = await attendance_service.update_record(
        db_session,
        teacher,
        record.id,
        AttendanceStatus.LATE,
        note="Arrived 15 mins late",
    )
    assert updated.status == AttendanceStatus.LATE.value
    assert updated.note == "Arrived 15 mins late"
    assert updated.marked_by == teacher.id
    assert updated.method == AttendanceMethod.MANUAL.value


# ---------------------------------------------------------------------------
# get_session_roster Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_roster(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    admin = await make_user(db_session, role=UserRole.ADMIN)
    course = await make_course(db_session, teacher=teacher)
    s1 = await make_user(db_session, role=UserRole.STUDENT, full_name="Alice")
    s2 = await make_user(db_session, role=UserRole.STUDENT, full_name="Bob")

    await make_enrollment(db_session, course=course, student=s1)
    await make_enrollment(db_session, course=course, student=s2)

    session_obj = await make_session(db_session, course=course)

    # Session not found
    with pytest.raises(NotFoundError) as exc:
        await attendance_service.get_session_roster(db_session, teacher, uuid.uuid4())
    assert exc.value.code == "SESSION_NOT_FOUND"

    # Course not found
    with patch.object(attendance_service.course_repo, "get_by_id", AsyncMock(return_value=None)):
        with pytest.raises(NotFoundError) as exc:
            await attendance_service.get_session_roster(db_session, teacher, session_obj.id)
        assert exc.value.code == "COURSE_NOT_FOUND"

    # Permission denied
    with pytest.raises(PermissionDeniedError) as exc:
        await attendance_service.get_session_roster(db_session, other_teacher, session_obj.id)
    assert exc.value.code == "PERMISSION_DENIED"

    # s1 has a record, s2 does not
    now = datetime.now(UTC)
    await make_record(
        db_session,
        class_session=session_obj,
        student=s1,
        status=AttendanceStatus.PRESENT,
        checked_in_at=now,
    )

    roster = await attendance_service.get_session_roster(db_session, teacher, session_obj.id)
    assert roster.session_id == session_obj.id
    assert roster.course_id == course.id
    assert len(roster.items) == 2

    s1_item = next(item for item in roster.items if item.student_id == s1.id)
    assert s1_item.status == AttendanceStatus.PRESENT
    assert s1_item.record_id is not None

    s2_item = next(item for item in roster.items if item.student_id == s2.id)
    assert s2_item.status is None
    assert s2_item.record_id is None

    # Admin can also get roster
    roster_admin = await attendance_service.get_session_roster(db_session, admin, session_obj.id)
    assert len(roster_admin.items) == 2


# ---------------------------------------------------------------------------
# list_student_records Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_student_records(db_session: AsyncSession) -> None:
    student = await make_user(db_session, role=UserRole.STUDENT)
    course = await make_course(db_session)
    session_obj = await make_session(db_session, course=course)

    await make_record(db_session, class_session=session_obj, student=student)

    page = await attendance_service.list_student_records(
        db_session, student, course_id=None, page=1, size=10
    )
    assert page.total == 1
    assert len(page.items) == 1
    assert page.items[0].student_id == student.id


# ---------------------------------------------------------------------------
# mark_absentees and on_session_closed Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_absentees_and_close_session_flow(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    s1 = await make_user(db_session, role=UserRole.STUDENT)
    s2 = await make_user(db_session, role=UserRole.STUDENT)
    s3 = await make_user(db_session, role=UserRole.STUDENT)

    await make_enrollment(db_session, course=course, student=s1)
    await make_enrollment(db_session, course=course, student=s2)
    await make_enrollment(db_session, course=course, student=s3)

    session_obj = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN.value,
        checkin_code="XYZ123",
    )

    # s1 already checked in
    await make_record(
        db_session,
        class_session=session_obj,
        student=s1,
        status=AttendanceStatus.PRESENT,
    )

    # Close session
    closed = await session_service.close_session(db_session, session_obj.id, teacher)
    assert closed.status == SessionStatus.CLOSED

    # Verify s2 and s3 were marked ABSENT, s1 is still PRESENT
    records = await attendance_service.attendance_repo.get_records_for_session(
        db_session, session_obj.id
    )
    assert len(records) == 3
    rec_by_student = {r.student_id: r for r in records}
    assert rec_by_student[s1.id].status == AttendanceStatus.PRESENT.value
    assert rec_by_student[s2.id].status == AttendanceStatus.ABSENT.value
    assert rec_by_student[s3.id].status == AttendanceStatus.ABSENT.value

    # Closing twice -> 409 and no duplicate records
    with pytest.raises(ConflictError) as exc:
        await session_service.close_session(db_session, session_obj.id, teacher)
    assert exc.value.status_code == 409
    records_after = await attendance_service.attendance_repo.get_records_for_session(
        db_session, session_obj.id
    )
    assert len(records_after) == 3


@pytest.mark.asyncio
async def test_close_session_failure_rolls_back_session_closure(
    db_session: AsyncSession,
) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student)

    session_obj = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN.value,
        checkin_code="FLFAIL",
    )
    session_id = session_obj.id
    await db_session.commit()

    # Simulate mark_absentees failure and verify session stays open via atomic rollback
    with (
        patch(
            "app.services.attendance_service.attendance_service.mark_absentees",
            side_effect=RuntimeError("Database failure while marking absentees"),
        ),
        pytest.raises(RuntimeError),
    ):
        await session_service.close_session(db_session, session_id, teacher)

    # Re-query session from repository to ensure the failure rolled back and session remains OPEN
    reloaded = await session_repository.get_by_id(db_session, session_id)
    assert reloaded is not None
    assert reloaded.status == SessionStatus.OPEN.value
    assert reloaded.checkin_code == "FLFAIL"
