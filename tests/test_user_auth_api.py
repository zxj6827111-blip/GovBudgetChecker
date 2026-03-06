import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api.main import app
from src.services.user_store import reset_user_store

API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")
ADMIN_PASSWORD = "admin123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    user_file = tmp_path / "users.json"
    monkeypatch.setenv("USER_FILE", str(user_file))
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", ADMIN_PASSWORD)
    reset_user_store()
    with TestClient(app) as test_client:
        yield test_client
    reset_user_store()


def _headers(session_token: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {"X-API-Key": API_KEY}
    if session_token:
        headers["X-Session-Token"] = session_token
    return headers


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return str(payload["token"])


def test_admin_can_create_user_and_user_can_login(client: TestClient):
    admin_token = _login(client, "admin", ADMIN_PASSWORD)

    create_response = client.post(
        "/api/users",
        headers=_headers(admin_token),
        json={"username": "auditor_01", "password": "AuditPass123"},
    )
    assert create_response.status_code == 200
    assert create_response.json()["username"] == "auditor_01"

    login_response = client.post(
        "/api/auth/login",
        json={"username": "auditor_01", "password": "AuditPass123"},
        headers=_headers(),
    )
    assert login_response.status_code == 200
    assert login_response.json()["user"]["is_admin"] is False


def test_non_admin_cannot_create_user(client: TestClient):
    admin_token = _login(client, "admin", ADMIN_PASSWORD)
    create_user = client.post(
        "/api/users",
        headers=_headers(admin_token),
        json={"username": "viewer_01", "password": "ViewerPass123"},
    )
    assert create_user.status_code == 200

    user_token = _login(client, "viewer_01", "ViewerPass123")
    forbidden = client.post(
        "/api/users",
        headers=_headers(user_token),
        json={"username": "blocked_create", "password": "BlockedPass123"},
    )
    assert forbidden.status_code == 403
    assert "admin" in forbidden.json()["detail"]


def test_logout_invalidates_session(client: TestClient):
    token = _login(client, "admin", ADMIN_PASSWORD)

    me_response = client.get("/api/auth/me", headers=_headers(token))
    assert me_response.status_code == 200
    assert me_response.json()["user"]["username"] == "admin"

    logout_response = client.post(
        "/api/auth/logout",
        headers=_headers(token),
    )
    assert logout_response.status_code == 200
    assert logout_response.json()["success"] is True

    expired_response = client.get("/api/auth/me", headers=_headers(token))
    assert expired_response.status_code == 401


def test_password_reset_flow(client: TestClient):
    admin_token = _login(client, "admin", ADMIN_PASSWORD)

    create_response = client.post(
        "/api/users",
        headers=_headers(admin_token),
        json={"username": "staff_01", "password": "OldPass123"},
    )
    assert create_response.status_code == 200

    wrong_login = client.post(
        "/api/auth/login",
        json={"username": "staff_01", "password": "WrongPass123"},
        headers=_headers(),
    )
    assert wrong_login.status_code == 401

    reset_response = client.patch(
        "/api/users/staff_01",
        headers=_headers(admin_token),
        json={"password": "NewPass123"},
    )
    assert reset_response.status_code == 200

    old_password_login = client.post(
        "/api/auth/login",
        json={"username": "staff_01", "password": "OldPass123"},
        headers=_headers(),
    )
    assert old_password_login.status_code == 401

    new_password_login = client.post(
        "/api/auth/login",
        json={"username": "staff_01", "password": "NewPass123"},
        headers=_headers(),
    )
    assert new_password_login.status_code == 200


def test_change_own_password_requires_old_password(client: TestClient):
    token = _login(client, "admin", ADMIN_PASSWORD)

    wrong_old = client.post(
        "/api/auth/change-password",
        json={"old_password": "BadOld123", "new_password": "AdminNext123"},
        headers=_headers(token),
    )
    assert wrong_old.status_code == 400

    success_change = client.post(
        "/api/auth/change-password",
        json={"old_password": ADMIN_PASSWORD, "new_password": "AdminNext123"},
        headers=_headers(token),
    )
    assert success_change.status_code == 200

    old_login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
        headers=_headers(),
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminNext123"},
        headers=_headers(),
    )
    assert new_login.status_code == 200
