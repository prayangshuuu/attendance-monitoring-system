import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPkMixin

if TYPE_CHECKING:
    from app.models.class_session import ClassSession
    from app.models.enrollment import Enrollment
    from app.models.user import User


class Course(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint(
            "min_attendance_pct >= 0 AND min_attendance_pct <= 100",
            name="min_attendance_pct_range",
        ),
    )

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    min_attendance_pct: Mapped[float] = mapped_column(Float, default=80.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    teacher: Mapped["User"] = relationship(
        back_populates="courses_taught",
        lazy="raise",
    )
    enrollments: Mapped[list["Enrollment"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    sessions: Mapped[list["ClassSession"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Course id={self.id} code={self.code!r} title={self.title!r}>"
