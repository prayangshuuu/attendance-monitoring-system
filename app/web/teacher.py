import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession
from app.core.exceptions import (
    AppError,
    ValidationAppError,
)
from app.models.class_session import ClassSession
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.enums import AttendanceStatus, SessionStatus
from app.models.user import User
from app.repositories.course_repository import course_repository
from app.repositories.session_repository import session_repository
from app.schemas.attendance import ManualMarkItem
from app.schemas.session import RecurringSessionCreate, SessionCreate
from app.schemas.user import PasswordChange
from app.services.attendance_service import attendance_service
from app.services.course_service import course_service
from app.services.report_service import report_service
from app.services.session_service import session_service
from app.services.user_service import user_service
from app.utils.clock import SystemClock
from app.web.deps import (
    build_form_model,
    htmx_download_redirect,
    is_partial_request,
    pagination_query,
    render_template,
    require_web_teacher,
    verify_csrf,
)

router = APIRouter(
    prefix="/teacher",
    tags=["web-teacher"],
    dependencies=[Depends(require_web_teacher)],
)


def _toast_header(message: str, type_: str = "success") -> str:
    """Helper to produce an HX-Trigger JSON string for toast notifications."""
    return json.dumps({"toast": {"type": type_, "message": message}})


def _parse_datetimes(
    starts_at: str | None = None,
    ends_at: str | None = None,
    date_val: str | None = None,
    start_time_val: str | None = None,
    end_time_val: str | None = None,
    duration_minutes: int | None = None,
) -> tuple[datetime, datetime]:
    """Parse start and end datetimes from either ISO strings or date + time inputs."""
    start_dt: datetime | None = None
    end_dt: datetime | None = None

    if starts_at:
        try:
            start_dt = datetime.fromisoformat(starts_at.strip())
            start_dt = (
                start_dt.replace(tzinfo=UTC)
                if start_dt.tzinfo is None
                else start_dt.astimezone(UTC)
            )
        except Exception as e:
            raise ValidationAppError(f"Invalid start datetime: {starts_at}") from e

    if ends_at:
        try:
            end_dt = datetime.fromisoformat(ends_at.strip())
            end_dt = end_dt.replace(tzinfo=UTC) if end_dt.tzinfo is None else end_dt.astimezone(UTC)
        except Exception as e:
            raise ValidationAppError(f"Invalid end datetime: {ends_at}") from e

    if start_dt is None and date_val and start_time_val:
        try:
            d = date.fromisoformat(date_val.strip())
            t = time.fromisoformat(start_time_val.strip())
            start_dt = datetime.combine(d, t, tzinfo=UTC)
        except Exception as e:
            raise ValidationAppError("Invalid date or start time format") from e

    if start_dt is None:
        raise ValidationAppError("Session start time and date are required")

    if end_dt is None:
        if date_val and end_time_val:
            try:
                d = date.fromisoformat(date_val.strip())
                t = time.fromisoformat(end_time_val.strip())
                end_dt = datetime.combine(d, t, tzinfo=UTC)
            except Exception as e:
                raise ValidationAppError("Invalid end time format") from e
        elif duration_minutes is not None and duration_minutes > 0:
            end_dt = start_dt + timedelta(minutes=duration_minutes)
        else:
            end_dt = start_dt + timedelta(minutes=60)

    if end_dt <= start_dt:
        raise ValidationAppError("Session end time must be after start time")

    return start_dt, end_dt


