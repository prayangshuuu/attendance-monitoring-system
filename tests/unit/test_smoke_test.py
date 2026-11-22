from typing import Any
from unittest.mock import MagicMock, patch

import httpx

from scripts.smoke_test import run_smoke_test


def _make_mock_response(
    status_code: int = 200,
    json_data: Any = None,
    text: str = "",
    content: bytes = b"",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    resp.content = content
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    return resp


def test_smoke_test_handles_health_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = _make_mock_response(
        status_code=500, text="Internal Server Error"
    )

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_handles_health_bad_payload() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = _make_mock_response(
        status_code=200, json_data={"status": "error", "database": "error"}
    )

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_handles_login_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = _make_mock_response(
        status_code=200, json_data={"status": "ok", "database": "ok"}
    )
    mock_client.post.return_value = _make_mock_response(status_code=401, text="Unauthorized")

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_handles_login_missing_token() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = _make_mock_response(
        status_code=200, json_data={"status": "ok", "database": "ok"}
    )
    mock_client.post.return_value = _make_mock_response(status_code=200, json_data={})

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_handles_exception_gracefully() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = httpx.ConnectError("Connection refused")

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_default_client_lifecycle() -> None:
    with patch("scripts.smoke_test.httpx.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_instance.get.return_value = _make_mock_response(
            status_code=503, text="Service Unavailable"
        )
        mock_client_cls.return_value = mock_instance

        success = run_smoke_test(client=None, base_url="http://fake-url:9999")
        assert success is False
        mock_instance.close.assert_called_once()


def test_smoke_test_full_happy_path() -> None:
    mock_client = MagicMock(spec=httpx.Client)

    # Sequence of GET calls:
    # 1. /health
    # 2. /api/v1/courses/{c_id}/sessions
    # 3. /api/v1/reports/courses/{c_id}
    # 4. /api/v1/reports/courses/{c_id}/export.csv
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": [{"ends_at": "2026-11-22T10:00:00Z"}]}),
        _make_mock_response(200, json_data={"course_id": "course-123"}),
        _make_mock_response(
            200,
            headers={"content-type": "text/csv; charset=utf-8"},
            content=b"student_id,name,status\nSTU1,Student 01,present\n",
        ),
    ]

    # Sequence of POST calls:
    # 1. admin login
    # 2. create teacher
    # 3. create student
    # 4. create course
    # 5. enroll student
    # 6. teacher login
    # 7. schedule session
    # 8. open session
    # 9. student login
    # 10. student check-in
    # 11. teacher close session
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"], "skipped": []}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(200, json_data={"checkin_code": "SMOKE1"}),
        _make_mock_response(200, json_data={"access_token": "token-student"}),
        _make_mock_response(200, json_data={"status": "present"}),
        _make_mock_response(200, json_data={"status": "closed"}),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is True


