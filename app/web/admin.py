import csv
import io
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession
from app.core.exceptions import (
    AppError,
    ValidationAppError,
)
from app.models.class_session import ClassSession
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.enums import SessionStatus, UserRole
from app.models.user import User
from app.repositories.course_repository import course_repository
from app.repositories.user_repository import user_repository
from app.schemas.course import CourseCreate
from app.schemas.user import PasswordChange, UserCreate, UserUpdate
from app.services.course_service import course_service
from app.services.enrollment_service import enrollment_service
from app.services.report_service import report_service, sanitize_csv_cell
from app.services.user_service import user_service
from app.web.deps import (
    build_form_model,
    htmx_download_redirect,
    is_partial_request,
    pagination_query,
    render_template,
    require_web_admin,
    verify_csrf,
)

router = APIRouter(
    prefix="/admin",
    tags=["web-admin"],
    dependencies=[Depends(require_web_admin)],
)


def _toast_header(message: str, type_: str = "success") -> str:
    """Helper to produce an HX-Trigger JSON string for toast notifications."""
    return json.dumps({"toast": {"type": type_, "message": message}})


# ==============================================================================
# 1. Admin Dashboard
# ==============================================================================


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
) -> Response:
    """Admin dashboard with institutional metrics, active sessions, and recent activity."""
    # 1. Institutional metrics
    metrics = await report_service.get_dashboard_metrics(db)

    # 2. Currently active live sessions
    stmt_active = (
        select(ClassSession)
        .where(ClassSession.status == SessionStatus.OPEN.value)
        .options(selectinload(ClassSession.course))
        .order_by(ClassSession.starts_at.desc())
        .limit(5)
    )
    res_active = await db.execute(stmt_active)
    active_sessions = list(res_active.scalars().all())

    # 3. Recent courses
    recent_courses, _ = await course_repository.list(
        session=db,
        user=current_user,
        page=1,
        size=5,
    )

    # 4. Recent users
    recent_users, _ = await user_repository.list(
        session=db,
        page=1,
        size=5,
    )

    return render_template(
        request=request,
        template_name="admin/dashboard.html",
        context={
            "active_page": "dashboard",
            "metrics": metrics,
            "active_sessions": active_sessions,
            "recent_courses": recent_courses,
            "recent_users": recent_users,
        },
    )


# ==============================================================================
# 2. User Management
# ==============================================================================


