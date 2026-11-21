from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPkMixin
from app.models.enums import UserRole

if TYPE_CHECKING:
    from app.models.attendance_record import AttendanceRecord
    from app.models.course import Course
    from app.models.enrollment import Enrollment


class User(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'teacher', 'student')",
            name="role",
        ),
    )

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=UserRole.STUDENT)
    student_id: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    courses_taught: Mapped[list["Course"]] = relationship(
        back_populates="teacher",
        lazy="raise",
    )
    enrollments: Mapped[list["Enrollment"]] = relationship(
        back_populates="student",
        lazy="raise",
    )
    attendance_records: Mapped[list["AttendanceRecord"]] = relationship(
        foreign_keys="AttendanceRecord.student_id",
        back_populates="student",
        lazy="raise",
    )
    marked_attendance_records: Mapped[list["AttendanceRecord"]] = relationship(
        foreign_keys="AttendanceRecord.marked_by",
        back_populates="marker",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"
