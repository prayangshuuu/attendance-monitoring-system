import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPkMixin

if TYPE_CHECKING:
    from app.models.course import Course
    from app.models.user import User


class Enrollment(Base, UUIDPkMixin):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("course_id", "student_id", name="uq_enrollments_course_id_student_id"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=func.now(),
        nullable=False,
    )

    course: Mapped["Course"] = relationship(
        back_populates="enrollments",
        lazy="raise",
    )
    student: Mapped["User"] = relationship(
        back_populates="enrollments",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Enrollment id={self.id} course_id={self.course_id} student_id={self.student_id}>"