# ==============================================================================
# 1. Teacher Dashboard
# ==============================================================================


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
) -> Response:
    """Teacher dashboard showing active live sessions, upcoming schedule, and courses summary."""
    # 1. Active live sessions
    stmt_active = (
        select(ClassSession)
        .join(Course, Course.id == ClassSession.course_id)
        .where(
            Course.teacher_id == current_user.id,
            ClassSession.status == SessionStatus.OPEN.value,
        )
        .options(selectinload(ClassSession.course))
        .order_by(ClassSession.starts_at.desc())
    )
    res_active = await db.execute(stmt_active)
    active_sessions = list(res_active.scalars().all())

    # 2. Upcoming scheduled sessions
    stmt_upcoming = (
        select(ClassSession)
        .join(Course, Course.id == ClassSession.course_id)
        .where(
            Course.teacher_id == current_user.id,
            ClassSession.status == SessionStatus.SCHEDULED.value,
        )
        .options(selectinload(ClassSession.course))
        .order_by(ClassSession.starts_at.asc())
        .limit(6)
    )
    res_upcoming = await db.execute(stmt_upcoming)
    upcoming_sessions = list(res_upcoming.scalars().all())

    # 3. Teacher's courses
    courses, total_courses = await course_repository.list(
        session=db, user=current_user, page=1, size=50
    )

    # 4. Total enrolled students across teacher's courses
    stmt_enrolled = (
        select(func.count(func.distinct(Enrollment.student_id)))
        .join(Course, Course.id == Enrollment.course_id)
        .where(Course.teacher_id == current_user.id)
    )
    res_enrolled = await db.execute(stmt_enrolled)
    total_students = res_enrolled.scalar_one() or 0

    return render_template(
        request=request,
        template_name="teacher/dashboard.html",
        context={
            "active_page": "dashboard",
            "active_sessions": active_sessions,
            "upcoming_sessions": upcoming_sessions,
            "courses": courses,
            "total_courses": total_courses,
            "total_students": total_students,
        },
    )


# ==============================================================================
# 2. Courses List & Course Detail
# ==============================================================================


