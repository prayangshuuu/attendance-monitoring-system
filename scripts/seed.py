import asyncio
import logging
import random
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, TypedDict

# Ensure project root is on sys.path when executed directly
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import async_session_maker, engine  # noqa: E402
from app.models.attendance_record import AttendanceRecord  # noqa: E402
from app.models.class_session import ClassSession  # noqa: E402
from app.models.course import Course  # noqa: E402
from app.models.enrollment import Enrollment  # noqa: E402
from app.models.enums import AttendanceMethod, AttendanceStatus, SessionStatus, UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.session import ensure_utc_datetime  # noqa: E402

logger = logging.getLogger(__name__)

# Constants for demo credentials
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "ChangeMe123!"

TEACHER_CREDENTIALS = [
    {"email": "teacher1@example.com", "full_name": "Prof. Alan Turing", "password": "Teacher123!"},
    {"email": "teacher2@example.com", "full_name": "Prof. Grace Hopper", "password": "Teacher123!"},
    {"email": "teacher3@example.com", "full_name": "Prof. Donald Knuth", "password": "Teacher123!"},
]

STUDENT_PASSWORD = "Student123!"


class CourseSeedData(TypedDict):
    code: str
    title: str
    teacher_email: str
    min_attendance_pct: float
    schedule_days: list[int]
    schedule_time: time


COURSES_DATA: list[CourseSeedData] = [
    {
        "code": "CS101",
        "title": "Introduction to Computer Science",
        "teacher_email": "teacher1@example.com",
        "min_attendance_pct": 80.0,
        "schedule_days": [0, 2],  # Monday, Wednesday
        "schedule_time": time(9, 0),
    },
    {
        "code": "CS102",
        "title": "Data Structures and Algorithms",
        "teacher_email": "teacher2@example.com",
        "min_attendance_pct": 80.0,
        "schedule_days": [1, 3],  # Tuesday, Thursday
        "schedule_time": time(10, 0),
    },
    {
        "code": "CS201",
        "title": "Database Systems",
        "teacher_email": "teacher1@example.com",
        "min_attendance_pct": 80.0,
        "schedule_days": [0, 2],  # Monday, Wednesday
        "schedule_time": time(14, 0),
    },
    {
        "code": "CS202",
        "title": "Operating Systems",
        "teacher_email": "teacher2@example.com",
        "min_attendance_pct": 80.0,
        "schedule_days": [1, 3],  # Tuesday, Thursday
        "schedule_time": time(14, 0),
    },
    {
        "code": "CS301",
        "title": "Distributed Systems",
        "teacher_email": "teacher3@example.com",
        "min_attendance_pct": 80.0,
        "schedule_days": [2, 4],  # Wednesday, Friday
        "schedule_time": time(16, 0),
    },
]

# Student index slices for course enrollments (ensures 20-30 students per course)
ENROLLMENT_SLICES = {
    "CS101": list(range(0, 25)),  # 25 students (student1 to student25)
    "CS102": list(range(5, 32)),  # 27 students (student6 to student32)
    "CS201": list(range(15, 40)),  # 25 students (student16 to student40)
    "CS202": list(range(0, 28)),  # 28 students (student1 to student28)
    "CS301": list(range(10, 36)),  # 26 students (student11 to student36)
}

OPEN_SESSION_COURSE_CODE = "CS101"
OPEN_SESSION_CHECKIN_CODE = "DEMO01"


