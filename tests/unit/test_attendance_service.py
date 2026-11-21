import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

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
from app.services.attendance_service import attendance_service
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
