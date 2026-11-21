from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.enums import UserRole
from app.models.user import User


class CourseRepository:
    @staticmethod
    async def get_by_id(session: AsyncSession, course_id: UUID | str) -> Course | None:
        if isinstance(course_id, str):
            try:
                course_id = UUID(course_id)
            except ValueError:
                return None
        stmt = select(Course).where(Course.id == course_id).options(selectinload(Course.teacher))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_code(session: AsyncSession, code: str) -> Course | None:
        stmt = (
            select(Course)
            .where(func.upper(Course.code) == code.strip().upper())
            .options(selectinload(Course.teacher))
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create(session: AsyncSession, course: Course) -> Course:
        session.add(course)
        await session.flush()
        return course

    @staticmethod
    async def update(session: AsyncSession, course: Course) -> Course:
        session.add(course)
        await session.flush()
        return course

    @staticmethod
    async def list(
        session: AsyncSession,
        user: User,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[Course], int]:
        conditions = []

        if user.role == UserRole.ADMIN:
            pass
        elif user.role == UserRole.TEACHER:
            conditions.append(Course.teacher_id == user.id)
        elif user.role == UserRole.STUDENT:
            conditions.append(
                Course.id.in_(select(Enrollment.course_id).where(Enrollment.student_id == user.id))
            )
        else:
            return [], 0

        if is_active is not None:
            conditions.append(Course.is_active == is_active)

        if search is not None and search.strip():
            term = f"%{search.strip()}%"
            conditions.append(
                or_(
                    Course.code.ilike(term),
                    Course.title.ilike(term),
                )
            )

        count_stmt = select(func.count(Course.id))
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        total_result = await session.execute(count_stmt)
        total = total_result.scalar_one()

        stmt = select(Course).options(selectinload(Course.teacher))
        if conditions:
            stmt = stmt.where(and_(*conditions))
        offset = max(0, (page - 1) * size)
        stmt = stmt.order_by(Course.created_at.desc(), Course.id.desc()).offset(offset).limit(size)
        result = await session.execute(stmt)
        courses = list(result.scalars().all())
        return courses, total


course_repository = CourseRepository()
