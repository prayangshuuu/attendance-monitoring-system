import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models.enums import SessionStatus
from app.schemas.session import (
    RecurringSessionCreate,
    RecurringSessionResult,
    SessionCreate,
    SessionOpenResponse,
    SessionRead,
    SessionUpdate,
    ensure_utc_datetime,
    validate_tz_datetime,
)


def test_validate_tz_datetime_helper() -> None:
    assert validate_tz_datetime(None) is None
    assert ensure_utc_datetime(None) is None

    # Naive raises ValueError
    naive = datetime(2026, 11, 22, 10, 0)
    with pytest.raises(ValueError, match="Datetime must be timezone-aware"):
        validate_tz_datetime(naive)

    # ensure_utc_datetime attaches UTC to naive
    attached = ensure_utc_datetime(naive)
    assert attached is not None
    assert attached.tzinfo == UTC

    # Non-UTC converts to UTC
    tz_plus_8 = timezone(timedelta(hours=8))
    dt_plus_8 = datetime(2026, 11, 22, 18, 0, tzinfo=tz_plus_8)
    utc_converted = validate_tz_datetime(dt_plus_8)
    assert utc_converted is not None
    assert utc_converted.tzinfo == UTC
    assert utc_converted.hour == 10


def test_session_create_valid() -> None:
    now = datetime.now(UTC)
    ends = now + timedelta(hours=1)
    session_in = SessionCreate(
        starts_at=now,
        ends_at=ends,
        late_after_minutes=15,
    )
    assert session_in.starts_at == now
    assert session_in.ends_at == ends
    assert session_in.late_after_minutes == 15


def test_session_create_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        SessionCreate(
            starts_at="2026-11-22T10:00:00",  # type: ignore[arg-type]
            ends_at="2026-11-22T11:00:00",  # type: ignore[arg-type]
        )
    assert "Datetime must be timezone-aware" in str(exc.value)


def test_session_create_ends_before_starts_rejected() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError) as exc:
        SessionCreate(
            starts_at=now,
            ends_at=now - timedelta(hours=1),
        )
    assert "ends_at must be after starts_at" in str(exc.value)

    # Equal times also rejected
    with pytest.raises(ValidationError) as exc2:
        SessionCreate(
            starts_at=now,
            ends_at=now,
        )
    assert "ends_at must be after starts_at" in str(exc2.value)


def test_session_create_negative_late_after_minutes() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError) as exc:
        SessionCreate(
            starts_at=now,
            ends_at=now + timedelta(hours=1),
            late_after_minutes=-1,
        )
    assert "greater than or equal to 0" in str(exc.value)


def test_session_update_valid_and_invalid() -> None:
    # 1. Empty update is valid
    update = SessionUpdate()
    assert update.starts_at is None
    assert update.ends_at is None
    assert update.late_after_minutes is None

    # 2. Valid partial update
    now = datetime.now(UTC)
    update2 = SessionUpdate(starts_at=now, late_after_minutes=5)
    assert update2.starts_at == now
    assert update2.late_after_minutes == 5

    # 3. Valid both times
    update3 = SessionUpdate(starts_at=now, ends_at=now + timedelta(hours=2))
    assert update3.starts_at == now
    assert update3.ends_at == now + timedelta(hours=2)

    # 4. Invalid ends_at <= starts_at
    with pytest.raises(ValidationError) as exc:
        SessionUpdate(starts_at=now, ends_at=now)
    assert "ends_at must be after starts_at" in str(exc.value)

    # 5. Invalid negative late_after_minutes
    with pytest.raises(ValidationError) as exc2:
        SessionUpdate(late_after_minutes=-5)
    assert "greater than or equal to 0" in str(exc2.value)

    # 6. Naive datetime rejected
    with pytest.raises(ValidationError) as exc3:
        SessionUpdate(starts_at="2026-11-22T10:00:00")  # type: ignore[arg-type]
    assert "Datetime must be timezone-aware" in str(exc3.value)


def test_recurring_session_create_valid() -> None:
    recurring = RecurringSessionCreate(
        weekday=0,
        start_time=time(9, 0),
        end_time=time(10, 30),
        from_date=date(2026, 11, 23),
        to_date=date(2026, 12, 21),
        timezone="Asia/Kuala_Lumpur",
        late_after_minutes=10,
    )
    assert recurring.weekday == 0
    assert recurring.timezone == "Asia/Kuala_Lumpur"


def test_recurring_session_create_validation_errors() -> None:
    # Invalid weekday
    with pytest.raises(ValidationError) as exc:
        RecurringSessionCreate(
            weekday=7,
            start_time=time(9, 0),
            end_time=time(10, 30),
            from_date=date(2026, 11, 23),
            to_date=date(2026, 12, 21),
            timezone="UTC",
        )
    assert "less than or equal to 6" in str(exc.value)

    # Invalid end_time <= start_time
    with pytest.raises(ValidationError) as exc2:
        RecurringSessionCreate(
            weekday=1,
            start_time=time(11, 0),
            end_time=time(10, 0),
            from_date=date(2026, 11, 23),
            to_date=date(2026, 12, 21),
            timezone="UTC",
        )
    assert "end_time must be after start_time" in str(exc2.value)

    # Invalid to_date < from_date
    with pytest.raises(ValidationError) as exc3:
        RecurringSessionCreate(
            weekday=1,
            start_time=time(9, 0),
            end_time=time(10, 0),
            from_date=date(2026, 12, 21),
            to_date=date(2026, 11, 23),
            timezone="UTC",
        )
    assert "to_date must be greater than or equal to from_date" in str(exc3.value)

    # Invalid timezone
    with pytest.raises(ValidationError) as exc4:
        RecurringSessionCreate(
            weekday=1,
            start_time=time(9, 0),
            end_time=time(10, 0),
            from_date=date(2026, 11, 23),
            to_date=date(2026, 12, 21),
            timezone="Mars/Olympus_Mons",
        )
    assert "Invalid timezone: Mars/Olympus_Mons" in str(exc4.value)

    # Negative late_after_minutes
    with pytest.raises(ValidationError) as exc5:
        RecurringSessionCreate(
            weekday=1,
            start_time=time(9, 0),
            end_time=time(10, 0),
            from_date=date(2026, 11, 23),
            to_date=date(2026, 12, 21),
            timezone="UTC",
            late_after_minutes=-1,
        )
    assert "greater than or equal to 0" in str(exc5.value)


def test_session_read_and_open_response_schemas() -> None:
    now = datetime.now(UTC)
    session_id = uuid.uuid4()
    course_id = uuid.uuid4()

    read = SessionRead(
        id=session_id,
        course_id=course_id,
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        status=SessionStatus.OPEN,
        late_after_minutes=10,
        checkin_code="K7N2P4",
        code_expires_at=now + timedelta(minutes=15),
        created_at=now,
        updated_at=now,
    )
    assert read.id == session_id
    assert read.status == SessionStatus.OPEN
    assert read.checkin_code == "K7N2P4"

    open_res = SessionOpenResponse(
        id=session_id,
        status=SessionStatus.OPEN,
        checkin_code="K7N2P4",
        code_expires_at=now + timedelta(minutes=15),
    )
    assert open_res.checkin_code == "K7N2P4"

    recurring_res = RecurringSessionResult(created=[read], skipped=2)
    assert len(recurring_res.created) == 1
    assert recurring_res.skipped == 2
