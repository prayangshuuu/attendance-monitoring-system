import asyncio
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.db.session import get_db
from app.main import create_app
from app.models.enums import AttendanceMethod, AttendanceStatus, SessionStatus, UserRole
from app.models.user import User
from app.services.attendance_service import attendance_service
from app.utils.rate_limiter import in_memory_rate_limiter
from tests.factories import make_course, make_enrollment, make_record, make_session, make_user


@pytest.fixture(autouse=True)
async def reset_rate_limiter() -> None:
    await in_memory_rate_limiter.reset()


@pytest.mark.asyncio
async def test_check_in_endpoint(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    course = await make_course(db_session, teacher=teacher_user)
    await make_enrollment(db_session, course=course, student=student_user)

    now = datetime.now(UTC)
    session_obj = await make_session(
        db_session,
        course=course,
        starts_at=now,
        ends_at=now + timedelta(hours=2),
        status=SessionStatus.OPEN.value,
        checkin_code="K9X2P1",
        code_expires_at=now + timedelta(minutes=15),
        late_after_minutes=10,
    )

    # 1. Unauthenticated -> 401
    res = await client.post(
        f"/api/v1/sessions/{session_obj.id}/check-in",
        json={"code": "K9X2P1"},
    )
    assert res.status_code == 401

    # 2. Invalid code -> 400
    res = await client.post(
        f"/api/v1/sessions/{session_obj.id}/check-in",
        headers=auth_headers(student_user),
        json={"code": "WRONG1"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_CODE"

    # 3. Successful check-in -> 200
    res = await client.post(
        f"/api/v1/sessions/{session_obj.id}/check-in",
        headers=auth_headers(student_user),
        json={"code": "k9x2p1"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == str(session_obj.id)
    assert data["student_id"] == str(student_user.id)
    assert data["status"] == AttendanceStatus.PRESENT
    assert data["method"] == AttendanceMethod.CODE
    assert data["checked_in_at"] is not None

    # 4. Already checked in -> 409
    res = await client.post(
        f"/api/v1/sessions/{session_obj.id}/check-in",
        headers=auth_headers(student_user),
        json={"code": "K9X2P1"},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "ALREADY_CHECKED_IN"


@pytest.mark.asyncio
async def test_check_in_rate_limiting(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    course = await make_course(db_session, teacher=teacher_user)
    await make_enrollment(db_session, course=course, student=student_user)

    now = datetime.now(UTC)
    session_obj = await make_session(
        db_session,
        course=course,
        starts_at=now,
        ends_at=now + timedelta(hours=2),
        status=SessionStatus.OPEN.value,
        checkin_code="K9X2P1",
        code_expires_at=now + timedelta(minutes=15),
    )

    # 5 attempts with invalid code
    for _ in range(5):
        res = await client.post(
            f"/api/v1/sessions/{session_obj.id}/check-in",
            headers=auth_headers(student_user),
            json={"code": "BADCODE"},
        )
        assert res.status_code == 400

    # 6th attempt -> 429 TOO_MANY_ATTEMPTS
    res = await client.post(
        f"/api/v1/sessions/{session_obj.id}/check-in",
        headers=auth_headers(student_user),
        json={"code": "BADCODE"},
    )
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"
    assert "Too many check-in attempts" in res.json()["error"]["message"]


@pytest.mark.asyncio
async def test_get_my_attendance_endpoint(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    course = await make_course(db_session, teacher=teacher_user)
    await make_enrollment(db_session, course=course, student=student_user)
    session_obj = await make_session(db_session, course=course)
    await make_record(
        db_session,
        class_session=session_obj,
        student=student_user,
        status=AttendanceStatus.PRESENT,
    )

    # 1. Student view own attendance
    res = await client.get(
        "/api/v1/me/attendance",
        headers=auth_headers(student_user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["student_id"] == str(student_user.id)

    # 2. Filter with course_id
    res = await client.get(
        f"/api/v1/me/attendance?course_id={course.id}",
        headers=auth_headers(student_user),
    )
    assert res.status_code == 200
    assert res.json()["total"] == 1

    # 3. Filter with non-existent course_id
    res = await client.get(
        f"/api/v1/me/attendance?course_id={uuid.uuid4()}",
        headers=auth_headers(student_user),
    )
    assert res.status_code == 200
    assert res.json()["total"] == 0

    # 4. Another student's records are never visible to current student
    other_student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=other_student)
    await make_record(
        db_session,
        class_session=session_obj,
        student=other_student,
        status=AttendanceStatus.LATE,
    )
    res = await client.get(
        "/api/v1/me/attendance",
        headers=auth_headers(student_user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["student_id"] == str(student_user.id)


@pytest.mark.asyncio
async def test_check_in_concurrent_endpoint() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:

        async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                yield session

        test_app = create_app()
        test_app.dependency_overrides[get_db] = override_get_db

        async with AsyncSession(engine, expire_on_commit=False) as s:
            teacher = await make_user(s, role=UserRole.TEACHER)
            student = await make_user(s, role=UserRole.STUDENT)
            course = await make_course(s, teacher=teacher)
            await make_enrollment(s, course=course, student=student)
            now = datetime.now(UTC)
            session_obj = await make_session(
                s,
                course=course,
                starts_at=now,
                ends_at=now + timedelta(hours=2),
                status=SessionStatus.OPEN.value,
                checkin_code="CONC02",
                code_expires_at=now + timedelta(minutes=15),
            )
            session_id = session_obj.id
            token = create_access_token(sub=student.id, role=student.role)
            await s.commit()

        headers = {"Authorization": f"Bearer {token}"}
        async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
            r1, r2 = await asyncio.gather(
                ac.post(
                    f"/api/v1/sessions/{session_id}/check-in",
                    headers=headers,
                    json={"code": "CONC02"},
                ),
                ac.post(
                    f"/api/v1/sessions/{session_id}/check-in",
                    headers=headers,
                    json={"code": "CONC02"},
                ),
            )
            statuses = sorted([r1.status_code, r2.status_code])
            assert statuses == [200, 409]

        async with AsyncSession(engine) as s:
            recs = await attendance_service.attendance_repo.get_records_for_session(s, session_id)
            assert len(recs) == 1
    finally:
        await engine.dispose()
