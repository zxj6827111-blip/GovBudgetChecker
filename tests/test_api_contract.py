import io
import os
import uuid

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
        files={"file": ("sample.pdf", io.BytesIO(_pdf_bytes()), "application/pdf")},
    )
    assert upload.status_code == 200
    payload = upload.json()
    job_id = payload["job_id"]

    jobs = client.get("/api/jobs")
    assert jobs.status_code == 200
    assert any(item["job_id"] == job_id for item in jobs.json())

    run = client.post(f"/api/documents/{job_id}/run", json={"mode": "legacy"})
    assert run.status_code == 200
    assert run.json()["status"] == "started"

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
        files={"file": ("linked.pdf", io.BytesIO(_pdf_bytes()), "application/pdf")},
    )
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    jobs = client.get(f"/api/organizations/{org_id}/jobs")
    assert jobs.status_code == 200
    job_ids = [item["job_id"] for item in jobs.json()["jobs"]]
    assert job_id in job_ids


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
