from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.class_session import ClassSession
from app.models.enums import SessionStatus


class SessionRepository:
    @staticmethod
    async def get_by_id(session: AsyncSession, session_id: UUID | str) -> ClassSession | None:
        if isinstance(session_id, str):
            try:
                session_id = UUID(session_id)
            except ValueError:
                return None
        stmt = select(ClassSession).where(ClassSession.id == session_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create(session: AsyncSession, class_session: ClassSession) -> ClassSession:
        session.add(class_session)
        await session.flush()
        await session.refresh(class_session)
        return class_session

    @staticmethod
    async def create_many(
        session: AsyncSession, class_sessions: list[ClassSession]
    ) -> list[ClassSession]:
        if not class_sessions:
            return []
        session.add_all(class_sessions)
        await session.flush()
        for s in class_sessions:
            await session.refresh(s)
        return class_sessions

    @staticmethod
    async def update(session: AsyncSession, class_session: ClassSession) -> ClassSession:
        session.add(class_session)
        await session.flush()
        await session.refresh(class_session)
        return class_session

    @staticmethod
    async def has_overlap(
        session: AsyncSession,
        course_id: UUID,
        starts_at: datetime,
        ends_at: datetime,
        exclude_session_id: UUID | None = None,
    ) -> bool:
        stmt = select(func.count(ClassSession.id)).where(
            ClassSession.course_id == course_id,
            ClassSession.status != SessionStatus.CANCELLED.value,
            ClassSession.starts_at < ends_at,
            ClassSession.ends_at > starts_at,
        )
        if exclude_session_id is not None:
            stmt = stmt.where(ClassSession.id != exclude_session_id)

        result = await session.execute(stmt)
        count = result.scalar_one()
        return count > 0

    @staticmethod
    async def list_course_sessions(
        session: AsyncSession,
        course_id: UUID,
        page: int = 1,
        size: int = 20,
        status: SessionStatus | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
    ) -> tuple[list[ClassSession], int]:
        conditions = [ClassSession.course_id == course_id]

        if status is not None:
            status_val = status.value if isinstance(status, SessionStatus) else status
            conditions.append(ClassSession.status == status_val)

        if from_dt is not None:
            conditions.append(ClassSession.starts_at >= from_dt)

        if to_dt is not None:
            conditions.append(ClassSession.starts_at <= to_dt)

        count_stmt = select(func.count(ClassSession.id)).where(and_(*conditions))
        total_result = await session.execute(count_stmt)
        total = total_result.scalar_one()

        offset = max(0, (page - 1) * size)
        stmt = (
            select(ClassSession)
            .where(and_(*conditions))
            .order_by(ClassSession.starts_at.asc(), ClassSession.id.asc())
            .offset(offset)
            .limit(size)
        )
        result = await session.execute(stmt)
        sessions = list(result.scalars().all())
        return sessions, total


session_repository = SessionRepository()
