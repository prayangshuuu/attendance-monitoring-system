import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SessionStatus, UserRole
from app.models.user import User
from tests.factories import make_course, make_enrollment, make_session, make_user


@pytest.mark.asyncio
async def test_create_session_endpoint(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_user: User,
    teacher_user: User,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    course = await make_course(db_session, teacher=teacher_user)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    now = datetime.now(UTC)

    payload = {
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=1, hours=2)).isoformat(),
        "late_after_minutes": 15,
    }

    # 1. Unauthenticated -> 401
    res = await client.post(f"/api/v1/courses/{course.id}/sessions", json=payload)
    assert res.status_code == 401

    # 2. Student -> 403
    res = await client.post(
        f"/api/v1/courses/{course.id}/sessions",
        headers=auth_headers(student_user),
        json=payload,
    )
    assert res.status_code == 403

    # 3. Non-owner teacher -> 403
    res = await client.post(
        f"/api/v1/courses/{course.id}/sessions",
        headers=auth_headers(other_teacher),
        json=payload,
    )
    assert res.status_code == 403

    # 4. Course not found -> 404
    res = await client.post(
        f"/api/v1/courses/{uuid.uuid4()}/sessions",
        headers=auth_headers(teacher_user),
        json=payload,
    )
    assert res.status_code == 404

    # 5. Naive datetime -> 422
    naive_payload = {
        "starts_at": "2026-11-25T10:00:00",
        "ends_at": "2026-11-25T12:00:00",
    }
    res = await client.post(
        f"/api/v1/courses/{course.id}/sessions",
        headers=auth_headers(teacher_user),
        json=naive_payload,
    )
    assert res.status_code == 422

    # 6. ends_at <= starts_at -> 422
    invalid_time_payload = {
        "starts_at": (now + timedelta(days=1, hours=2)).isoformat(),
        "ends_at": (now + timedelta(days=1)).isoformat(),
    }
    res = await client.post(
        f"/api/v1/courses/{course.id}/sessions",
        headers=auth_headers(teacher_user),
        json=invalid_time_payload,
    )
    assert res.status_code == 422

    # 7. Owner teacher -> 201 Created
    res = await client.post(
        f"/api/v1/courses/{course.id}/sessions",
        headers=auth_headers(teacher_user),
        json=payload,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["course_id"] == str(course.id)
    assert data["status"] == "scheduled"
    assert data["late_after_minutes"] == 15
    assert "created_at" in data
    assert "updated_at" in data

    # 8. Overlapping session -> 409 Conflict
    res = await client.post(
        f"/api/v1/courses/{course.id}/sessions",
        headers=auth_headers(admin_user),
        json=payload,
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "SESSION_OVERLAP"


@pytest.mark.asyncio
async def test_create_recurring_endpoint(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    course = await make_course(db_session, teacher=teacher_user)

    payload = {
        "weekday": 2,  # Wednesday
        "start_time": "14:00:00",
        "end_time": "16:00:00",
        "from_date": "2026-11-01",
        "to_date": "2026-11-20",
        "timezone": "Asia/Kuala_Lumpur",
        "late_after_minutes": 10,
    }

    # 1. Student -> 403
    res = await client.post(
        f"/api/v1/courses/{course.id}/sessions/recurring",
        headers=auth_headers(student_user),
        json=payload,
    )
    assert res.status_code == 403

    # 2. Invalid timezone -> 422
    bad_tz = {**payload, "timezone": "Bad/Timezone"}
    res = await client.post(
        f"/api/v1/courses/{course.id}/sessions/recurring",
        headers=auth_headers(teacher_user),
        json=bad_tz,
    )
    assert res.status_code == 422

    # 3. Success -> 201 (Wednesdays: Nov 4, 11, 18 -> 3 sessions)
    res = await client.post(
        f"/api/v1/courses/{course.id}/sessions/recurring",
        headers=auth_headers(teacher_user),
        json=payload,
    )
    assert res.status_code == 201
    data = res.json()
    assert len(data["created"]) == 3
    assert data["skipped"] == 0

    # 4. Calling again -> all 3 skipped
    res2 = await client.post(
        f"/api/v1/courses/{course.id}/sessions/recurring",
        headers=auth_headers(teacher_user),
        json=payload,
    )
    assert res2.status_code == 201
    data2 = res2.json()
    assert len(data2["created"]) == 0
    assert data2["skipped"] == 3


@pytest.mark.asyncio
async def test_get_and_list_sessions_endpoint(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    other_student = await make_user(db_session, role=UserRole.STUDENT)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher_user)
    await make_enrollment(db_session, course=course, student=student_user)

    now = datetime.now(UTC)
    s_open = await make_session(
        db_session,
        course=course,
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        status=SessionStatus.OPEN.value,
        checkin_code="K9X7P2",
        code_expires_at=now + timedelta(minutes=15),
    )

    # 1. GET /sessions/{id}
    # Non-enrolled student -> 403
    res = await client.get(
        f"/api/v1/sessions/{s_open.id}",
        headers=auth_headers(other_student),
    )
    assert res.status_code == 403

    # Non-owner teacher -> 403
    res = await client.get(
        f"/api/v1/sessions/{s_open.id}",
        headers=auth_headers(other_teacher),
    )
    assert res.status_code == 403

    # Enrolled student -> 200 with masked checkin_code
    res = await client.get(
        f"/api/v1/sessions/{s_open.id}",
        headers=auth_headers(student_user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(s_open.id)
    assert data["checkin_code"] is None
    assert data["code_expires_at"] is None

    # Teacher -> 200 with visible checkin_code
    res = await client.get(
        f"/api/v1/sessions/{s_open.id}",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 200
    assert res.json()["checkin_code"] == "K9X7P2"
    assert res.json()["code_expires_at"] is not None

    # Session not found -> 404
    res = await client.get(
        f"/api/v1/sessions/{uuid.uuid4()}",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 404

    # 2. GET /courses/{course_id}/sessions
    # Naive from query param -> 422
    res = await client.get(
        f"/api/v1/courses/{course.id}/sessions?from=2026-11-20T10:00:00",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 422

    # Query with status filter and aware range
    from_iso = (now - timedelta(hours=1)).isoformat()
    to_iso = (now + timedelta(hours=2)).isoformat()
    res = await client.get(
        f"/api/v1/courses/{course.id}/sessions",
        params={"status": "open", "from": from_iso, "to": to_iso},
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 200
    list_data = res.json()
    assert list_data["total"] == 1
    assert list_data["items"][0]["checkin_code"] == "K9X7P2"

    # Student listing -> checkin_code masked
    res = await client.get(
        f"/api/v1/courses/{course.id}/sessions",
        headers=auth_headers(student_user),
    )
    assert res.status_code == 200
    assert res.json()["items"][0]["checkin_code"] is None


@pytest.mark.asyncio
async def test_update_session_endpoint(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    course = await make_course(db_session, teacher=teacher_user)
    s = await make_session(db_session, course=course, status=SessionStatus.SCHEDULED.value)

    # 1. Student -> 403
    res = await client.patch(
        f"/api/v1/sessions/{s.id}",
        headers=auth_headers(student_user),
        json={"late_after_minutes": 5},
    )
    assert res.status_code == 403

    # 2. Teacher success -> 200
    res = await client.patch(
        f"/api/v1/sessions/{s.id}",
        headers=auth_headers(teacher_user),
        json={"late_after_minutes": 25},
    )
    assert res.status_code == 200
    assert res.json()["late_after_minutes"] == 25


@pytest.mark.asyncio
async def test_session_lifecycle_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher_user)
    s = await make_session(db_session, course=course, status=SessionStatus.SCHEDULED.value)

    # 1. Open session
    # Non-owner teacher -> 403
    res = await client.post(
        f"/api/v1/sessions/{s.id}/open",
        headers=auth_headers(other_teacher),
    )
    assert res.status_code == 403

    # Student -> 403
    res = await client.post(
        f"/api/v1/sessions/{s.id}/open",
        headers=auth_headers(student_user),
    )
    assert res.status_code == 403

    # Teacher opens -> 200 SessionOpenResponse
    res = await client.post(
        f"/api/v1/sessions/{s.id}/open",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 200
    open_data = res.json()
    assert open_data["status"] == "open"
    code1 = open_data["checkin_code"]
    assert len(code1) == 6

    # Open again -> 409 (invalid transition)
    res = await client.post(
        f"/api/v1/sessions/{s.id}/open",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_SESSION_TRANSITION"

    # 2. Regenerate code
    res = await client.post(
        f"/api/v1/sessions/{s.id}/regenerate-code",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 200
    regen_data = res.json()
    assert regen_data["status"] == "open"
    code2 = regen_data["checkin_code"]
    assert code2 != code1

    # 3. Close session
    res = await client.post(
        f"/api/v1/sessions/{s.id}/close",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 200
    close_data = res.json()
    assert close_data["status"] == "closed"
    assert close_data["checkin_code"] is None
    assert close_data["code_expires_at"] is None

    # Regenerate code on closed session -> 409
    res = await client.post(
        f"/api/v1/sessions/{s.id}/regenerate-code",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_SESSION_STATUS"

    # 4. Cancel session
    s2 = await make_session(db_session, course=course, status=SessionStatus.SCHEDULED.value)
    res = await client.post(
        f"/api/v1/sessions/{s2.id}/cancel",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 200
    cancel_data = res.json()
    assert cancel_data["status"] == "cancelled"

    # Cancel closed session -> 409
    res = await client.post(
        f"/api/v1/sessions/{s.id}/cancel",
        headers=auth_headers(teacher_user),
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_SESSION_TRANSITION"


@pytest.mark.asyncio
async def test_session_crossing_midnight_utc_lifecycle_and_checkin(
    app: FastAPI,
    client: AsyncClient,
    db_session: AsyncSession,
    teacher_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    course = await make_course(db_session, teacher=teacher_user)
    s1 = await make_user(db_session, role=UserRole.STUDENT, full_name="Student Early")
    s2 = await make_user(db_session, role=UserRole.STUDENT, full_name="Student Midnight")
    await make_enrollment(db_session, course=course, student=s1)
    await make_enrollment(db_session, course=course, student=s2)

    start_time = datetime(2026, 12, 1, 23, 0, tzinfo=UTC)
    end_time = datetime(2026, 12, 2, 1, 0, tzinfo=UTC)

    # 1. Schedule session crossing midnight UTC
    res = await client.post(
        f"/api/v1/courses/{course.id}/sessions",
        headers=auth_headers(teacher_user),
        json={
            "starts_at": start_time.isoformat(),
            "ends_at": end_time.isoformat(),
            "late_after_minutes": 15,
        },
    )
    assert res.status_code == 201
    session_id = res.json()["id"]

    # 2. Overlap detection across midnight: attempt to create overlapping session 23:30 - 00:30
    overlap_res = await client.post(
        f"/api/v1/courses/{course.id}/sessions",
        headers=auth_headers(teacher_user),
        json={
            "starts_at": datetime(2026, 12, 1, 23, 30, tzinfo=UTC).isoformat(),
            "ends_at": datetime(2026, 12, 2, 0, 30, tzinfo=UTC).isoformat(),
            "late_after_minutes": 10,
        },
    )
    assert overlap_res.status_code == 409
    assert overlap_res.json()["error"]["code"] == "SESSION_OVERLAP"

    # Helper clock class
    class FrozenClock:
        def __init__(self, frozen_time: datetime) -> None:
            self._frozen_time = frozen_time

        def now(self) -> datetime:
            return self._frozen_time

    from app.api.deps import get_clock

    # 3. Open session at 23:00 UTC (code valid until 23:15 UTC)
    app.dependency_overrides[get_clock] = lambda: FrozenClock(start_time)
    open_res = await client.post(
        f"/api/v1/sessions/{session_id}/open",
        headers=auth_headers(teacher_user),
    )
    assert open_res.status_code == 200
    checkin_code = open_res.json()["checkin_code"]

    # 4. Check-in before midnight (23:10 UTC, 10 min after start <= 15 min threshold -> PRESENT)
    app.dependency_overrides[get_clock] = lambda: FrozenClock(
        datetime(2026, 12, 1, 23, 10, tzinfo=UTC)
    )
    ci1 = await client.post(
        f"/api/v1/sessions/{session_id}/check-in",
        headers=auth_headers(s1),
        json={"code": checkin_code},
    )
    assert ci1.status_code == 200
    assert ci1.json()["status"] == "present"

    # 5. Teacher regenerates code after midnight (00:00 UTC, code now valid until 00:15 UTC)
    app.dependency_overrides[get_clock] = lambda: FrozenClock(
        datetime(2026, 12, 2, 0, 0, tzinfo=UTC)
    )
    regen_res = await client.post(
        f"/api/v1/sessions/{session_id}/regenerate-code",
        headers=auth_headers(teacher_user),
    )
    assert regen_res.status_code == 200
    new_code = regen_res.json()["checkin_code"]

    # 6. Check-in after midnight (00:05 UTC next day, 65 min after start > 15 min threshold -> LATE)
    app.dependency_overrides[get_clock] = lambda: FrozenClock(
        datetime(2026, 12, 2, 0, 5, tzinfo=UTC)
    )
    ci2 = await client.post(
        f"/api/v1/sessions/{session_id}/check-in",
        headers=auth_headers(s2),
        json={"code": new_code},
    )
    assert ci2.status_code == 200
    assert ci2.json()["status"] == "late"

    app.dependency_overrides.pop(get_clock, None)
