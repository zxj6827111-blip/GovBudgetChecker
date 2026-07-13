"""Tests for P1 write-endpoint require_login enforcement."""

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api import runtime
from api.main import app
from api.routes import analyze as analyze_mod
from api.routes import jobs as jobs_mod
from src.services.user_store import reset_user_store

API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")
ADMIN_PASSWORD = "AdminPass123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    user_file = tmp_path / "users.json"
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setenv("USER_FILE", str(user_file))
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("JOB_QUEUE_ENABLED", "false")
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)
    reset_user_store()
    with TestClient(app) as test_client:
        yield test_client
    reset_user_store()


def _headers(session_token: str | None = None) -> dict[str, str]:
    h: dict[str, str] = {"X-API-Key": API_KEY}
    if session_token:
        h["X-Session-Token"] = session_token
    return h


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["token"])


# ---------------------------------------------------------------------------
# Helpers: create a regular user via admin
# ---------------------------------------------------------------------------


def _create_user(client: TestClient, admin_token: str, username: str, password: str) -> None:
    resp = client.post(
        "/api/users",
        headers=_headers(admin_token),
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text


def _create_job(upload_root: Path, job_id: str = "job-read-test") -> Path:
    job_dir = upload_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "test.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "job_id": job_id,
            "status": "done",
            "result": {
                "issues": {
                    "all": [{"id": "iss-1", "title": "test issue"}],
                    "error": [],
                    "warn": [],
                    "info": [],
                }
            },
        },
    )
    return job_dir


def _create_owned_job(upload_root: Path, job_id: str, username: str) -> Path:
    job_dir = _create_job(upload_root, job_id)
    payload = runtime.read_json_file(job_dir / "status.json", default={})
    payload["created_by"] = username
    runtime.write_json_file(job_dir / "status.json", payload)
    return job_dir


# ---------------------------------------------------------------------------
# No-session → 401 (TESTING=false to bypass the test shortcut)
# ---------------------------------------------------------------------------