@router.get("/courses", response_class=HTMLResponse)
async def courses_list(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query()] = None,
) -> Response:
    """List courses where course.teacher_id == current_user.id."""
    courses, total = await course_repository.list(
        session=db,
        user=current_user,
        page=page,
        size=size,
        search=search,
    )

    # Calculate enrollment counts per course
    course_items: list[dict[str, Any]] = []
    for c in courses:
        stmt_count = select(func.count(Enrollment.id)).where(Enrollment.course_id == c.id)
        res_count = await db.execute(stmt_count)
        enrolled_count = res_count.scalar_one() or 0
        course_items.append({"course": c, "enrolled_count": enrolled_count})

    total_pages = max(1, (total + size - 1) // size)

    return render_template(
        request=request,
        template_name="teacher/courses.html",
        context={
            "active_page": "courses",
            "courses": courses,
            "course_items": course_items,
            "total_courses": total,
            "current_page": page,
            "total_pages": total_pages,
            "search": search,
            "pagination_query": pagination_query(search=search, size=size if size != 20 else None),
        },
    )


@router.get("/courses/{id}", response_class=HTMLResponse)
async def course_detail(
    request: Request,
    id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Response:
    """Course detail page with enrolled student roster, past sessions list, and attendance rate."""
    course = await course_service.get_course(session=db, course_id=id, current_user=current_user)

    # Get course attendance report with students
    report = await report_service.get_course_report(
        session=db, course_id=id, current_user=current_user
    )

    # Get sessions for this course
    sessions, total_sessions = await session_repository.list_course_sessions(
        session=db,
        course_id=id,
        page=page,
        size=size,
    )

    at_risk_count = sum(1 for s in report.students if s.is_at_risk)

    return render_template(
        request=request,
        template_name="teacher/course_detail.html",
        context={
            "active_page": "courses",
            "course": course,
            "report": report,
            "sessions": sessions,
            "total_sessions": total_sessions,
            "at_risk_count": at_risk_count,
            "current_page": page,
            "page_size": size,
            "total_pages": max(1, (total_sessions + size - 1) // size),
            "pagination_query": pagination_query(size=size if size != 20 else None),
        },
    )


# ==============================================================================
# 3. Session Management (Schedule, Recurring, List)
# ==============================================================================


async def _sessions_context(
    db: DbSession,
    current_user: User,
    course_id: UUID | None = None,
    status_filter: str | None = None,
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """Build the template context for the teacher sessions page (list + schedule forms)."""
    courses, _ = await course_repository.list(session=db, user=current_user, page=1, size=100)

    conditions = [Course.teacher_id == current_user.id]
    if course_id:
        conditions.append(ClassSession.course_id == course_id)
    if status_filter:
        conditions.append(ClassSession.status == status_filter.lower())

    count_stmt = (
        select(func.count(ClassSession.id))
        .join(Course, Course.id == ClassSession.course_id)
        .where(and_(*conditions))
    )
    total_res = await db.execute(count_stmt)
    total_sessions = total_res.scalar_one() or 0

    stmt = (
        select(ClassSession)
        .join(Course, Course.id == ClassSession.course_id)
        .where(and_(*conditions))
        .options(selectinload(ClassSession.course))
        .order_by(ClassSession.starts_at.desc(), ClassSession.id.desc())
        .offset(max(0, (page - 1) * size))
        .limit(size)
    )
    res = await db.execute(stmt)
    sessions = list(res.scalars().all())

    total_pages = max(1, (total_sessions + size - 1) // size)
    today_str = date.today().isoformat()
    three_months_str = (date.today() + timedelta(days=90)).isoformat()

    return {
        "active_page": "sessions",
        "sessions": sessions,
        "courses": courses,
        "selected_course_id": str(course_id) if course_id else None,
        "current_status": status_filter,
        "total_sessions": total_sessions,
        "current_page": page,
        "total_pages": total_pages,
        "pagination_query": pagination_query(
            course_id=course_id, status=status_filter, size=size if size != 20 else None
        ),
        "default_date": today_str,
        "default_to_date": three_months_str,
    }


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_page(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    course_id: Annotated[UUID | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Response:
    """Session management: upcoming, live, and past sessions."""
    context = await _sessions_context(db, current_user, course_id, status_filter, page, size)
    return render_template(
        request=request,
        template_name="teacher/sessions.html",
        context=context,
    )


@router.post("/sessions")
async def schedule_session(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    csrf_dep: None = Depends(verify_csrf),
    course_id: Annotated[UUID | None, Form()] = None,
    starts_at: Annotated[str | None, Form()] = None,
    ends_at: Annotated[str | None, Form()] = None,
    date_val: Annotated[str | None, Form(alias="date")] = None,
    start_time_val: Annotated[str | None, Form(alias="start_time")] = None,
    end_time_val: Annotated[str | None, Form(alias="end_time")] = None,
    duration_minutes: Annotated[int | None, Form()] = None,
    late_after_minutes: Annotated[int, Form()] = 10,
) -> Response:
    """Schedule a new single class session."""
    try:
        if course_id is None:
            raise ValidationAppError("Course is required", code="INVALID_COURSE")
        start_dt, end_dt = _parse_datetimes(
            starts_at=starts_at,
            ends_at=ends_at,
            date_val=date_val,
            start_time_val=start_time_val,
            end_time_val=end_time_val,
            duration_minutes=duration_minutes,
        )
        session_read = await session_service.create_session(
            session=db,
            course_id=course_id,
            current_user=current_user,
            data=build_form_model(
                SessionCreate,
                starts_at=start_dt,
                ends_at=end_dt,
                late_after_minutes=late_after_minutes,
            ),
        )
    except AppError as exc:
        is_htmx = is_partial_request(request)
        if is_htmx:
            return Response(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                headers={"HX-Trigger": _toast_header(exc.message, "danger")},
                content=exc.message,
            )
        # Full page re-render with error
        context = await _sessions_context(db, current_user, course_id=course_id)
        context["error"] = exc.message
        return render_template(
            request=request,
            template_name="teacher/sessions.html",
            context=context,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    target_url = f"/teacher/sessions/{session_read.id}"
    is_htmx = is_partial_request(request)
    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "HX-Redirect": target_url,
                "HX-Trigger": _toast_header("Session scheduled successfully!"),
            },
        )
    return RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sessions/recurring")
async def schedule_recurring_sessions(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    csrf_dep: None = Depends(verify_csrf),
    course_id: Annotated[UUID | None, Form()] = None,
    weekday: Annotated[int, Form()] = 0,
    start_time_val: Annotated[str, Form(alias="start_time")] = "10:00",
    end_time_val: Annotated[str, Form(alias="end_time")] = "11:30",
    from_date_val: Annotated[str, Form(alias="from_date")] = "",
    to_date_val: Annotated[str, Form(alias="to_date")] = "",
    timezone_val: Annotated[str, Form(alias="timezone")] = "UTC",
    late_after_minutes: Annotated[int, Form()] = 10,
) -> Response:
    """Generate recurring sessions for a course across a date range."""
    try:
        if course_id is None:
            raise ValidationAppError("Course is required", code="INVALID_COURSE")
        st_time = time.fromisoformat(start_time_val.strip())
        en_time = time.fromisoformat(end_time_val.strip())
        f_date = date.fromisoformat(from_date_val.strip())
        t_date = date.fromisoformat(to_date_val.strip())

        result = await session_service.create_recurring(
            session=db,
            course_id=course_id,
            current_user=current_user,
            data=build_form_model(
                RecurringSessionCreate,
                weekday=weekday,
                start_time=st_time,
                end_time=en_time,
                from_date=f_date,
                to_date=t_date,
                timezone=timezone_val or "UTC",
                late_after_minutes=late_after_minutes,
            ),
        )
    except Exception as exc:
        err_msg = str(getattr(exc, "message", exc))
        is_htmx = is_partial_request(request)
        if is_htmx:
            return Response(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                headers={"HX-Trigger": _toast_header(err_msg, "danger")},
                content=err_msg,
            )
        context = await _sessions_context(db, current_user, course_id=course_id)
        context["error"] = err_msg
        return render_template(
            request=request,
            template_name="teacher/sessions.html",
            context=context,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    msg = f"{len(result.created)} sessions created ({result.skipped} skipped due to overlap)."
    is_htmx = is_partial_request(request)
    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "HX-Redirect": f"/teacher/sessions?course_id={course_id}",
                "HX-Trigger": _toast_header(msg),
            },
        )
    return RedirectResponse(
        url=f"/teacher/sessions?course_id={course_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ==============================================================================
# 4. Live Session Page & Code Controls
# ==============================================================================


@router.get("/sessions/{id}", response_class=HTMLResponse)
async def live_session_page(
    request: Request,
    id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
) -> Response:
    """Live session page with check-in code, countdown timer, controls, and roster."""
    # Verify access & load session with course
    class_session = await session_repository.get_by_id(db, id)
    if class_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    course = await course_repository.get_by_id(db, class_session.course_id)
    if course is None or course.teacher_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Load session with course relationship explicitly loaded
    stmt = (
        select(ClassSession).where(ClassSession.id == id).options(selectinload(ClassSession.course))
    )
    class_session_loaded = (await db.execute(stmt)).scalar_one()

    # Get live attendance roster
    roster = await attendance_service.get_session_roster(
        session=db, teacher_or_admin=current_user, session_id=id
    )

    # Compute attendance statistics
    present_cnt = 0
    late_cnt = 0
    absent_cnt = 0
    excused_cnt = 0
    for it in roster.items:
        if it.status:
            st = (it.status.value if hasattr(it.status, "value") else str(it.status)).lower()
            if st == AttendanceStatus.PRESENT.value:
                present_cnt += 1
            elif st == AttendanceStatus.LATE.value:
                late_cnt += 1
            elif st == AttendanceStatus.ABSENT.value:
                absent_cnt += 1
            elif st == AttendanceStatus.EXCUSED.value:
                excused_cnt += 1

    stats = {
        "total": len(roster.items),
        "present": present_cnt,
        "late": late_cnt,
        "absent": absent_cnt,
        "excused": excused_cnt,
    }

    return render_template(
        request=request,
        template_name="teacher/live_session.html",
        context={
            "active_page": "live",
            "session": class_session_loaded,
            "roster": roster,
            "stats": stats,
        },
    )


@router.post("/sessions/{id}/open")
async def open_session(
    request: Request,
    id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    csrf_dep: None = Depends(verify_csrf),
) -> Response:
    """Open session and generate a 6-character check-in code with expiration."""
    try:
        await session_service.open_session(session=db, session_id=id, current_user=current_user)
    except AppError as exc:
        is_htmx = is_partial_request(request)
        if is_htmx:
            return Response(
                status_code=status.HTTP_400_BAD_REQUEST,
                headers={"HX-Trigger": _toast_header(exc.message, "danger")},
                content=exc.message,
            )
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    redirect_url = f"/teacher/sessions/{id}"
    is_htmx = is_partial_request(request)
    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "HX-Redirect": redirect_url,
                "HX-Trigger": _toast_header("Session opened! Check-in code is now active."),
            },
        )
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sessions/{id}/close")
async def close_session(
    request: Request,
    id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    csrf_dep: None = Depends(verify_csrf),
) -> Response:
    """Close session, auto-marking unrecorded students as absent."""
    try:
        await session_service.close_session(session=db, session_id=id, current_user=current_user)
    except AppError as exc:
        is_htmx = is_partial_request(request)
        if is_htmx:
            return Response(
                status_code=status.HTTP_400_BAD_REQUEST,
                headers={"HX-Trigger": _toast_header(exc.message, "danger")},
                content=exc.message,
            )
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    redirect_url = f"/teacher/sessions/{id}"
    is_htmx = is_partial_request(request)
    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "HX-Redirect": redirect_url,
                "HX-Trigger": _toast_header("Session closed. Unrecorded students marked absent."),
            },
        )
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sessions/{id}/regenerate-code")
async def regenerate_code(
    request: Request,
    id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    csrf_dep: None = Depends(verify_csrf),
) -> Response:
    """Regenerate a new 6-character check-in code for an active open session."""
    try:
        await session_service.regenerate_code(session=db, session_id=id, current_user=current_user)
    except AppError as exc:
        is_htmx = is_partial_request(request)
        if is_htmx:
            return Response(
                status_code=status.HTTP_400_BAD_REQUEST,
                headers={"HX-Trigger": _toast_header(exc.message, "danger")},
                content=exc.message,
            )
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    redirect_url = f"/teacher/sessions/{id}"
    is_htmx = is_partial_request(request)
    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "HX-Redirect": redirect_url,
                "HX-Trigger": _toast_header("New check-in code generated successfully."),
            },
        )
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


