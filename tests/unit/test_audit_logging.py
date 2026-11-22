from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AttendanceStatus, SessionStatus, UserRole
from app.schemas.attendance import ManualMarkItem
from app.schemas.user import UserUpdate
from app.services.attendance_service import attendance_service
from app.services.enrollment_service import enrollment_service
from app.services.user_service import user_service
from tests.factories import make_course, make_session, make_user


@pytest.mark.asyncio
async def test_audit_log_attendance_update_record(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student = await make_user(db_session, role=UserRole.STUDENT, student_id="STU-AUDIT-1")
    course = await make_course(db_session, teacher_id=teacher.id)
    await enrollment_service.enroll_students(
        db_session, course.id, [student.id], current_user=teacher
    )

    class_session = await make_session(
        db_session,
        course_id=course.id,
        status=SessionStatus.OPEN,
        checkin_code="123456",
        code_expires_at=datetime.now(UTC),
    )

    # Initial mark
    record = (
        await attendance_service.bulk_mark(
            db_session,
            teacher,
            class_session.id,
            [ManualMarkItem(student_id=student.id, status=AttendanceStatus.ABSENT)],
        )
    )[0]

    with caplog.at_level(logging.INFO, logger="audit"):
        await attendance_service.update_record(
            db_session,
            teacher,
            record.id,
            status=AttendanceStatus.PRESENT,
            note="Excuse accepted",
        )

    audit_logs = [r for r in caplog.records if r.name == "audit"]
    assert len(audit_logs) >= 1
    log_entry = audit_logs[-1]
    assert "Manual attendance override" in log_entry.getMessage()
    assert log_entry.actor_id == str(teacher.id)
    assert log_entry.target_student_id == str(student.id)
    assert log_entry.session_id == str(class_session.id)
    assert log_entry.old_status == AttendanceStatus.ABSENT.value
    assert log_entry.new_status == AttendanceStatus.PRESENT.value


@pytest.mark.asyncio
async def test_audit_log_attendance_bulk_mark(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    teacher = await make_user(db_session, role=UserRole.TEACHER)
    student1 = await make_user(db_session, role=UserRole.STUDENT, student_id="STU-AUDIT-2")
    student2 = await make_user(db_session, role=UserRole.STUDENT, student_id="STU-AUDIT-3")
    course = await make_course(db_session, teacher_id=teacher.id)
    await enrollment_service.enroll_students(
        db_session, course.id, [student1.id, student2.id], current_user=teacher
    )

    class_session = await make_session(
        db_session,
        course_id=course.id,
        status=SessionStatus.OPEN,
        checkin_code="654321",
        code_expires_at=datetime.now(UTC),
    )

    with caplog.at_level(logging.INFO, logger="audit"):
        await attendance_service.bulk_mark(
            db_session,
            teacher,
            class_session.id,
            [
                ManualMarkItem(student_id=student1.id, status=AttendanceStatus.PRESENT),
                ManualMarkItem(student_id=student2.id, status=AttendanceStatus.LATE),
            ],
        )

    audit_logs = [r for r in caplog.records if r.name == "audit"]
    assert len(audit_logs) >= 2
    student_ids = {r.target_student_id for r in audit_logs}
    assert str(student1.id) in student_ids
    assert str(student2.id) in student_ids


@pytest.mark.asyncio
async def test_audit_log_user_role_change(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    target_user = await make_user(db_session, role=UserRole.TEACHER)

    with caplog.at_level(logging.INFO, logger="audit"):
        await user_service.update_user(
            db_session,
            target_user.id,
            UserUpdate(role=UserRole.ADMIN),
            actor_id=admin.id,
        )

    audit_logs = [r for r in caplog.records if r.name == "audit"]
    assert len(audit_logs) == 1
    log_entry = audit_logs[0]
    assert "User role changed" in log_entry.getMessage()
    assert log_entry.actor_id == str(admin.id)
    assert log_entry.target_user_id == str(target_user.id)
    assert log_entry.old_role == UserRole.TEACHER.value
    assert log_entry.new_role == UserRole.ADMIN.value


@pytest.mark.asyncio
async def test_no_audit_log_when_user_role_unchanged(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    admin = await make_user(db_session, role=UserRole.ADMIN)
    target_user = await make_user(db_session, role=UserRole.TEACHER)

    with caplog.at_level(logging.INFO, logger="audit"):
        await user_service.update_user(
            db_session,
            target_user.id,
            UserUpdate(full_name="New Name"),
            actor_id=admin.id,
        )

    audit_logs = [r for r in caplog.records if r.name == "audit"]
    assert len(audit_logs) == 0
