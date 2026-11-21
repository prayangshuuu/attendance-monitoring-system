import re
from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator

from app.models.enums import UserRole

PASSWORD_REGEX = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")
PASSWORD_REQUIREMENT_MSG = (
    "Password must be at least 8 characters long and contain at least one letter and one number"
)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    role: UserRole
    student_id: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# Alias for backward compatibility
UserResponse = UserRead


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    role: UserRole
    password: str
    student_id: str | None = None

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower().strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if not PASSWORD_REGEX.match(v):
            raise ValueError(PASSWORD_REQUIREMENT_MSG)
        return v

    @field_validator("student_id")
    @classmethod
    def strip_student_id(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip()
            return stripped if stripped else None
        return None

    @model_validator(mode="after")
    def validate_student_id_and_role(self) -> Self:
        if self.role == UserRole.STUDENT:
            if not self.student_id:
                raise ValueError("student_id is required when role is student")
        else:
            if self.student_id is not None:
                raise ValueError(f"student_id must be None for {self.role.value} role")
        return self


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = None
    role: UserRole | None = None
    student_id: str | None = None
    is_active: bool | None = None

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str | None) -> str | None:
        if v is not None:
            return v.lower().strip()
        return None

    @field_validator("student_id")
    @classmethod
    def strip_student_id(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip()
            return stripped if stripped else None
        return None

    @model_validator(mode="after")
    def validate_student_id_and_role(self) -> Self:
        if self.role is not None:
            if self.role == UserRole.STUDENT:
                if not self.student_id:
                    raise ValueError("student_id is required when role is student")
            else:
                if self.student_id is not None:
                    raise ValueError(f"student_id must be None for {self.role.value} role")
        return self


class PasswordChange(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if not PASSWORD_REGEX.match(v):
            raise ValueError(PASSWORD_REQUIREMENT_MSG)
        return v
