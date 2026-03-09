from __future__ import annotations

import io
import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api.main import app
from api import runtime
from src.services import org_matcher as org_matcher_module
from src.services import org_storage as org_storage_module

API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")


def _pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer << /Size 4 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )


def _patch_runtime_state(tmp_path: Path, monkeypatch) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)
    runtime._JOB_SUMMARY_CACHE.clear()

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(org_storage_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(org_storage_module, "ORG_FILE", data_dir / "organizations.json")
    monkeypatch.setattr(org_storage_module, "LINKS_FILE", data_dir / "job_org_links.json")
    monkeypatch.setattr(org_storage_module, "_storage_instance", None)
    monkeypatch.setattr(org_matcher_module, "_matcher_instance", None)


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def _create_org(name: str, level: str = "department") -> dict:
    storage = runtime.require_org_storage()
    org = runtime.Organization(
        id=runtime.Organization.generate_id(name, level),
        name=name,
        level=level,
        parent_id=None,
        keywords=[name],
    )
    created = storage.add(org)
    return runtime.to_dict(created)


def test_upload_auto_matches_organization_from_filename(tmp_path: Path, monkeypatch):
    _patch_runtime_state(tmp_path, monkeypatch)
    client = TestClient(app)

    org_id = _create_org("上海市普陀区民政局")["id"]

    upload = client.post(
        "/api/documents/upload",
        headers=_headers(),
        files={
            "file": (
                "上海市普陀区民政局2025年预算公开.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )

    assert upload.status_code == 200
    payload = upload.json()
    assert payload["organization_id"] == org_id
    assert payload["organization_name"] == "上海市普陀区民政局"
    assert payload["organization_match_type"] == "auto"
    assert payload["organization_match_confidence"] >= 0.6

    status = runtime.get_job_status_payload(payload["job_id"])
    assert status["organization_id"] == org_id
    assert status["organization_name"] == "上海市普陀区民政局"
    assert status["organization_match_type"] == "auto"

    link = runtime.require_org_storage().get_job_org(payload["job_id"])
    assert link is not None
    assert link.org_id == org_id
    assert link.match_type == "auto"


def test_manual_association_overrides_auto_match(tmp_path: Path, monkeypatch):
    _patch_runtime_state(tmp_path, monkeypatch)
    client = TestClient(app)

    matched_org_id = _create_org("上海市普陀区民政局")["id"]
    override_org_id = _create_org("上海市普陀区财政局")["id"]

    upload = client.post(
        "/api/documents/upload",
        headers=_headers(),
        files={
            "file": (
                "上海市普陀区民政局2025年预算公开.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]
    assert upload.json()["organization_id"] == matched_org_id
    assert upload.json()["organization_match_type"] == "auto"

    associate = client.post(
        f"/api/jobs/{job_id}/associate",
        json={"org_id": override_org_id},
        headers=_headers(),
    )
    assert associate.status_code == 200
    associate_payload = associate.json()
    assert associate_payload["organization_id"] == override_org_id
    assert associate_payload["organization_name"] == "上海市普陀区财政局"
    assert associate_payload["organization_match_type"] == "manual"
    assert associate_payload["organization_match_confidence"] == 1.0

    status = runtime.get_job_status_payload(job_id)
    assert status["organization_id"] == override_org_id
    assert status["organization_name"] == "上海市普陀区财政局"
    assert status["organization_match_type"] == "manual"
    assert status["organization_match_confidence"] == 1.0

    link = runtime.require_org_storage().get_job_org(job_id)
    assert link is not None
    assert link.org_id == override_org_id
    assert link.match_type == "manual"


def test_job_org_suggestions_returns_current_and_candidates(tmp_path: Path, monkeypatch):
    _patch_runtime_state(tmp_path, monkeypatch)
    client = TestClient(app)

    matched_org_id = _create_org("上海市普陀区民政局")["id"]
    _create_org("上海市普陀区财政局")
    _create_org("上海市普陀区教育局")

    upload = client.post(
        "/api/documents/upload",
        headers=_headers(),
        files={
            "file": (
                "上海市普陀区民政局2025年预算公开.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    suggestions_resp = client.get(
        f"/api/jobs/{job_id}/org-suggestions?top_n=3",
        headers=_headers(),
    )
    assert suggestions_resp.status_code == 200
    payload = suggestions_resp.json()

    assert payload["job_id"] == job_id
    assert payload["current"] is not None
    assert payload["current"]["organization"]["id"] == matched_org_id
    assert payload["current"]["match_type"] == "auto"
    assert payload["current"]["confidence"] >= 0.6
    assert len(payload["suggestions"]) >= 1
    assert payload["suggestions"][0]["organization"]["id"] == matched_org_id
