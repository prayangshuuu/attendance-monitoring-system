import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.utils.clock import get_clock
from app.utils.rate_limiter import in_memory_rate_limiter


class ControllableClock:
    def __init__(self, current_time: datetime | None = None) -> None:
        self._current_time = current_time or datetime.now(UTC)

    def set_time(self, new_time: datetime) -> None:
        self._current_time = new_time

    def advance(self, td: timedelta) -> None:
        self._current_time += td

    def now(self) -> datetime:
        return self._current_time


@pytest.fixture(autouse=True)
async def reset_rate_limiter() -> None:
    await in_memory_rate_limiter.reset()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_full_semester_lifecycle_e2e(
    client: AsyncClient,
    app: FastAPI,
    db_session: AsyncSession,
    admin_user: User,
) -> None:
    """E2E test verifying complete semester lifecycle through HTTP endpoints.

    Flow:
    a) Admin logs in, creates 1 teacher and 5 students.
    b) Admin creates CS101 course (min_attendance_pct=80.0) assigned to teacher.
    c) Admin enrolls all 5 students.
    d) Teacher logs in and schedules 3 class sessions.
    e) Session 1: S1 on time, S2 late, S3-S5 miss -> Teacher closes (auto-mark absentees).
    f) Session 2: S1, S2, S4 on time, S3 late, S5 misses -> Teacher closes.
    g) Session 3: S1, S2, S4 on time, S3 and S5 miss -> Teacher closes.
    h) Teacher overrides S5's absence in Session 1 to status=excused, note="Medical leave".
    i) Verification of course report, at-risk list, and CSV export.
    """
    test_clock = ControllableClock(datetime(2026, 9, 1, 8, 55, tzinfo=UTC))
    app.dependency_overrides[get_clock] = lambda: test_clock

    try:
        # -------------------------------------------------------------------------
        # a) Admin logs in and creates 1 teacher and 5 students
        # -------------------------------------------------------------------------
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_user.email, "password": "adminpassword123"},
        )
        assert login_res.status_code == 200, login_res.text
        admin_token = login_res.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Create 1 teacher
        teacher_res = await client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={
                "email": "teacher.e2e@example.com",
                "password": "Password123!",
                "full_name": "Professor Charles",
                "role": "teacher",
            },
        )
        assert teacher_res.status_code == 201, teacher_res.text
        teacher_id = teacher_res.json()["id"]

        # Create 5 students (student_1 .. student_5)
        students: dict[int, dict[str, str]] = {}
        for i in range(1, 6):
            stu_res = await client.post(
                "/api/v1/users",
                headers=admin_headers,
                json={
                    "email": f"student_{i}@example.com",
                    "password": "Password123!",
                    "full_name": f"Student {i}",
                    "role": "student",
                    "student_id": f"STU00{i}",
                },
            )
            assert stu_res.status_code == 201, stu_res.text
            students[i] = stu_res.json()

        # -------------------------------------------------------------------------
        # b) Admin creates course CS101 (min_attendance_pct=80.0) assigned to teacher
        # -------------------------------------------------------------------------
        course_res = await client.post(
            "/api/v1/courses",
            headers=admin_headers,
            json={
                "code": "CS101",
                "title": "Introduction to Computer Science",
                "teacher_id": teacher_id,
                "min_attendance_pct": 80.0,
                "is_active": True,
            },
        )
        assert course_res.status_code == 201, course_res.text
        course_id = course_res.json()["id"]

        # -------------------------------------------------------------------------
        # c) Admin enrolls all 5 students
        # -------------------------------------------------------------------------
        enroll_res = await client.post(
            f"/api/v1/courses/{course_id}/enrollments",
            headers=admin_headers,
            json={"student_ids": [students[i]["id"] for i in range(1, 6)]},
        )
        assert enroll_res.status_code == 200, enroll_res.text
        assert len(enroll_res.json()["enrolled"]) == 5

        # -------------------------------------------------------------------------
        # d) Teacher logs in and schedules 3 class sessions
        # -------------------------------------------------------------------------
        teacher_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "teacher.e2e@example.com", "password": "Password123!"},
        )
        assert teacher_login.status_code == 200, teacher_login.text
        teacher_token = teacher_login.json()["access_token"]
        teacher_headers = {"Authorization": f"Bearer {teacher_token}"}

        # Authenticate all 5 students for later check-ins
        student_headers: dict[int, dict[str, str]] = {}
        for i in range(1, 6):
            stu_login = await client.post(
                "/api/v1/auth/login",
                json={"email": f"student_{i}@example.com", "password": "Password123!"},
            )
            assert stu_login.status_code == 200, stu_login.text
            student_headers[i] = {"Authorization": f"Bearer {stu_login.json()['access_token']}"}

        # Schedule Session 1: 2026-09-01 09:00 - 10:30 UTC
        s1_start = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
        s1_res = await client.post(
            f"/api/v1/courses/{course_id}/sessions",
            headers=teacher_headers,
            json={
                "starts_at": s1_start.isoformat(),
                "ends_at": (s1_start + timedelta(hours=1, minutes=30)).isoformat(),
                "late_after_minutes": 10,
            },
        )
        assert s1_res.status_code == 201, s1_res.text
        session_1_id = s1_res.json()["id"]

        # Schedule Session 2: 2026-09-03 09:00 - 10:30 UTC
        s2_start = datetime(2026, 9, 3, 9, 0, tzinfo=UTC)
        s2_res = await client.post(
            f"/api/v1/courses/{course_id}/sessions",
            headers=teacher_headers,
            json={
                "starts_at": s2_start.isoformat(),
                "ends_at": (s2_start + timedelta(hours=1, minutes=30)).isoformat(),
                "late_after_minutes": 10,
            },
        )
        assert s2_res.status_code == 201, s2_res.text
        session_2_id = s2_res.json()["id"]

        # Schedule Session 3: 2026-09-05 09:00 - 10:30 UTC
        s3_start = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
        s3_res = await client.post(
            f"/api/v1/courses/{course_id}/sessions",
            headers=teacher_headers,
            json={
                "starts_at": s3_start.isoformat(),
                "ends_at": (s3_start + timedelta(hours=1, minutes=30)).isoformat(),
                "late_after_minutes": 10,
            },
        )
        assert s3_res.status_code == 201, s3_res.text
        session_3_id = s3_res.json()["id"]

        # -------------------------------------------------------------------------
        # e) Session 1 execution
        # -------------------------------------------------------------------------
        # Teacher opens session
        test_clock.set_time(s1_start)
        open_res1 = await client.post(
            f"/api/v1/sessions/{session_1_id}/open",
            headers=teacher_headers,
        )
        assert open_res1.status_code == 200, open_res1.text
        code_1 = open_res1.json()["checkin_code"]

        # Student 1 checks in on time (2 minutes in) -> present
        test_clock.set_time(s1_start + timedelta(minutes=2))
        s1_checkin = await client.post(
            f"/api/v1/sessions/{session_1_id}/check-in",
            headers=student_headers[1],
            json={"code": code_1},
        )
        assert s1_checkin.status_code == 200, s1_checkin.text
        assert s1_checkin.json()["status"] == "present"

        # Student 2 checks in late (12 minutes in > 10 min late threshold) -> late
        test_clock.set_time(s1_start + timedelta(minutes=12))
        s2_checkin = await client.post(
            f"/api/v1/sessions/{session_1_id}/check-in",
            headers=student_headers[2],
            json={"code": code_1},
        )
        assert s2_checkin.status_code == 200, s2_checkin.text
        assert s2_checkin.json()["status"] == "late"

        # Students 3, 4, 5 do not check in
        # Teacher closes session -> auto-marks absentees for students 3, 4, 5
        test_clock.set_time(s1_start + timedelta(hours=1, minutes=30))
        close_res1 = await client.post(
            f"/api/v1/sessions/{session_1_id}/close",
            headers=teacher_headers,
        )
        assert close_res1.status_code == 200, close_res1.text
        assert close_res1.json()["status"] == "closed"

        # -------------------------------------------------------------------------
        # f) Session 2 execution
        # -------------------------------------------------------------------------
        # Teacher opens session
        test_clock.set_time(s2_start)
        open_res2 = await client.post(
            f"/api/v1/sessions/{session_2_id}/open",
            headers=teacher_headers,
        )
        assert open_res2.status_code == 200, open_res2.text
        code_2 = open_res2.json()["checkin_code"]

        # Student 1 checks in on time -> present
        test_clock.set_time(s2_start + timedelta(minutes=2))
        s1_c2 = await client.post(
            f"/api/v1/sessions/{session_2_id}/check-in",
            headers=student_headers[1],
            json={"code": code_2},
        )
        assert s1_c2.status_code == 200, s1_c2.text
        assert s1_c2.json()["status"] == "present"

        # Student 2 checks in on time -> present
        test_clock.set_time(s2_start + timedelta(minutes=3))
        s2_c2 = await client.post(
            f"/api/v1/sessions/{session_2_id}/check-in",
            headers=student_headers[2],
            json={"code": code_2},
        )
        assert s2_c2.status_code == 200, s2_c2.text
        assert s2_c2.json()["status"] == "present"

        # Student 3 checks in late (12 minutes in) -> late
        test_clock.set_time(s2_start + timedelta(minutes=12))
        s3_c2 = await client.post(
            f"/api/v1/sessions/{session_2_id}/check-in",
            headers=student_headers[3],
            json={"code": code_2},
        )
        assert s3_c2.status_code == 200, s3_c2.text
        assert s3_c2.json()["status"] == "late"

        # Student 4 checks in on time (clock set back to 5 mins into session) -> present
        test_clock.set_time(s2_start + timedelta(minutes=5))
        s4_c2 = await client.post(
            f"/api/v1/sessions/{session_2_id}/check-in",
            headers=student_headers[4],
            json={"code": code_2},
        )
        assert s4_c2.status_code == 200, s4_c2.text
        assert s4_c2.json()["status"] == "present"

        # Student 5 misses
        # Teacher closes session
        test_clock.set_time(s2_start + timedelta(hours=1, minutes=30))
        close_res2 = await client.post(
            f"/api/v1/sessions/{session_2_id}/close",
            headers=teacher_headers,
        )
        assert close_res2.status_code == 200, close_res2.text
        assert close_res2.json()["status"] == "closed"

        # -------------------------------------------------------------------------
        # g) Session 3 execution
        # -------------------------------------------------------------------------
        # Teacher opens session
        test_clock.set_time(s3_start)
        open_res3 = await client.post(
            f"/api/v1/sessions/{session_3_id}/open",
            headers=teacher_headers,
        )
        assert open_res3.status_code == 200, open_res3.text
        code_3 = open_res3.json()["checkin_code"]

        # Student 1 on time -> present
        test_clock.set_time(s3_start + timedelta(minutes=2))
        s1_c3 = await client.post(
            f"/api/v1/sessions/{session_3_id}/check-in",
            headers=student_headers[1],
            json={"code": code_3},
        )
        assert s1_c3.status_code == 200, s1_c3.text
        assert s1_c3.json()["status"] == "present"

        # Student 2 on time -> present
        test_clock.set_time(s3_start + timedelta(minutes=3))
        s2_c3 = await client.post(
            f"/api/v1/sessions/{session_3_id}/check-in",
            headers=student_headers[2],
            json={"code": code_3},
        )
        assert s2_c3.status_code == 200, s2_c3.text
        assert s2_c3.json()["status"] == "present"

        # Student 4 on time -> present
        test_clock.set_time(s3_start + timedelta(minutes=4))
        s4_c3 = await client.post(
            f"/api/v1/sessions/{session_3_id}/check-in",
            headers=student_headers[4],
            json={"code": code_3},
        )
        assert s4_c3.status_code == 200, s4_c3.text
        assert s4_c3.json()["status"] == "present"

        # Students 3 and 5 miss
        # Teacher closes session
        test_clock.set_time(s3_start + timedelta(hours=1, minutes=30))
        close_res3 = await client.post(
            f"/api/v1/sessions/{session_3_id}/close",
            headers=teacher_headers,
        )
        assert close_res3.status_code == 200, close_res3.text
        assert close_res3.json()["status"] == "closed"

        # -------------------------------------------------------------------------
        # h) Teacher manually overrides Student 5's absence in Session 1 to excused
        # -------------------------------------------------------------------------
        roster_res = await client.get(
            f"/api/v1/sessions/{session_1_id}/attendance",
            headers=teacher_headers,
        )
        assert roster_res.status_code == 200, roster_res.text
        s1_roster = roster_res.json()["items"]
        s5_item = next(item for item in s1_roster if item["student_id"] == students[5]["id"])
        assert s5_item["status"] == "absent"
        s5_record_id = s5_item["record_id"]
        assert s5_record_id is not None

        override_res = await client.patch(
            f"/api/v1/attendance/{s5_record_id}",
            headers=teacher_headers,
            json={"status": "excused", "note": "Medical leave"},
        )
        assert override_res.status_code == 200, override_res.text
        assert override_res.json()["status"] == "excused"
        assert override_res.json()["note"] == "Medical leave"

        # -------------------------------------------------------------------------
        # i) Verification: Course report, At-risk report, CSV export
        # -------------------------------------------------------------------------
        # Verification 1: GET /api/v1/reports/courses/{course_id}
        rep_res = await client.get(
            f"/api/v1/reports/courses/{course_id}",
            headers=teacher_headers,
        )
        assert rep_res.status_code == 200, rep_res.text
        course_report = rep_res.json()
        assert course_report["total_closed_sessions"] == 3
        assert course_report["enrolled_students_count"] == 5

        students_rep = {s["student_id"]: s for s in course_report["students"]}

        # Student 1: 3 present, 0 late, 0 absent, 0 excused -> 100.0%, is_at_risk=False
        rep1 = students_rep[students[1]["id"]]
        assert rep1["present_count"] == 3
        assert rep1["late_count"] == 0
        assert rep1["absent_count"] == 0
        assert rep1["excused_count"] == 0
        assert rep1["total_sessions"] == 3
        assert rep1["attended_sessions"] == 3
        assert rep1["percentage"] == 100.0
        assert rep1["is_at_risk"] is False

        # Student 2: 2 present, 1 late, 0 absent, 0 excused -> 100.0%, is_at_risk=False
        rep2 = students_rep[students[2]["id"]]
        assert rep2["present_count"] == 2
        assert rep2["late_count"] == 1
        assert rep2["absent_count"] == 0
        assert rep2["excused_count"] == 0
        assert rep2["total_sessions"] == 3
        assert rep2["attended_sessions"] == 3
        assert rep2["percentage"] == 100.0
        assert rep2["is_at_risk"] is False

        # Student 3: 0 present, 1 late, 2 absent, 0 excused -> 33.33%, is_at_risk=True
        rep3 = students_rep[students[3]["id"]]
        assert rep3["present_count"] == 0
        assert rep3["late_count"] == 1
        assert rep3["absent_count"] == 2
        assert rep3["excused_count"] == 0
        assert rep3["total_sessions"] == 3
        assert rep3["attended_sessions"] == 1
        assert rep3["percentage"] == 33.33
        assert rep3["is_at_risk"] is True

        # Student 4: 2 present, 0 late, 1 absent, 0 excused -> 66.67%, is_at_risk=True
        rep4 = students_rep[students[4]["id"]]
        assert rep4["present_count"] == 2
        assert rep4["late_count"] == 0
        assert rep4["absent_count"] == 1
        assert rep4["excused_count"] == 0
        assert rep4["total_sessions"] == 3
        assert rep4["attended_sessions"] == 2
        assert rep4["percentage"] == 66.67
        assert rep4["is_at_risk"] is True

        # Student 5: 0 present, 0 late, 2 absent, 1 excused -> 33.33%, is_at_risk=True
        rep5 = students_rep[students[5]["id"]]
        assert rep5["present_count"] == 0
        assert rep5["late_count"] == 0
        assert rep5["absent_count"] == 2
        assert rep5["excused_count"] == 1
        assert rep5["total_sessions"] == 3
        assert rep5["attended_sessions"] == 1
        assert rep5["percentage"] == 33.33
        assert rep5["is_at_risk"] is True

        # Verification 2: GET /api/v1/reports/courses/{course_id}/at-risk
        at_risk_res = await client.get(
            f"/api/v1/reports/courses/{course_id}/at-risk",
            headers=teacher_headers,
        )
        assert at_risk_res.status_code == 200, at_risk_res.text
        at_risk_data = at_risk_res.json()
        assert len(at_risk_data) == 3
        at_risk_ids = {item["student_id"] for item in at_risk_data}
        assert at_risk_ids == {
            students[3]["id"],
            students[4]["id"],
            students[5]["id"],
        }
        for item in at_risk_data:
            assert item["is_at_risk"] is True

        # Verification 3: GET /api/v1/reports/courses/{course_id}/export.csv
        csv_res = await client.get(
            f"/api/v1/reports/courses/{course_id}/export.csv",
            headers=teacher_headers,
        )
        assert csv_res.status_code == 200, csv_res.text
        assert "text/csv" in csv_res.headers["content-type"]

        csv_lines = list(csv.reader(io.StringIO(csv_res.text)))
        expected_header = [
            "student_id",
            "student_name",
            "student_email",
            "student_code",
            "present_count",
            "late_count",
            "absent_count",
            "excused_count",
            "total_sessions",
            "attended_sessions",
            "percentage",
            "is_at_risk",
        ]
        assert csv_lines[0] == expected_header
        assert len(csv_lines) == 6  # 1 header + 5 students

        csv_rows_by_id = {row[0]: row for row in csv_lines[1:]}

        # Student 1 row
        r1 = csv_rows_by_id[students[1]["id"]]
        assert r1[1] == "Student 1"
        assert r1[2] == "student_1@example.com"
        assert r1[3] == "STU001"
        assert r1[4] == "3"
        assert r1[5] == "0"
        assert r1[6] == "0"
        assert r1[7] == "0"
        assert r1[8] == "3"
        assert r1[9] == "3"
        assert r1[10] == "100.00"
        assert r1[11] == "False"

        # Student 2 row
        r2 = csv_rows_by_id[students[2]["id"]]
        assert r2[1] == "Student 2"
        assert r2[2] == "student_2@example.com"
        assert r2[3] == "STU002"
        assert r2[4] == "2"
        assert r2[5] == "1"
        assert r2[6] == "0"
        assert r2[7] == "0"
        assert r2[8] == "3"
        assert r2[9] == "3"
        assert r2[10] == "100.00"
        assert r2[11] == "False"

        # Student 3 row
        r3 = csv_rows_by_id[students[3]["id"]]
        assert r3[1] == "Student 3"
        assert r3[2] == "student_3@example.com"
        assert r3[3] == "STU003"
        assert r3[4] == "0"
        assert r3[5] == "1"
        assert r3[6] == "2"
        assert r3[7] == "0"
        assert r3[8] == "3"
        assert r3[9] == "1"
        assert r3[10] == "33.33"
        assert r3[11] == "True"

        # Student 4 row
        r4 = csv_rows_by_id[students[4]["id"]]
        assert r4[1] == "Student 4"
        assert r4[2] == "student_4@example.com"
        assert r4[3] == "STU004"
        assert r4[4] == "2"
        assert r4[5] == "0"
        assert r4[6] == "1"
        assert r4[7] == "0"
        assert r4[8] == "3"
        assert r4[9] == "2"
        assert r4[10] == "66.67"
        assert r4[11] == "True"

        # Student 5 row
        r5 = csv_rows_by_id[students[5]["id"]]
        assert r5[1] == "Student 5"
        assert r5[2] == "student_5@example.com"
        assert r5[3] == "STU005"
        assert r5[4] == "0"
        assert r5[5] == "0"
        assert r5[6] == "2"
        assert r5[7] == "1"
        assert r5[8] == "3"
        assert r5[9] == "1"
        assert r5[10] == "33.33"
        assert r5[11] == "True"

    finally:
        app.dependency_overrides.pop(get_clock, None)
