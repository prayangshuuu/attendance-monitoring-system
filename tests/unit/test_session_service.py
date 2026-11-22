import uuid
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationAppError,
)
from app.models.enums import SessionStatus, UserRole
from app.models.user import User
from app.schemas.session import (
    RecurringSessionCreate,
    SessionCreate,
    SessionUpdate,
)
from app.services.session_service import (
    ALLOWED_TRANSITIONS,
    CHECKIN_CODE_ALPHABET,
    generate_checkin_code,
    mask_session_for_user,
    on_session_closed,
    session_service,
    validate_transition,
)
from app.utils.clock import Clock
from tests.factories import make_course, make_enrollment, make_session, make_user


class FrozenClock:
    def __init__(self, frozen_time: datetime) -> None:
        self._time = frozen_time

    def now(self) -> datetime:
        return self._time


# -------------------------------------------------------------------------
# Transition Table Tests
# -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("from_status", "to_status", "is_allowed"),
    [(s1, s2, s2 in ALLOWED_TRANSITIONS[s1]) for s1 in SessionStatus for s2 in SessionStatus],
)
def test_all_session_transitions(
    from_status: SessionStatus, to_status: SessionStatus, is_allowed: bool
) -> None:
    if is_allowed:
        validate_transition(from_status, to_status)
    else:
        with pytest.raises(ConflictError) as exc:
            validate_transition(from_status, to_status)
        assert exc.value.code == "INVALID_SESSION_TRANSITION"
        assert f"Cannot transition session from {from_status} to {to_status}" in exc.value.message


def test_code_generation() -> None:
    for _ in range(50):
        code = generate_checkin_code()
        assert len(code) == 6
        assert all(c in CHECKIN_CODE_ALPHABET for c in code)
        assert not any(c in code for c in ("0", "O", "1", "I"))


@pytest.mark.asyncio
async def test_on_session_closed_hook(db_session: AsyncSession) -> None:
    session_obj = await make_session(db_session)
    # Hook runs without raising
    await on_session_closed(db_session, session_obj)


def test_mask_session_for_user() -> None:
    class DummySession:
        id = uuid.uuid4()
        course_id = uuid.uuid4()
        starts_at = datetime.now(UTC)
        ends_at = datetime.now(UTC) + timedelta(hours=1)
        status = SessionStatus.OPEN.value
        late_after_minutes = 10
        checkin_code = "XYZ123"
        code_expires_at = datetime.now(UTC) + timedelta(minutes=15)
        created_at = datetime.now(UTC)
        updated_at = datetime.now(UTC)

    teacher = User(
        id=uuid.uuid4(), email="t@x.com", full_name="T", role=UserRole.TEACHER, hashed_password=""
    )
    student = User(
        id=uuid.uuid4(), email="s@x.com", full_name="S", role=UserRole.STUDENT, hashed_password=""
    )

    s_teacher = mask_session_for_user(DummySession(), teacher)  # type: ignore[arg-type]
    assert s_teacher.checkin_code == "XYZ123"
    assert s_teacher.code_expires_at is not None

    s_student = mask_session_for_user(DummySession(), student)  # type: ignore[arg-type]
    assert s_student.checkin_code is None
    assert s_student.code_expires_at is None


# -------------------------------------------------------------------------
# SessionService: create_session & create_recurring
# -------------------------------------------------------------------------


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


# -------------------------------------------------------------------------
# SessionService: get_session & list_course_sessions
# -------------------------------------------------------------------------


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
        status=SessionStatus.OPEN.value,
        checkin_code="ABC987",
        code_expires_at=datetime.now(UTC) + timedelta(minutes=15),
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

    # 4. Enrolled student -> sees masked code
    res_student = await session_service.get_session(db_session, s.id, student_enrolled)
    assert res_student.id == s.id
    assert res_student.checkin_code is None
    assert res_student.code_expires_at is None

    # 5. Course teacher -> sees code
    res_teacher = await session_service.get_session(db_session, s.id, teacher)
    assert res_teacher.checkin_code == "ABC987"
    assert res_teacher.code_expires_at is not None

    # 6. Admin -> sees code
    res_admin = await session_service.get_session(db_session, s.id, admin)
    assert res_admin.checkin_code == "ABC987"


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
        status=SessionStatus.OPEN.value,
        checkin_code="K7L9M2",
    )

    # 1. Course not found
    with pytest.raises(NotFoundError):
        await session_service.list_course_sessions(db_session, uuid.uuid4(), teacher)

    # 2. Permission denied (other teacher)
    with pytest.raises(PermissionDeniedError):
        await session_service.list_course_sessions(db_session, course.id, other_teacher)

    # 3. Student view -> masked
    res_student = await session_service.list_course_sessions(db_session, course.id, student)
    assert res_student.total == 1
    assert res_student.items[0].checkin_code is None

    # 4. Teacher view -> unmasked
    res_teacher = await session_service.list_course_sessions(db_session, course.id, teacher)
    assert res_teacher.total == 1
    assert res_teacher.items[0].checkin_code == "K7L9M2"


# -------------------------------------------------------------------------
# SessionService: update_session
# -------------------------------------------------------------------------


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

    # 4. Update ends_at <= starts_at (updating only ends_at to before starts_at)
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

    # 7. Valid update without late_after_minutes (branch 261->264)
    updated_no_late = await session_service.update_session(
        db_session,
        s1.id,
        teacher,
        SessionUpdate(
            starts_at=t1 + timedelta(minutes=40),
            ends_at=t1 + timedelta(hours=1, minutes=40),
        ),
    )
    assert updated_no_late.starts_at == t1 + timedelta(minutes=40)
    assert updated_no_late.late_after_minutes == 20

    # 8. Start/end time is None (line 238)
    with patch.object(
        session_service,
        "_get_session_or_404",
        AsyncMock(return_value=(s1, course)),
    ):
        s1.starts_at = None  # in-memory only
        with pytest.raises(ValidationAppError) as exc4:
            await session_service.update_session(
                db_session,
                s1.id,
                teacher,
                SessionUpdate(late_after_minutes=5),
            )
        assert exc4.value.code == "INVALID_SESSION_TIME"


