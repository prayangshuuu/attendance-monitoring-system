import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attendance_record import AttendanceRecord
from app.models.class_session import ClassSession
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.enums import AttendanceMethod, AttendanceStatus, SessionStatus, UserRole
from app.models.user import User


async def make_user(
    db: AsyncSession,
    email: str | None = None,
    full_name: str = "Test User",
    hashed_password: str = "hashed_secret_password",
    role: str = UserRole.STUDENT,
    student_id: str | None = None,
    is_active: bool = True,
    **kwargs: Any,
) -> User:
    if email is None:
        email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    if student_id is None and role == UserRole.STUDENT and "student_id" not in kwargs:
        student_id = f"STU_{uuid.uuid4().hex[:8]}"

    user = User(
        email=email.lower(),
        full_name=full_name,
        hashed_password=hashed_password,
        role=role,
        student_id=student_id,
        is_active=is_active,
        **kwargs,
    )
    db.add(user)
    await db.flush()
    return user


async def make_course(
    db: AsyncSession,
    teacher: User | None = None,
    *,
    code: str | None = None,
    title: str = "Introduction to Computer Science",
    min_attendance_pct: float = 80.0,
    is_active: bool = True,
    teacher_id: uuid.UUID | None = None,
    **kwargs: Any,
) -> Course:
    if teacher_id is None:
        if teacher is None:
            teacher = await make_user(db, role=UserRole.TEACHER, full_name="Professor Smith")
        teacher_id = teacher.id

    if code is None:
        code = f"CS{uuid.uuid4().hex[:6].upper()}"

    course = Course(
        code=code,
        title=title,
        teacher_id=teacher_id,
        min_attendance_pct=min_attendance_pct,
        is_active=is_active,
        **kwargs,
    )
    db.add(course)
    await db.flush()
    return course


async def make_session(
    db: AsyncSession,
    course: Course | None = None,
    *,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    status: str = SessionStatus.SCHEDULED,
    checkin_code: str | None = None,
    code_expires_at: datetime | None = None,
    late_after_minutes: int = 10,
    course_id: uuid.UUID | None = None,
    **kwargs: Any,
) -> ClassSession:
    if course_id is None:
        if course is None:
            course = await make_course(db)
        course_id = course.id

    if starts_at is None:
        starts_at = datetime.now(UTC)
    if ends_at is None:
        ends_at = starts_at + timedelta(hours=1, minutes=30)

    class_session = ClassSession(
        course_id=course_id,
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
        checkin_code=checkin_code,
        code_expires_at=code_expires_at,
        late_after_minutes=late_after_minutes,
        **kwargs,
    )
    db.add(class_session)
    await db.flush()
    return class_session


async def make_enrollment(
    db: AsyncSession,
    course: Course | None = None,
    student: User | None = None,
    *,
    course_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
    enrolled_at: datetime | None = None,
    **kwargs: Any,
) -> Enrollment:
    if course_id is None:
        if course is None:
            course = await make_course(db)
        course_id = course.id

    if student_id is None:
        if student is None:
            student = await make_user(db, role=UserRole.STUDENT)
        student_id = student.id

    enrollment_kwargs: dict[str, Any] = {
        "course_id": course_id,
        "student_id": student_id,
        **kwargs,
    }
    if enrolled_at is not None:
        enrollment_kwargs["enrolled_at"] = enrolled_at

    enrollment = Enrollment(**enrollment_kwargs)
    db.add(enrollment)
    await db.flush()
    return enrollment


async def make_record(
    db: AsyncSession,
    class_session: ClassSession | None = None,
    student: User | None = None,
    *,
    session_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
    status: str = AttendanceStatus.PRESENT,
    checked_in_at: datetime | None = None,
    marked_by: uuid.UUID | None = None,
    marker: User | None = None,
    method: str = AttendanceMethod.MANUAL,
    note: str | None = None,
    **kwargs: Any,
) -> AttendanceRecord:
    if "session" in kwargs:
        class_session = kwargs.pop("session")

    if session_id is None:
        if class_session is None:
            class_session = await make_session(db)
        session_id = class_session.id

    if student_id is None:
        if student is None:
            student = await make_user(db, role=UserRole.STUDENT)
        student_id = student.id

    if marked_by is None and marker is not None:
        marked_by = marker.id

    if checked_in_at is None and status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE):
        checked_in_at = datetime.now(UTC)

    record = AttendanceRecord(
        session_id=session_id,
        student_id=student_id,
        status=status,
        checked_in_at=checked_in_at,
        marked_by=marked_by,
        method=method,
        note=note,
        **kwargs,
    )
    db.add(record)
    await db.flush()
    return record
