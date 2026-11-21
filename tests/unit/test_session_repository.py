import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.class_session import ClassSession
from app.models.enums import SessionStatus
from app.repositories.session_repository import session_repository
from tests.factories import make_course, make_session


@pytest.mark.asyncio
async def test_session_repository_create_and_get(db_session: AsyncSession) -> None:
    course = await make_course(db_session)
    now = datetime.now(UTC)

    session_obj = ClassSession(
        course_id=course.id,
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        late_after_minutes=10,
        status=SessionStatus.SCHEDULED.value,
    )
    created = await session_repository.create(db_session, session_obj)
    assert created.id is not None

    # Get by UUID
    found = await session_repository.get_by_id(db_session, created.id)
    assert found is not None
    assert found.id == created.id

    # Get by string UUID
    found_str = await session_repository.get_by_id(db_session, str(created.id))
    assert found_str is not None
    assert found_str.id == created.id

    # Get with invalid UUID string
    assert await session_repository.get_by_id(db_session, "invalid-uuid") is None

    # Non-existent UUID
    assert await session_repository.get_by_id(db_session, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_session_repository_create_many(db_session: AsyncSession) -> None:
    # Empty list
    empty_res = await session_repository.create_many(db_session, [])
    assert empty_res == []

    course = await make_course(db_session)
    now = datetime.now(UTC)

    s1 = ClassSession(
        course_id=course.id,
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        late_after_minutes=10,
        status=SessionStatus.SCHEDULED.value,
    )
    s2 = ClassSession(
        course_id=course.id,
        starts_at=now + timedelta(days=7),
        ends_at=now + timedelta(days=7, hours=1),
        late_after_minutes=10,
        status=SessionStatus.SCHEDULED.value,
    )
    res = await session_repository.create_many(db_session, [s1, s2])
    assert len(res) == 2
    assert s1.id is not None
    assert s2.id is not None


@pytest.mark.asyncio
async def test_session_repository_update(db_session: AsyncSession) -> None:
    course = await make_course(db_session)
    s = await make_session(db_session, course=course)

    s.status = SessionStatus.OPEN.value
    s.checkin_code = "XYZ123"
    updated = await session_repository.update(db_session, s)
    assert updated.status == SessionStatus.OPEN.value
    assert updated.checkin_code == "XYZ123"


@pytest.mark.asyncio
async def test_session_repository_has_overlap(db_session: AsyncSession) -> None:
    course = await make_course(db_session)
    other_course = await make_course(db_session)
    base_time = datetime(2026, 11, 25, 10, 0, 0, tzinfo=UTC)
    s = await make_session(
        db_session,
        course=course,
        starts_at=base_time,
        ends_at=base_time + timedelta(hours=2),  # 10:00 - 12:00
        status=SessionStatus.SCHEDULED.value,
    )

    # 1. Exact overlap: 10:00 - 12:00
    assert await session_repository.has_overlap(
        db_session, course.id, base_time, base_time + timedelta(hours=2)
    )

    # 2. Starts before, ends inside: 09:30 - 10:30
    assert await session_repository.has_overlap(
        db_session, course.id, base_time - timedelta(minutes=30), base_time + timedelta(minutes=30)
    )

    # 3. Starts inside, ends after: 11:30 - 12:30
    assert await session_repository.has_overlap(
        db_session,
        course.id,
        base_time + timedelta(hours=1, minutes=30),
        base_time + timedelta(hours=2, minutes=30),
    )

    # 4. Enclosing: 09:00 - 13:00
    assert await session_repository.has_overlap(
        db_session,
        course.id,
        base_time - timedelta(hours=1),
        base_time + timedelta(hours=3),
    )

    # 5. Inside: 10:30 - 11:30
    assert await session_repository.has_overlap(
        db_session,
        course.id,
        base_time + timedelta(minutes=30),
        base_time + timedelta(hours=1, minutes=30),
    )

    # 6. Back-to-back before: 08:00 - 10:00 -> NO OVERLAP
    assert not await session_repository.has_overlap(
        db_session, course.id, base_time - timedelta(hours=2), base_time
    )

    # 7. Back-to-back after: 12:00 - 14:00 -> NO OVERLAP
    assert not await session_repository.has_overlap(
        db_session, course.id, base_time + timedelta(hours=2), base_time + timedelta(hours=4)
    )

    # 8. Exclude session ID: s overlaps with itself unless excluded
    assert not await session_repository.has_overlap(
        db_session,
        course.id,
        base_time,
        base_time + timedelta(hours=2),
        exclude_session_id=s.id,
    )

    # 9. Cancelled sessions do NOT count as overlap
    s.status = SessionStatus.CANCELLED.value
    await session_repository.update(db_session, s)
    assert not await session_repository.has_overlap(
        db_session, course.id, base_time, base_time + timedelta(hours=2)
    )

    # 10. Different course has no overlap
    assert not await session_repository.has_overlap(
        db_session, other_course.id, base_time, base_time + timedelta(hours=2)
    )


@pytest.mark.asyncio
async def test_session_repository_list_course_sessions(db_session: AsyncSession) -> None:
    course = await make_course(db_session)
    other_course = await make_course(db_session)

    t1 = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
    t3 = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)

    s1 = await make_session(
        db_session,
        course=course,
        starts_at=t1,
        ends_at=t1 + timedelta(hours=1),
        status=SessionStatus.CLOSED.value,
    )
    s2 = await make_session(
        db_session,
        course=course,
        starts_at=t2,
        ends_at=t2 + timedelta(hours=1),
        status=SessionStatus.OPEN.value,
    )
    s3 = await make_session(
        db_session,
        course=course,
        starts_at=t3,
        ends_at=t3 + timedelta(hours=1),
        status=SessionStatus.SCHEDULED.value,
    )
    # Session for other course
    await make_session(
        db_session,
        course=other_course,
        starts_at=t1,
        ends_at=t1 + timedelta(hours=1),
    )

    # List all for course
    items, total = await session_repository.list_course_sessions(
        db_session, course.id, page=1, size=10
    )
    assert total == 3
    assert [s.id for s in items] == [s1.id, s2.id, s3.id]

    # Filter by status (SessionStatus enum)
    items_open, total_open = await session_repository.list_course_sessions(
        db_session, course.id, page=1, size=10, status=SessionStatus.OPEN
    )
    assert total_open == 1
    assert items_open[0].id == s2.id

    # Filter by from_dt
    items_from, total_from = await session_repository.list_course_sessions(
        db_session, course.id, page=1, size=10, from_dt=t2
    )
    assert total_from == 2
    assert [s.id for s in items_from] == [s2.id, s3.id]

    # Filter by to_dt
    items_to, total_to = await session_repository.list_course_sessions(
        db_session, course.id, page=1, size=10, to_dt=t2
    )
    assert total_to == 2
    assert [s.id for s in items_to] == [s1.id, s2.id]

    # Pagination: page 2, size 2
    items_p2, total_p2 = await session_repository.list_course_sessions(
        db_session, course.id, page=2, size=2
    )
    assert total_p2 == 3
    assert len(items_p2) == 1
    assert items_p2[0].id == s3.id
