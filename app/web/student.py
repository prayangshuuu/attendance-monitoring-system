import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession
from app.core.exceptions import (
    AppError,
    ConflictError,
    PermissionDeniedError,
    ValidationAppError,
)
from app.models.attendance_record import AttendanceRecord
from app.models.class_session import ClassSession
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.enums import AttendanceStatus
from app.models.user import User
from app.repositories.attendance_repository import attendance_repository
from app.repositories.course_repository import course_repository
from app.repositories.session_repository import session_repository
from app.schemas.report import StudentReportItem
from app.schemas.user import PasswordChange
from app.services.attendance_service import attendance_service
from app.services.report_service import report_service
from app.services.user_service import user_service
from app.web.deps import (
    build_form_model,
    is_partial_request,
    render_template,
    require_web_student,
    verify_csrf,
)

router = APIRouter(
    prefix="/student",
    tags=["web-student"],
    dependencies=[Depends(require_web_student)],
)


def _toast_header(message: str, type_: str = "success") -> str:
    """Helper to produce an HX-Trigger JSON string for toast notifications."""
    return json.dumps({"toast": {"type": type_, "message": message}})


# ==============================================================================
# 1. Student Dashboard (GET / or GET /dashboard)
# ==============================================================================


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def student_dashboard(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_student)],
) -> Response:
    """Render student dashboard with overall attendance %, at-risk alert, and courses."""
    report = await report_service.get_student_report(
        session=db,
        student_id=current_user.id,
        current_user=current_user,
    )

    total_enrolled = len(report.courses)
    total_sessions = sum(c.total_sessions for c in report.courses)
    total_attended = sum(c.attended_sessions for c in report.courses)
    overall_percentage: float | None = (
        round((total_attended / total_sessions) * 100.0, 1) if total_sessions > 0 else None
    )

    at_risk_courses = [c for c in report.courses if c.is_at_risk]
    is_at_risk = len(at_risk_courses) > 0

    # Active open sessions for enrolled courses
    active_sessions = await attendance_service.get_active_sessions_for_student(
        session=db,
        student_id=current_user.id,
    )

    # Determine which active sessions the student has already checked into
    active_session_ids = [s.id for s in active_sessions]
    checked_in_records: dict[UUID, AttendanceRecord] = {}
    if active_session_ids:
        stmt = select(AttendanceRecord).where(
            AttendanceRecord.student_id == current_user.id,
            AttendanceRecord.session_id.in_(active_session_ids),
        )
        records = (await db.execute(stmt)).scalars().all()
        checked_in_records = {r.session_id: r for r in records}

    return render_template(
        request=request,
        template_name="student/dashboard.html",
        context={
            "active_page": "dashboard",
            "report": report,
            "total_enrolled": total_enrolled,
            "total_sessions": total_sessions,
            "total_attended": total_attended,
            "overall_percentage": overall_percentage,
            "at_risk_courses": at_risk_courses,
            "is_at_risk": is_at_risk,
            "active_sessions": active_sessions,
            "checked_in_records": checked_in_records,
        },
    )


# ==============================================================================
# 2. Check-in Page (GET /checkin & GET /check-in)
# ==============================================================================


@router.get("/checkin", response_class=HTMLResponse)
@router.get("/check-in", response_class=HTMLResponse)
async def checkin_page(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_student)],
) -> Response:
    """Render check-in form page with code input and enrolled courses with active sessions."""
    active_sessions = await attendance_service.get_active_sessions_for_student(
        session=db,
        student_id=current_user.id,
    )

    active_session_ids = [s.id for s in active_sessions]
    checked_in_records: dict[UUID, AttendanceRecord] = {}
    if active_session_ids:
        stmt = select(AttendanceRecord).where(
            AttendanceRecord.student_id == current_user.id,
            AttendanceRecord.session_id.in_(active_session_ids),
        )
        records = (await db.execute(stmt)).scalars().all()
        checked_in_records = {r.session_id: r for r in records}

    return render_template(
        request=request,
        template_name="student/checkin.html",
        context={
            "active_page": "checkin",
            "active_sessions": active_sessions,
            "checked_in_records": checked_in_records,
        },
    )


# ==============================================================================
# 3. Check-in Submit (POST /checkin & POST /check-in)
# ==============================================================================


