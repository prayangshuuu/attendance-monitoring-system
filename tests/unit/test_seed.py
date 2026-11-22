from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attendance_record import AttendanceRecord
from app.models.class_session import ClassSession
from app.models.course import Course
from app.models.enums import SessionStatus, UserRole
from app.models.user import User
from app.schemas.session import ensure_utc_datetime
from scripts.seed import _print_summary, seed_data


@pytest.mark.asyncio
async def test_seed_data_is_idempotent(db_session: AsyncSession) -> None:
    # First seed invocation
    res1 = await seed_data(db_session)

    assert res1 is not None
    assert res1["admins"] == 1
    assert res1["teachers"] == 3
    assert res1["students"] == 40
    assert res1["courses"] == 5
    assert res1["sessions"] == 61  # 60 closed + 1 open
    assert res1["attendance_records"] > 0

    # Second seed invocation on the same database session
    res2 = await seed_data(db_session)

    # Calling seed_data twice produces identical counts (zero duplicates created)
    assert res1 == res2

    # Verify directly via database queries
    user_count = (await db_session.execute(select(func.count(User.id)))).scalar_one()
    course_count = (await db_session.execute(select(func.count(Course.id)))).scalar_one()
    session_count = (await db_session.execute(select(func.count(ClassSession.id)))).scalar_one()
    attendance_count = (
        await db_session.execute(select(func.count(AttendanceRecord.id)))
    ).scalar_one()

    assert user_count == res1["users"]
    assert course_count == 5
    assert session_count == 61
    assert attendance_count == res1["attendance_records"]

    # Verify role counts
    teacher_count = (
        await db_session.execute(
            select(func.count(User.id)).where(User.role == UserRole.TEACHER.value)
        )
    ).scalar_one()
    student_count = (
        await db_session.execute(
            select(func.count(User.id)).where(User.role == UserRole.STUDENT.value)
        )
    ).scalar_one()
    admin_count = (
        await db_session.execute(
            select(func.count(User.id)).where(User.role == UserRole.ADMIN.value)
        )
    ).scalar_one()

    assert teacher_count == 3
    assert student_count == 40
    assert admin_count >= 1

    # Verify open session details
    open_sessions = (
        await db_session.scalars(
            select(ClassSession).where(ClassSession.status == SessionStatus.OPEN.value)
        )
    ).all()
    assert len(open_sessions) == 1
    open_sess = open_sessions[0]
    assert open_sess.checkin_code == "DEMO01"
    assert open_sess.code_expires_at is not None
    expires_at = ensure_utc_datetime(open_sess.code_expires_at)
    assert expires_at is not None
    assert expires_at > datetime.now(UTC)

    # Verify closed sessions count
    closed_count = (
        await db_session.execute(
            select(func.count(ClassSession.id)).where(
                ClassSession.status == SessionStatus.CLOSED.value
            )
        )
    ).scalar_one()
    assert closed_count == 60


@pytest.mark.asyncio
async def test_seed_data_print_summary(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    res = await seed_data(db_session)
    _print_summary(res)
    captured = capsys.readouterr()
    assert "ATTENDANCE MONITORING SYSTEM - DEMO SEED" in captured.out
    assert "admin@example.com" in captured.out
    assert "DEMO01" in captured.out
