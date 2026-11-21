from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User


class UserRepository:
    @staticmethod
    async def get_by_id(session: AsyncSession, user_id: UUID | str) -> User | None:
        if isinstance(user_id, str):
            try:
                user_id = UUID(user_id)
            except ValueError:
                return None
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_email(session: AsyncSession, email: str) -> User | None:
        stmt = select(User).where(func.lower(User.email) == func.lower(email.strip()))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_student_id(session: AsyncSession, student_id: str) -> User | None:
        stmt = select(User).where(func.lower(User.student_id) == func.lower(student_id.strip()))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create(session: AsyncSession, user: User) -> User:
        session.add(user)
        await session.flush()
        return user

    @staticmethod
    async def create_many(session: AsyncSession, users: list[User]) -> list[User]:
        session.add_all(users)
        await session.flush()
        return users

    @staticmethod
    async def update(session: AsyncSession, user: User) -> User:
        session.add(user)
        await session.flush()
        return user

    @staticmethod
    async def list(
        session: AsyncSession,
        page: int = 1,
        size: int = 20,
        role: str | UserRole | None = None,
        is_active: bool | None = None,
        search: str | None = None,
    ) -> tuple[list[User], int]:
        conditions = []
        if role is not None:
            role_val = role.value if isinstance(role, UserRole) else str(role)
            conditions.append(User.role == role_val)
        if is_active is not None:
            conditions.append(User.is_active == is_active)
        if search is not None and search.strip():
            term = f"%{search.strip()}%"
            conditions.append(
                or_(
                    User.full_name.ilike(term),
                    User.email.ilike(term),
                    User.student_id.ilike(term),
                )
            )

        count_stmt = select(func.count(User.id))
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        total_result = await session.execute(count_stmt)
        total = total_result.scalar_one()

        stmt = select(User)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        offset = max(0, (page - 1) * size)
        stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).offset(offset).limit(size)
        result = await session.execute(stmt)
        users = list(result.scalars().all())
        return users, total


user_repository = UserRepository()