# ==============================================================================
# 5. Live Roster HTMX Polling & Attendance Override
# ==============================================================================


@router.get("/sessions/{id}/roster", response_class=HTMLResponse)
async def session_roster(
    request: Request,
    id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
) -> Response:
    """Live roster partial for HTMX polling (hx-trigger='every 5s')."""
    stmt = (
        select(ClassSession).where(ClassSession.id == id).options(selectinload(ClassSession.course))
    )
    class_session = (await db.execute(stmt)).scalar_one_or_none()
    if class_session is None or class_session.course.teacher_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    roster = await attendance_service.get_session_roster(
        session=db, teacher_or_admin=current_user, session_id=id
    )

    return render_template(
        request=request,
        template_name="teacher/partials/session_roster.html",
        context={
            "session": class_session,
            "session_id": str(id),
            "roster": roster,
        },
    )


@router.post("/sessions/{id}/attendance")
async def update_attendance(
    request: Request,
    id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    csrf_dep: None = Depends(verify_csrf),
    student_id: Annotated[UUID | None, Form()] = None,
    status_val: Annotated[str, Form(alias="status")] = "",
    note: Annotated[str | None, Form()] = None,
) -> Response:
    """Manual attendance status override for a student in this session."""
    try:
        if student_id is None:
            raise ValidationAppError("student_id is required", code="INVALID_STUDENT")
        if not status_val:
            raise ValidationAppError("status is required", code="INVALID_STATUS")
        try:
            normalized_status = AttendanceStatus(status_val.strip().lower())
        except ValueError as err:
            raise ValidationAppError(
                f"Invalid status: {status_val}", code="INVALID_STATUS"
            ) from err

        await attendance_service.bulk_mark(
            session=db,
            teacher=current_user,
            session_id=id,
            items=[ManualMarkItem(student_id=student_id, status=normalized_status, note=note)],
        )
    except AppError as exc:
        is_htmx = is_partial_request(request)
        if is_htmx:
            return Response(
                status_code=status.HTTP_400_BAD_REQUEST,
                headers={"HX-Trigger": _toast_header(exc.message, "danger")},
                content=exc.message,
            )
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    # Fetch updated roster
    stmt = (
        select(ClassSession).where(ClassSession.id == id).options(selectinload(ClassSession.course))
    )
    class_session = (await db.execute(stmt)).scalar_one()
    roster = await attendance_service.get_session_roster(
        session=db, teacher_or_admin=current_user, session_id=id
    )

    is_htmx = is_partial_request(request)
    if is_htmx:
        return render_template(
            request=request,
            template_name="teacher/partials/session_roster.html",
            context={
                "session": class_session,
                "session_id": str(id),
                "roster": roster,
            },
            headers={
                "HX-Trigger": _toast_header(f"Marked as {normalized_status.value.capitalize()}")
            },
        )

    return RedirectResponse(url=f"/teacher/sessions/{id}", status_code=status.HTTP_303_SEE_OTHER)


