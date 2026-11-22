import contextlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.main import create_app, lifespan


@pytest.mark.asyncio
async def test_get_db_generator() -> None:
    generator = get_db()
    session = await anext(generator)
    assert isinstance(session, AsyncSession)
    await session.close()
    with contextlib.suppress(StopAsyncIteration):
        await anext(generator)


@pytest.mark.asyncio
async def test_lifespan_handler() -> None:
    app = create_app()
    async with lifespan(app):
        pass


@pytest.mark.asyncio
async def test_get_db_generator_exception_rollback() -> None:
    generator = get_db()
    session = await anext(generator)
    assert isinstance(session, AsyncSession)
    with pytest.raises(RuntimeError, match="Simulated database failure"):
        await generator.athrow(RuntimeError("Simulated database failure"))
