import io
import os
import uuid

from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api.main import app
from api import runtime


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
    assert "issue_error" in linked_job
    assert "issue_warn" in linked_job
    assert "issue_info" in linked_job
    assert "has_issues" in linked_job
    assert "top_issue_rules" in linked_job
    assert "report_year" in linked_job
    assert linked_job["report_year"] == 2026
    assert "report_kind" in linked_job
    assert linked_job["report_kind"] == "final"


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


def test_department_stats_include_jobs_from_child_units():
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
    assert stats[dept_id]["job_count"] >= 1

    dept_jobs_resp = client.get(f"/api/organizations/{dept_id}/jobs?include_children=true")
    assert dept_jobs_resp.status_code == 200
    dept_job_ids = [item["job_id"] for item in dept_jobs_resp.json()["jobs"]]
    assert job_id in dept_job_ids
