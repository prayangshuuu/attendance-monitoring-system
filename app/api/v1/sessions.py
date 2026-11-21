from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import ClockDep, CurrentUser, DbSession
from app.models.enums import SessionStatus
from app.schemas.common import Page, PaginationParams
from app.schemas.session import (
    RecurringSessionCreate,
    RecurringSessionResult,
    SessionCreate,
    SessionOpenResponse,
    SessionRead,
    SessionUpdate,
    TzAwareDatetime,
)
from app.services.session_service import session_service

router = APIRouter(tags=["sessions"])


@router.post(
    "/courses/{course_id}/sessions",
    response_model=SessionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a new class session",
)
async def create_session(
    course_id: UUID,
    data: SessionCreate,
    current_user: CurrentUser,
    db: DbSession,
    clock: ClockDep,
) -> SessionRead:
    return await session_service.create_session(
        session=db,
        course_id=course_id,
        current_user=current_user,
        data=data,
        clock=clock,
    )


@router.post(
    "/courses/{course_id}/sessions/recurring",
    response_model=RecurringSessionResult,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule recurring class sessions",
)
async def create_recurring_sessions(
    course_id: UUID,
    data: RecurringSessionCreate,
    current_user: CurrentUser,
    db: DbSession,
    clock: ClockDep,
) -> RecurringSessionResult:
    return await session_service.create_recurring(
        session=db,
        course_id=course_id,
        current_user=current_user,
        data=data,
        clock=clock,
    )


@router.get(
    "/courses/{course_id}/sessions",
    response_model=Page[SessionRead],
    status_code=status.HTTP_200_OK,
    summary="List sessions for a course with pagination and filters",
)
async def list_course_sessions(
    course_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    pagination: Annotated[PaginationParams, Depends()],
    status_filter: Annotated[
        SessionStatus | None, Query(alias="status", description="Filter by session status")
    ] = None,
    from_dt: Annotated[
        TzAwareDatetime | None,
        Query(alias="from", description="Filter sessions starting on or after"),
    ] = None,
    to_dt: Annotated[
        TzAwareDatetime | None,
        Query(alias="to", description="Filter sessions starting on or before"),
    ] = None,
) -> Page[SessionRead]:
    return await session_service.list_course_sessions(
        session=db,
        course_id=course_id,
        current_user=current_user,
        page=pagination.page,
        size=pagination.size,
        status=status_filter,
        from_dt=from_dt,
        to_dt=to_dt,
    )


@router.get(
    "/sessions/{id}",
    response_model=SessionRead,
    status_code=status.HTTP_200_OK,
    summary="Get class session details by ID",
)
async def get_session(
    id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> SessionRead:
    return await session_service.get_session(
        session=db,
        session_id=id,
        current_user=current_user,
    )


@router.patch(
    "/sessions/{id}",
    response_model=SessionRead,
    status_code=status.HTTP_200_OK,
    summary="Update a scheduled class session",
)
async def update_session(
    id: UUID,
    data: SessionUpdate,
    current_user: CurrentUser,
    db: DbSession,
    clock: ClockDep,
) -> SessionRead:
    return await session_service.update_session(
        session=db,
        session_id=id,
        current_user=current_user,
        data=data,
        clock=clock,
    )


@router.post(
    "/sessions/{id}/open",
    response_model=SessionOpenResponse,
    status_code=status.HTTP_200_OK,
    summary="Open a scheduled class session",
)
async def open_session(
    id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    clock: ClockDep,
) -> SessionOpenResponse:
    return await session_service.open_session(
        session=db,
        session_id=id,
        current_user=current_user,
        clock=clock,
    )


@router.post(
    "/sessions/{id}/regenerate-code",
    response_model=SessionOpenResponse,
    status_code=status.HTTP_200_OK,
    summary="Regenerate check-in code for an open class session",
)
async def regenerate_code(
    id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    clock: ClockDep,
) -> SessionOpenResponse:
    return await session_service.regenerate_code(
        session=db,
        session_id=id,
        current_user=current_user,
        clock=clock,
    )


@router.post(
    "/sessions/{id}/close",
    response_model=SessionRead,
    status_code=status.HTTP_200_OK,
    summary="Close an open class session",
)
async def close_session(
    id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    clock: ClockDep,
) -> SessionRead:
    return await session_service.close_session(
        session=db,
        session_id=id,
        current_user=current_user,
        clock=clock,
    )


@router.post(
    "/sessions/{id}/cancel",
    response_model=SessionRead,
    status_code=status.HTTP_200_OK,
    summary="Cancel a scheduled class session",
)
async def cancel_session(
    id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    clock: ClockDep,
) -> SessionRead:
    return await session_service.cancel_session(
        session=db,
        session_id=id,
        current_user=current_user,
        clock=clock,
    )
