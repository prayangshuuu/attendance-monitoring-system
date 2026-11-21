import secrets
from datetime import timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationAppError,
)
from app.models.attendance_record import AttendanceRecord
from app.models.enums import AttendanceMethod, AttendanceStatus, SessionStatus
from app.models.user import User
from app.repositories.attendance_repository import AttendanceRepository, attendance_repository
from app.repositories.course_repository import CourseRepository, course_repository
from app.repositories.enrollment_repository import (
    EnrollmentRepository,
    enrollment_repository,
)
from app.repositories.session_repository import SessionRepository, session_repository
from app.schemas.attendance import AttendanceRead
from app.schemas.common import Page
from app.schemas.session import ensure_utc_datetime
from app.utils.clock import Clock, SystemClock


class AttendanceService:
    def __init__(
        self,
        attendance_repo: AttendanceRepository | None = None,
        session_repo: SessionRepository | None = None,
        course_repo: CourseRepository | None = None,
        enrollment_repo: EnrollmentRepository | None = None,
    ) -> None:
        self.attendance_repo = attendance_repo or attendance_repository
        self.session_repo = session_repo or session_repository
        self.course_repo = course_repo or course_repository
        self.enrollment_repo = enrollment_repo or enrollment_repository

    async def check_in(
        self,
        session: AsyncSession,
        student: User,
        session_id: UUID,
        code: str,
        clock: Clock | None = None,
    ) -> AttendanceRecord:
        active_clock = clock or SystemClock()

        # 1. SESSION_NOT_FOUND (404)
        class_session = await self.session_repo.get_by_id(session, session_id)
        if class_session is None:
            raise NotFoundError("Session not found", code="SESSION_NOT_FOUND")

        # 2. NOT_ENROLLED (403)
        is_enrolled = await self.enrollment_repo.is_enrolled(
            session, class_session.course_id, student.id
        )
        if not is_enrolled:
            raise PermissionDeniedError(
                "Student is not enrolled in this course", code="NOT_ENROLLED"
            )

        # 3. SESSION_NOT_OPEN (409)
        if class_session.status != SessionStatus.OPEN.value:
            raise ConflictError("Session is not open for check-in", code="SESSION_NOT_OPEN")

        # 4. INVALID_CODE (400) (case-insensitive check using secrets.compare_digest)
        actual_code = class_session.checkin_code or ""
        submitted_code = code.strip()
        if not actual_code or not secrets.compare_digest(
            submitted_code.upper(), actual_code.upper()
        ):
            raise ValidationAppError("Invalid check-in code", code="INVALID_CODE")

        # 5. CODE_EXPIRED (400) (compare with clock.now())
        now_dt = ensure_utc_datetime(active_clock.now())
        assert now_dt is not None
        code_expires_at = ensure_utc_datetime(class_session.code_expires_at)
        if code_expires_at is not None and now_dt > code_expires_at:
            raise ValidationAppError("Check-in code has expired", code="CODE_EXPIRED")

        # 6. ALREADY_CHECKED_IN (409)
        existing = await self.attendance_repo.get_by_session_and_student(
            session, session_id, student.id
        )
        if existing is not None:
            raise ConflictError(
                "Student has already checked in for this session",
                code="ALREADY_CHECKED_IN",
            )

        # Calculate status: if past late threshold -> LATE, else PRESENT
        starts_at = ensure_utc_datetime(class_session.starts_at)
        assert starts_at is not None
        late_threshold = starts_at + timedelta(minutes=class_session.late_after_minutes)
        status = AttendanceStatus.LATE if now_dt > late_threshold else AttendanceStatus.PRESENT

        record = AttendanceRecord(
            session_id=session_id,
            student_id=student.id,
            status=status.value,
            checked_in_at=now_dt,
            method=AttendanceMethod.CODE.value,
            marked_by=None,
            note=None,
        )

        try:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        except IntegrityError:
            await session.rollback()
            raise ConflictError(
                "Student has already checked in for this session",
                code="ALREADY_CHECKED_IN",
            ) from None
        return record

    async def list_student_records(
        self,
        session: AsyncSession,
        student: User,
        course_id: UUID | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[AttendanceRead]:
        records, total = await self.attendance_repo.list_student_records(
            session=session,
            student_id=student.id,
            course_id=course_id,
            page=page,
            size=size,
        )
        return Page[AttendanceRead](
            items=[AttendanceRead.model_validate(r) for r in records],
            total=total,
            page=page,
            size=size,
        )


attendance_service = AttendanceService()