# ==============================================================================
# 6. Reports & CSV Export
# ==============================================================================


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    course_id: Annotated[UUID | None, Query()] = None,
    from_dt: Annotated[date | None, Query()] = None,
    to_dt: Annotated[date | None, Query()] = None,
    export: Annotated[str | None, Query()] = None,
) -> Response:
    """Teacher's course attendance reports, at-risk warnings, and CSV export."""
    courses, _ = await course_repository.list(session=db, user=current_user, page=1, size=100)

    selected_course: Course | None = None
    if course_id:
        selected_course = await course_repository.get_by_id(db, course_id)
        if selected_course and selected_course.teacher_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    elif courses:
        selected_course = courses[0]

    from_datetime = datetime.combine(from_dt, time.min, tzinfo=UTC) if from_dt else None
    to_datetime = datetime.combine(to_dt, time.max, tzinfo=UTC) if to_dt else None

    # Handle CSV Export
    if export == "csv" and selected_course:
        if (redirect := htmx_download_redirect(request)) is not None:
            return redirect
        csv_file = await report_service.export_course_csv(
            session=db,
            course_id=selected_course.id,
            current_user=current_user,
            clock=SystemClock(),
            from_dt=from_datetime,
            to_dt=to_datetime,
        )
        return Response(
            content=csv_file.content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{csv_file.filename}"'},
        )

    report = None
    at_risk_students = []
    if selected_course:
        report = await report_service.get_course_report(
            session=db,
            course_id=selected_course.id,
            current_user=current_user,
            from_dt=from_datetime,
            to_dt=to_datetime,
        )
        at_risk_students = await report_service.get_course_at_risk(
            session=db,
            course_id=selected_course.id,
            current_user=current_user,
            from_dt=from_datetime,
            to_dt=to_datetime,
        )

    return render_template(
        request=request,
        template_name="teacher/reports.html",
        context={
            "active_page": "reports",
            "courses": courses,
            "selected_course": selected_course,
            "report": report,
            "at_risk_students": at_risk_students,
            "from_date_val": from_dt.isoformat() if from_dt else "",
            "to_date_val": to_dt.isoformat() if to_dt else "",
        },
    )


