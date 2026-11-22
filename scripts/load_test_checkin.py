import asyncio
import logging
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

# Ensure project root is on sys.path
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.security import create_access_token, hash_password  # noqa: E402
from app.db.session import async_session_maker  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.attendance_record import AttendanceRecord  # noqa: E402
from app.models.class_session import ClassSession  # noqa: E402
from app.models.course import Course  # noqa: E402
from app.models.enrollment import Enrollment  # noqa: E402
from app.models.enums import SessionStatus, UserRole  # noqa: E402
from app.models.user import User  # noqa: E402

# Suppress verbose access and httpx logs during load test output
logging.getLogger("app.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

NUM_STUDENTS = 50
CHECKIN_CODE = "PRF50X"


async def setup_test_data(session: AsyncSession) -> tuple[Course, ClassSession, list[User]]:
    """Create test teacher, course, 50 enrolled students, and an open session."""
    now = datetime.now(UTC)

    # 1. Teacher
    teacher_email = f"loadtest_teacher_{uuid4().hex[:8]}@example.com"
    pwd_hash = hash_password("TestPass123!")
    teacher = User(
        email=teacher_email,
        full_name="LoadTest Teacher",
        hashed_password=pwd_hash,
        role=UserRole.TEACHER.value,
        is_active=True,
    )
    session.add(teacher)
    await session.flush()

    # 2. Course
    course = Course(
        code=f"LT{uuid4().hex[:6].upper()}",
        title="Load Test Concurrency Course",
        teacher_id=teacher.id,
        min_attendance_pct=75.0,
    )
    session.add(course)
    await session.flush()

    # 3. 50 Students & Enrollments
    students: list[User] = []
    enrollments: list[Enrollment] = []
    batch_suffix = uuid4().hex[:6]

    for i in range(1, NUM_STUDENTS + 1):
        stu = User(
            email=f"loadtest_stu_{batch_suffix}_{i:03d}@example.com",
            full_name=f"LoadTest Student {i:03d}",
            hashed_password=pwd_hash,
            role=UserRole.STUDENT.value,
            is_active=True,
        )
        students.append(stu)

    session.add_all(students)
    await session.flush()

    for stu in students:
        enr = Enrollment(
            course_id=course.id,
            student_id=stu.id,
            enrolled_at=now,
        )
        enrollments.append(enr)

    session.add_all(enrollments)
    await session.flush()

    # 4. Open Class Session
    class_session = ClassSession(
        course_id=course.id,
        starts_at=now - timedelta(minutes=5),
        ends_at=now + timedelta(hours=2),
        status=SessionStatus.OPEN.value,
        checkin_code=CHECKIN_CODE,
        code_expires_at=now + timedelta(minutes=30),
        late_after_minutes=15,
    )
    session.add(class_session)
    await session.commit()
    await session.refresh(class_session)

    return course, class_session, students


async def cleanup_test_data(course_id: UUID, teacher_id: UUID, student_ids: list[UUID]) -> None:
    """Clean up all created test entities."""
    async with async_session_maker() as session:
        await session.execute(delete(Course).where(Course.id == course_id))
        await session.execute(delete(User).where(User.id.in_(student_ids + [teacher_id])))
        await session.commit()


def calculate_latency_metrics(latencies_ms: list[float]) -> dict[str, float]:
    """Calculate min, max, median, and p95 latency."""
    sorted_lat = sorted(latencies_ms)
    n = len(sorted_lat)
    p95_idx = int(0.95 * n)
    if p95_idx >= n:
        p95_idx = n - 1

    return {
        "min": min(latencies_ms),
        "max": max(latencies_ms),
        "median": statistics.median(latencies_ms),
        "p95": sorted_lat[p95_idx],
    }


async def run_load_test() -> dict:
    """Execute concurrent check-in load test."""
    print("=" * 80)
    print("STARTING 50 CONCURRENT CHECK-IN LOAD TEST")
    print("=" * 80)

    app = create_app()

    # Setup
    async with async_session_maker() as db_session:
        course, session_obj, students = await setup_test_data(db_session)
        session_id = session_obj.id
        teacher_id = course.teacher_id
        student_ids = [s.id for s in students]

    print(f"Setup complete: Course ID={course.id}, Session ID={session_id}")
    print(f"Total students enrolled: {len(students)}")

    try:
        # Generate tokens
        student_tokens = [create_access_token(sub=s.id, role=s.role) for s in students]

        # -------------------------------------------------------------
        # Phase 1: 50 concurrent valid check-ins
        # -------------------------------------------------------------
        print("\n--> Launching 50 concurrent check-in requests (asyncio.gather)...")
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=30.0
        ) as client:

            async def single_check_in(token: str, student_id: UUID) -> tuple[int, float, dict]:
                headers = {"Authorization": f"Bearer {token}"}
                t0 = time.perf_counter()
                res = await client.post(
                    f"/api/v1/sessions/{session_id}/check-in",
                    headers=headers,
                    json={"code": CHECKIN_CODE},
                )
                t1 = time.perf_counter()
                elapsed_ms = (t1 - t0) * 1000.0
                return res.status_code, elapsed_ms, res.json()

            t_start = time.perf_counter()
            tasks = [
                single_check_in(token, s.id)
                for token, s in zip(student_tokens, students, strict=True)
            ]
            responses = await asyncio.gather(*tasks)
            total_wall_time_ms = (time.perf_counter() - t_start) * 1000.0

        status_codes = [r[0] for r in responses]
        latencies = [r[1] for r in responses]

        count_200 = status_codes.count(200)
        count_400 = status_codes.count(400)
        count_409 = status_codes.count(409)
        count_500 = sum(1 for c in status_codes if c >= 500)
        other_codes = [c for c in status_codes if c not in (200, 400, 409)]

        print("\nPhase 1 Results (50 Concurrent Check-ins):")
        print(f"  Wall time: {total_wall_time_ms:.2f} ms")
        print(f"  HTTP 200 OK:      {count_200}")
        print(f"  HTTP 400 BadReq:  {count_400}")
        print(f"  HTTP 409 Conf:    {count_409}")
        print(f"  HTTP 500+ Errors: {count_500}")
        if other_codes:
            print(f"  Other codes:      {other_codes}")

        # Assertions
        assert count_500 == 0, f"Expected 0 server errors (500), got {count_500}"
        assert all(c in (200, 400, 409) for c in status_codes), (
            f"Unexpected status codes: {status_codes}"
        )
        assert count_200 == NUM_STUDENTS, (
            f"Expected all {NUM_STUDENTS} to succeed (200), got {count_200}"
        )

        metrics = calculate_latency_metrics(latencies)
        print("\nLatency Metrics (Phase 1 - 50 Concurrent First Check-ins):")
        print(f"  Min:    {metrics['min']:.2f} ms")
        print(f"  Median: {metrics['median']:.2f} ms")
        print(f"  p95:    {metrics['p95']:.2f} ms")
        print(f"  Max:    {metrics['max']:.2f} ms")

        # -------------------------------------------------------------
        # Phase 2: Duplicate check-ins (assert all return 409, zero 500s)
        # -------------------------------------------------------------
        print("\n--> Launching 50 concurrent duplicate check-ins (testing 409 conflict)...")
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=30.0
        ) as client:
            t_start2 = time.perf_counter()
            dup_tasks = [
                single_check_in(token, s.id)
                for token, s in zip(student_tokens, students, strict=True)
            ]
            dup_responses = await asyncio.gather(*dup_tasks)
            dup_wall_time_ms = (time.perf_counter() - t_start2) * 1000.0

        dup_codes = [r[0] for r in dup_responses]
        dup_latencies = [r[1] for r in dup_responses]

        dup_count_409 = dup_codes.count(409)
        dup_count_500 = sum(1 for c in dup_codes if c >= 500)

        print("\nPhase 2 Results (50 Concurrent Duplicate Check-ins):")
        print(f"  Wall time: {dup_wall_time_ms:.2f} ms")
        print(f"  HTTP 409 Conflict: {dup_count_409}")
        print(f"  HTTP 500+ Errors:  {dup_count_500}")

        assert dup_count_500 == 0, f"Expected 0 server errors on duplicate, got {dup_count_500}"
        assert dup_count_409 == NUM_STUDENTS, (
            f"Expected all {NUM_STUDENTS} to return 409, got {dup_count_409}"
        )

        dup_metrics = calculate_latency_metrics(dup_latencies)
        print("\nLatency Metrics (Phase 2 - 50 Concurrent Duplicate Check-ins):")
        print(f"  Min:    {dup_metrics['min']:.2f} ms")
        print(f"  Median: {dup_metrics['median']:.2f} ms")
        print(f"  p95:    {dup_metrics['p95']:.2f} ms")
        print(f"  Max:    {dup_metrics['max']:.2f} ms")

        # Verify DB records
        async with async_session_maker() as db_session:
            records_count_res = await db_session.execute(
                select(AttendanceRecord).where(AttendanceRecord.session_id == session_id)
            )
            saved_records = records_count_res.scalars().all()
            print(f"\nDB Verification: exactly {len(saved_records)} attendance records in DB.")
            assert len(saved_records) == NUM_STUDENTS

        return {
            "phase1": {
                "total_wall_time_ms": total_wall_time_ms,
                "count_200": count_200,
                "count_400": count_400,
                "count_409": count_409,
                "count_500": count_500,
                "metrics": metrics,
            },
            "phase2": {
                "total_wall_time_ms": dup_wall_time_ms,
                "count_409": dup_count_409,
                "count_500": dup_count_500,
                "metrics": dup_metrics,
            },
        }

    finally:
        print("\nCleaning up test data...")
        await cleanup_test_data(course.id, teacher_id, student_ids)
        print("Cleanup completed.")


if __name__ == "__main__":
    result = asyncio.run(run_load_test())
    print("\nLOAD TEST PASSED SUCCESSFULLY!")
