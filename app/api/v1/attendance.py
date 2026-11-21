from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import ClockDep, CurrentUser, DbSession, RateLimiterDep
from app.core.exceptions import AppError
from app.models.attendance_record import AttendanceRecord
from app.schemas.attendance import (
    AttendanceRead,
    CheckInRequest,
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