class TestWriteEndpointsRequireSession:
    """Write endpoints must reject requests without a valid session."""

    @pytest.fixture(autouse=True)
    def _disable_testing_bypass(self, monkeypatch):
        monkeypatch.setenv("TESTING", "false")

    def test_analyze_no_session_returns_401(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(
            runtime, "start_analysis", AsyncMock(return_value={"status": "ok"})
        )
        resp = client.post(
            "/api/analyze/job-xxx",
            headers=_headers(),
            json={},
        )
        assert resp.status_code == 401

    def test_associate_no_session_returns_401(self, client: TestClient):
        resp = client.post(
            "/api/jobs/job-xxx/associate",
            headers=_headers(),
            json={"org_id": "org-1"},
        )
        assert resp.status_code == 401

    def test_reanalyze_no_session_returns_401(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(
            runtime, "reanalyze_job", AsyncMock(return_value={"status": "ok"})
        )
        resp = client.post(
            "/api/jobs/job-xxx/reanalyze",
            headers=_headers(),
            json={},
        )
        assert resp.status_code == 401

    def test_ignore_issue_no_session_returns_401(self, client: TestClient):
        resp = client.post(
            "/api/jobs/job-xxx/issues/ignore",
            headers=_headers(),
            json={"issue_id": "iss-1"},
        )
        assert resp.status_code == 401

    def test_document_run_no_session_returns_401(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(
            runtime, "start_analysis", AsyncMock(return_value={"status": "ok"})
        )
        resp = client.post(
            "/api/documents/version-xxx/run",
            headers=_headers(),
            json={},
        )
        assert resp.status_code == 401

    def test_document_preflight_no_session_returns_401(self, client: TestClient):
        resp = client.post(
            "/api/documents/preflight",
            headers=_headers(),
            files={"file": ("sample.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
        )
        assert resp.status_code == 401

    def test_document_upload_no_session_returns_401(self, client: TestClient):
        resp = client.post(
            "/api/documents/upload",
            headers=_headers(),
            files={"file": ("sample.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
        )
        assert resp.status_code == 401

    def test_legacy_upload_no_session_returns_401(self, client: TestClient):
        resp = client.post(
            "/upload",
            headers=_headers(),
            files={"file": ("sample.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
        )
        assert resp.status_code == 401


class TestWriteEndpointsInvalidSession:
    """Write endpoints must reject requests with an invalid session token."""

    @pytest.fixture(autouse=True)
    def _disable_testing_bypass(self, monkeypatch):
        monkeypatch.setenv("TESTING", "false")

    def test_analyze_invalid_token_returns_401(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(
            runtime, "start_analysis", AsyncMock(return_value={"status": "ok"})
        )
        resp = client.post(
            "/api/analyze/job-xxx",
            headers=_headers("bogus-token-xyz"),
            json={},
        )
        assert resp.status_code == 401

    def test_associate_invalid_token_returns_401(self, client: TestClient):
        resp = client.post(
            "/api/jobs/job-xxx/associate",
            headers=_headers("bogus-token-xyz"),
            json={"org_id": "org-1"},
        )
        assert resp.status_code == 401

    def test_reanalyze_invalid_token_returns_401(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(
            runtime, "reanalyze_job", AsyncMock(return_value={"status": "ok"})
        )
        resp = client.post(
            "/api/jobs/job-xxx/reanalyze",
            headers=_headers("bogus-token-xyz"),
            json={},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Valid session → can access (does NOT require admin)
# ---------------------------------------------------------------------------


class TestSensitiveReadEndpointsRequireSession:
    """Sensitive read endpoints must reject requests without a valid session."""

    @pytest.fixture(autouse=True)
    def _disable_testing_bypass(self, monkeypatch):
        monkeypatch.setenv("TESTING", "false")

    def test_jobs_list_no_session_returns_401(self, client: TestClient):
        resp = client.get("/api/jobs", headers=_headers())
        assert resp.status_code == 401

    def test_job_status_no_session_returns_401(self, client: TestClient):
        resp = client.get("/api/jobs/job-read-test/status", headers=_headers())
        assert resp.status_code == 401

    def test_legacy_job_status_no_session_returns_401(self, client: TestClient):
        resp = client.get("/jobs/job-read-test/status", headers=_headers())
        assert resp.status_code == 401

    def test_job_detail_no_session_returns_401(self, client: TestClient):
        resp = client.get("/api/jobs/job-read-test", headers=_headers())
        assert resp.status_code == 401

    def test_job_review_no_session_returns_401(self, client: TestClient):
        resp = client.get("/api/jobs/job-read-test/review", headers=_headers())
        assert resp.status_code == 401

    def test_job_structured_ingest_no_session_returns_401(self, client: TestClient):
        resp = client.get("/api/jobs/job-read-test/structured-ingest", headers=_headers())
        assert resp.status_code == 401

    def test_job_org_suggestions_no_session_returns_401(self, client: TestClient):
        resp = client.get("/api/jobs/job-read-test/org-suggestions", headers=_headers())
        assert resp.status_code == 401

    def test_report_download_no_session_returns_401(self, client: TestClient):
        resp = client.get(
            "/api/reports/download?job_id=job-read-test&format=json",
            headers=_headers(),
        )
        assert resp.status_code == 401

    def test_report_batch_download_no_session_returns_401(self, client: TestClient):
        resp = client.post(
            "/api/reports/download-batch",
            headers=_headers(),
            json={"job_ids": ["job-read-test"]},
        )
        assert resp.status_code == 401

    def test_source_pdf_no_session_returns_401(self, client: TestClient):
        resp = client.get("/api/files/job-read-test/source", headers=_headers())
        assert resp.status_code == 401

    def test_source_preview_no_session_returns_401(self, client: TestClient):
        resp = client.get("/api/files/job-read-test/preview", headers=_headers())
        assert resp.status_code == 401


class TestWriteEndpointsWithValidSession:
    """Regular (non-admin) user can access these write endpoints."""

    @pytest.fixture(autouse=True)
    def _enable_testing(self, monkeypatch):
        monkeypatch.setenv("TESTING", "true")

    def test_regular_user_can_trigger_analyze(
        self, client: TestClient, monkeypatch
    ):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "analyst", "AnalystPass1")
        user_token = _login(client, "analyst", "AnalystPass1")
        _create_owned_job(runtime.UPLOAD_ROOT, "j1", "analyst")

        mock_start = AsyncMock(return_value={"job_id": "j1", "status": "started"})
        monkeypatch.setattr(runtime, "start_analysis", mock_start)

        resp = client.post(
            "/api/analyze/j1",
            headers=_headers(user_token),
            json={"mode": "dual"},
        )
        assert resp.status_code == 200
        mock_start.assert_called_once()

    def test_regular_user_can_associate(
        self, client: TestClient, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "editor", "EditorPass1")
        user_token = _login(client, "editor", "EditorPass1")

        dept_name = f"测试部门-{os.urandom(4).hex()}"
        org_resp = client.post(
            "/api/organizations",
            headers=_headers(admin_token),
            json={"name": dept_name, "level": "department"},
        )
        org_id = org_resp.json()["id"]
        scope_response = client.patch(
            "/api/users/editor",
            headers=_headers(admin_token),
            json={"organization_ids": [org_id]},
        )
        assert scope_response.status_code == 200
        _create_owned_job(tmp_path, "job-assoc-test", "editor")

        resp = client.post(
            "/api/jobs/job-assoc-test/associate",
            headers=_headers(user_token),
            json={"org_id": org_id},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_regular_user_can_reanalyze(
        self, client: TestClient, monkeypatch
    ):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "reviewer", "ReviewerPass1")
        user_token = _login(client, "reviewer", "ReviewerPass1")
        _create_owned_job(runtime.UPLOAD_ROOT, "j2", "reviewer")

        mock_reanalyze = AsyncMock(
            return_value={"job_id": "j2", "status": "queued"}
        )
        monkeypatch.setattr(runtime, "reanalyze_job", mock_reanalyze)

        resp = client.post(
            "/api/jobs/j2/reanalyze",
            headers=_headers(user_token),
            json={},
        )
        assert resp.status_code == 200

    def test_regular_user_can_ignore_issue(
        self, client: TestClient, tmp_path, monkeypatch
    ):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "qa", "QaPass1")
        user_token = _login(client, "qa", "QaPass1")

        _create_owned_job(tmp_path, "job-ignore-test", "qa")
        monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
        resp = client.post(
            "/api/jobs/job-ignore-test/issues/ignore",
            headers=_headers(user_token),
            json={"issue_id": "iss-1"},
        )
        assert resp.status_code == 200

    def test_regular_user_can_read_sensitive_job_data(self, client: TestClient):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "reader", "ReaderPass1")
        user_token = _login(client, "reader", "ReaderPass1")
        _create_owned_job(runtime.UPLOAD_ROOT, "job-read-test", "reader")

        jobs = client.get("/api/jobs", headers=_headers(user_token))
        assert jobs.status_code == 200

        detail = client.get("/api/jobs/job-read-test", headers=_headers(user_token))
        assert detail.status_code == 200
        assert detail.json()["job_id"] == "job-read-test"

        report = client.get(
            "/api/reports/download?job_id=job-read-test&format=json",
            headers=_headers(user_token),
        )
        assert report.status_code == 200
        assert report.json()["job_id"] == "job-read-test"

        source = client.get("/api/files/job-read-test/source", headers=_headers(user_token))
        assert source.status_code == 200
        assert source.headers["content-type"].startswith("application/pdf")


class TestJobOwnerIsolation:
    """Regular users must not access jobs owned by another user."""

    def test_jobs_list_hides_other_users_owned_jobs(self, client: TestClient):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "alice", "AlicePass1")
        _create_user(client, admin_token, "bob", "BobPass1")
        alice_token = _login(client, "alice", "AlicePass1")
        bob_token = _login(client, "bob", "BobPass1")
        _create_owned_job(runtime.UPLOAD_ROOT, "alice-job", "alice")
        _create_owned_job(runtime.UPLOAD_ROOT, "bob-job", "bob")

        alice_jobs = client.get("/api/jobs", headers=_headers(alice_token)).json()
        bob_jobs = client.get("/api/jobs", headers=_headers(bob_token)).json()
        admin_jobs = client.get("/api/jobs", headers=_headers(admin_token)).json()

        assert {item["job_id"] for item in alice_jobs} == {"alice-job"}
        assert {item["job_id"] for item in bob_jobs} == {"bob-job"}
        assert {"alice-job", "bob-job"}.issubset({item["job_id"] for item in admin_jobs})

    def test_regular_user_cannot_read_other_users_job_detail(self, client: TestClient):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "owner", "OwnerPass1")
        _create_user(client, admin_token, "intruder", "IntruderPass1")
        intruder_token = _login(client, "intruder", "IntruderPass1")
        _create_owned_job(runtime.UPLOAD_ROOT, "owned-job", "owner")

        resp = client.get("/api/jobs/owned-job", headers=_headers(intruder_token))

        assert resp.status_code == 403

    def test_regular_user_cannot_download_other_users_report_or_source(
        self,
        client: TestClient,
    ):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "owner2", "Owner2Pass1")
        _create_user(client, admin_token, "intruder2", "Intruder2Pass1")
        intruder_token = _login(client, "intruder2", "Intruder2Pass1")
        _create_owned_job(runtime.UPLOAD_ROOT, "owned-download-job", "owner2")

        report = client.get(
            "/api/reports/download?job_id=owned-download-job&format=json",
            headers=_headers(intruder_token),
        )
        source = client.get(
            "/api/files/owned-download-job/source",
            headers=_headers(intruder_token),
        )

        assert report.status_code == 403
        assert source.status_code == 403

    def test_regular_user_cannot_batch_download_other_users_report(self, client: TestClient):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "owner_batch", "OwnerBatch1")
        _create_user(client, admin_token, "intruder_batch", "IntruderBatch1")
        intruder_token = _login(client, "intruder_batch", "IntruderBatch1")
        _create_owned_job(runtime.UPLOAD_ROOT, "owned-batch-job", "owner_batch")

        resp = client.post(
            "/api/reports/download-batch",
            headers=_headers(intruder_token),
            json={"job_ids": ["owned-batch-job"]},
        )

        assert resp.status_code == 403

    def test_regular_user_cannot_mutate_other_users_job(self, client: TestClient):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "owner3", "Owner3Pass1")
        _create_user(client, admin_token, "intruder3", "Intruder3Pass1")
        intruder_token = _login(client, "intruder3", "Intruder3Pass1")
        _create_owned_job(runtime.UPLOAD_ROOT, "owned-mutate-job", "owner3")

        reanalyze = client.post(
            "/api/jobs/owned-mutate-job/reanalyze",
            headers=_headers(intruder_token),
            json={},
        )
        ignore = client.post(
            "/api/jobs/owned-mutate-job/issues/ignore",
            headers=_headers(intruder_token),
            json={"issue_id": "iss-1"},
        )

        assert reanalyze.status_code == 403
        assert ignore.status_code == 403

    def test_legacy_unowned_jobs_are_admin_only(self, client: TestClient):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "legacy_reader", "LegacyPass1")
        user_token = _login(client, "legacy_reader", "LegacyPass1")
        _create_job(runtime.UPLOAD_ROOT, "legacy-job")

        resp = client.get("/api/jobs/legacy-job", headers=_headers(user_token))

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Admin-only endpoints still require admin (not just login)
# ---------------------------------------------------------------------------


class TestAdminEndpointsStillProtected:
    """Regular user must NOT access admin-only endpoints."""

    def test_regular_user_cannot_batch_delete(
        self, client: TestClient
    ):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "viewer", "ViewerPass1")
        user_token = _login(client, "viewer", "ViewerPass1")

        resp = client.post(
            "/api/jobs/batch-delete",
            headers=_headers(user_token),
            json={"job_ids": ["j1"]},
        )
        assert resp.status_code == 403

    def test_regular_user_cannot_manage_users(self, client: TestClient):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "viewer2", "Viewer2Pass1")
        user_token = _login(client, "viewer2", "Viewer2Pass1")

        resp = client.post(
            "/api/users",
            headers=_headers(user_token),
            json={"username": "hacker", "password": "HackPass1"},
        )
        assert resp.status_code == 403

    def test_regular_user_cannot_manage_organizations(
        self, client: TestClient
    ):
        admin_token = _login(client, "admin", ADMIN_PASSWORD)
        _create_user(client, admin_token, "viewer3", "Viewer3Pass1")
        user_token = _login(client, "viewer3", "Viewer3Pass1")

        resp = client.post(
            "/api/organizations",
            headers=_headers(user_token),
            json={"name": "非法部门", "level": "department"},
        )
        assert resp.status_code == 403