# -------------------------------------------------------------------------
# SessionService: lifecycle (open, regenerate, close, cancel)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_session_lifecycle(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    s = await make_session(db_session, course=course, status=SessionStatus.SCHEDULED.value)

    frozen_now = datetime(2026, 11, 25, 9, 55, tzinfo=UTC)
    clock: Clock = FrozenClock(frozen_now)

    # 1. Session not found
    with pytest.raises(NotFoundError):
        await session_service.open_session(db_session, uuid.uuid4(), teacher, clock=clock)

    # 2. Non-owner teacher -> 403
    with pytest.raises(PermissionDeniedError):
        await session_service.open_session(db_session, s.id, other_teacher, clock=clock)

    # 3. Success
    open_res = await session_service.open_session(db_session, s.id, teacher, clock=clock)
    assert open_res.status == SessionStatus.OPEN
    assert len(open_res.checkin_code) == 6
    assert open_res.code_expires_at == frozen_now + timedelta(minutes=15)

    # 4. Open again -> ConflictError (invalid transition OPEN -> OPEN)
    with pytest.raises(ConflictError) as exc:
        await session_service.open_session(db_session, s.id, teacher, clock=clock)
    assert exc.value.code == "INVALID_SESSION_TRANSITION"


@pytest.mark.asyncio
async def test_regenerate_code_lifecycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)
    s = await make_session(
        db_session,
        course=course,
        status=SessionStatus.SCHEDULED.value,
    )

    # 1. Regenerate while scheduled -> ConflictError
    with pytest.raises(ConflictError) as exc:
        await session_service.regenerate_code(db_session, s.id, teacher)
    assert exc.value.code == "INVALID_SESSION_STATUS"

    # Open session first
    s.status = SessionStatus.OPEN.value
    s.checkin_code = "OLD123"
    await db_session.flush()

    # 2. Session not found
    with pytest.raises(NotFoundError):
        await session_service.regenerate_code(db_session, uuid.uuid4(), teacher)

    # 3. Permission denied
    with pytest.raises(PermissionDeniedError):
        await session_service.regenerate_code(db_session, s.id, other_teacher)

    # 4. Success with clock
    new_now = datetime(2026, 11, 25, 10, 10, tzinfo=UTC)
    regen_res = await session_service.regenerate_code(
        db_session, s.id, teacher, clock=FrozenClock(new_now)
    )
    assert regen_res.checkin_code != "OLD123"
    assert regen_res.code_expires_at == new_now + timedelta(minutes=15)

    # 5. Collision loop test (while loop coverage)
    import sys

    ss_module = sys.modules["app.services.session_service"]
    mock_codes = iter(["CURR12", "CURR12", "DIFF34"])
    monkeypatch.setattr(
        ss_module,
        "generate_checkin_code",
        lambda: next(mock_codes),
    )
    s.checkin_code = "CURR12"
    await db_session.flush()
    regen_res2 = await session_service.regenerate_code(db_session, s.id, teacher)
    assert regen_res2.checkin_code == "DIFF34"


@pytest.mark.asyncio
async def test_close_and_cancel_session_lifecycle(db_session: AsyncSession) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher)

    # Close tests
    s_open = await make_session(
        db_session,
        course=course,
        status=SessionStatus.OPEN.value,
        checkin_code="VAL123",
        code_expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    # Close not found
    with pytest.raises(NotFoundError):
        await session_service.close_session(db_session, uuid.uuid4(), teacher)

    # Close permission denied
    with pytest.raises(PermissionDeniedError):
        await session_service.close_session(db_session, s_open.id, other_teacher)

    # Close success
    closed = await session_service.close_session(db_session, s_open.id, teacher)
    assert closed.status == SessionStatus.CLOSED
    assert closed.checkin_code is None
    assert closed.code_expires_at is None

    # Close already closed -> ConflictError
    with pytest.raises(ConflictError) as exc:
        await session_service.close_session(db_session, s_open.id, teacher)
    assert exc.value.code == "INVALID_SESSION_TRANSITION"

    # Cancel tests
    s_sched = await make_session(db_session, course=course, status=SessionStatus.SCHEDULED.value)

    # Cancel not found
    with pytest.raises(NotFoundError):
        await session_service.cancel_session(db_session, uuid.uuid4(), teacher)

    # Cancel permission denied
    with pytest.raises(PermissionDeniedError):
        await session_service.cancel_session(db_session, s_sched.id, other_teacher)

    # Cancel success
    cancelled = await session_service.cancel_session(db_session, s_sched.id, teacher)
    assert cancelled.status == SessionStatus.CANCELLED
    assert cancelled.checkin_code is None

    # Cancel already cancelled -> ConflictError
    with pytest.raises(ConflictError) as exc2:
        await session_service.cancel_session(db_session, s_sched.id, teacher)
    assert exc2.value.code == "INVALID_SESSION_TRANSITION"

    # Cancel from OPEN -> ConflictError
    s_open2 = await make_session(db_session, course=course, status=SessionStatus.OPEN.value)
    with pytest.raises(ConflictError) as exc3:
        await session_service.cancel_session(db_session, s_open2.id, teacher)
    assert exc3.value.code == "INVALID_SESSION_TRANSITION"
