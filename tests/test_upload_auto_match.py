from __future__ import annotations

import io
import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api.main import app
from api import runtime
from api.routes import upload as upload_route
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


def _create_org(
    name: str,
    level: str = "department",
    parent_id: str | None = None,
    keywords: list[str] | None = None,
) -> dict:
    storage = runtime.require_org_storage()
    org = runtime.Organization(
        id=runtime.Organization.generate_id(name, level),
        name=name,
        level=level,
        parent_id=parent_id,
        keywords=keywords or [name],
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


def test_manual_association_moves_job_between_organization_job_lists(
    tmp_path: Path, monkeypatch
):
    _patch_runtime_state(tmp_path, monkeypatch)
    client = TestClient(app)

    original_org_id = _create_org("上海市普陀区民政局")["id"]
    target_org_id = _create_org("上海市普陀区财政局")["id"]

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
    assert upload.json()["organization_id"] == original_org_id

    original_jobs_before = client.get(f"/api/organizations/{original_org_id}/jobs")
    assert original_jobs_before.status_code == 200
    assert any(item["job_id"] == job_id for item in original_jobs_before.json()["jobs"])

    target_jobs_before = client.get(f"/api/organizations/{target_org_id}/jobs")
    assert target_jobs_before.status_code == 200
    assert all(item["job_id"] != job_id for item in target_jobs_before.json()["jobs"])

    associate = client.post(
        f"/api/jobs/{job_id}/associate",
        json={"org_id": target_org_id},
        headers=_headers(),
    )
    assert associate.status_code == 200

    original_jobs_after = client.get(f"/api/organizations/{original_org_id}/jobs")
    assert original_jobs_after.status_code == 200
    assert all(item["job_id"] != job_id for item in original_jobs_after.json()["jobs"])

    target_jobs_after = client.get(f"/api/organizations/{target_org_id}/jobs")
    assert target_jobs_after.status_code == 200
    moved_job = next(
        item for item in target_jobs_after.json()["jobs"] if item["job_id"] == job_id
    )
    assert moved_job["organization_id"] == target_org_id
    assert moved_job["organization_name"] == "上海市普陀区财政局"
    assert moved_job["organization_match_type"] == "manual"


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


def test_batch_rematch_preview_and_apply_updates_auto_association(tmp_path: Path, monkeypatch):
    _patch_runtime_state(tmp_path, monkeypatch)
    client = TestClient(app)

    wrong_org_id = _create_org("涓婃捣甯傛櫘闄€鍖烘皯鏀垮眬")["id"]
    correct_org_id = _create_org("涓婃捣甯傛櫘闄€鍖鸿储鏀垮眬")["id"]

    upload = client.post(
        "/api/documents/upload",
        headers=_headers(),
        files={
            "file": (
                "涓婃捣甯傛櫘闄€鍖鸿储鏀垮眬2025骞撮绠楀叕寮€.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    runtime.set_job_organization(job_id, wrong_org_id, match_type="auto", confidence=0.82)

    storage = runtime.require_org_storage()
    correct_org = storage.get_by_id(correct_org_id)
    assert correct_org is not None

    class _FakeMatcher:
        def suggest_matches(self, filename: str, first_page_text: str = "", top_n: int = 5):
            _ = (filename, first_page_text, top_n)
            return [(correct_org, 0.96)]

    monkeypatch.setattr(org_matcher_module, "_matcher_instance", _FakeMatcher())

    preview = client.post(
        "/api/jobs/rematch-organizations",
        headers=_headers(),
        json={"dry_run": True, "minimum_confidence": 0.6},
    )
    assert preview.status_code == 200, preview.text
    preview_payload = preview.json()
    assert preview_payload["status"] == "preview"
    assert preview_payload["candidate_count"] == 1
    assert preview_payload["updated_count"] == 0
    candidate = preview_payload["matches"][0]
    assert candidate["job_id"] == job_id
    assert candidate["action"] == "reassociate"
    assert candidate["current"]["organization_id"] == wrong_org_id
    assert candidate["suggested"]["organization_id"] == correct_org_id
    assert candidate["suggested"]["confidence"] == 0.96

    apply_resp = client.post(
        "/api/jobs/rematch-organizations",
        headers=_headers(),
        json={"dry_run": False, "minimum_confidence": 0.6},
    )
    assert apply_resp.status_code == 200, apply_resp.text
    apply_payload = apply_resp.json()
    assert apply_payload["status"] == "applied"
    assert apply_payload["candidate_count"] == 1
    assert apply_payload["updated_count"] == 1

    status_payload = runtime.get_job_status_payload(job_id)
    assert status_payload["organization_id"] == correct_org_id
    assert status_payload["organization_match_type"] == "auto"
    assert status_payload["organization_match_confidence"] == 0.96

    link = runtime.require_org_storage().get_job_org(job_id)
    assert link is not None
    assert link.org_id == correct_org_id
    assert link.match_type == "auto"


def test_preflight_extracts_unit_budget_cover_metadata(tmp_path: Path, monkeypatch):
    _patch_runtime_state(tmp_path, monkeypatch)
    client = TestClient(app)

    department = _create_org("上海市普陀区人民政府石泉路街道办事处")
    unit = _create_org(
        "上海市普陀区人民政府石泉路街道办事处（本级）",
        level="unit",
        parent_id=department["id"],
        keywords=[
            "上海市普陀区人民政府石泉路街道办事处（本级）",
            "上海市普陀区人民政府石泉路街道办事处",
            "石泉路街道办事处本级",
        ],
    )

    monkeypatch.setattr(
        runtime,
        "extract_pdf_page_texts_from_bytes",
        lambda content, max_pages=3: [
            "\n".join(
                [
                    "上海市普陀区2026年区级单位预算",
                    "预算单位：上海市普陀区人民政府石泉路街道办事处（本级）",
                ]
            )
        ],
    )

    response = client.post(
        "/api/documents/preflight",
        headers=_headers(),
        files={
            "file": (
                "generic.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["report_year"] == 2026
    assert payload["doc_type"] == "dept_budget"
    assert payload["report_kind"] == "budget"
    assert payload["scope_hint"] == "unit"
    assert payload["cover_title"] == "上海市普陀区2026年区级单位预算"
    assert payload["cover_org_name"] == "上海市普陀区人民政府石泉路街道办事处（本级）"
    assert payload["cover_org_label"] == "预算单位"
    assert payload["current"]["organization_id"] == unit["id"]
    assert payload["current"]["organization_name"] == unit["name"]
    assert payload["current"]["level"] == "unit"
    assert payload["current"]["department_id"] == department["id"]
    assert payload["current"]["department_name"] == department["name"]
    assert payload["current"]["match_basis"] == "cover_field"


def test_preflight_extracts_department_budget_cover_metadata(tmp_path: Path, monkeypatch):
    _patch_runtime_state(tmp_path, monkeypatch)
    client = TestClient(app)

    department = _create_org("上海市普陀区人民政府石泉路街道办事处")
    _create_org(
        "上海市普陀区人民政府石泉路街道办事处（本级）",
        level="unit",
        parent_id=department["id"],
    )

    monkeypatch.setattr(
        runtime,
        "extract_pdf_page_texts_from_bytes",
        lambda content, max_pages=3: [
            "\n".join(
                [
                    "上海市普陀区2026年区级部门预算",
                    "预算主管部门：上海市普陀区人民政府石泉路街道办事处",
                ]
            )
        ],
    )

    response = client.post(
        "/api/documents/preflight",
        headers=_headers(),
        files={
            "file": (
                "generic.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["report_year"] == 2026
    assert payload["doc_type"] == "dept_budget"
    assert payload["report_kind"] == "budget"
    assert payload["scope_hint"] == "department"
    assert payload["cover_title"] == "上海市普陀区2026年区级部门预算"
    assert payload["cover_org_name"] == "上海市普陀区人民政府石泉路街道办事处"
    assert payload["cover_org_label"] == "预算主管部门"
    assert payload["current"]["organization_id"] == department["id"]
    assert payload["current"]["organization_name"] == department["name"]
    assert payload["current"]["level"] == "department"
    assert payload["current"]["department_id"] == department["id"]
    assert payload["current"]["department_name"] == department["name"]
    assert payload["current"]["match_basis"] == "cover_field"


def test_upload_auto_match_prefers_cover_org_name_over_filename_only_match(tmp_path: Path, monkeypatch):
    _patch_runtime_state(tmp_path, monkeypatch)
    client = TestClient(app)

    wrong_org = _create_org("上海市普陀区财政局")
    correct_org = _create_org("上海市普陀区人民政府石泉路街道办事处")
    storage = runtime.require_org_storage()
    correct_org_record = storage.get_by_id(correct_org["id"])
    wrong_org_record = storage.get_by_id(wrong_org["id"])
    assert correct_org_record is not None
    assert wrong_org_record is not None

    monkeypatch.setattr(
        runtime,
        "extract_pdf_page_texts",
        lambda pdf_path, max_pages=3: [
            "\n".join(
                [
                    "上海市普陀区2026年区级部门预算",
                    "预算主管部门：上海市普陀区人民政府石泉路街道办事处",
                ]
            )
        ],
    )

    class _FakeMatcher:
        def __init__(self):
            self.calls: list[tuple[str, str, int]] = []

        def suggest_matches(self, filename: str, first_page_text: str = "", top_n: int = 5):
            self.calls.append((filename, first_page_text, top_n))
            if filename == "上海市普陀区人民政府石泉路街道办事处":
                return [(correct_org_record, 0.97)]
            return [(wrong_org_record, 0.93)]

    fake_matcher = _FakeMatcher()
    monkeypatch.setattr(upload_route, "get_org_matcher", lambda: fake_matcher)

    response = client.post(
        "/api/documents/upload",
        headers=_headers(),
        files={
            "file": (
                "2026年度预算公开.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["organization_id"] == correct_org["id"]
    assert payload["organization_name"] == correct_org["name"]
    assert fake_matcher.calls
    assert fake_matcher.calls[0][0] == "上海市普陀区人民政府石泉路街道办事处"
