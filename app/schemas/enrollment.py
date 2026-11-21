from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.user import UserRead


class EnrollmentCreate(BaseModel):
    student_ids: list[UUID]


class EnrollmentResult(BaseModel):
    enrolled: list[UUID]
    skipped: list[UUID]


class EnrollmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    course_id: UUID
    student_id: UUID
    enrolled_at: datetime
    student: UserRead | None = None
