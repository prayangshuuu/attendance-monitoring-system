from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attendance_record import AttendanceRecord
from app.models.class_session import ClassSession
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.enums import AttendanceStatus, SessionStatus, UserRole
from app.models.user import User
from app.schemas.report import (
    CourseReport,
    DashboardSummary,
    SessionSummary,
    StudentCourseSummary,
    StudentFullReport,
    StudentReportItem,
)
from app.utils.clock import Clock


class ReportRepository:
    @staticmethod
    async def get_course_report(
        session: AsyncSession,
        course: Course,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
    ) -> CourseReport:
        session_conditions = [
            ClassSession.course_id == course.id,
            ClassSession.status == SessionStatus.CLOSED.value,
        ]
        if from_dt is not None:
            session_conditions.append(ClassSession.starts_at >= from_dt)
        if to_dt is not None:
            session_conditions.append(ClassSession.starts_at <= to_dt)

        count_stmt = select(func.count(ClassSession.id)).where(and_(*session_conditions))
        total_closed_sessions = (await session.execute(count_stmt)).scalar_one()

        closed_session_ids_stmt = select(ClassSession.id).where(and_(*session_conditions))

        stmt = (
            select(
                User.id.label("student_id"),
                User.full_name.label("student_name"),
                User.email.label("student_email"),
                User.student_id.label("student_code"),
                func.count(
                    case((AttendanceRecord.status == AttendanceStatus.PRESENT.value, 1))
                ).label("present_count"),
                func.count(case((AttendanceRecord.status == AttendanceStatus.LATE.value, 1))).label(
                    "late_count"
                ),
                func.count(
                    case((AttendanceRecord.status == AttendanceStatus.ABSENT.value, 1))
                ).label("absent_count"),
                func.count(
                    case((AttendanceRecord.status == AttendanceStatus.EXCUSED.value, 1))
                ).label("excused_count"),
            )
            .select_from(Enrollment)
            .join(User, Enrollment.student_id == User.id)
            .outerjoin(
                AttendanceRecord,
                and_(
                    AttendanceRecord.student_id == User.id,
                    AttendanceRecord.session_id.in_(closed_session_ids_stmt),
                ),
            )
            .where(Enrollment.course_id == course.id)
            .group_by(User.id, User.full_name, User.email, User.student_id)
            .order_by(User.full_name.asc(), User.id.asc())
        )
        rows = (await session.execute(stmt)).all()

        students: list[StudentCourseSummary] = []
        for row in rows:
            present = row.present_count
            late = row.late_count
            excused = row.excused_count
            rec_absent = row.absent_count
            attended = present + late + excused
            total_sessions = total_closed_sessions
            absent = max(rec_absent, total_sessions - attended)

            if total_sessions == 0:
                percentage = None
                is_at_risk = False
            else:
                percentage = round((attended / total_sessions) * 100, 2)
                is_at_risk = percentage < course.min_attendance_pct

            students.append(
                StudentCourseSummary(
                    student_id=row.student_id,
                    student_name=row.student_name,
                    student_email=row.student_email,
                    student_code=row.student_code,
                    present_count=present,
                    late_count=late,
                    absent_count=absent,
                    excused_count=excused,
                    total_sessions=total_sessions,
                    attended_sessions=attended,
                    percentage=percentage,
                    is_at_risk=is_at_risk,
                )
            )

        valid_percentages = [s.percentage for s in students if s.percentage is not None]
        average_percentage = (
            round(sum(valid_percentages) / len(valid_percentages), 2) if valid_percentages else None
        )

        return CourseReport(
            course_id=course.id,
            course_code=course.code,
            course_title=course.title,
            min_attendance_pct=course.min_attendance_pct,
            total_closed_sessions=total_closed_sessions,
            enrolled_students_count=len(students),
            average_percentage=average_percentage,
            average_pct=average_percentage,
            students=students,
        )

    @staticmethod
    async def get_session_summary(
        session: AsyncSession,
        class_session: ClassSession,
    ) -> SessionSummary:
        stmt = select(
            func.count(case((AttendanceRecord.status == AttendanceStatus.PRESENT.value, 1))).label(
                "present"
            ),
            func.count(case((AttendanceRecord.status == AttendanceStatus.LATE.value, 1))).label(
                "late"
            ),
            func.count(case((AttendanceRecord.status == AttendanceStatus.ABSENT.value, 1))).label(
                "absent"
            ),
            func.count(case((AttendanceRecord.status == AttendanceStatus.EXCUSED.value, 1))).label(
                "excused"
            ),
            func.count(AttendanceRecord.id).label("total"),
        ).where(AttendanceRecord.session_id == class_session.id)

        res = await session.execute(stmt)
        row = res.one()
        present_count = row.present or 0
        late_count = row.late or 0
        absent_count = row.absent or 0
        excused_count = row.excused or 0
        total_records = row.total or 0

        attendance_rate: float | None = None
        if total_records > 0:
            attended = present_count + late_count + excused_count
            attendance_rate = round((attended / total_records) * 100, 2)

        return SessionSummary(
            session_id=class_session.id,
            course_id=class_session.course_id,
            starts_at=class_session.starts_at,
            ends_at=class_session.ends_at,
            status=SessionStatus(class_session.status),
            present_count=present_count,
            late_count=late_count,
            absent_count=absent_count,
            excused_count=excused_count,
            total_records=total_records,
            attendance_rate=attendance_rate,
        )

    @staticmethod
    async def get_student_report(
        session: AsyncSession,
        student: User,
        teacher_id: UUID | None = None,
    ) -> StudentFullReport | None:
        stmt = (
            select(Course)
            .join(Enrollment, Enrollment.course_id == Course.id)
            .where(Enrollment.student_id == student.id)
        )
        if teacher_id is not None:
            stmt = stmt.where(Course.teacher_id == teacher_id)
        stmt = stmt.order_by(Course.code.asc(), Course.id.asc())
        courses = list((await session.execute(stmt)).scalars().all())

        if teacher_id is not None and not courses:
            return None

        if not courses:
            return StudentFullReport(
                student_id=student.id,
                student_name=student.full_name,
                student_email=student.email,
                courses=[],
            )

        course_ids = [c.id for c in courses]

        stmt_sessions = (
            select(ClassSession.course_id, func.count(ClassSession.id))
            .where(
                ClassSession.course_id.in_(course_ids),
                ClassSession.status == SessionStatus.CLOSED.value,
            )
            .group_by(ClassSession.course_id)
        )
        session_counts = dict((await session.execute(stmt_sessions)).all())

        stmt_records = (
            select(
                ClassSession.course_id,
                func.count(
                    case((AttendanceRecord.status == AttendanceStatus.PRESENT.value, 1))
                ).label("present"),
                func.count(case((AttendanceRecord.status == AttendanceStatus.LATE.value, 1))).label(
                    "late"
                ),
                func.count(
                    case((AttendanceRecord.status == AttendanceStatus.ABSENT.value, 1))
                ).label("absent"),
                func.count(
                    case((AttendanceRecord.status == AttendanceStatus.EXCUSED.value, 1))
                ).label("excused"),
            )
            .select_from(AttendanceRecord)
            .join(ClassSession, AttendanceRecord.session_id == ClassSession.id)
            .where(
                AttendanceRecord.student_id == student.id,
                ClassSession.course_id.in_(course_ids),
                ClassSession.status == SessionStatus.CLOSED.value,
            )
            .group_by(ClassSession.course_id)
        )
        records_res = (await session.execute(stmt_records)).all()
        records_by_course = {
            row.course_id: {
                "present": row.present,
                "late": row.late,
                "absent": row.absent,
                "excused": row.excused,
            }
            for row in records_res
        }

        items: list[StudentReportItem] = []
        for course in courses:
            total_sessions = session_counts.get(course.id, 0)
            rec_counts = records_by_course.get(
                course.id, {"present": 0, "late": 0, "absent": 0, "excused": 0}
            )
            present_cnt = rec_counts["present"]
            late_cnt = rec_counts["late"]
            excused_cnt = rec_counts["excused"]
            rec_absent_cnt = rec_counts["absent"]
            attended = present_cnt + late_cnt + excused_cnt
            absent_cnt = max(rec_absent_cnt, total_sessions - attended)

            if total_sessions == 0:
                percentage = None
                is_at_risk = False
            else:
                percentage = round((attended / total_sessions) * 100, 2)
                is_at_risk = percentage < course.min_attendance_pct

            items.append(
                StudentReportItem(
                    course_id=course.id,
                    course_code=course.code,
                    course_title=course.title,
                    min_attendance_pct=course.min_attendance_pct,
                    present_count=present_cnt,
                    late_count=late_cnt,
                    absent_count=absent_cnt,
                    excused_count=excused_cnt,
                    total_sessions=total_sessions,
                    attended_sessions=attended,
                    percentage=percentage,
                    is_at_risk=is_at_risk,
                )
            )

        return StudentFullReport(
            student_id=student.id,
            student_name=student.full_name,
            student_email=student.email,
            courses=items,
        )

    @staticmethod
    async def get_dashboard_summary(
        session: AsyncSession,
        clock: Clock,
    ) -> DashboardSummary:
        role_counts = await session.execute(
            select(User.role, func.count(User.id)).group_by(User.role)
        )
        users_by_role = {role.value: 0 for role in UserRole}
        for role_val, count in role_counts:
            users_by_role[role_val] = count
        total_users = sum(users_by_role.values())

        active_courses_res = await session.execute(
            select(func.count(Course.id)).where(Course.is_active.is_(True))
        )
        active_courses = active_courses_res.scalar_one()

        now = clock.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)

        sessions_today_res = await session.execute(
            select(func.count(ClassSession.id)).where(
                ClassSession.starts_at >= today_start,
                ClassSession.starts_at < tomorrow_start,
                ClassSession.status != SessionStatus.CANCELLED.value,
            )
        )
        sessions_today = sessions_today_res.scalar_one()

        closed_sessions_today = select(ClassSession.id).where(
            ClassSession.starts_at >= today_start,
            ClassSession.starts_at < tomorrow_start,
            ClassSession.status == SessionStatus.CLOSED.value,
        )

        rate_stmt = select(
            func.count(
                case(
                    (
                        AttendanceRecord.status.in_(
                            [
                                AttendanceStatus.PRESENT.value,
                                AttendanceStatus.LATE.value,
                                AttendanceStatus.EXCUSED.value,
                            ]
                        ),
                        1,
                    )
                )
            ).label("attended"),
            func.count(AttendanceRecord.id).label("total"),
        ).where(AttendanceRecord.session_id.in_(closed_sessions_today))
        rate_res = await session.execute(rate_stmt)
        rate_row = rate_res.one()
        attended_records = rate_row.attended or 0
        total_records = rate_row.total or 0

        attendance_rate_today: float | None = None
        if total_records > 0:
            attendance_rate_today = round((attended_records / total_records) * 100, 2)

        return DashboardSummary(
            total_users=total_users,
            users_by_role=users_by_role,
            active_courses=active_courses,
            sessions_today=sessions_today,
            attendance_rate_today=attendance_rate_today,
        )


report_repository = ReportRepository()
