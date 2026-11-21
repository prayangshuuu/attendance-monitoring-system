import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import async_session_maker
from app.models.enums import UserRole
from app.models.user import User

logger = logging.getLogger(__name__)


async def bootstrap_admin(session: AsyncSession | None = None) -> User | None:
    if session is not None:
        return await _create_admin_if_not_exists(session)

    if settings.ENVIRONMENT == "testing":
        return None

    try:
        async with async_session_maker() as new_session, new_session.begin():
            return await _create_admin_if_not_exists(new_session)
    except Exception as exc:
        logger.warning("Could not bootstrap admin user: %s", exc)
        return None


async def _create_admin_if_not_exists(session: AsyncSession) -> User | None:
    stmt = select(User).where(User.email == settings.FIRST_ADMIN_EMAIL.lower().strip())
    result = await session.execute(stmt)
    existing_user = result.scalar_one_or_none()
    if existing_user is not None:
        logger.info("Admin user already exists: %s", existing_user.email)
        return existing_user

    admin_user = User(
        email=settings.FIRST_ADMIN_EMAIL.lower().strip(),
        full_name="System Administrator",
        hashed_password=hash_password(settings.FIRST_ADMIN_PASSWORD),
        role=UserRole.ADMIN,
        is_active=True,
    )
    session.add(admin_user)
    await session.flush()
    logger.info("Admin user created successfully: %s", admin_user.email)
    return admin_user


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(bootstrap_admin())
