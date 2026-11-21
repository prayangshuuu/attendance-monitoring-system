import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.enums import AttendanceMethod, AttendanceStatus
from app.schemas.attendance import (
    AttendanceRead,
    AttendanceUpdate,
    BulkMarkRequest,
    CheckInRequest,
    ManualMarkItem,
    SessionAttendanceRosterItem,
    SessionAttendanceRosterResponse,
)


def test_check_in_request_valid() -> None:
    req = CheckInRequest(code="ABC123")
    assert req.code == "ABC123"


def test_check_in_request_invalid_empty() -> None:
    with pytest.raises(ValidationError):
        CheckInRequest(code="")


def test_attendance_read_valid() -> None:
    rec_id = uuid.uuid4()
    s_id = uuid.uuid4()
    stu_id = uuid.uuid4()
    now = datetime.now(UTC)

    read = AttendanceRead(
        id=rec_id,
        session_id=s_id,
        student_id=stu_id,
        status=AttendanceStatus.PRESENT,
        checked_in_at=now,
        marked_by=None,
        method=AttendanceMethod.CODE,
        note=None,
        created_at=now,
        updated_at=now,
    )
    assert read.id == rec_id
    assert read.status == AttendanceStatus.PRESENT
    assert read.method == AttendanceMethod.CODE


def test_manual_mark_item_and_bulk_request() -> None:
    s_id = uuid.uuid4()
    item = ManualMarkItem(
        student_id=s_id,
        status=AttendanceStatus.LATE,
        note="Traffic delay",
    )
    assert item.student_id == s_id
    assert item.status == AttendanceStatus.LATE
    assert item.note == "Traffic delay"

    req = BulkMarkRequest(items=[item])
    assert len(req.items) == 1
    assert req.items[0].student_id == s_id


def test_attendance_update() -> None:
    up = AttendanceUpdate(status=AttendanceStatus.EXCUSED, note="Doctor note provided")
    assert up.status == AttendanceStatus.EXCUSED
    assert up.note == "Doctor note provided"


def test_session_attendance_roster_items_and_response() -> None:
    s_id = uuid.uuid4()
    c_id = uuid.uuid4()
    stu_id = uuid.uuid4()
    now = datetime.now(UTC)

    item1 = SessionAttendanceRosterItem(
        student_id=stu_id,
        student_name="Alice Smith",
        student_email="alice@example.com",
        student_code="STU001",
        record_id=uuid.uuid4(),
        status=AttendanceStatus.PRESENT,
        checked_in_at=now,
        method=AttendanceMethod.CODE,
        note=None,
    )

    item2 = SessionAttendanceRosterItem(
        student_id=uuid.uuid4(),
        student_name="Bob Jones",
        student_email="bob@example.com",
    )

    resp = SessionAttendanceRosterResponse(
        session_id=s_id,
        course_id=c_id,
        items=[item1, item2],
    )
    assert resp.session_id == s_id
    assert resp.course_id == c_id
    assert len(resp.items) == 2
    assert resp.items[0].status == AttendanceStatus.PRESENT
    assert resp.items[1].status is None