@router.post("/checkin")
@router.post("/check-in")
async def checkin_submit(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_student)],
    csrf_dep: None = Depends(verify_csrf),
    code: Annotated[str, Form()] = "",
    session_id: Annotated[str, Form()] = "",
) -> Response:
    """Submit 6-character check-in code and mark attendance.

    Supports HTMX partial updates returning partials/checkin_result.html or full page reload.
    """
    is_htmx = is_partial_request(request)
    cleaned_code = code.strip().upper()

    # Active sessions lookup helper for re-rendering context on errors
    async def _get_active_sessions_ctx() -> tuple[list[ClassSession], dict[UUID, AttendanceRecord]]:
        sessions = await attendance_service.get_active_sessions_for_student(
            session=db, student_id=current_user.id
        )
        s_ids = [s.id for s in sessions]
        chk_map: dict[UUID, AttendanceRecord] = {}
        if s_ids:
            s_recs = (
                (
                    await db.execute(
                        select(AttendanceRecord).where(
                            AttendanceRecord.student_id == current_user.id,
                            AttendanceRecord.session_id.in_(s_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            chk_map = {r.session_id: r for r in s_recs}
        return sessions, chk_map

    # Validation: code length
    if not cleaned_code:
        err_msg = "Please enter your 6-character check-in code."
        headers = {"HX-Trigger": _toast_header(err_msg, "error")} if is_htmx else None
        active_s, chk_m = await _get_active_sessions_ctx()
        return render_template(
            request=request,
            template_name="student/checkin.html",
            context={
                "active_page": "checkin",
                "error": err_msg,
                "code": "",
                "active_sessions": active_s,
                "checked_in_records": chk_m,
            },
            partial_template="student/partials/checkin_result.html",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers=headers,
        )

    if len(cleaned_code) != 6:
        err_msg = "Check-in code must be exactly 6 characters."
        headers = {"HX-Trigger": _toast_header(err_msg, "error")} if is_htmx else None
        active_s, chk_m = await _get_active_sessions_ctx()
        return render_template(
            request=request,
            template_name="student/checkin.html",
            context={
                "active_page": "checkin",
                "error": err_msg,
                "code": cleaned_code,
                "active_sessions": active_s,
                "checked_in_records": chk_m,
            },
            partial_template="student/partials/checkin_result.html",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers=headers,
        )

    # Resolve target session_id
    target_session_id: UUID | None = None
    if session_id.strip():
        try:
            target_session_id = UUID(session_id.strip())
        except ValueError:
            err_msg = "Invalid class session selected."
            headers = {"HX-Trigger": _toast_header(err_msg, "error")} if is_htmx else None
            active_s, chk_m = await _get_active_sessions_ctx()
            return render_template(
                request=request,
                template_name="student/checkin.html",
                context={
                    "active_page": "checkin",
                    "error": err_msg,
                    "code": cleaned_code,
                    "active_sessions": active_s,
                    "checked_in_records": chk_m,
                },
                partial_template="student/partials/checkin_result.html",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                headers=headers,
            )
    else:
        # Auto-resolve active session for student
        active_sessions, _ = await _get_active_sessions_ctx()
        if not active_sessions:
            err_msg = "No active class sessions are currently open for your courses."
            headers = {"HX-Trigger": _toast_header(err_msg, "error")} if is_htmx else None
            return render_template(
                request=request,
                template_name="student/checkin.html",
                context={
                    "active_page": "checkin",
                    "error": err_msg,
                    "code": cleaned_code,
                    "active_sessions": [],
                    "checked_in_records": {},
                },
                partial_template="student/partials/checkin_result.html",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                headers=headers,
            )
        elif len(active_sessions) == 1:
            target_session_id = active_sessions[0].id
        else:
            # Check if one of the open sessions matches the code
            matching = [
                s for s in active_sessions if (s.checkin_code or "").upper() == cleaned_code
            ]
            target_session_id = matching[0].id if len(matching) == 1 else active_sessions[0].id

    # Call attendance service
    try:
        record = await attendance_service.check_in_student(
            session=db,
            session_id=target_session_id,
            student_id=current_user.id,
            code=cleaned_code,
        )
    except ValidationAppError as exc:
        if exc.code == "INVALID_CODE":
            err_msg = "Invalid check-in code. Please verify the code displayed in class."
        elif exc.code == "CODE_EXPIRED":
            err_msg = "Check-in code has expired. Please ask your instructor for a fresh code."
        else:
            err_msg = exc.message
        headers = {"HX-Trigger": _toast_header(err_msg, "error")} if is_htmx else None
        active_s, chk_m = await _get_active_sessions_ctx()
        return render_template(
            request=request,
            template_name="student/checkin.html",
            context={
                "active_page": "checkin",
                "error": err_msg,
                "code": cleaned_code,
                "active_sessions": active_s,
                "checked_in_records": chk_m,
            },
            partial_template="student/partials/checkin_result.html",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers=headers,
        )
    except ConflictError as exc:
        if exc.code == "ALREADY_CHECKED_IN":
            err_msg = "You have already checked in for this session."
        elif exc.code == "SESSION_NOT_OPEN":
            err_msg = "This class session is no longer open for check-in."
        else:
            err_msg = exc.message
        headers = {"HX-Trigger": _toast_header(err_msg, "warning")} if is_htmx else None
        active_s, chk_m = await _get_active_sessions_ctx()
        return render_template(
            request=request,
            template_name="student/checkin.html",
            context={
                "active_page": "checkin",
                "error": err_msg,
                "code": cleaned_code,
                "active_sessions": active_s,
                "checked_in_records": chk_m,
            },
            partial_template="student/partials/checkin_result.html",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers=headers,
        )
    except PermissionDeniedError:
        err_msg = "You are not enrolled in the course for this session."
        headers = {"HX-Trigger": _toast_header(err_msg, "error")} if is_htmx else None
        active_s, chk_m = await _get_active_sessions_ctx()
        return render_template(
            request=request,
            template_name="student/checkin.html",
            context={
                "active_page": "checkin",
                "error": err_msg,
                "code": cleaned_code,
                "active_sessions": active_s,
                "checked_in_records": chk_m,
            },
            partial_template="student/partials/checkin_result.html",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers=headers,
        )
    except AppError as exc:
        headers = {"HX-Trigger": _toast_header(exc.message, "error")} if is_htmx else None
        active_s, chk_m = await _get_active_sessions_ctx()
        return render_template(
            request=request,
            template_name="student/checkin.html",
            context={
                "active_page": "checkin",
                "error": exc.message,
                "code": cleaned_code,
                "active_sessions": active_s,
                "checked_in_records": chk_m,
            },
            partial_template="student/partials/checkin_result.html",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers=headers,
        )

    # Success feedback construction
    class_session = await session_repository.get_by_id(db, record.session_id)
    course = (
        await course_repository.get_by_id(db, class_session.course_id) if class_session else None
    )

    is_late = record.status == AttendanceStatus.LATE.value
    status_display = "Marked Late" if is_late else "Marked Present"
    time_display = (
        record.checked_in_at.strftime("%H:%M:%S UTC")
        if record.checked_in_at
        else datetime.now(UTC).strftime("%H:%M:%S UTC")
    )
    course_name = f"{course.code} ({course.title})" if course else "Class Session"

    success_msg = (
        f"{status_display} for {course_name} at {time_display}."
        if not is_late
        else (
            f"{status_display} (recorded after late threshold) for {course_name} at {time_display}."
        )
    )

    toast_type = "warning" if is_late else "success"
    headers = {"HX-Trigger": _toast_header(success_msg, toast_type)} if is_htmx else None

    active_s, chk_m = await _get_active_sessions_ctx()
    return render_template(
        request=request,
        template_name="student/checkin.html",
        context={
            "active_page": "checkin",
            "success": success_msg,
            "record": record,
            "course": course,
            "status_display": status_display,
            "time_display": time_display,
            "is_late": is_late,
            "active_sessions": active_s,
            "checked_in_records": chk_m,
        },
        partial_template="student/partials/checkin_result.html",
        status_code=status.HTTP_200_OK,
        headers=headers,
    )


# ==============================================================================
# 4. Attendance History (GET /attendance)
# ==============================================================================


@router.get("/attendance", response_class=HTMLResponse)
async def attendance_history(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_student)],
    course_id: Annotated[UUID | None, Query(description="Filter by course ID")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Response:
    """Render complete attendance history for student with status badges and filters."""
    # Enrolled courses for the filter dropdown
    stmt_courses = (
        select(Course)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(Enrollment.student_id == current_user.id)
        .order_by(Course.code.asc(), Course.id.asc())
    )
    enrolled_courses = list((await db.execute(stmt_courses)).scalars().all())

    # Attendance records list with eager-loaded session & course
    records, total = await attendance_repository.list_student_records(
        session=db,
        student_id=current_user.id,
        course_id=course_id,
        page=page,
        size=size,
    )

    total_pages = max(1, (total + size - 1) // size)

    # Summary counts across all (or filtered) records for stat chips
    count_present = sum(1 for r in records if r.status == AttendanceStatus.PRESENT.value)
    count_late = sum(1 for r in records if r.status == AttendanceStatus.LATE.value)
    count_absent = sum(1 for r in records if r.status == AttendanceStatus.ABSENT.value)
    count_excused = sum(1 for r in records if r.status == AttendanceStatus.EXCUSED.value)

    return render_template(
        request=request,
        template_name="student/attendance.html",
        context={
            "active_page": "attendance",
            "enrolled_courses": enrolled_courses,
            "records": records,
            "total": total,
            "page": page,
            "size": size,
            "total_pages": total_pages,
            "selected_course_id": str(course_id) if course_id else "",
            "count_present": count_present,
            "count_late": count_late,
            "count_absent": count_absent,
            "count_excused": count_excused,
        },
    )


# ==============================================================================
# 5. Enrolled Courses (GET /courses)
# ==============================================================================


@router.get("/courses", response_class=HTMLResponse)
async def enrolled_courses_page(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_student)],
) -> Response:
    """List enrolled courses with teacher name, attendance %, and threshold warning."""
    report = await report_service.get_student_report(
        session=db,
        student_id=current_user.id,
        current_user=current_user,
    )
    report_map: dict[UUID, StudentReportItem] = {c.course_id: c for c in report.courses}

    stmt_courses = (
        select(Course)
        .options(selectinload(Course.teacher))
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(Enrollment.student_id == current_user.id)
        .order_by(Course.code.asc(), Course.id.asc())
    )
    courses = list((await db.execute(stmt_courses)).scalars().all())

    # Build combined course view models
    courses_data: list[dict[str, Any]] = []
    for course in courses:
        rep = report_map.get(course.id)
        pct = rep.percentage if rep else None
        is_at_risk = rep.is_at_risk if rep else False
        total_closed = rep.total_sessions if rep else 0
        attended_cnt = rep.attended_sessions if rep else 0

        courses_data.append(
            {
                "course": course,
                "teacher": course.teacher,
                "percentage": pct,
                "is_at_risk": is_at_risk,
                "min_attendance_pct": course.min_attendance_pct,
                "total_closed": total_closed,
                "attended": attended_cnt,
                "present_count": rep.present_count if rep else 0,
                "late_count": rep.late_count if rep else 0,
                "absent_count": rep.absent_count if rep else 0,
                "excused_count": rep.excused_count if rep else 0,
            }
        )

    return render_template(
        request=request,
        template_name="student/courses.html",
        context={
            "active_page": "courses",
            "courses_data": courses_data,
        },
    )


# ==============================================================================
# 6. Profile & Password Change (GET /profile & POST /profile/password)
# ==============================================================================


@router.get("/profile", response_class=HTMLResponse)
async def student_profile(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_student)],
) -> Response:
    """Display student profile information and password change form."""
    stmt_courses = select(func.count(Enrollment.id)).where(Enrollment.student_id == current_user.id)
    courses_count = (await db.execute(stmt_courses)).scalar_one() or 0

    return render_template(
        request=request,
        template_name="student/profile.html",
        context={
            "active_page": "profile",
            "courses_count": courses_count,
        },
    )


@router.post("/profile/password")
async def change_student_password(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_student)],
    csrf_dep: None = Depends(verify_csrf),
    old_password: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    confirm_password: Annotated[str, Form()] = "",
) -> Response:
    """Change student account password."""
    stmt_courses = select(func.count(Enrollment.id)).where(Enrollment.student_id == current_user.id)
    courses_count = (await db.execute(stmt_courses)).scalar_one() or 0

    is_htmx = is_partial_request(request)

    if not old_password or not new_password or not confirm_password:
        err_msg = "All password fields are required."
        headers = {"HX-Trigger": _toast_header(err_msg, "error")} if is_htmx else None
        return render_template(
            request=request,
            template_name="student/profile.html",
            context={
                "active_page": "profile",
                "error": err_msg,
                "courses_count": courses_count,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers=headers,
        )

    if new_password != confirm_password:
        err_msg = "New password and confirmation password do not match."
        headers = {"HX-Trigger": _toast_header(err_msg, "error")} if is_htmx else None
        return render_template(
            request=request,
            template_name="student/profile.html",
            context={
                "active_page": "profile",
                "error": err_msg,
                "courses_count": courses_count,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers=headers,
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
        headers = {"HX-Trigger": _toast_header(exc.message, "error")} if is_htmx else None
        return render_template(
            request=request,
            template_name="student/profile.html",
            context={
                "active_page": "profile",
                "error": exc.message,
                "courses_count": courses_count,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers=headers,
        )

    success_msg = "Your password has been changed successfully."
    headers = {"HX-Trigger": _toast_header(success_msg, "success")} if is_htmx else None
    return render_template(
        request=request,
        template_name="student/profile.html",
        context={
            "active_page": "profile",
            "success": success_msg,
            "courses_count": courses_count,
        },
        headers=headers,
    )
