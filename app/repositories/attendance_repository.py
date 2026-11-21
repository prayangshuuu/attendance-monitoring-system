from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.attendance_record import AttendanceRecord
from app.models.class_session import ClassSession


class AttendanceRepository:
    @staticmethod
    async def get_by_id(session: AsyncSession, record_id: UUID | str) -> AttendanceRecord | None:
        if isinstance(record_id, str):
            try:
                record_id = UUID(record_id)
            except ValueError:
                return None

        stmt = (
            select(AttendanceRecord)
            .where(AttendanceRecord.id == record_id)
            .options(selectinload(AttendanceRecord.session).selectinload(ClassSession.course))
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_session_and_student(
        session: AsyncSession, session_id: UUID, student_id: UUID
    ) -> AttendanceRecord | None:
        stmt = select(AttendanceRecord).where(
            AttendanceRecord.session_id == session_id,
            AttendanceRecord.student_id == student_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_records_for_session(
        session: AsyncSession, session_id: UUID
    ) -> list[AttendanceRecord]:
        stmt = (
            select(AttendanceRecord)
            .where(AttendanceRecord.session_id == session_id)
            .order_by(AttendanceRecord.created_at.asc(), AttendanceRecord.id.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create(session: AsyncSession, record: AttendanceRecord) -> AttendanceRecord:
        session.add(record)
        await session.flush()
        await session.refresh(record)
        return record

    @staticmethod
    async def create_many(
        session: AsyncSession, records: list[AttendanceRecord]
    ) -> list[AttendanceRecord]:
        if not records:
            return []
        session.add_all(records)
        await session.flush()
        for r in records:
            await session.refresh(r)
        return records

    @staticmethod
    async def update(session: AsyncSession, record: AttendanceRecord) -> AttendanceRecord:
        session.add(record)
        await session.flush()
        await session.refresh(record)
        return record

    @staticmethod
    async def list_student_records(
        session: AsyncSession,
        student_id: UUID,
        course_id: UUID | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[AttendanceRecord], int]:
        conditions = [AttendanceRecord.student_id == student_id]

        if course_id is not None:
            count_stmt = (
                select(func.count(AttendanceRecord.id))
                .join(ClassSession, AttendanceRecord.session_id == ClassSession.id)
                .where(*conditions, ClassSession.course_id == course_id)
            )
            stmt = (
                select(AttendanceRecord)
                .join(ClassSession, AttendanceRecord.session_id == ClassSession.id)
                .where(*conditions, ClassSession.course_id == course_id)
                .order_by(AttendanceRecord.created_at.desc(), AttendanceRecord.id.desc())
                .offset(max(0, (page - 1) * size))
                .limit(size)
            )
        else:
            count_stmt = select(func.count(AttendanceRecord.id)).where(*conditions)
            stmt = (
                select(AttendanceRecord)
                .where(*conditions)
                .order_by(AttendanceRecord.created_at.desc(), AttendanceRecord.id.desc())
                .offset(max(0, (page - 1) * size))
                .limit(size)
            )

        total_result = await session.execute(count_stmt)
        total = total_result.scalar_one()

        result = await session.execute(stmt)
        records = list(result.scalars().all())
        return records, total


attendance_repository = AttendanceRepository()
