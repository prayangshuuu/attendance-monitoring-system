import os
from collections.abc import AsyncGenerator, Callable
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENVIRONMENT"] = "testing"

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.core.security import create_access_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from tests.factories import make_user  # noqa: E402


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        await conn.begin_nested()

        session = AsyncSession(bind=conn, expire_on_commit=False)

        @event.listens_for(session.sync_session, "after_transaction_end")
        def restart_savepoint(session: Session, transaction: object) -> None:
            if conn.closed or conn.sync_connection is None:
                return
            if not conn.sync_connection.in_nested_transaction():
                conn.sync_connection.begin_nested()

        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest.fixture
def app(db_session: AsyncSession) -> FastAPI:
    application = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture
def auth_headers() -> Callable[[User], dict[str, str]]:
    def _make_headers(user: User) -> dict[str, str]:
        token = create_access_token(sub=user.id, role=user.role)
        return {"Authorization": f"Bearer {token}"}

    return _make_headers


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    return await make_user(
        db_session,
        email="admin_fixture@example.com",
        full_name="Admin Fixture User",
        role=UserRole.ADMIN,
        password="adminpassword123",
    )


@pytest_asyncio.fixture
async def teacher_user(db_session: AsyncSession) -> User:
    return await make_user(
        db_session,
        email="teacher_fixture@example.com",
        full_name="Teacher Fixture User",
        role=UserRole.TEACHER,
        password="teacherpassword123",
    )


@pytest_asyncio.fixture
async def student_user(db_session: AsyncSession) -> User:
    return await make_user(
        db_session,
        email="student_fixture@example.com",
        full_name="Student Fixture User",
        role=UserRole.STUDENT,
        password="studentpassword123",
    )