@router.get("/reports/{id}/csv")
async def export_course_csv(
    request: Request,
    id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    from_dt: Annotated[date | None, Query()] = None,
    to_dt: Annotated[date | None, Query()] = None,
) -> Response:
    """Download CSV attendance report for a specific course."""
    if (redirect := htmx_download_redirect(request)) is not None:
        return redirect
    from_datetime = datetime.combine(from_dt, time.min, tzinfo=UTC) if from_dt else None
    to_datetime = datetime.combine(to_dt, time.max, tzinfo=UTC) if to_dt else None

    csv_file = await report_service.export_course_csv(
        session=db,
        course_id=id,
        current_user=current_user,
        clock=SystemClock(),
        from_dt=from_datetime,
        to_dt=to_datetime,
    )
    return Response(
        content=csv_file.content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{csv_file.filename}"'},
    )


# ==============================================================================
# 7. Profile & Password Management
# ==============================================================================


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
) -> Response:
    """Teacher profile page with personal info and password change form."""
    stmt_courses = select(func.count(Course.id)).where(Course.teacher_id == current_user.id)
    courses_count = (await db.execute(stmt_courses)).scalar_one() or 0

    return render_template(
        request=request,
        template_name="teacher/profile.html",
        context={
            "active_page": "profile",
            "courses_count": courses_count,
        },
    )


