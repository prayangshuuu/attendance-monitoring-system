from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse

from app.api.deps import AdminUser, ClockDep, CurrentUser, DbSession, TeacherOrAdmin
from app.schemas.report import (
    AtRiskStudent,
    CourseReport,
    DashboardSummary,
    SessionSummary,
    StudentFullReport,
)
from app.services.report_service import report_service

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/courses/{id}", response_model=CourseReport)
async def get_course_report(
    id: UUID,
    db: DbSession,
    current_user: TeacherOrAdmin,
    from_dt: Annotated[datetime | None, Query(alias="from")] = None,
    to_dt: Annotated[datetime | None, Query(alias="to")] = None,
) -> CourseReport:
    return await report_service.get_course_report(
        session=db,
        course_id=id,
        current_user=current_user,
        from_dt=from_dt,
        to_dt=to_dt,
    )


@router.get("/courses/{id}/at-risk", response_model=list[AtRiskStudent])
async def get_course_at_risk(
    id: UUID,
    db: DbSession,
    current_user: TeacherOrAdmin,
    from_dt: Annotated[datetime | None, Query(alias="from")] = None,
    to_dt: Annotated[datetime | None, Query(alias="to")] = None,
) -> list[AtRiskStudent]:
    return await report_service.get_course_at_risk(
        session=db,
        course_id=id,
        current_user=current_user,
        from_dt=from_dt,
        to_dt=to_dt,
    )


@router.get("/courses/{id}/export.csv")
async def export_course_csv(
    id: UUID,
    db: DbSession,
    current_user: TeacherOrAdmin,
    clock: ClockDep,
    from_dt: Annotated[datetime | None, Query(alias="from")] = None,
    to_dt: Annotated[datetime | None, Query(alias="to")] = None,
) -> Response:
    csv_export = await report_service.export_course_csv(
        session=db,
        course_id=id,
        current_user=current_user,
        clock=clock,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    return StreamingResponse(
        iter([csv_export.content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{csv_export.filename}"'},
    )


@router.get("/sessions/{id}", response_model=SessionSummary)
async def get_session_summary(
    id: UUID,
    db: DbSession,
    current_user: TeacherOrAdmin,
) -> SessionSummary:
    return await report_service.get_session_summary(
        session=db,
        session_id=id,
        current_user=current_user,
    )


@router.get("/students/{id}", response_model=StudentFullReport)
async def get_student_report(
    id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> StudentFullReport:
    return await report_service.get_student_report(
        session=db,
        student_id=id,
        current_user=current_user,
    )


@router.get("/dashboard", response_model=DashboardSummary)
async def get_dashboard(
    db: DbSession,
    current_user: AdminUser,
    clock: ClockDep,
) -> DashboardSummary:
    return await report_service.get_dashboard(
        session=db,
        current_user=current_user,
        clock=clock,
    )