def test_smoke_test_with_409_conflicts() -> None:
    mock_client = MagicMock(spec=httpx.Client)

    # Sequence of GET calls:
    # 1. /health
    # 2. list teachers for 409 fallback
    # 3. list students for 409 fallback
    # 4. list courses for 409 fallback
    # 5. /api/v1/courses/{c_id}/sessions
    # 6. /api/v1/reports/courses/{c_id}
    # 7. /api/v1/reports/courses/{c_id}/export.csv
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(
            200, json_data={"items": [{"id": "teacher-409", "email": "teacher1@example.com"}]}
        ),
        _make_mock_response(
            200, json_data={"items": [{"id": "student-409", "email": "student1@example.com"}]}
        ),
        _make_mock_response(200, json_data={"items": [{"id": "course-409", "code": "CS101"}]}),
        _make_mock_response(200, json_data={"items": []}),
        _make_mock_response(200, json_data={"course_id": "course-409"}),
        _make_mock_response(
            200,
            headers={"content-type": "text/csv"},
            content=b"test,data\n1,2",
        ),
    ]

    # Sequence of POST calls:
    # 1. admin login
    # 2. create teacher (returns 409)
    # 3. create student (returns 409)
    # 4. create course (returns 409)
    # 5. enroll student
    # 6. teacher login
    # 7. schedule session
    # 8. open session
    # 9. student login
    # 10. student check-in
    # 11. teacher close session
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(409, text="Teacher already exists"),
        _make_mock_response(409, text="Student already exists"),
        _make_mock_response(409, text="Course already exists"),
        _make_mock_response(200, json_data={"enrolled": ["student-409"], "skipped": []}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-409"}),
        _make_mock_response(200, json_data={"checkin_code": "CODE409"}),
        _make_mock_response(200, json_data={"access_token": "token-student"}),
        _make_mock_response(200, json_data={"status": "present"}),
        _make_mock_response(200, json_data={"status": "closed"}),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is True


def test_smoke_test_step3_teacher_create_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(500, text="Internal Server Error"),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step3_student_create_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(500, text="Student creation failed"),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step3_course_create_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(500, text="Course creation failed"),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step3_enrollment_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(500, text="Enrollment failed"),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step4_teacher_login_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(401, text="Invalid teacher credentials"),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step4_schedule_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(400, text="Invalid schedule"),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step4_open_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(500, text="Failed to open session"),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step4_open_missing_code() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(200, json_data={}),  # missing checkin_code
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step5_student_login_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(200, json_data={"checkin_code": "LIVE1"}),
        _make_mock_response(401, text="Invalid student login"),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step5_checkin_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(200, json_data={"checkin_code": "LIVE1"}),
        _make_mock_response(200, json_data={"access_token": "token-student"}),
        _make_mock_response(400, text="Checkin expired"),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step6_close_session_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(200, json_data={"checkin_code": "LIVE1"}),
        _make_mock_response(200, json_data={"access_token": "token-student"}),
        _make_mock_response(200, json_data={"status": "present"}),
        _make_mock_response(500, text="Failed to close"),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step6_close_session_wrong_status() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(200, json_data={"checkin_code": "LIVE1"}),
        _make_mock_response(200, json_data={"access_token": "token-student"}),
        _make_mock_response(200, json_data={"status": "present"}),
        _make_mock_response(200, json_data={"status": "open"}),  # expected "closed"
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step7_report_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
        _make_mock_response(500, text="Failed to get report"),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(200, json_data={"checkin_code": "LIVE1"}),
        _make_mock_response(200, json_data={"access_token": "token-student"}),
        _make_mock_response(200, json_data={"status": "present"}),
        _make_mock_response(200, json_data={"status": "closed"}),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step7_report_mismatch() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
        _make_mock_response(200, json_data={"course_id": "different-course-id"}),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(200, json_data={"checkin_code": "LIVE1"}),
        _make_mock_response(200, json_data={"access_token": "token-student"}),
        _make_mock_response(200, json_data={"status": "present"}),
        _make_mock_response(200, json_data={"status": "closed"}),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step7_csv_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
        _make_mock_response(200, json_data={"course_id": "course-123"}),
        _make_mock_response(500, text="Failed to export CSV"),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(200, json_data={"checkin_code": "LIVE1"}),
        _make_mock_response(200, json_data={"access_token": "token-student"}),
        _make_mock_response(200, json_data={"status": "present"}),
        _make_mock_response(200, json_data={"status": "closed"}),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step7_csv_invalid_content_type() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
        _make_mock_response(200, json_data={"course_id": "course-123"}),
        _make_mock_response(200, headers={"content-type": "application/json"}, content=b"{}"),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(200, json_data={"checkin_code": "LIVE1"}),
        _make_mock_response(200, json_data={"access_token": "token-student"}),
        _make_mock_response(200, json_data={"status": "present"}),
        _make_mock_response(200, json_data={"status": "closed"}),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_step7_csv_empty_content() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [
        _make_mock_response(200, json_data={"status": "ok", "database": "ok"}),
        _make_mock_response(200, json_data={"items": []}),
        _make_mock_response(200, json_data={"course_id": "course-123"}),
        _make_mock_response(200, headers={"content-type": "text/csv"}, content=b""),
    ]
    mock_client.post.side_effect = [
        _make_mock_response(200, json_data={"access_token": "token-admin"}),
        _make_mock_response(201, json_data={"id": "teacher-123"}),
        _make_mock_response(201, json_data={"id": "student-123"}),
        _make_mock_response(201, json_data={"id": "course-123"}),
        _make_mock_response(200, json_data={"enrolled": ["student-123"]}),
        _make_mock_response(200, json_data={"access_token": "token-teacher"}),
        _make_mock_response(201, json_data={"id": "sess-123"}),
        _make_mock_response(200, json_data={"checkin_code": "LIVE1"}),
        _make_mock_response(200, json_data={"access_token": "token-student"}),
        _make_mock_response(200, json_data={"status": "present"}),
        _make_mock_response(200, json_data={"status": "closed"}),
    ]

    success = run_smoke_test(client=mock_client)
    assert success is False
