from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import ClockDep, CurrentUser, DbSession, RateLimiterDep, TeacherOrAdmin
from app.core.exceptions import AppError
from app.models.attendance_record import AttendanceRecord
from app.schemas.attendance import (
    AttendanceRead,
    AttendanceUpdate,
    BulkMarkRequest,
    CheckInRequest,
    SessionAttendanceRosterResponse,
)
from app.schemas.common import Page, PaginationParams
from app.services.attendance_service import attendance_service

router = APIRouter(tags=["attendance"])


@router.post(
    "/sessions/{id}/check-in",
    response_model=AttendanceRead,
    status_code=status.HTTP_200_OK,
    summary="Student check-in using passcode",
)
async def check_in(
    id: UUID,
    body: CheckInRequest,
    current_user: CurrentUser,
    db: DbSession,
    clock: ClockDep,
    rate_limiter: RateLimiterDep,
) -> AttendanceRecord:
    key = f"{id}:{current_user.id}"
    allowed = await rate_limiter.is_allowed(key, max_requests=5, window_seconds=60)
    if not allowed:
        raise AppError(
            message="Too many check-in attempts. Please try again later.",
            code="TOO_MANY_ATTEMPTS",
            status_code=429,
        )
    return await attendance_service.check_in(
        session=db,
        student=current_user,
        session_id=id,
        code=body.code,
        clock=clock,
    )


@router.get(
    "/sessions/{id}/attendance",
    response_model=SessionAttendanceRosterResponse,
    status_code=status.HTTP_200_OK,
    summary="Get session attendance roster",
)
async def get_session_attendance(
    id: UUID,
    current_user: TeacherOrAdmin,
    db: DbSession,
) -> SessionAttendanceRosterResponse:
    return await attendance_service.get_session_roster(
        session=db,
        teacher_or_admin=current_user,
        session_id=id,
    )


@router.put(
    "/sessions/{id}/attendance",
    response_model=list[AttendanceRead],
    status_code=status.HTTP_200_OK,
    summary="Bulk manual mark session attendance",
)
async def bulk_mark_attendance(
    id: UUID,
    body: BulkMarkRequest,
    current_user: TeacherOrAdmin,
    db: DbSession,
) -> list[AttendanceRecord]:
    return await attendance_service.bulk_mark(
        session=db,
        teacher=current_user,
        session_id=id,
        items=body.items,
    )


@router.patch(
    "/attendance/{record_id}",
    response_model=AttendanceRead,
    status_code=status.HTTP_200_OK,
    summary="Update single attendance record",
)
async def update_attendance_record(
    record_id: UUID,
    body: AttendanceUpdate,
    current_user: TeacherOrAdmin,
    db: DbSession,
) -> AttendanceRecord:
    return await attendance_service.update_record(
        session=db,
        teacher=current_user,
        record_id=record_id,
        status=body.status,
        note=body.note,
    )


@router.get(
    "/me/attendance",
    response_model=Page[AttendanceRead],
    status_code=status.HTTP_200_OK,
    summary="Get current user's attendance records",
)
async def get_my_attendance(
    current_user: CurrentUser,
    db: DbSession,
    pagination: Annotated[PaginationParams, Depends()],
    course_id: Annotated[UUID | None, Query(description="Filter attendance by course ID")] = None,
) -> Page[AttendanceRead]:
    return await attendance_service.list_student_records(
        session=db,
        student=current_user,
        course_id=course_id,
        page=pagination.page,
        size=pagination.size,
    )
