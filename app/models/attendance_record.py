import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPkMixin
from app.models.enums import AttendanceMethod, AttendanceStatus

if TYPE_CHECKING:
    from app.models.class_session import ClassSession
    from app.models.user import User


class AttendanceRecord(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "student_id",
            name="uq_attendance_records_session_id_student_id",
        ),
        CheckConstraint(
            "status IN ('present', 'late', 'absent', 'excused')",
            name="attendance_status",
        ),
        CheckConstraint(
            "method IN ('code', 'manual')",
            name="attendance_method",
        ),
        Index("ix_attendance_records_session_status", "session_id", "status"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("class_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=AttendanceStatus.ABSENT,
        nullable=False,
    )
    checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    marked_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    method: Mapped[str] = mapped_column(
        String(20),
        default=AttendanceMethod.MANUAL,
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    session: Mapped["ClassSession"] = relationship(
        back_populates="attendance_records",
        lazy="raise",
    )
    student: Mapped["User"] = relationship(
        foreign_keys=[student_id],
        back_populates="attendance_records",
        lazy="raise",
    )
    marker: Mapped["User | None"] = relationship(
        foreign_keys=[marked_by],
        back_populates="marked_attendance_records",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return (
            f"<AttendanceRecord id={self.id} session_id={self.session_id} "
            f"student_id={self.student_id} status={self.status!r}>"
        )