@router.get("/users", response_class=HTMLResponse)
async def list_users(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    q: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    role: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Response:
    """Paginated user list with search and role filter. Supports HTMX partial updates."""
    context = await _users_context(db, page=page, size=size, q=q or search, role=role)
    return render_template(
        request=request,
        template_name="admin/users.html",
        partial_template="admin/partials/user_table.html",
        context=context,
    )


async def _users_context(
    db: DbSession,
    page: int = 1,
    size: int = 20,
    q: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Build the template context for the admin users page (shared by list + error re-renders)."""
    query_text = (q or "").strip() or None
    role_filter: str | None = None
    if role and role.strip() and role.strip().lower() != "all":
        role_filter = role.strip().lower()

    users_page = await user_service.list_users(
        session=db,
        page=page,
        size=size,
        role=role_filter,
        search=query_text,
    )

    return {
        "active_page": "users",
        "users": users_page.items,
        "total_users": users_page.total,
        "current_page": page,
        "page_size": size,
        "total_pages": max(1, (users_page.total + size - 1) // size),
        "q": query_text or "",
        "selected_role": role or "",
        "pagination_query": pagination_query(
            q=query_text, role=role_filter, size=size if size != 20 else None
        ),
    }


async def _render_users_error(request: Request, db: DbSession, error: str) -> Response:
    """Full-page re-render of the users list with an error banner (non-partial requests).

    Restores the search/role filters the admin was viewing, taken from the page URL
    (HX-Current-URL for htmx/boosted requests, else Referer).
    """
    page_url = request.headers.get("HX-Current-URL") or request.headers.get("Referer") or ""
    params = parse_qs(urlsplit(page_url).query)
    q = next(iter(params.get("q") or params.get("search") or []), None)
    role = next(iter(params.get("role") or []), None)
    context = await _users_context(db, q=q, role=role)
    context["error"] = error
    return render_template(
        request=request,
        template_name="admin/users.html",
        context=context,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


@router.post("/users")
async def create_user(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    csrf_dep: None = Depends(verify_csrf),
    email: Annotated[str, Form()] = "",
    full_name: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "student",
    password: Annotated[str, Form()] = "",
    student_id: Annotated[str | None, Form()] = None,
) -> Response:
    """Create a new user account. Supports HTMX form submissions."""
    is_htmx = is_partial_request(request)
    role_val = role.strip().lower()
    clean_email = email.strip().lower()
    clean_name = full_name.strip()
    clean_stu_id = student_id.strip() if student_id and student_id.strip() else None

    try:
        if role_val not in [r.value for r in UserRole]:
            raise ValidationAppError(f"Invalid role: {role_val}")

        if role_val == UserRole.STUDENT.value and not clean_stu_id:
            raise ValidationAppError("Student ID is required for the student role")

        if role_val != UserRole.STUDENT.value:
            clean_stu_id = None

        new_user = await user_service.create_user(
            session=db,
            data=build_form_model(
                UserCreate,
                email=clean_email,
                full_name=clean_name,
                role=UserRole(role_val),
                password=password,
                student_id=clean_stu_id,
            ),
        )
    except AppError as exc:
        if is_htmx:
            return Response(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                headers={"HX-Trigger": _toast_header(exc.message, "danger")},
                content=exc.message,
            )
        # Full page re-render with error
        return await _render_users_error(request, db, exc.message)

    success_msg = f"User '{new_user.full_name}' ({new_user.email}) created successfully."
    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "HX-Redirect": "/admin/users",
                "HX-Trigger": _toast_header(success_msg),
            },
        )
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{id}/status")
async def toggle_user_status(
    request: Request,
    id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    csrf_dep: None = Depends(verify_csrf),
) -> Response:
    """Toggle user active status (soft-delete / reactivation). Cannot deactivate self."""
    is_htmx = is_partial_request(request)
    target_user = await user_service.get_user_by_id(session=db, user_id=id)

    if target_user.id == current_user.id and target_user.is_active:
        err_msg = "You cannot deactivate your own administrator account"
        if is_htmx:
            return Response(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                headers={"HX-Trigger": _toast_header(err_msg, "danger")},
                content=err_msg,
            )
        return await _render_users_error(request, db, err_msg)

    if target_user.is_active:
        await user_service.deactivate_user(session=db, user_id=id, admin_id=current_user.id)
        msg = f"User '{target_user.email}' deactivated."
    else:
        await user_service.update_user(
            session=db,
            user_id=id,
            data=UserUpdate(is_active=True),
            actor_id=current_user.id,
        )
        msg = f"User '{target_user.email}' reactivated."

    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "HX-Redirect": "/admin/users",
                "HX-Trigger": _toast_header(msg),
            },
        )
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/import")
async def import_users(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    file: Annotated[UploadFile, File(...)],
    csrf_dep: None = Depends(verify_csrf),
) -> Response:
    """Bulk import users from an uploaded CSV file."""
    is_htmx = is_partial_request(request)

    try:
        content = await file.read()
        created_users = await user_service.import_users_csv(session=db, file_content=content)
    except AppError as exc:
        err_msg = exc.message
        if hasattr(exc, "details") and exc.details and "errors" in exc.details:
            row_errs = exc.details["errors"]
            if row_errs:
                first_err = row_errs[0]
                row_num = first_err.get("row", "?")
                field = first_err.get("field", "")
                msg = first_err.get("message", first_err.get("error", ""))
                err_msg = f"Row {row_num} error ({field}): {msg}"

        if is_htmx:
            return Response(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                headers={"HX-Trigger": _toast_header(err_msg, "danger")},
                content=err_msg,
            )
        return await _render_users_error(request, db, err_msg)

    msg = f"Successfully imported {len(created_users)} users."
    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "HX-Redirect": "/admin/users",
                "HX-Trigger": _toast_header(msg),
            },
        )
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


# ==============================================================================
# 3. Course Management
# ==============================================================================


async def _courses_context(
    db: DbSession,
    current_user: User,
    page: int = 1,
    size: int = 20,
    query_text: str | None = None,
) -> dict[str, Any]:
    """Build the template context for the admin courses page (list + create modal)."""
    courses, total = await course_repository.list(
        session=db,
        user=current_user,
        page=page,
        size=size,
        search=query_text,
    )

    # Compute enrollment counts per course
    course_ids = [c.id for c in courses]
    counts_map: dict[UUID, int] = {}
    if course_ids:
        count_stmt = (
            select(Enrollment.course_id, func.count(Enrollment.id))
            .where(Enrollment.course_id.in_(course_ids))
            .group_by(Enrollment.course_id)
        )
        res_counts = await db.execute(count_stmt)
        for cid, cnt in res_counts.all():
            counts_map[cid] = cnt

    course_items = [
        {
            "course": c,
            "teacher_name": c.teacher.full_name if c.teacher else "Unassigned",
            "enrolled_count": counts_map.get(c.id, 0),
        }
        for c in courses
    ]

    # Fetch active teachers for create modal dropdown
    teachers, _ = await user_repository.list(
        session=db,
        role=UserRole.TEACHER,
        is_active=True,
        page=1,
        size=100,
    )

    total_pages = max(1, (total + size - 1) // size)

    return {
        "active_page": "courses",
        "courses": courses,
        "course_items": course_items,
        "teachers": teachers,
        "total_courses": total,
        "current_page": page,
        "page_size": size,
        "total_pages": total_pages,
        "search": query_text or "",
        "pagination_query": pagination_query(search=query_text, size=size if size != 20 else None),
    }


@router.get("/courses", response_class=HTMLResponse)
async def list_courses(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    q: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Response:
    """List all courses with teacher name, enrollment count, and min attendance %.

    Supports HTMX partials.
    """
    query_text = (q or search or "").strip() or None
    context = await _courses_context(db, current_user, page, size, query_text)

    return render_template(
        request=request,
        template_name="admin/courses.html",
        partial_template="admin/partials/course_table.html",
        context=context,
    )


@router.post("/courses")
async def create_course(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    csrf_dep: None = Depends(verify_csrf),
    code: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    teacher_id: Annotated[UUID | None, Form()] = None,
    min_attendance_pct: Annotated[float, Form()] = 80.0,
) -> Response:
    """Create a new course with teacher assignment and attendance threshold."""
    is_htmx = is_partial_request(request)

    try:
        if teacher_id is None:
            raise ValidationAppError("Teacher assignment is required")
        new_course = await course_service.create_course(
            session=db,
            data=build_form_model(
                CourseCreate,
                code=code,
                title=title,
                teacher_id=teacher_id,
                min_attendance_pct=min_attendance_pct,
            ),
        )
    except AppError as exc:
        if is_htmx:
            return Response(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                headers={"HX-Trigger": _toast_header(exc.message, "danger")},
                content=exc.message,
            )
        context = await _courses_context(db, current_user)
        context["error"] = exc.message
        return render_template(
            request=request,
            template_name="admin/courses.html",
            context=context,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    msg = f"Course '{new_course.code}' — '{new_course.title}' created successfully."
    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "HX-Redirect": "/admin/courses",
                "HX-Trigger": _toast_header(msg),
            },
        )
    return RedirectResponse(url="/admin/courses", status_code=status.HTTP_303_SEE_OTHER)


# ==============================================================================
# 4. Enrollment Management
# ==============================================================================


@router.get("/enrollments", response_class=HTMLResponse)
async def enrollments_redirect(
    current_user: Annotated[User, Depends(require_web_admin)],
    course_id: Annotated[UUID | None, Query()] = None,
) -> Response:
    """Helper entry point for enrollments from sidebar or direct navigation."""
    if course_id:
        return RedirectResponse(
            url=f"/admin/courses/{course_id}/enrollments",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(url="/admin/courses", status_code=status.HTTP_303_SEE_OTHER)


async def _enrollments_context(
    db: DbSession,
    course_id: UUID,
    current_user: User,
    page: int = 1,
    size: int = 50,
) -> dict[str, Any]:
    """Build the template context for a course's enrollment management page."""
    course = await course_service.get_course(
        session=db, course_id=course_id, current_user=current_user
    )

    # Enrolled students with pagination
    students_page = await enrollment_service.list_course_students(
        session=db,
        course_id=course_id,
        current_user=current_user,
        page=page,
        size=size,
    )

    # Query active students who are not yet enrolled for quick dropdown selection
    enrolled_ids_stmt = select(Enrollment.student_id).where(Enrollment.course_id == course_id)
    enrolled_ids_res = await db.execute(enrolled_ids_stmt)
    enrolled_ids = set(enrolled_ids_res.scalars().all())

    avail_stmt = (
        select(User)
        .where(
            User.role == UserRole.STUDENT.value,
            User.is_active.is_(True),
        )
        .order_by(User.full_name.asc())
        .limit(200)
    )
    avail_res = await db.execute(avail_stmt)
    all_students = list(avail_res.scalars().all())
    available_students = [s for s in all_students if s.id not in enrolled_ids]

    total_pages = max(1, (students_page.total + size - 1) // size)

    return {
        "active_page": "enrollments",
        "course": course,
        "students": students_page.items,
        "total_students": students_page.total,
        "current_page": page,
        "total_pages": total_pages,
        "available_students": available_students,
    }


@router.get("/courses/{id}/enrollments", response_class=HTMLResponse)
async def view_course_enrollments(
    request: Request,
    id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Response:
    """View and manage student enrollments for a specific course."""
    context = await _enrollments_context(db, id, current_user, page, size)
    return render_template(
        request=request,
        template_name="admin/enrollments.html",
        context=context,
    )


@router.post("/courses/{id}/enrollments")
async def enroll_students(
    request: Request,
    id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    csrf_dep: None = Depends(verify_csrf),
    student_ids: Annotated[str | None, Form()] = None,
    student_id: Annotated[str | None, Form()] = None,
) -> Response:
    """Enroll students into a course by single or comma-separated UUIDs, emails, or student IDs."""
    is_htmx = is_partial_request(request)
    raw_input = (student_ids or student_id or "").strip()
    tokens = [t.strip() for t in re.split(r"[,\n\r]+", raw_input) if t.strip()]
    parsed_ids: list[UUID] = []

    try:
        if not tokens:
            raise ValidationAppError(
                "Please provide at least one student ID, email, or student code."
            )
        for token in tokens:
            # 1. Check if token is a valid UUID
            try:
                parsed_ids.append(UUID(token))
                continue
            except ValueError:
                pass

            # 2. Check if token matches a student's email or student_id code
            token_lower = token.lower()
            user_by_email = await user_repository.get_by_email(db, token_lower)
            if user_by_email:
                parsed_ids.append(user_by_email.id)
                continue

            user_by_stu = await user_repository.get_by_student_id(db, token)
            if user_by_stu:
                parsed_ids.append(user_by_stu.id)
                continue

            raise ValidationAppError(f"Student not found matching: '{token}'")

        result = await enrollment_service.enroll_students(
            session=db,
            course_id=id,
            student_ids=parsed_ids,
            current_user=current_user,
        )
    except AppError as exc:
        if is_htmx:
            return Response(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                headers={"HX-Trigger": _toast_header(exc.message, "danger")},
                content=exc.message,
            )
        context = await _enrollments_context(db, id, current_user)
        context["error"] = exc.message
        return render_template(
            request=request,
            template_name="admin/enrollments.html",
            context=context,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    msg = f"Enrolled {len(result.enrolled)} student(s)."
    if result.skipped:
        msg += f" ({len(result.skipped)} already enrolled)"

    target_url = f"/admin/courses/{id}/enrollments"
    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "HX-Redirect": target_url,
                "HX-Trigger": _toast_header(msg),
            },
        )
    return RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/courses/{id}/enrollments/{student_id}/delete")
async def unenroll_student(
    request: Request,
    id: UUID,
    student_id: UUID,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    csrf_dep: None = Depends(verify_csrf),
) -> Response:
    """Unenroll a student from a course."""
    is_htmx = is_partial_request(request)
    await enrollment_service.unenroll(
        session=db,
        course_id=id,
        student_id=student_id,
        current_user=current_user,
    )
    msg = "Student unenrolled successfully."
    target_url = f"/admin/courses/{id}/enrollments"
    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "HX-Redirect": target_url,
                "HX-Trigger": _toast_header(msg),
            },
        )
    return RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)


