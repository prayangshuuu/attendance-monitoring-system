import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

# Ensure project root is on sys.path when executed directly
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

DEFAULT_BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")


def run_smoke_test(client: httpx.Client | None = None, base_url: str | None = None) -> bool:
    """Executes the end-to-end smoke test against the attendance monitoring API.

    Returns True if all steps pass, False otherwise.
    """
    target_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    created_client = client is None
    active_client = client or httpx.Client(base_url=target_url, timeout=30.0)

    try:
        print("=" * 80)
        print(f"STARTING SMOKE TEST against {target_url}")
        print("=" * 80)

        # ---------------------------------------------------------------------
        # Step 1: Health check -> status 200, database "ok"
        # ---------------------------------------------------------------------
        print("\n[Step 1/7] Checking /health endpoint...")
        health_resp = active_client.get("/health")
        if health_resp.status_code != 200:
            print(f"FAILED: /health returned {health_resp.status_code}: {health_resp.text}")
            return False

        health_data = health_resp.json()
        if health_data.get("status") != "ok" or health_data.get("database") != "ok":
            print(f"FAILED: Unexpected health payload: {health_data}")
            return False
        print(
            f"  -> PASS: Health check OK (status={health_data.get('status')}, "
            f"database={health_data.get('database')})"
        )

        # ---------------------------------------------------------------------
        # Step 2: Admin login -> get access token
        # ---------------------------------------------------------------------
        print("\n[Step 2/7] Admin login...")
        login_resp = active_client.post(
            "/api/v1/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        if login_resp.status_code != 200:
            print(f"FAILED: Admin login returned {login_resp.status_code}: {login_resp.text}")
            return False

        admin_token = login_resp.json().get("access_token")
        if not admin_token:
            print(f"FAILED: Missing access_token in login response: {login_resp.json()}")
            return False
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        print("  -> PASS: Admin login successful, access token acquired")

        # ---------------------------------------------------------------------
        # Step 3: Create / get teacher, student, course
        # ---------------------------------------------------------------------
        print("\n[Step 3/7] Preparing teacher, student, and course...")
        teacher_email = os.getenv("TEACHER_EMAIL", "teacher1@example.com")
        teacher_password = os.getenv("TEACHER_PASSWORD", "Teacher123!")

        # 3a. Create or get teacher
        t_resp = active_client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={
                "email": teacher_email,
                "full_name": "Prof. Alan Turing",
                "role": "teacher",
                "password": teacher_password,
            },
        )
        if t_resp.status_code == 201:
            teacher_id = t_resp.json()["id"]
        elif t_resp.status_code == 409:
            list_t = active_client.get(
                "/api/v1/users",
                headers=admin_headers,
                params={"search": teacher_email},
            )
            list_t.raise_for_status()
            users: list[dict[str, Any]] = list_t.json().get("items", [])
            teacher_id = next(u["id"] for u in users if u["email"].lower() == teacher_email.lower())
        else:
            print(f"FAILED: Create teacher failed: {t_resp.status_code} {t_resp.text}")
            return False

        # 3b. Create or get student
        student_email = os.getenv("STUDENT_EMAIL", "student1@example.com")
        student_password = os.getenv("STUDENT_PASSWORD", "Student123!")
        student_code = os.getenv("STUDENT_CODE", "STU1001")

        s_resp = active_client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={
                "email": student_email,
                "full_name": "Student 01",
                "role": "student",
                "student_id": student_code,
                "password": student_password,
            },
        )
        if s_resp.status_code == 201:
            student_id = s_resp.json()["id"]
        elif s_resp.status_code == 409:
            list_s = active_client.get(
                "/api/v1/users",
                headers=admin_headers,
                params={"search": student_email},
            )
            list_s.raise_for_status()
            users = list_s.json().get("items", [])
            student_id = next(u["id"] for u in users if u["email"].lower() == student_email.lower())
        else:
            print(f"FAILED: Create student failed: {s_resp.status_code} {s_resp.text}")
            return False

        # 3c. Create or get course
        course_code = "CS101"
        c_resp = active_client.post(
            "/api/v1/courses",
            headers=admin_headers,
            json={
                "code": course_code,
                "title": "Introduction to Computer Science",
                "teacher_id": teacher_id,
                "min_attendance_pct": 80.0,
                "is_active": True,
            },
        )
        if c_resp.status_code == 201:
            course_id = c_resp.json()["id"]
        elif c_resp.status_code == 409:
            list_c = active_client.get(
                "/api/v1/courses",
                headers=admin_headers,
                params={"search": course_code},
            )
            list_c.raise_for_status()
            courses: list[dict[str, Any]] = list_c.json().get("items", [])
            course_id = next(c["id"] for c in courses if c["code"].upper() == course_code)
        else:
            print(f"FAILED: Create course failed: {c_resp.status_code} {c_resp.text}")
            return False

        # 3d. Enroll student into course
        enroll_resp = active_client.post(
            f"/api/v1/courses/{course_id}/enrollments",
            headers=admin_headers,
            json={"student_ids": [student_id]},
        )
        if enroll_resp.status_code != 200:
            print(f"FAILED: Enroll student failed: {enroll_resp.status_code} {enroll_resp.text}")
            return False

        print(
            f"  -> PASS: Entities ready (Course ID: {course_id}, "
            f"Teacher: {teacher_email}, Student: {student_email})"
        )

        # ---------------------------------------------------------------------
        # Step 4: Teacher login -> schedule session -> open session -> get checkin code
        # ---------------------------------------------------------------------
        print("\n[Step 4/7] Teacher login, schedule session, and open session...")
        t_login_resp = active_client.post(
            "/api/v1/auth/login",
            json={"email": teacher_email, "password": teacher_password},
        )
        if t_login_resp.status_code != 200:
            print(f"FAILED: Teacher login failed: {t_login_resp.status_code} {t_login_resp.text}")
            return False
        teacher_token = t_login_resp.json()["access_token"]
        teacher_headers = {"Authorization": f"Bearer {teacher_token}"}

        # Calculate non-overlapping session times
        sessions_resp = active_client.get(
            f"/api/v1/courses/{course_id}/sessions",
            headers=teacher_headers,
        )
        sessions_resp.raise_for_status()
        existing_sessions: list[dict[str, Any]] = sessions_resp.json().get("items", [])

        now_utc = datetime.now(UTC)
        max_end = now_utc
        for sess in existing_sessions:
            ends_str = sess.get("ends_at", "")
            if ends_str:
                try:
                    ends_dt = datetime.fromisoformat(ends_str.replace("Z", "+00:00"))
                    if ends_dt > max_end:
                        max_end = ends_dt
                except Exception:
                    pass

        session_start = max_end + timedelta(hours=1)
        session_end = session_start + timedelta(hours=1, minutes=30)

        sched_resp = active_client.post(
            f"/api/v1/courses/{course_id}/sessions",
            headers=teacher_headers,
            json={
                "starts_at": session_start.isoformat(),
                "ends_at": session_end.isoformat(),
                "late_after_minutes": 15,
            },
        )
        if sched_resp.status_code != 201:
            print(f"FAILED: Schedule session failed: {sched_resp.status_code} {sched_resp.text}")
            return False
        session_id = sched_resp.json()["id"]

        # Open session to obtain check-in code
        open_resp = active_client.post(
            f"/api/v1/sessions/{session_id}/open",
            headers=teacher_headers,
        )
        if open_resp.status_code != 200:
            print(f"FAILED: Open session failed: {open_resp.status_code} {open_resp.text}")
            return False
        checkin_code = open_resp.json().get("checkin_code")
        if not checkin_code:
            print(f"FAILED: Open session response missing checkin_code: {open_resp.json()}")
            return False

        print(
            f"  -> PASS: Session {session_id} scheduled and opened with live code: {checkin_code}"
        )

        # ---------------------------------------------------------------------
        # Step 5: Student login -> check in using live code
        # ---------------------------------------------------------------------
        print("\n[Step 5/7] Student login and live code check-in...")
        s_login_resp = active_client.post(
            "/api/v1/auth/login",
            json={"email": student_email, "password": student_password},
        )
        if s_login_resp.status_code != 200:
            print(f"FAILED: Student login failed: {s_login_resp.status_code} {s_login_resp.text}")
            return False
        student_token = s_login_resp.json()["access_token"]
        student_headers = {"Authorization": f"Bearer {student_token}"}

        checkin_resp = active_client.post(
            f"/api/v1/sessions/{session_id}/check-in",
            headers=student_headers,
            json={"code": checkin_code},
        )
        if checkin_resp.status_code != 200:
            print(
                f"FAILED: Student check-in failed: {checkin_resp.status_code} {checkin_resp.text}"
            )
            return False

        record_status = checkin_resp.json().get("status")
        print(f"  -> PASS: Student checked in successfully with status: {record_status}")

        # ---------------------------------------------------------------------
        # Step 6: Teacher closes session
        # ---------------------------------------------------------------------
        print("\n[Step 6/7] Teacher closes session...")
        close_resp = active_client.post(
            f"/api/v1/sessions/{session_id}/close",
            headers=teacher_headers,
        )
        if close_resp.status_code != 200:
            print(f"FAILED: Close session failed: {close_resp.status_code} {close_resp.text}")
            return False

        closed_status = close_resp.json().get("status")
        if closed_status != "closed":
            print(f"FAILED: Expected session status 'closed', got '{closed_status}'")
            return False
        print(f"  -> PASS: Session closed successfully (status={closed_status})")

        # ---------------------------------------------------------------------
        # Step 7: Query course report and CSV export
        # ---------------------------------------------------------------------
        print("\n[Step 7/7] Querying course report and CSV export...")
        report_resp = active_client.get(
            f"/api/v1/reports/courses/{course_id}",
            headers=teacher_headers,
        )
        if report_resp.status_code != 200:
            print(f"FAILED: Get course report failed: {report_resp.status_code} {report_resp.text}")
            return False
        report_data = report_resp.json()
        if report_data.get("course_id") != course_id:
            print(f"FAILED: Report course_id mismatch: {report_data}")
            return False

        csv_resp = active_client.get(
            f"/api/v1/reports/courses/{course_id}/export.csv",
            headers=teacher_headers,
        )
        if csv_resp.status_code != 200:
            print(f"FAILED: Export CSV failed: {csv_resp.status_code} {csv_resp.text}")
            return False

        content_type = csv_resp.headers.get("content-type", "")
        if "text/csv" not in content_type:
            print(f"FAILED: Unexpected CSV Content-Type: {content_type}")
            return False
        if len(csv_resp.content) == 0:
            print("FAILED: CSV export content is empty")
            return False

        csv_len = len(csv_resp.content)
        print(f"  -> PASS: Course report verified, CSV export received ({csv_len} bytes)")

        # ---------------------------------------------------------------------
        # Completion
        # ---------------------------------------------------------------------
        print("\n" + "=" * 80)
        print("SMOKE TEST SUCCESSFUL: All 7 steps completed with 0 errors!")
        print("=" * 80 + "\n")
        return True

    except Exception as exc:
        print(f"\nUNHANDLED EXCEPTION in smoke test: {exc}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        if created_client:
            active_client.close()


if __name__ == "__main__":
    success = run_smoke_test()
    sys.exit(0 if success else 1)
