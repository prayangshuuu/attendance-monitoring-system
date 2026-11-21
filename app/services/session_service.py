from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.models.class_session import ClassSession
from app.models.course import Course
from app.models.enums import SessionStatus, UserRole
from app.models.user import User
from app.repositories.course_repository import course_repository
from app.repositories.enrollment_repository import enrollment_repository
from app.repositories.session_repository import session_repository
from app.schemas.common import Page
from app.schemas.session import (
    RecurringSessionCreate,
    RecurringSessionResult,
    SessionCreate,
    SessionRead,
    SessionUpdate,
    ensure_utc_datetime,
)
from app.services.permissions import ensure_can_manage_course, ensure_can_view_course
from app.utils.clock import Clock


class SessionService:
    async def _get_course_or_404(self, session: AsyncSession, course_id: UUID) -> Course:
        course = await course_repository.get_by_id(session, course_id)
        if course is None:
            raise NotFoundError("Course not found", code="COURSE_NOT_FOUND")
        return course

    async def _get_session_or_404(
        self, session: AsyncSession, session_id: UUID
    ) -> tuple[ClassSession, Course]:
        class_session = await session_repository.get_by_id(session, session_id)
        if class_session is None:
            raise NotFoundError("Session not found", code="SESSION_NOT_FOUND")
        course = await self._get_course_or_404(session, class_session.course_id)
        return class_session, course

    async def _ensure_can_view(self, session: AsyncSession, course: Course, user: User) -> None:
        is_enrolled = False
        if user.role == UserRole.STUDENT:
            is_enrolled = await enrollment_repository.is_enrolled(session, course.id, user.id)
        ensure_can_view_course(user, course, is_enrolled=is_enrolled)

    async def create_session(
        self,
        session: AsyncSession,
        course_id: UUID,
        current_user: User,
        data: SessionCreate,
        clock: Clock | None = None,
    ) -> SessionRead:
        course = await self._get_course_or_404(session, course_id)
        ensure_can_manage_course(current_user, course)

        has_overlap = await session_repository.has_overlap(
            session, course_id, data.starts_at, data.ends_at
        )
        if has_overlap:
            raise ConflictError(
                "Session overlaps with an existing session in this course",
                code="SESSION_OVERLAP",
            )

        class_session = ClassSession(
            course_id=course_id,
            starts_at=data.starts_at,
            ends_at=data.ends_at,
            late_after_minutes=data.late_after_minutes,
            status=SessionStatus.SCHEDULED.value,
        )
        created = await session_repository.create(session, class_session)
        return SessionRead.model_validate(created)

    async def create_recurring(
        self,
        session: AsyncSession,
        course_id: UUID,
        current_user: User,
        data: RecurringSessionCreate,
        clock: Clock | None = None,
    ) -> RecurringSessionResult:
        course = await self._get_course_or_404(session, course_id)
        ensure_can_manage_course(current_user, course)

        tz = ZoneInfo(data.timezone)
        cur = data.from_date
        days_ahead = (data.weekday - cur.weekday()) % 7
        cur = cur + timedelta(days=days_ahead)

        to_create: list[ClassSession] = []
        skipped = 0

        while cur <= data.to_date:
            local_start = datetime.combine(cur, data.start_time, tzinfo=tz)
            local_end = datetime.combine(cur, data.end_time, tzinfo=tz)
            utc_start = local_start.astimezone(UTC)
            utc_end = local_end.astimezone(UTC)

            overlaps = await session_repository.has_overlap(session, course_id, utc_start, utc_end)
            if overlaps:
                skipped += 1
            else:
                to_create.append(
                    ClassSession(
                        course_id=course_id,
                        starts_at=utc_start,
                        ends_at=utc_end,
                        late_after_minutes=data.late_after_minutes,
                        status=SessionStatus.SCHEDULED.value,
                    )
                )
            cur += timedelta(days=7)

        created_sessions = await session_repository.create_many(session, to_create)
        return RecurringSessionResult(
            created=[SessionRead.model_validate(s) for s in created_sessions],
            skipped=skipped,
        )

    async def get_session(
        self,
        session: AsyncSession,
        session_id: UUID,
        current_user: User,
    ) -> SessionRead:
        class_session, course = await self._get_session_or_404(session, session_id)
        await self._ensure_can_view(session, course, current_user)
        return SessionRead.model_validate(class_session)

    async def list_course_sessions(
        self,
        session: AsyncSession,
        course_id: UUID,
        current_user: User,
        page: int = 1,
        size: int = 20,
        status: SessionStatus | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
    ) -> Page[SessionRead]:
        course = await self._get_course_or_404(session, course_id)
        await self._ensure_can_view(session, course, current_user)

        sessions, total = await session_repository.list_course_sessions(
            session=session,
            course_id=course_id,
            page=page,
            size=size,
            status=status,
            from_dt=from_dt,
            to_dt=to_dt,
        )

        items = [SessionRead.model_validate(s) for s in sessions]
        return Page(items=items, total=total, page=page, size=size)

    async def update_session(
        self,
        session: AsyncSession,
        session_id: UUID,
        current_user: User,
        data: SessionUpdate,
        clock: Clock | None = None,
    ) -> SessionRead:
        class_session, course = await self._get_session_or_404(session, session_id)
        ensure_can_manage_course(current_user, course)

        if class_session.status != SessionStatus.SCHEDULED.value:
            raise ConflictError(
                f"Cannot update session in {class_session.status} status",
                code="INVALID_SESSION_STATUS",
            )

        new_starts_at = (
            data.starts_at
            if data.starts_at is not None
            else ensure_utc_datetime(class_session.starts_at)
        )
        new_ends_at = (
            data.ends_at if data.ends_at is not None else ensure_utc_datetime(class_session.ends_at)
        )
        assert new_starts_at is not None
        assert new_ends_at is not None

        if new_ends_at <= new_starts_at:
            raise ValidationAppError("ends_at must be after starts_at", code="VALIDATION_ERROR")

        has_overlap = await session_repository.has_overlap(
            session,
            class_session.course_id,
            new_starts_at,
            new_ends_at,
            exclude_session_id=class_session.id,
        )
        if has_overlap:
            raise ConflictError(
                "Session overlaps with an existing session in this course",
                code="SESSION_OVERLAP",
            )

        class_session.starts_at = new_starts_at
        class_session.ends_at = new_ends_at
        if data.late_after_minutes is not None:
            class_session.late_after_minutes = data.late_after_minutes

        updated = await session_repository.update(session, class_session)
        return SessionRead.model_validate(updated)


session_service = SessionService()
