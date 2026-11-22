from unittest.mock import MagicMock, patch

import httpx

from scripts.smoke_test import run_smoke_test


def test_smoke_test_handles_health_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_client.get.return_value = mock_resp

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_handles_health_bad_payload() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "error", "database": "error"}
    mock_client.get.return_value = mock_resp

    success = run_smoke_test(client=mock_client)
    assert success is False


def test_smoke_test_handles_login_failure() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    # Health check succeeds
    health_resp = MagicMock()
    health_resp.status_code = 200
    health_resp.json.return_value = {"status": "ok", "database": "ok"}
    mock_client.get.return_value = health_resp

    # Login fails
    login_resp = MagicMock()
    login_resp.status_code = 401
    login_resp.text = "Unauthorized"
    mock_client.post.return_value = login_resp

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
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = "Service Unavailable"
        mock_instance.get.return_value = mock_resp
        mock_client_cls.return_value = mock_instance

        success = run_smoke_test(client=None, base_url="http://fake-url:9999")
        assert success is False
        mock_instance.close.assert_called_once()