# ==============================================================================
# 5. Session Management
# ==============================================================================


@router.get("/sessions", response_class=HTMLResponse)
async def list_sessions(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    course_id: Annotated[UUID | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Response:
    """List all sessions across courses with status and course filter."""
    courses, _ = await course_repository.list(session=db, user=current_user, page=1, size=100)

    conditions = []
    if course_id:
        conditions.append(ClassSession.course_id == course_id)
    if status_filter and status_filter.strip() and status_filter.strip().lower() != "all":
        conditions.append(ClassSession.status == status_filter.strip().lower())

    count_stmt = select(func.count(ClassSession.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one()

    offset = max(0, (page - 1) * size)
    stmt = (
        select(ClassSession)
        .options(selectinload(ClassSession.course))
        .order_by(ClassSession.starts_at.desc(), ClassSession.id.desc())
        .offset(offset)
        .limit(size)
    )
    if conditions:
        stmt = stmt.where(*conditions)

    res = await db.execute(stmt)
    sessions = list(res.scalars().all())

    total_pages = max(1, (total + size - 1) // size)

    return render_template(
        request=request,
        template_name="admin/sessions.html",
        context={
            "active_page": "sessions",
            "sessions": sessions,
            "courses": courses,
            "selected_course_id": str(course_id) if course_id else "",
            "current_status": status_filter or "",
            "total_sessions": total,
            "current_page": page,
            "page_size": size,
            "total_pages": total_pages,
            "pagination_query": pagination_query(
                course_id=course_id,
                status=status_filter.strip() if status_filter else None,
                size=size if size != 20 else None,
            ),
        },
    )


# ==============================================================================
# 6. Reports & Analytics
# ==============================================================================


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    course_id: Annotated[UUID | None, Query()] = None,
    export: Annotated[str | None, Query()] = None,
) -> Response:
    """Institutional attendance report, at-risk student list, and CSV export."""
    # 1. System-wide metrics
    metrics = await report_service.get_dashboard_metrics(db)

    # 2. Courses list for filtering
    courses, _ = await course_repository.list(session=db, user=current_user, page=1, size=100)

    # 3. Collect at-risk students
    at_risk_list: list[dict[str, Any]] = []
    courses_to_inspect = (
        [c for c in courses if c.id == course_id]
        if course_id
        else [c for c in courses if c.is_active]
    )

    for c in courses_to_inspect:
        c_report = await report_service.get_course_report(
            session=db,
            course_id=c.id,
            current_user=current_user,
        )
        for s in c_report.students:
            if s.is_at_risk:
                at_risk_list.append(
                    {
                        "course": c,
                        "student": s,
                    }
                )

    # CSV Export if requested
    if export == "csv":
        return _generate_reports_csv(at_risk_list, course_id=course_id)

    return render_template(
        request=request,
        template_name="admin/reports.html",
        context={
            "active_page": "reports",
            "metrics": metrics,
            "courses": courses,
            "selected_course_id": str(course_id) if course_id else "",
            "at_risk_students": at_risk_list,
        },
    )


@router.get("/reports/csv")
async def export_reports_csv(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    course_id: Annotated[UUID | None, Query()] = None,
) -> Response:
    """Dedicated endpoint to download CSV attendance report for at-risk students."""
    if (redirect := htmx_download_redirect(request)) is not None:
        return redirect
    courses, _ = await course_repository.list(session=db, user=current_user, page=1, size=100)
    at_risk_list: list[dict[str, Any]] = []
    courses_to_inspect = (
        [c for c in courses if c.id == course_id]
        if course_id
        else [c for c in courses if c.is_active]
    )

    for c in courses_to_inspect:
        c_report = await report_service.get_course_report(
            session=db,
            course_id=c.id,
            current_user=current_user,
        )
        for s in c_report.students:
            if s.is_at_risk:
                at_risk_list.append(
                    {
                        "course": c,
                        "student": s,
                    }
                )

    return _generate_reports_csv(at_risk_list, course_id=course_id)


def _generate_reports_csv(
    at_risk_list: list[dict[str, Any]], course_id: UUID | None = None
) -> Response:
    """Build CSV response for institutional or course at-risk report."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "student_id",
            "student_name",
            "student_email",
            "student_code",
            "course_code",
            "course_title",
            "attendance_pct",
            "min_attendance_pct",
            "present_count",
            "late_count",
            "absent_count",
            "excused_count",
            "total_sessions",
            "is_at_risk",
        ]
    )

    for item in at_risk_list:
        c: Course = item["course"]
        s = item["student"]
        writer.writerow(
            [
                sanitize_csv_cell(s.student_id),
                sanitize_csv_cell(s.student_name),
                sanitize_csv_cell(s.student_email),
                sanitize_csv_cell(s.student_code or ""),
                sanitize_csv_cell(c.code),
                sanitize_csv_cell(c.title),
                sanitize_csv_cell(f"{s.percentage:.2f}" if s.percentage is not None else ""),
                sanitize_csv_cell(c.min_attendance_pct),
                sanitize_csv_cell(s.present_count),
                sanitize_csv_cell(s.late_count),
                sanitize_csv_cell(s.absent_count),
                sanitize_csv_cell(s.excused_count),
                sanitize_csv_cell(s.total_sessions),
                sanitize_csv_cell(s.is_at_risk),
            ]
        )

    date_str = datetime.now(UTC).strftime("%Y%m%d")
    filename = (
        f"at_risk_report_{course_id}_{date_str}.csv"
        if course_id
        else f"institutional_at_risk_report_{date_str}.csv"
    )

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ==============================================================================
# 7. Admin Profile & Password Management
# ==============================================================================


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
) -> Response:
    """Admin profile view with account information and security settings."""
    metrics = await report_service.get_dashboard_metrics(db)

    return render_template(
        request=request,
        template_name="admin/profile.html",
        context={
            "active_page": "profile",
            "user": current_user,
            "metrics": metrics,
        },
    )


@router.post("/profile/password")
async def change_password(
    request: Request,
    db: DbSession,
    current_user: Annotated[User, Depends(require_web_admin)],
    csrf_dep: None = Depends(verify_csrf),
    old_password: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    confirm_password: Annotated[str | None, Form()] = None,
) -> Response:
    """Change administrator password."""
    is_htmx = is_partial_request(request)

    try:
        if confirm_password is not None and confirm_password != new_password:
            raise ValidationAppError("New passwords do not match")

        await user_service.change_password(
            session=db,
            user_id=current_user.id,
            data=build_form_model(
                PasswordChange, old_password=old_password, new_password=new_password
            ),
        )
    except AppError as exc:
        if is_htmx:
            return Response(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                headers={"HX-Trigger": _toast_header(exc.message, "danger")},
                content=exc.message,
            )
        metrics = await report_service.get_dashboard_metrics(db)
        return render_template(
            request=request,
            template_name="admin/profile.html",
            context={
                "active_page": "profile",
                "user": current_user,
                "error": exc.message,
                "metrics": metrics,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    msg = "Password changed successfully."
    if is_htmx:
        return Response(
            status_code=status.HTTP_200_OK,
            headers={"HX-Trigger": _toast_header(msg)},
        )
    # Full page (plain or boosted form POST): re-render with a success banner, matching
    # the teacher/student profile flows. Boosted requests also get the toast.
    metrics = await report_service.get_dashboard_metrics(db)
    return render_template(
        request=request,
        template_name="admin/profile.html",
        context={
            "active_page": "profile",
            "user": current_user,
            "success": msg,
            "metrics": metrics,
        },
        headers={"HX-Trigger": _toast_header(msg)}
        if request.headers.get("HX-Request") == "true"
        else None,
    )
