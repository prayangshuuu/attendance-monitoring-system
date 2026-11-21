from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class CourseTeacherSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str


class CourseCreate(BaseModel):
    code: str
    title: str
    teacher_id: UUID
    min_attendance_pct: float = 80.0
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def normalize_code(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if not cleaned:
            raise ValueError("Course code cannot be empty")
        return cleaned

    @field_validator("title")
    @classmethod
    def normalize_title(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Course title cannot be empty")
        return cleaned

    @field_validator("min_attendance_pct")
    @classmethod
    def validate_min_attendance_pct(cls, v: float) -> float:
        if v < 0.0 or v > 100.0:
            raise ValueError("min_attendance_pct must be between 0.0 and 100.0")
        return v


class CourseUpdate(BaseModel):
    code: str | None = None
    title: str | None = None
    teacher_id: UUID | None = None
    min_attendance_pct: float | None = None
    is_active: bool | None = None

    @field_validator("code")
    @classmethod
    def normalize_code(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip().upper()
            if not cleaned:
                raise ValueError("Course code cannot be empty")
            return cleaned
        return None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip()
            if not cleaned:
                raise ValueError("Course title cannot be empty")
            return cleaned
        return None

    @field_validator("min_attendance_pct")
    @classmethod
    def validate_min_attendance_pct(cls, v: float | None) -> float | None:
        if v is not None and (v < 0.0 or v > 100.0):
            raise ValueError("min_attendance_pct must be between 0.0 and 100.0")
        return v


class CourseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    title: str
    teacher_id: UUID
    min_attendance_pct: float
    is_active: bool
    teacher: CourseTeacherSummary | None = None
    created_at: datetime
    updated_at: datetime


CourseResponse = CourseRead
