import io
import os

from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api.main import app


def _pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer << /Size 4 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )


def test_api_role_keeps_job_queued_without_inline_fallback(monkeypatch):
    monkeypatch.setenv("JOB_QUEUE_ENABLED", "true")
    monkeypatch.setenv("JOB_QUEUE_ROLE", "api")
    monkeypatch.setenv("JOB_QUEUE_INLINE_FALLBACK", "false")

    client = TestClient(app)

    upload = client.post(
        "/api/documents/upload",
        files={"file": ("split_mode.pdf", io.BytesIO(_pdf_bytes()), "application/pdf")},
    )
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    run = client.post(f"/api/documents/{job_id}/run", json={"mode": "dual"})
    assert run.status_code == 200
    payload = run.json()
    assert payload["status"] == "started"
    assert payload["dispatch"] == "queued_waiting_worker"

    status = client.get(f"/api/jobs/{job_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "queued"


def test_ready_endpoint_does_not_require_local_queue_in_api_role(monkeypatch):
    monkeypatch.setenv("JOB_QUEUE_ENABLED", "true")
    monkeypatch.setenv("JOB_QUEUE_ROLE", "api")

    client = TestClient(app)
    ready = client.get("/ready")
    assert ready.status_code == 200
    payload = ready.json()
    assert payload["details"]["queue_role"] == "api"
    assert payload["details"]["local_queue_required"] is False
    assert payload["checks"]["job_queue_started"] is True