@router.post("/profile/password")
async def change_password(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
    csrf_dep: None = Depends(verify_csrf),
    old_password: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    confirm_password: Annotated[str, Form()] = "",
) -> Response:
    """Change teacher account password."""
    stmt_courses = select(func.count(Course.id)).where(Course.teacher_id == current_user.id)
    courses_count = (await db.execute(stmt_courses)).scalar_one() or 0

    if new_password != confirm_password:
        return render_template(
            request=request,
            template_name="teacher/profile.html",
            context={
                "active_page": "profile",
                "error": "New password and confirmation password do not match.",
                "courses_count": courses_count,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    try:
        await user_service.change_password(
            session=db,
            user_id=current_user.id,
            data=build_form_model(
                PasswordChange, old_password=old_password, new_password=new_password
            ),
        )
    except AppError as exc:
        return render_template(
            request=request,
            template_name="teacher/profile.html",
            context={
                "active_page": "profile",
                "error": exc.message,
                "courses_count": courses_count,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    is_htmx = is_partial_request(request)
    headers = {"HX-Trigger": _toast_header("Password changed successfully!")} if is_htmx else None

    return render_template(
        request=request,
        template_name="teacher/profile.html",
        context={
            "active_page": "profile",
            "success": "Your password has been changed successfully.",
            "courses_count": courses_count,
        },
        headers=headers,
    )


# ==============================================================================
# 8. Live Session Shortcut Navigation (/teacher/live)
# ==============================================================================


@router.get("/live", response_class=RedirectResponse)
async def live_shortcut(
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_teacher)],
) -> RedirectResponse:
    """Shortcut /teacher/live: redirects to active open session or sessions page."""
    stmt = (
        select(ClassSession.id)
        .join(Course, Course.id == ClassSession.course_id)
        .where(
            Course.teacher_id == current_user.id,
            ClassSession.status == SessionStatus.OPEN.value,
        )
        .order_by(ClassSession.starts_at.desc())
        .limit(1)
    )
    res = await db.execute(stmt)
    live_id = res.scalar_one_or_none()

    if live_id:
        return RedirectResponse(
            url=f"/teacher/sessions/{live_id}", status_code=status.HTTP_302_FOUND
        )
    return RedirectResponse(url="/teacher/sessions", status_code=status.HTTP_302_FOUND)
