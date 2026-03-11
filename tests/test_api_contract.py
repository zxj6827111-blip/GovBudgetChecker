import io
import os
import uuid
import json
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api.main import app
from api import runtime
from api.routes import organizations as organization_routes


def _pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer << /Size 4 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )


def test_health_and_ready_endpoints():
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] in {"ready", "not_ready"}


def test_document_upload_and_job_alias_routes():
    client = TestClient(app)

    upload = client.post(
        "/api/documents/upload",
        files={
            "file": (
                "sample_budget_2025.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 200
    payload = upload.json()
    job_id = payload["job_id"]

    report_pdf = client.get(f"/api/reports/download?job_id={job_id}")
    assert report_pdf.status_code == 200
    assert report_pdf.headers["content-type"].startswith("application/pdf")

    report_json = client.get(f"/api/reports/download?job_id={job_id}&format=json")
    assert report_json.status_code == 200
    assert report_json.json()["job_id"] == job_id

    report_csv = client.get(f"/api/reports/download?job_id={job_id}&format=csv")
    assert report_csv.status_code == 200
    assert report_csv.headers["content-type"].startswith("text/csv")

    jobs = client.get("/api/jobs")
    assert jobs.status_code == 200
    assert any(item["job_id"] == job_id for item in jobs.json())

    run = client.post(
        f"/api/documents/{job_id}/run",
        json={"mode": "legacy", "doc_type": "dept_budget", "fiscal_year": "2025"},
    )
    assert run.status_code == 200
    assert run.json()["status"] == "started"

    jobs_after_run = client.get("/api/jobs")
    assert jobs_after_run.status_code == 200
    uploaded_job = next(
        item for item in jobs_after_run.json() if item["job_id"] == job_id
    )
    assert uploaded_job["report_kind"] == "budget"
    assert uploaded_job["report_year"] == 2025

    status = client.get(f"/api/jobs/{job_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] in {"queued", "processing", "done", "error"}


def test_local_rule_issue_can_be_ignored(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    runtime._JOB_SUMMARY_CACHE.clear()

    job_id = "job-local-ignore-001"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    issue_id = "rule:V33-001:abcd1234"
    status_payload = {
        "job_id": job_id,
        "status": "done",
        "progress": 100,
        "filename": "local-rule-ignore.pdf",
        "rule_findings": [
            {
                "id": issue_id,
                "source": "rule",
                "rule_id": "V33-001",
                "severity": "medium",
                "message": "本地规则命中",
                "location": {"page": 1},
            }
        ],
        "issues": {
            "error": [],
            "warn": [
                {
                    "id": issue_id,
                    "source": "rule",
                    "rule_id": "V33-001",
                    "severity": "medium",
                    "message": "本地规则命中",
                    "location": {"page": 1},
                }
            ],
            "info": [],
            "all": [
                {
                    "id": issue_id,
                    "source": "rule",
                    "rule_id": "V33-001",
                    "severity": "medium",
                    "message": "本地规则命中",
                    "location": {"page": 1},
                }
            ],
        },
    }
    (job_dir / "status.json").write_text(
        json.dumps(status_payload, ensure_ascii=False),
        encoding="utf-8",
    )

    client = TestClient(app)
    response = client.post(
        f"/api/jobs/{job_id}/issues/ignore",
        json={"issue_id": issue_id},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ignored_issue_id"] == issue_id
    assert payload["ignored_issue_count"] == 1
    assert payload["ignored_issue_ids"] == [issue_id]
    assert payload["rule_findings"] == []
    assert payload["issues"]["warn"] == []
    assert payload["issues"]["all"] == []


def test_document_upload_rejects_duplicate_same_org_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)

    client = TestClient(app)
    create_org = client.post(
        "/api/organizations",
        json={"name": f"重复检测单位-{uuid.uuid4().hex[:8]}", "level": "unit"},
    )
    assert create_org.status_code == 200
    org_id = create_org.json()["id"]

    upload_payload = {
        "org_unit_id": org_id,
        "fiscal_year": "2025",
        "doc_type": "dept_budget",
    }
    first_upload = client.post(
        "/api/documents/upload",
        data=upload_payload,
        files={
            "file": (
                "duplicate_budget_2025.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )
    assert first_upload.status_code == 200, first_upload.text

    duplicate_upload = client.post(
        "/api/documents/upload",
        data=upload_payload,
        files={
            "file": (
                "duplicate_budget_2025.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )
    assert duplicate_upload.status_code == 409
    assert "重复上传" in str(duplicate_upload.json()["detail"])


def test_document_upload_rejects_pdf_exceeding_page_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "MAX_UPLOAD_PAGES", 1)
    monkeypatch.setattr(runtime, "get_pdf_page_count", lambda _path: 2)
    tmp_path.mkdir(parents=True, exist_ok=True)

    client = TestClient(app)
    upload = client.post(
        "/api/documents/upload",
        files={
            "file": (
                "too_many_pages.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 413
    assert "页数超过限制" in str(upload.json()["detail"])


def test_organization_association_flow():
    client = TestClient(app)

    create_org = client.post(
        "/api/organizations",
        json={"name": f"测试单位-{uuid.uuid4().hex[:8]}", "level": "unit"},
    )
    assert create_org.status_code == 200
    org = create_org.json()
    org_id = org["id"]

    upload = client.post(
        "/api/documents/upload",
        data={"org_unit_id": org_id},
        files={
            "file": (
                "linked_final_2026.pdf",
                io.BytesIO(_pdf_bytes()),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    jobs = client.get(f"/api/organizations/{org_id}/jobs")
    assert jobs.status_code == 200
    jobs_payload = jobs.json()["jobs"]
    job_ids = [item["job_id"] for item in jobs_payload]
    assert job_id in job_ids

    linked_job = next(item for item in jobs_payload if item["job_id"] == job_id)
    assert "issue_total" in linked_job
    assert "merged_issue_total" in linked_job
    assert "issue_error" in linked_job
    assert "issue_warn" in linked_job
    assert "issue_info" in linked_job
    assert "has_issues" in linked_job
    assert "top_issue_rules" in linked_job
    assert "report_year" in linked_job
    assert linked_job["report_year"] == 2026
    assert "report_kind" in linked_job
    assert linked_job["report_kind"] == "final"


def test_department_stats_prefers_merged_issue_total(monkeypatch, tmp_path):
    client = TestClient(app)
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    create_dept = client.post(
        "/api/organizations",
        json={"name": f"merged-stats-dept-{uuid.uuid4().hex[:8]}", "level": "department"},
    )
    assert create_dept.status_code == 200
    dept_id = create_dept.json()["id"]

    create_unit = client.post(
        "/api/organizations",
        json={
            "name": f"merged-stats-unit-{uuid.uuid4().hex[:8]}",
            "level": "unit",
            "parent_id": dept_id,
        },
    )
    assert create_unit.status_code == 200
    unit_id = create_unit.json()["id"]

    storage = runtime.require_org_storage()
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    storage.link_job(job_id, unit_id, match_type="manual", confidence=1.0)

    def _fake_collect_job_summary(_job_dir):
        return {
            "job_id": job_id,
            "issue_total": 3,
            "merged_issue_total": 5,
        }

    monkeypatch.setattr(runtime, "collect_job_summary", _fake_collect_job_summary)

    stats_resp = client.get(f"/api/departments/{dept_id}/stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()["stats"]
    assert stats[unit_id]["job_count"] == 1
    assert stats[unit_id]["issue_total"] == 5
    assert stats[unit_id]["has_issues"] is True


def test_departments_list_and_tree_use_aggregated_merged_issue_total(monkeypatch, tmp_path):
    client = TestClient(app)
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    create_dept = client.post(
        "/api/organizations",
        json={"name": f"tree-dept-{uuid.uuid4().hex[:8]}", "level": "department"},
    )
    assert create_dept.status_code == 200
    dept_id = create_dept.json()["id"]

    create_unit = client.post(
        "/api/organizations",
        json={
            "name": f"tree-unit-{uuid.uuid4().hex[:8]}",
            "level": "unit",
            "parent_id": dept_id,
        },
    )
    assert create_unit.status_code == 200
    unit_id = create_unit.json()["id"]

    storage = runtime.require_org_storage()
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    storage.link_job(job_id, unit_id, match_type="manual", confidence=1.0)

    def _fake_collect_job_summary(_job_dir):
        return {
            "job_id": job_id,
            "issue_total": 2,
            "merged_issue_total": 4,
        }

    monkeypatch.setattr(runtime, "collect_job_summary", _fake_collect_job_summary)
    organization_routes.clear_department_stats_cache()

    departments_resp = client.get("/api/departments")
    assert departments_resp.status_code == 200
    departments = departments_resp.json()["departments"]
    department_payload = next(item for item in departments if item["id"] == dept_id)
    assert department_payload["job_count"] == 1
    assert department_payload["issue_count"] == 4

    tree_resp = client.get("/api/organizations")
    assert tree_resp.status_code == 200
    tree = tree_resp.json()["tree"]
    department_node = next(item for item in tree if item["id"] == dept_id)
    unit_node = next(item for item in department_node["children"] if item["id"] == unit_id)
    assert department_node["job_count"] == 1
    assert department_node["issue_count"] == 4
    assert unit_node["job_count"] == 1
    assert unit_node["issue_count"] == 4


def test_organization_jobs_filters_stale_links_without_mutating_links():
    client = TestClient(app)

    create_org = client.post(
        "/api/organizations",
        json={"name": f"stale-job-org-{uuid.uuid4().hex[:8]}", "level": "unit"},
    )
    assert create_org.status_code == 200
    org_id = create_org.json()["id"]

    stale_job_id = f"missing-{uuid.uuid4().hex[:12]}"
    runtime.require_org_storage().link_job(stale_job_id, org_id)

    jobs = client.get(f"/api/organizations/{org_id}/jobs")
    assert jobs.status_code == 200
    payload = jobs.json()
    assert payload["jobs"] == []
    assert payload["total"] == 0
    assert runtime.require_org_storage().get_job_org(stale_job_id) is not None

    runtime.require_org_storage().unlink_job(stale_job_id)


def test_department_unit_endpoints_with_legacy_endpoints_compatible():
    client = TestClient(app)

    dept_name = f"测试部门-{uuid.uuid4().hex[:8]}"
    create_dept = client.post(
        "/api/organizations",
        json={"name": dept_name, "level": "department"},
    )
    assert create_dept.status_code == 200
    dept = create_dept.json()
    dept_id = dept["id"]

    create_unit = client.post(
        "/api/organizations",
        json={
            "name": f"测试单位-{uuid.uuid4().hex[:8]}",
            "level": "unit",
            "parent_id": dept_id,
        },
    )
    assert create_unit.status_code == 200
    unit = create_unit.json()

    departments_resp = client.get("/api/departments")
    assert departments_resp.status_code == 200
    departments = departments_resp.json()["departments"]
    assert any(item["id"] == dept_id for item in departments)

    units_resp = client.get(f"/api/departments/{dept_id}/units")
    assert units_resp.status_code == 200
    units = units_resp.json()["units"]
    assert any(item["id"] == unit["id"] for item in units)

    tree_resp = client.get("/api/organizations")
    assert tree_resp.status_code == 200
    assert tree_resp.json()["total"] >= 2

    list_resp = client.get("/api/organizations/list")
    assert list_resp.status_code == 200
    org_ids = [item["id"] for item in list_resp.json()["organizations"]]
    assert dept_id in org_ids
    assert unit["id"] in org_ids

    not_found_resp = client.get("/api/departments/not-exist-dept/units")
    assert not_found_resp.status_code == 404


def test_organization_jobs_scope_controls_child_inclusion():
    client = TestClient(app)

    create_dept = client.post(
        "/api/organizations",
        json={"name": f"范围测试部门-{uuid.uuid4().hex[:8]}", "level": "department"},
    )
    assert create_dept.status_code == 200
    dept_id = create_dept.json()["id"]

    create_unit = client.post(
        "/api/organizations",
        json={
            "name": f"范围测试单位-{uuid.uuid4().hex[:8]}",
            "level": "unit",
            "parent_id": dept_id,
        },
    )
    assert create_unit.status_code == 200
    unit_id = create_unit.json()["id"]

    upload = client.post(
        "/api/documents/upload",
        data={"org_unit_id": unit_id},
        files={"file": ("scope.pdf", io.BytesIO(_pdf_bytes()), "application/pdf")},
    )
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    # Default scope should return only jobs directly linked to this organization.
    dept_direct = client.get(f"/api/organizations/{dept_id}/jobs")
    assert dept_direct.status_code == 200
    dept_direct_job_ids = [item["job_id"] for item in dept_direct.json()["jobs"]]
    assert job_id not in dept_direct_job_ids

    # Explicit subtree scope should include jobs linked to child units.
    dept_with_children = client.get(
        f"/api/organizations/{dept_id}/jobs?include_children=true"
    )
    assert dept_with_children.status_code == 200
    dept_with_children_job_ids = [
        item["job_id"] for item in dept_with_children.json()["jobs"]
    ]
    assert job_id in dept_with_children_job_ids


def test_department_stats_only_count_direct_jobs():
    client = TestClient(app)

    create_dept = client.post(
        "/api/organizations",
        json={"name": f"stats-dept-{uuid.uuid4().hex[:8]}", "level": "department"},
    )
    assert create_dept.status_code == 200
    dept_id = create_dept.json()["id"]

    create_unit = client.post(
        "/api/organizations",
        json={
            "name": f"stats-unit-{uuid.uuid4().hex[:8]}",
            "level": "unit",
            "parent_id": dept_id,
        },
    )
    assert create_unit.status_code == 200
    unit_id = create_unit.json()["id"]

    upload = client.post(
        "/api/documents/upload",
        data={"org_unit_id": unit_id},
        files={"file": ("stats.pdf", io.BytesIO(_pdf_bytes()), "application/pdf")},
    )
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    stats_resp = client.get(f"/api/departments/{dept_id}/stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()["stats"]
    assert stats[dept_id]["job_count"] == 0
    assert stats[unit_id]["job_count"] >= 1

    dept_jobs_resp = client.get(f"/api/organizations/{dept_id}/jobs?include_children=true")
    assert dept_jobs_resp.status_code == 200
    dept_job_ids = [item["job_id"] for item in dept_jobs_resp.json()["jobs"]]
    assert job_id in dept_job_ids


def test_job_reanalyze_endpoint_creates_new_job(tmp_path, monkeypatch):
    class _DummyQueue:
        def __init__(self) -> None:
            self.enqueued: list[str] = []

        async def enqueue(self, job_id: str) -> None:
            self.enqueued.append(job_id)

    async def _dummy_runner(_job_dir):
        return None

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", False)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)

    queue = _DummyQueue()
    monkeypatch.setattr(runtime, "_job_queue", queue)

    source_job_dir = tmp_path / "job-old"
    source_job_dir.mkdir(parents=True)
    (source_job_dir / "history_2025.pdf").write_bytes(_pdf_bytes())
    runtime.write_json_file(
        source_job_dir / "status.json",
        {
            "job_id": "job-old",
            "status": "done",
            "progress": 100,
            "filename": "history_2025.pdf",
            "size": len(_pdf_bytes()),
            "saved_path": "job-old/history_2025.pdf",
            "checksum": "checksum-old",
            "fiscal_year": "2025",
            "doc_type": "dept_budget",
            "report_year": 2025,
            "use_local_rules": True,
            "use_ai_assist": False,
            "mode": "legacy",
        },
    )

    client = TestClient(app)
    response = client.post("/api/jobs/job-old/reanalyze", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "started"
    assert payload["source_job_id"] == "job-old"
    assert payload["job_id"] != "job-old"
    assert queue.enqueued == [payload["job_id"]]

    detail = client.get(f"/api/jobs/{payload['job_id']}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["status"] == "queued"
    assert detail_payload["filename"] == "history_2025.pdf"
    assert detail_payload["fiscal_year"] == "2025"
    assert detail_payload["doc_type"] == "dept_budget"


def test_job_reanalyze_all_endpoint_batches_jobs(tmp_path, monkeypatch):
    class _DummyQueue:
        def __init__(self) -> None:
            self.enqueued: list[str] = []

        async def enqueue(self, job_id: str) -> None:
            self.enqueued.append(job_id)

    async def _dummy_runner(_job_dir):
        return None

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", False)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)

    queue = _DummyQueue()
    monkeypatch.setattr(runtime, "_job_queue", queue)

    done_job_dir = tmp_path / "job-done"
    done_job_dir.mkdir(parents=True)
    (done_job_dir / "done_2025.pdf").write_bytes(_pdf_bytes())
    runtime.write_json_file(
        done_job_dir / "status.json",
        {
            "job_id": "job-done",
            "status": "done",
            "progress": 100,
            "filename": "done_2025.pdf",
            "size": len(_pdf_bytes()),
            "saved_path": "job-done/done_2025.pdf",
            "checksum": "checksum-done",
            "fiscal_year": "2025",
            "doc_type": "dept_budget",
            "report_year": 2025,
        },
    )

    running_job_dir = tmp_path / "job-running"
    running_job_dir.mkdir(parents=True)
    (running_job_dir / "running_2025.pdf").write_bytes(_pdf_bytes())
    runtime.write_json_file(
        running_job_dir / "status.json",
        {
            "job_id": "job-running",
            "status": "running",
            "progress": 45,
            "filename": "running_2025.pdf",
        },
    )

    client = TestClient(app)
    response = client.post("/api/jobs/reanalyze-all", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "started"
    assert payload["requested_count"] == 2
    assert payload["created_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["failed_count"] == 0
    assert len(queue.enqueued) == 1

    new_job_id = payload["created"][0]["job_id"]
    detail = client.get(f"/api/jobs/{new_job_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["status"] == "queued"
    assert detail_payload["filename"] == "done_2025.pdf"


def test_structured_ingest_cleanup_endpoint_returns_runtime_payload(monkeypatch):
    client = TestClient(app)

    async def _fake_cleanup(body=None):
        assert body == {"dry_run": True}
        return {
            "status": "preview",
            "dry_run": True,
            "cleanup_document_version_count": 2,
            "cleanup_job_count": 3,
        }

    monkeypatch.setattr(runtime, "cleanup_structured_ingest_history", _fake_cleanup)

    response = client.post("/api/jobs/structured-ingest-cleanup", json={"dry_run": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "preview"
    assert payload["cleanup_document_version_count"] == 2
    assert payload["cleanup_job_count"] == 3


def test_job_issue_ignore_endpoint_returns_filtered_status(monkeypatch):
    client = TestClient(app)

    def _fake_ignore(job_id: str, issue_id: str):
        assert job_id == "job-issue"
        assert issue_id == "ai:001"
        return {
            "job_id": job_id,
            "status": "done",
            "ignored_issue_id": issue_id,
            "ignored_issue_ids": [issue_id],
            "result": {
                "ai_findings": [],
                "rule_findings": [],
                "merged": {
                    "totals": {"ai": 0, "rule": 0, "merged": 0, "conflicts": 0, "agreements": 0},
                    "conflicts": [],
                    "agreements": [],
                },
            },
        }

    monkeypatch.setattr(runtime, "ignore_job_issue", _fake_ignore)

    response = client.post("/api/jobs/job-issue/issues/ignore", json={"issue_id": "ai:001"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ignored_issue_id"] == "ai:001"
    assert payload["ignored_issue_ids"] == ["ai:001"]
    assert payload["result"]["ai_findings"] == []
