from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationAppError
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.course_repository import CourseRepository, course_repository
from app.repositories.enrollment_repository import EnrollmentRepository, enrollment_repository
from app.repositories.report_repository import ReportRepository, report_repository
from app.repositories.session_repository import SessionRepository, session_repository
from app.repositories.user_repository import UserRepository, user_repository
from app.schemas.report import (
    AtRiskStudent,
    CourseReport,
    DashboardSummary,
    SessionSummary,
    StudentFullReport,
)
from app.services.permissions import ensure_can_manage_course
from app.utils.clock import Clock


def normalize_datetime(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class ReportService:
    def __init__(
        self,
        report_repo: ReportRepository | None = None,
        course_repo: CourseRepository | None = None,
        session_repo: SessionRepository | None = None,
        user_repo: UserRepository | None = None,
        enrollment_repo: EnrollmentRepository | None = None,
    ) -> None:
        self.report_repo = report_repo or report_repository
        self.course_repo = course_repo or course_repository
        self.session_repo = session_repo or session_repository
        self.user_repo = user_repo or user_repository
        self.enrollment_repo = enrollment_repo or enrollment_repository

    async def get_course_report(
        self,
        session: AsyncSession,
        course_id: UUID,
        current_user: User,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
    ) -> CourseReport:
        course = await self.course_repo.get_by_id(session, course_id)
        if course is None:
            raise NotFoundError("Course not found", code="COURSE_NOT_FOUND")

        ensure_can_manage_course(current_user, course)

        norm_from = normalize_datetime(from_dt)
        norm_to = normalize_datetime(to_dt)

        if norm_from is not None and norm_to is not None and norm_to < norm_from:
            raise ValidationAppError(
                "to_dt must be greater than or equal to from_dt",
                code="VALIDATION_ERROR",
            )

        return await self.report_repo.get_course_report(
            session=session,
            course=course,
            from_dt=norm_from,
            to_dt=norm_to,
        )

    async def get_course_at_risk(
        self,
        session: AsyncSession,
        course_id: UUID,
        current_user: User,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
    ) -> list[AtRiskStudent]:
        report = await self.get_course_report(
            session=session,
            course_id=course_id,
            current_user=current_user,
            from_dt=from_dt,
            to_dt=to_dt,
        )

        at_risk_students = [
            AtRiskStudent(
                student_id=s.student_id,
                student_name=s.student_name,
                student_email=s.student_email,
                student_code=s.student_code,
                percentage=s.percentage,
                min_attendance_pct=report.min_attendance_pct,
            )
            for s in report.students
            if s.is_at_risk and s.percentage is not None
        ]
        at_risk_students.sort(key=lambda s: (s.percentage, s.student_name))
        return at_risk_students

    async def get_session_summary(
        self,
        session: AsyncSession,
        session_id: UUID,
        current_user: User,
    ) -> SessionSummary:
        class_session = await self.session_repo.get_by_id(session, session_id)
        if class_session is None:
            raise NotFoundError("Session not found", code="SESSION_NOT_FOUND")

        course = await self.course_repo.get_by_id(session, class_session.course_id)
        if course is None:
            raise NotFoundError("Course not found", code="COURSE_NOT_FOUND")

        ensure_can_manage_course(current_user, course)

        return await self.report_repo.get_session_summary(session, class_session)

    async def get_student_report(
        self,
        session: AsyncSession,
        student_id: UUID,
        current_user: User,
    ) -> StudentFullReport:
        student = await self.user_repo.get_by_id(session, student_id)
        if student is None:
            raise NotFoundError("Student not found", code="STUDENT_NOT_FOUND")

        is_admin = current_user.role == UserRole.ADMIN
        is_self = current_user.id == student.id
        is_teacher = current_user.role == UserRole.TEACHER

        if not (is_admin or is_self or is_teacher):
            raise PermissionDeniedError(
                "You do not have permission to view this student report",
                code="PERMISSION_DENIED",
            )

        teacher_id = current_user.id if is_teacher and not is_admin else None

        report = await self.report_repo.get_student_report(
            session=session,
            student=student,
            teacher_id=teacher_id,
        )

        if report is None:
            raise PermissionDeniedError(
                "You do not teach this student",
                code="PERMISSION_DENIED",
            )

        return report

    async def get_dashboard(
        self,
        session: AsyncSession,
        current_user: User,
        clock: Clock,
    ) -> DashboardSummary:
        if current_user.role != UserRole.ADMIN:
            raise PermissionDeniedError(
                "Only administrators can view the dashboard summary",
                code="PERMISSION_DENIED",
            )

        return await self.report_repo.get_dashboard_summary(session, clock)


report_service = ReportService()
