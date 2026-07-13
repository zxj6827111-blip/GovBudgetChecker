from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api import runtime
from api.main import app
from src.services import org_matcher as org_matcher_module
from src.services import org_storage as org_storage_module
from src.services.user_store import reset_user_store


API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")
ADMIN_PASSWORD = "AdminPass123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("USER_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("JOB_QUEUE_ENABLED", "false")
    monkeypatch.setenv("AUDIT_LOG_PATH", str(data_dir / "audit.jsonl"))
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)
    monkeypatch.setattr(org_storage_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(org_storage_module, "ORG_FILE", data_dir / "organizations.json")
    monkeypatch.setattr(org_storage_module, "LINKS_FILE", data_dir / "job_org_links.json")
    monkeypatch.setattr(org_storage_module, "_storage_instance", None)
    monkeypatch.setattr(org_matcher_module, "_matcher_instance", None)
    reset_user_store()
    with TestClient(app) as test_client:
        yield test_client
    reset_user_store()


def _headers(session_token: str | None = None) -> dict[str, str]:
    headers = {"X-API-Key": API_KEY}
    if session_token:
        headers["X-Session-Token"] = session_token
    return headers


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login",
        headers=_headers(),
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


def _create_org(name: str) -> str:
    storage = runtime.require_org_storage()
    org = runtime.Organization(
        id=runtime.Organization.generate_id(name, "unit", None),
        name=name,
        level="unit",
        keywords=[name],
    )
    return str(storage.add(org).id)


def _create_job(job_id: str, owner: str, org_id: str, issue_id: str) -> None:
    job_dir = runtime.UPLOAD_ROOT / job_id
    job_dir.mkdir(parents=True)
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "job_id": job_id,
            "status": "done",
            "created_by": owner,
            "organization_id": org_id,
            "organization_name": f"organization-{org_id}",
            "result": {
                "issues": {
                    "all": [
                        {
                            "id": issue_id,
                            "title": f"trusted-title-{issue_id}",
                            "severity": "high",
                            "page": 3,
                        }
                    ]
                }
            },
        },
    )
    runtime.require_org_storage().link_job(job_id, org_id, match_type="manual")


def _create_scoped_user(client: TestClient, username: str, org_id: str) -> str:
    admin_token = _login(client, "admin", ADMIN_PASSWORD)
    response = client.post(
        "/api/users",
        headers=_headers(admin_token),
        json={
            "username": username,
            "password": "UserPass123",
            "organization_ids": [org_id],
        },
    )
    assert response.status_code == 200, response.text
    return _login(client, username, "UserPass123")


def test_issue_update_enforces_org_scope_and_uses_server_metadata(client: TestClient):
    org_a = _create_org("unit-a")
    org_b = _create_org("unit-b")
    _create_job("job-a", "seed", org_a, "issue-a")
    _create_job("job-b", "seed", org_b, "issue-b")
    user_token = _create_scoped_user(client, "unit_a_user", org_a)

    denied = client.post(
        "/api/workflow",
        headers=_headers(user_token),
        json={
            "action": "update_issue",
            "job_id": "job-b",
            "issue_id": "issue-b",
            "status": "confirmed",
        },
    )
    assert denied.status_code == 403

    updated = client.post(
        "/api/workflow",
        headers=_headers(user_token),
        json={
            "action": "update_issue",
            "job_id": "job-a",
            "issue_id": "issue-a",
            "status": "confirmed",
            "organization_id": org_b,
            "title": "client-controlled title",
        },
    )
    assert updated.status_code == 200, updated.text
    record = updated.json()["issues"]["job-a::issue-a"]
    assert record["organization_id"] == org_a
    assert record["title"] == "trusted-title-issue-a"


def test_remediation_package_rejects_cross_org_jobs_and_is_not_leaked(client: TestClient):
    org_a = _create_org("unit-a")
    org_b = _create_org("unit-b")
    _create_job("job-a", "seed", org_a, "issue-a")
    _create_job("job-b", "seed", org_b, "issue-b")
    user_token = _create_scoped_user(client, "unit_a_user", org_a)

    denied = client.post(
        "/api/workflow",
        headers=_headers(user_token),
        json={
            "action": "create_package",
            "name": "cross-org",
            "job_ids": ["job-a", "job-b"],
            "issue_keys": ["job-a::issue-a", "job-b::issue-b"],
        },
    )
    assert denied.status_code == 403

    admin_token = _login(client, "admin", ADMIN_PASSWORD)
    created = client.post(
        "/api/workflow",
        headers=_headers(admin_token),
        json={
            "action": "create_package",
            "name": "admin-cross-org",
            "job_ids": ["job-a", "job-b"],
            "issue_keys": ["job-a::issue-a", "job-b::issue-b"],
        },
    )
    assert created.status_code == 200, created.text

    visible = client.get("/api/workflow", headers=_headers(user_token))
    assert visible.status_code == 200
    assert visible.json()["packages"] == []
    assert set(visible.json()["issues"]) == {"job-a::issue-a"}
