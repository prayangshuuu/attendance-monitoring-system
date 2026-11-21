import pytest
from sqlalchemy import Column, Integer, String, Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

isolation_table = Table(
    "isolation_test_items",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(50), nullable=False),
    extend_existing=True,
)


@pytest.mark.asyncio
async def test_db_isolation_step_1_insert(db_session: AsyncSession) -> None:
    # Ensure table exists
    conn = await db_session.connection()
    await conn.run_sync(Base.metadata.create_all, tables=[isolation_table])

    # Insert a record and commit
    await db_session.execute(isolation_table.insert().values(id=1, name="isolated_item"))
    await db_session.commit()

    # Verify it is present in the current test
    result = await db_session.execute(select(isolation_table))
    items = result.fetchall()
    assert len(items) == 1
    assert items[0][1] == "isolated_item"


@pytest.mark.asyncio
async def test_db_isolation_step_2_verify_empty(db_session: AsyncSession) -> None:
    # In step 2, the rollback from step 1's fixture tear down must ensure no rows exist
    conn = await db_session.connection()
    await conn.run_sync(Base.metadata.create_all, tables=[isolation_table])

    result = await db_session.execute(select(isolation_table))
    items = result.fetchall()
    assert len(items) == 0