async def seed_data(session: AsyncSession) -> dict[str, Any]:
    """Idempotently seeds demo data for the attendance monitoring system.

    Returns a summary dictionary of entity counts.
    """
    logger.info("Starting database seed...")
    rng = random.Random(42)  # Deterministic seed for reproducible attendance

    # 1. Seed or retrieve Admin user
    admin_user = await session.scalar(select(User).where(User.email == ADMIN_EMAIL.lower().strip()))
    if admin_user is None:
        admin_user = User(
            email=ADMIN_EMAIL.lower().strip(),
            full_name="System Administrator",
            hashed_password=hash_password(ADMIN_PASSWORD),
            role=UserRole.ADMIN.value,
            student_id=None,
            is_active=True,
        )
        session.add(admin_user)
        await session.flush()
        logger.info("Created admin user: %s", admin_user.email)

    # 2. Seed or retrieve Teachers
    teachers: dict[str, User] = {}
    for t_data in TEACHER_CREDENTIALS:
        t_email = t_data["email"].lower().strip()
        teacher = await session.scalar(select(User).where(User.email == t_email))
        if teacher is None:
            teacher = User(
                email=t_email,
                full_name=t_data["full_name"],
                hashed_password=hash_password(t_data["password"]),
                role=UserRole.TEACHER.value,
                student_id=None,
                is_active=True,
            )
            session.add(teacher)
            await session.flush()
            logger.info("Created teacher: %s (%s)", teacher.full_name, teacher.email)
        teachers[t_email] = teacher

    # 3. Seed or retrieve 40 Students (STU1001 .. STU1040)
    students: list[User] = []
    for i in range(1, 41):
        s_email = f"student{i}@example.com".lower()
        s_id = f"STU{1000 + i}"
        student = await session.scalar(select(User).where(User.email == s_email))
        if student is None:
            student = User(
                email=s_email,
                full_name=f"Student {i:02d}",
                hashed_password=hash_password(STUDENT_PASSWORD),
                role=UserRole.STUDENT.value,
                student_id=s_id,
                is_active=True,
            )
            session.add(student)
            await session.flush()
        students.append(student)

    logger.info("Ensured 40 students STU1001..STU1040")

    # 4. Seed or retrieve 5 Courses
    courses: dict[str, Course] = {}
    for c_data in COURSES_DATA:
        code = c_data["code"]
        teacher = teachers[c_data["teacher_email"]]
        course = await session.scalar(select(Course).where(Course.code == code))
        if course is None:
            course = Course(
                code=code,
                title=c_data["title"],
                teacher_id=teacher.id,
                min_attendance_pct=c_data["min_attendance_pct"],
                is_active=True,
            )
            session.add(course)
            await session.flush()
            logger.info("Created course: %s - %s", course.code, course.title)
        courses[code] = course

    # 5. Enroll students across courses
    for code, student_indices in ENROLLMENT_SLICES.items():
        course = courses[code]
        for idx in student_indices:
            stu = students[idx]
            existing_enrollment = await session.scalar(
                select(Enrollment).where(
                    Enrollment.course_id == course.id,
                    Enrollment.student_id == stu.id,
                )
            )
            if existing_enrollment is None:
                enrollment = Enrollment(
                    course_id=course.id,
                    student_id=stu.id,
                )
                session.add(enrollment)
        await session.flush()

    logger.info("Ensured student enrollments across 5 courses")

    # 6. Generate 6 weeks of past closed sessions with realistic randomized attendance
    now = datetime.now(UTC)
    current_week_monday = now.date() - timedelta(days=now.weekday())

    for c_data in COURSES_DATA:
        code = c_data["code"]
        course = courses[code]

        # Check existing closed sessions count
        existing_closed_count = (
            await session.execute(
                select(func.count(ClassSession.id)).where(
                    ClassSession.course_id == course.id,
                    ClassSession.status == SessionStatus.CLOSED.value,
                )
            )
        ).scalar_one()

        # Enrolled student IDs for this course (sorted for deterministic iteration)
        enrolled_ids_result = await session.scalars(
            select(Enrollment.student_id)
            .where(Enrollment.course_id == course.id)
            .order_by(Enrollment.student_id)
        )
        enrolled_student_ids = list(enrolled_ids_result.all())

        # If course already has at least 12 closed sessions, closed sessions are fully seeded
        if existing_closed_count < 12:
            schedule_days = c_data["schedule_days"]
            session_time = c_data["schedule_time"]

            # 6 weeks past: w = 6 down to 1
            for w in range(6, 0, -1):
                week_monday = current_week_monday - timedelta(weeks=w)
                for day_offset in schedule_days:
                    session_date = week_monday + timedelta(days=day_offset)
                    starts_at = datetime.combine(session_date, session_time, tzinfo=UTC)
                    ends_at = starts_at + timedelta(hours=1, minutes=30)

                    # Check if this exact session already exists
                    existing_session = await session.scalar(
                        select(ClassSession).where(
                            ClassSession.course_id == course.id,
                            ClassSession.starts_at == starts_at,
                        )
                    )
                    if existing_session is None:
                        session_obj = ClassSession(
                            course_id=course.id,
                            starts_at=starts_at,
                            ends_at=ends_at,
                            status=SessionStatus.CLOSED.value,
                            checkin_code=None,
                            code_expires_at=None,
                            late_after_minutes=10,
                        )
                        session.add(session_obj)
                        await session.flush()
                    else:
                        session_obj = existing_session

                    # Deterministic attendance generation for each enrolled student
                    for stu_id in enrolled_student_ids:
                        # Advance RNG deterministically
                        val = rng.random()
                        if val < 0.78:
                            # 78% Present via Code
                            status_val = AttendanceStatus.PRESENT.value
                            method_val = AttendanceMethod.CODE.value
                            delay_sec = rng.randint(30, 8 * 60)
                            checked_in_at = session_obj.starts_at + timedelta(seconds=delay_sec)
                            marked_by = None
                            note = None
                        elif val < 0.88:
                            # 10% Late via Code
                            status_val = AttendanceStatus.LATE.value
                            method_val = AttendanceMethod.CODE.value
                            delay_sec = rng.randint(11 * 60, 25 * 60)
                            checked_in_at = session_obj.starts_at + timedelta(seconds=delay_sec)
                            marked_by = None
                            note = "Arrived late"
                        elif val < 0.95:
                            # 7% Absent (Manual)
                            status_val = AttendanceStatus.ABSENT.value
                            method_val = AttendanceMethod.MANUAL.value
                            checked_in_at = None
                            marked_by = course.teacher_id
                            note = "Unexcused absence"
                        else:
                            # 5% Excused (Manual)
                            status_val = AttendanceStatus.EXCUSED.value
                            method_val = AttendanceMethod.MANUAL.value
                            checked_in_at = None
                            marked_by = course.teacher_id
                            note = "Excused absence"

                        existing_att = await session.scalar(
                            select(AttendanceRecord).where(
                                AttendanceRecord.session_id == session_obj.id,
                                AttendanceRecord.student_id == stu_id,
                            )
                        )
                        if existing_att is None:
                            record = AttendanceRecord(
                                session_id=session_obj.id,
                                student_id=stu_id,
                                status=status_val,
                                checked_in_at=checked_in_at,
                                marked_by=marked_by,
                                method=method_val,
                                note=note,
                            )
                            session.add(record)
                    await session.flush()

    # 7. Create or refresh 1 currently OPEN session with active checkin code and expiration
    open_course = courses[OPEN_SESSION_COURSE_CODE]
    existing_open = await session.scalar(
        select(ClassSession).where(
            ClassSession.course_id == open_course.id,
            ClassSession.status == SessionStatus.OPEN.value,
        )
    )

    if existing_open is None:
        open_session = ClassSession(
            course_id=open_course.id,
            starts_at=now - timedelta(minutes=10),
            ends_at=now + timedelta(minutes=80),
            status=SessionStatus.OPEN.value,
            checkin_code=OPEN_SESSION_CHECKIN_CODE,
            code_expires_at=now + timedelta(hours=2),
            late_after_minutes=15,
        )
        session.add(open_session)
        await session.flush()
        logger.info(
            "Created open session for %s with code %s",
            open_course.code,
            OPEN_SESSION_CHECKIN_CODE,
        )
    else:
        # If open session exists, ensure code and expiration are active
        existing_expires = ensure_utc_datetime(existing_open.code_expires_at)
        if existing_expires is None or existing_expires < now:
            existing_open.code_expires_at = now + timedelta(hours=2)
        if not existing_open.checkin_code:
            existing_open.checkin_code = OPEN_SESSION_CHECKIN_CODE
        await session.flush()
        logger.info("Refreshed active open session for %s", open_course.code)

    await session.commit()

    # Calculate summary counts
    user_count = (await session.execute(select(func.count(User.id)))).scalar_one()
    course_count = (await session.execute(select(func.count(Course.id)))).scalar_one()
    enrollment_count = (await session.execute(select(func.count(Enrollment.id)))).scalar_one()
    session_count = (await session.execute(select(func.count(ClassSession.id)))).scalar_one()
    attendance_count = (await session.execute(select(func.count(AttendanceRecord.id)))).scalar_one()

    summary: dict[str, Any] = {
        "users": user_count,
        "admins": 1,
        "teachers": 3,
        "students": 40,
        "courses": course_count,
        "enrollments": enrollment_count,
        "sessions": session_count,
        "attendance_records": attendance_count,
    }

    logger.info("Database seeding completed successfully.")
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    banner = "=" * 80
    sub_banner = "-" * 80
    print(f"\n{banner}")
    print("                    ATTENDANCE MONITORING SYSTEM - DEMO SEED")
    print(banner)
    print("\nDatabase seeded successfully!\n")
    print("Summary Counts:")
    print(
        f"  - Users:               {summary['users']} "
        f"({summary['admins']} Admin, {summary['teachers']} Teachers, "
        f"{summary['students']} Students)"
    )
    print(f"  - Courses:             {summary['courses']}")
    print(f"  - Enrollments:         {summary['enrollments']}")
    print(f"  - Sessions:            {summary['sessions']} (Past closed + 1 active open)")
    print(f"  - Attendance Records:  {summary['attendance_records']}")
    print(f"\n{sub_banner}")
    print("Demo Credentials:")
    print(f"{'Role':<10} {'Email':<25} {'Password':<16} {'Details'}")
    print(sub_banner)
    print(f"{'Admin':<10} {ADMIN_EMAIL:<25} {ADMIN_PASSWORD:<16} System Administrator")
    for t in TEACHER_CREDENTIALS:
        print(f"{'Teacher':<10} {t['email']:<25} {t['password']:<16} {t['full_name']}")
    print(
        f"{'Student':<10} {'student1@example.com':<25} {STUDENT_PASSWORD:<16} "
        f"Student 01 (ID: STU1001)"
    )
    print(f"{'Student':<10} {'...':<25} {'...':<16} ...")
    print(
        f"{'Student':<10} {'student40@example.com':<25} {STUDENT_PASSWORD:<16} "
        f"Student 40 (ID: STU1040)"
    )
    print(sub_banner)
    print("\nCurrently Open Session:")
    print(f"  - Course:       {OPEN_SESSION_COURSE_CODE}")
    print(f"  - Check-in Code: {OPEN_SESSION_CHECKIN_CODE}")
    print("  - Status:       Active and valid for check-in")
    print(f"{banner}\n")


async def main() -> None:
    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        summary = await seed_data(session)
        _print_summary(summary)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(main())
