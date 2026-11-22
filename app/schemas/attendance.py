from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AttendanceMethod, AttendanceStatus


class CheckInRequest(BaseModel):
    code: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Session check-in code",
        examples=["ABC123"],
    )


class AttendanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    student_id: UUID
    status: AttendanceStatus
    checked_in_at: datetime | None = None
    marked_by: UUID | None = None
    method: AttendanceMethod
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class ManualMarkItem(BaseModel):
    student_id: UUID
    status: AttendanceStatus
    note: str | None = None


class BulkMarkRequest(BaseModel):
    items: list[ManualMarkItem]


class AttendanceUpdate(BaseModel):
    status: AttendanceStatus
    note: str | None = None


class SessionAttendanceRosterItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    student_id: UUID
    student_name: str
    student_email: str
    student_code: str | None = None
    record_id: UUID | None = None
    status: AttendanceStatus | None = None
    checked_in_at: datetime | None = None
    method: AttendanceMethod | None = None
    note: str | None = None


class SessionAttendanceRosterResponse(BaseModel):
    session_id: UUID
    course_id: UUID
    items: list[SessionAttendanceRosterItem]
