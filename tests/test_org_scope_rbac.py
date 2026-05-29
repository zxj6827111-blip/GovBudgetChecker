from __future__ import annotations

import os
from pathlib import Path

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
    user_file = tmp_path / "users.json"
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("USER_FILE", str(user_file))
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("JOB_QUEUE_ENABLED", "false")
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
    return str(response.json()["token"])


def _create_org(name: str, level: str, parent_id: str | None = None) -> str:
    storage = runtime.require_org_storage()
    org = runtime.Organization(
        id=runtime.Organization.generate_id(name, level, parent_id),
        name=name,
        level=level,
        parent_id=parent_id,
        keywords=[name],
    )
    return str(storage.add(org).id)


def _create_job(job_id: str, *, owner: str, org_id: str) -> None:
    job_dir = runtime.UPLOAD_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "job_id": job_id,
            "status": "done",
            "filename": "source.pdf",
            "organization_id": org_id,
            "created_by": owner,
            "result": {
                "issues": {
                    "all": [{"id": f"{job_id}-issue", "title": "issue"}],
                    "error": [],
                    "warn": [],
                    "info": [],
                }
            },
        },
    )
    runtime.require_org_storage().link_job(job_id, org_id, match_type="manual")


def _setup_org_tree() -> dict[str, str]:
    dept_a = _create_org("部门A", "department")
    unit_a1 = _create_org("单位A1", "unit", dept_a)
    unit_a2 = _create_org("单位A2", "unit", dept_a)
    dept_b = _create_org("部门B", "department")
    unit_b1 = _create_org("单位B1", "unit", dept_b)
    return {
        "dept_a": dept_a,
        "unit_a1": unit_a1,
        "unit_a2": unit_a2,
        "dept_b": dept_b,
        "unit_b1": unit_b1,
    }


def test_admin_can_assign_user_organization_scope(client: TestClient):
    orgs = _setup_org_tree()
    admin_token = _login(client, "admin", ADMIN_PASSWORD)

    created = client.post(
        "/api/users",
        headers=_headers(admin_token),
        json={
            "username": "dept_viewer",
            "password": "DeptPass123",
            "organization_ids": [orgs["dept_a"]],
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["organization_ids"] == [orgs["dept_a"]]

    updated = client.patch(
        "/api/users/dept_viewer",
        headers=_headers(admin_token),
        json={"organization_ids": [orgs["unit_b1"]]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["organization_ids"] == [orgs["unit_b1"]]


def test_unknown_organization_scope_is_rejected(client: TestClient):
    admin_token = _login(client, "admin", ADMIN_PASSWORD)

    response = client.post(
        "/api/users",
        headers=_headers(admin_token),
        json={
            "username": "bad_scope",
            "password": "BadScope123",
            "organization_ids": ["missing-org"],
        },
    )

    assert response.status_code == 400
    assert "unknown organization_ids" in response.json()["detail"]


def test_department_scope_can_access_child_unit_jobs(client: TestClient):
    orgs = _setup_org_tree()
    _create_job("a1-job", owner="uploader", org_id=orgs["unit_a1"])
    _create_job("b1-job", owner="uploader", org_id=orgs["unit_b1"])

    admin_token = _login(client, "admin", ADMIN_PASSWORD)
    client.post(
        "/api/users",
        headers=_headers(admin_token),
        json={
            "username": "dept_a_reader",
            "password": "DeptAReader1",
            "organization_ids": [orgs["dept_a"]],
        },
    )
    token = _login(client, "dept_a_reader", "DeptAReader1")

    allowed = client.get("/api/jobs/a1-job", headers=_headers(token))
    denied = client.get("/api/jobs/b1-job", headers=_headers(token))
    jobs = client.get(
        f"/api/organizations/{orgs['dept_a']}/jobs?include_children=true",
        headers=_headers(token),
    )

    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert jobs.status_code == 200
    assert {item["job_id"] for item in jobs.json()["jobs"]} == {"a1-job"}


def test_unit_scope_does_not_leak_sibling_unit_jobs_or_stats(client: TestClient):
    orgs = _setup_org_tree()
    _create_job("a1-job", owner="uploader", org_id=orgs["unit_a1"])
    _create_job("a2-job", owner="uploader", org_id=orgs["unit_a2"])

    admin_token = _login(client, "admin", ADMIN_PASSWORD)
    client.post(
        "/api/users",
        headers=_headers(admin_token),
        json={
            "username": "unit_reader",
            "password": "UnitReader1",
            "organization_ids": [orgs["unit_a1"]],
        },
    )
    token = _login(client, "unit_reader", "UnitReader1")

    org_tree = client.get("/api/organizations", headers=_headers(token))
    sibling_jobs = client.get(
        f"/api/organizations/{orgs['unit_a2']}/jobs",
        headers=_headers(token),
    )

    assert org_tree.status_code == 200
    assert sibling_jobs.status_code == 403
    root = org_tree.json()["tree"][0]
    assert root["id"] == orgs["dept_a"]
    assert root["job_count"] == 1
    assert [child["id"] for child in root["children"]] == [orgs["unit_a1"]]
    assert root["children"][0]["job_count"] == 1
