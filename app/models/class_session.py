import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPkMixin
from app.models.enums import SessionStatus

if TYPE_CHECKING:
    from app.models.attendance_record import AttendanceRecord
    from app.models.course import Course


class ClassSession(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "class_sessions"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_at_after_starts_at"),
        CheckConstraint("late_after_minutes >= 0", name="late_after_minutes_non_negative"),
        CheckConstraint(
            "status IN ('scheduled', 'open', 'closed', 'cancelled')",
            name="session_status",
        ),
        Index("ix_class_sessions_course_id_starts_at", "course_id", "starts_at"),
        Index("ix_class_sessions_course_status_starts", "course_id", "status", "starts_at"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=SessionStatus.SCHEDULED,
        nullable=False,
    )
    checkin_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    late_after_minutes: Mapped[int] = mapped_column(Integer, default=10, nullable=False)

    course: Mapped["Course"] = relationship(
        back_populates="sessions",
        lazy="raise",
    )
    attendance_records: Mapped[list["AttendanceRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<ClassSession id={self.id} course_id={self.course_id} status={self.status!r}>"
