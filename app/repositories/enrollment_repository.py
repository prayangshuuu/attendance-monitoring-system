from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enrollment import Enrollment
from app.models.user import User


class EnrollmentRepository:
    @staticmethod
    async def get_enrollment(
        session: AsyncSession,
        course_id: UUID,
        student_id: UUID,
    ) -> Enrollment | None:
        stmt = select(Enrollment).where(
            Enrollment.course_id == course_id,
            Enrollment.student_id == student_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def is_enrolled(
        session: AsyncSession,
        course_id: UUID,
        student_id: UUID,
    ) -> bool:
        stmt = select(func.count(Enrollment.id)).where(
            Enrollment.course_id == course_id,
            Enrollment.student_id == student_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one() > 0

    @staticmethod
    async def get_enrolled_student_ids(
        session: AsyncSession,
        course_id: UUID,
        student_ids: list[UUID],
    ) -> set[UUID]:
        if not student_ids:
            return set()
        stmt = select(Enrollment.student_id).where(
            Enrollment.course_id == course_id,
            Enrollment.student_id.in_(student_ids),
        )
        result = await session.execute(stmt)
        return set(result.scalars().all())

    @staticmethod
    async def create_enrollments(
        session: AsyncSession,
        enrollments: list[Enrollment],
    ) -> list[Enrollment]:
        session.add_all(enrollments)
        await session.flush()
        return enrollments

    @staticmethod
    async def delete_enrollment(
        session: AsyncSession,
        course_id: UUID,
        student_id: UUID,
    ) -> bool:
        stmt = select(Enrollment).where(
            Enrollment.course_id == course_id,
            Enrollment.student_id == student_id,
        )
        result = await session.execute(stmt)
        enrollment = result.scalar_one_or_none()
        if enrollment is None:
            return False
        await session.delete(enrollment)
        await session.flush()
        return True

    @staticmethod
    async def list_course_students(
        session: AsyncSession,
        course_id: UUID,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[User], int]:
        count_stmt = (
            select(func.count(User.id))
            .join(Enrollment, Enrollment.student_id == User.id)
            .where(Enrollment.course_id == course_id)
        )
        total_result = await session.execute(count_stmt)
        total = total_result.scalar_one()

        offset = max(0, (page - 1) * size)
        stmt = (
            select(User)
            .join(Enrollment, Enrollment.student_id == User.id)
            .where(Enrollment.course_id == course_id)
            .order_by(Enrollment.enrolled_at.asc(), User.id.asc())
            .offset(offset)
            .limit(size)
        )
        result = await session.execute(stmt)
        students = list(result.scalars().all())
        return students, total


enrollment_repository = EnrollmentRepository()
