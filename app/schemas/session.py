from datetime import UTC, date, datetime, time
from typing import Annotated, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.functional_validators import AfterValidator

from app.models.enums import SessionStatus


def validate_tz_datetime(v: datetime | None) -> datetime | None:
    if v is None:
        return None
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("Datetime must be timezone-aware")
    return v.astimezone(UTC)


def ensure_utc_datetime(v: datetime | None) -> datetime | None:
    if v is None:
        return None
    if v.tzinfo is None:
        return v.replace(tzinfo=UTC)
    return v.astimezone(UTC)


TzAwareDatetime = Annotated[datetime, AfterValidator(validate_tz_datetime)]
UtcDatetime = Annotated[datetime, AfterValidator(ensure_utc_datetime)]


class SessionCreate(BaseModel):
    starts_at: TzAwareDatetime
    ends_at: TzAwareDatetime
    late_after_minutes: int = Field(default=10, ge=0)

    @model_validator(mode="after")
    def validate_ends_after_starts(self) -> Self:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class SessionUpdate(BaseModel):
    starts_at: TzAwareDatetime | None = None
    ends_at: TzAwareDatetime | None = None
    late_after_minutes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_ends_after_starts(self) -> Self:
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("ends_at must be after starts_at")
        return self


class RecurringSessionCreate(BaseModel):
    weekday: int = Field(..., ge=0, le=6, description="0=Monday..6=Sunday")
    start_time: time
    end_time: time
    from_date: date
    to_date: date
    timezone: str
    late_after_minutes: int = Field(default=10, ge=0)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError(f"Invalid timezone: {v}") from None
        return v

    @model_validator(mode="after")
    def validate_times_and_dates(self) -> Self:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if self.to_date < self.from_date:
            raise ValueError("to_date must be greater than or equal to from_date")
        return self


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    course_id: UUID
    starts_at: UtcDatetime
    ends_at: UtcDatetime
    status: SessionStatus
    late_after_minutes: int
    checkin_code: str | None = None
    code_expires_at: UtcDatetime | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime


SessionResponse = SessionRead


class SessionOpenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: SessionStatus
    checkin_code: str
    code_expires_at: UtcDatetime


class RecurringSessionResult(BaseModel):
    created: list[SessionRead]
    skipped: int
