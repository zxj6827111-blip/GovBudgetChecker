import hashlib
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routes import materials,files
from api.material_access import MaterialAccessScope


def test_material_reports_do_not_expose_ungranted_container(monkeypatch):
    monkeypatch.setattr(materials,"require_login",lambda _:(None,"",{"username":"limited"}))
    monkeypatch.setattr(materials,"_build_access_scope",lambda _:MaterialAccessScope(visible_org_ids=["mine"],container_org_ids={"mine"}))
    class Storage:
        def get_all(self):return []
    monkeypatch.setattr(materials.runtime,"require_org_storage",lambda:Storage())
    app=FastAPI()
    app.include_router(materials.router)
    with TestClient(app) as client:
        assert client.get("/api/materials/reports?org=secret").status_code==403


def test_report_uses_task_authorization_instead_of_stale_database_owner(monkeypatch):
    from api.routes import reviews
    calls=[]
    monkeypatch.setattr(reviews,"_job_access_ok",lambda user,job:(calls.append(job) or False))
    assert not materials._report_job_is_accessible({"username":"reader"},{"job_id":"job","organization_id":"stale-visible"})
    assert calls==["job"]


def test_download_checks_requested_file_hash_after_authorization(tmp_path,monkeypatch):
    path=tmp_path/"original.pdf"
    path.write_bytes(b"original pdf bytes")
    calls=[]
    monkeypatch.setattr(files,"require_job_access",lambda *args,**kwargs:calls.append("authorized"))
    monkeypatch.setattr(files,"_resolve_source_pdf",lambda _:path)
    app=FastAPI()
    app.include_router(files.router)
    with TestClient(app) as client:
        assert client.get("/api/files/job/source?checksum="+"0"*64).status_code==409
        response=client.get("/api/files/job/source?checksum="+hashlib.sha256(path.read_bytes()).hexdigest())
        assert response.status_code==200 and response.content==path.read_bytes()
        assert len(calls)==2


def test_missing_original_does_not_get_a_download_link(tmp_path,monkeypatch):
    monkeypatch.setattr(materials.runtime,"UPLOAD_ROOT",tmp_path)
    item={"slot_id":"slot","current_version":{"source_job_id":"missing"}}
    materials._report_source_availability(item)
    assert item["current_version"]["source_job_id"] is None
    assert item["current_version"]["source_unavailable_reason"]=="source_not_found"


def test_clear_catalog_checks_material_refs_even_without_job_links(tmp_path,monkeypatch):
    from src.services import org_storage
    from src.schemas.organization import Organization
    monkeypatch.setattr(org_storage,"DATA_DIR",tmp_path)
    monkeypatch.setattr(org_storage,"ORG_FILE",tmp_path/"organizations.json")
    monkeypatch.setattr(org_storage,"LINKS_FILE",tmp_path/"links.json")
    store=org_storage.OrganizationStorage()
    store.add(Organization(id="legacy",name="原部门",level="department"))
    before=org_storage.ORG_FILE.read_bytes()
    result=store.import_from_list([{"name":"新部门","level":"department"}],clear_existing=True,referenced_org_ids=["legacy"])
    assert not result.success and org_storage.ORG_FILE.read_bytes()==before
    result=store.import_from_list([{"name":"原部门","level":"department"}],clear_existing=True,referenced_org_ids=["legacy"])
    assert result.success and store.get_by_id("legacy") is not None


@pytest.mark.parametrize("category", ["department", "units"])
def test_report_category_cannot_expand_organization_permission(category, monkeypatch):
    monkeypatch.setattr(materials, "require_login", lambda _: (None, "", {"username": "limited"}))
    monkeypatch.setattr(materials, "_build_access_scope", lambda _: MaterialAccessScope(visible_org_ids=["mine"], container_org_ids={"mine"}))
    class Storage:
        def get_all(self): return []
    monkeypatch.setattr(materials.runtime, "require_org_storage", lambda: Storage())
    app = FastAPI()
    app.include_router(materials.router)
    with TestClient(app) as client:
        assert client.get(f"/api/materials/reports?org=secret&report_category={category}").status_code == 403
        assert client.get("/api/materials/reports?report_category=invalid").status_code == 422


def test_material_report_delete_requires_admin(monkeypatch):
    from api import auth_utils

    monkeypatch.setattr(
        auth_utils,
        "require_login",
        lambda _: (None, "tok", {"username": "operator", "is_admin": False}),
    )
    app = FastAPI()
    app.include_router(materials.router)
    slot_id = "11111111-1111-1111-1111-111111111111"
    with TestClient(app) as client:
        assert client.delete(f"/api/materials/reports/{slot_id}").status_code == 403
        assert (
            client.post(
                "/api/materials/reports/batch-delete",
                json={"slot_ids": [slot_id], "job_ids": []},
            ).status_code
            == 403
        )


def test_material_report_batch_delete_rejects_empty_payload(monkeypatch):
    monkeypatch.setattr(
        materials,
        "require_admin",
        lambda _: (None, "", {"username": "admin", "is_admin": True}),
    )
    app = FastAPI()
    app.include_router(materials.router)
    with TestClient(app) as client:
        response = client.post(
            "/api/materials/reports/batch-delete",
            json={"slot_ids": ["", "  "], "job_ids": [" "]},
        )
        assert response.status_code == 400


def test_material_report_delete_propagates_409_and_404(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        materials,
        "require_admin",
        lambda _: (None, "", {"username": "admin", "is_admin": True}),
    )

    async def raise_conflict(_fn):
        raise HTTPException(
            status_code=409,
            detail="该报告存在正在执行的分析任务，请等待分析完成后再删除",
        )

    monkeypatch.setattr(materials, "_with_connection", raise_conflict)
    app = FastAPI()
    app.include_router(materials.router)
    with TestClient(app) as client:
        assert (
            client.delete(
                "/api/materials/reports/11111111-1111-1111-1111-111111111111"
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/materials/reports/batch-delete",
                json={"slot_ids": ["11111111-1111-1111-1111-111111111111"], "job_ids": []},
            ).status_code
            == 409
        )

    async def return_missing(_fn):
        return {
            "requested_slot_count": 1,
            "requested_job_count": 0,
            "deleted_slot_ids": [],
            "reset_baseline_slot_ids": [],
            "updated_slot_ids": [],
            "deleted_version_ids": [],
            "deleted_document_ids": [],
            "deleted_job_ids": [],
            "deleted_review_session_count": 0,
            "failed": [
                {
                    "slot_id": "11111111-1111-1111-1111-111111111111",
                    "status_code": 404,
                    "detail": "material slot not found",
                }
            ],
        }

    monkeypatch.setattr(materials, "_with_connection", return_missing)
    with TestClient(app) as client:
        assert (
            client.delete(
                "/api/materials/reports/11111111-1111-1111-1111-111111111111"
            ).status_code
            == 404
        )


def test_material_report_delete_endpoints_succeed_and_audit(monkeypatch):
    monkeypatch.setattr(
        materials,
        "require_admin",
        lambda _: (None, "", {"username": "root-admin", "is_admin": True}),
    )
    audit_calls = []
    monkeypatch.setattr(materials, "append_audit_event", lambda **kw: audit_calls.append(kw))

    async def fake_with_conn(_fn):
        return {
            "requested_slot_count": 1,
            "requested_job_count": 1,
            "deleted_slot_ids": ["11111111-1111-1111-1111-111111111111"],
            "reset_baseline_slot_ids": [],
            "updated_slot_ids": [],
            "deleted_version_ids": [12],
            "deleted_document_ids": [7],
            "deleted_job_ids": ["job-1"],
            "deleted_review_session_count": 1,
            "failed": [],
        }

    monkeypatch.setattr(materials, "_with_connection", fake_with_conn)
    app = FastAPI()
    app.include_router(materials.router)
    slot_id = "11111111-1111-1111-1111-111111111111"
    with TestClient(app) as client:
        single_resp = client.delete(f"/api/materials/reports/{slot_id}")
        assert single_resp.status_code == 200
        assert single_resp.json()["ok"] is True
        assert single_resp.json()["data"]["deleted_slot_ids"] == [slot_id]

        batch_resp = client.post(
            "/api/materials/reports/batch-delete",
            json={"slot_ids": [slot_id], "job_ids": ["job-legacy"]},
        )
        assert batch_resp.status_code == 200
        assert batch_resp.json()["ok"] is True

    assert [c["action"] for c in audit_calls] == [
        "material_report.delete",
        "material_report.batch_delete",
    ]
    assert all(c["actor"] == "root-admin" for c in audit_calls)


def test_purge_workflow_records_for_jobs_cleans_all_sections(tmp_path, monkeypatch):
    import json
    from api import runtime
    from src.services import issue_workflow_store

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    state_file = tmp_path / ".issue_workflow.json"
    initial_state = {
        "version": 1,
        "issues": {
            "job-del::1": {"job_id": "job-del", "issue_id": "1", "status": "confirmed"},
            "job-keep::1": {"job_id": "job-keep", "issue_id": "1", "status": "confirmed"},
        },
        "decision_events": [
            {"id": "ev-1", "job_id": "job-del", "issue_id": "1", "status": "confirmed"},
            {"id": "ev-2", "job_id": "job-keep", "issue_id": "1", "status": "confirmed"},
        ],
        "review_locks": {
            "job-del": {"locked_by": "admin"},
            "job-slot": {"slot_id": "slot-del", "locked_by": "admin"},
            "job-keep": {"slot_id": "slot-keep", "locked_by": "admin"},
        },
        "legacy_ignore_migrations": {
            "job-del": {"migrated": True},
            "job-keep": {"migrated": True},
        },
        "packages": [
            {
                "id": "pkg-only-del",
                "name": "仅删除包",
                "job_ids": ["job-del"],
                "issue_keys": ["job-del::1"],
                "requested_issue_keys": ["job-del::1"],
                "report_snapshots": {"job-del": {"count": 1}},
            },
            {
                "id": "pkg-shared",
                "name": "共享包",
                "job_ids": ["job-del", "job-keep"],
                "issue_keys": ["job-del::1", "job-keep::1"],
                "requested_issue_keys": ["job-del::1", "job-keep::1"],
                "report_snapshots": {"job-del": {"count": 1}, "job-keep": {"count": 2}},
            },
        ],
    }
    state_file.write_text(json.dumps(initial_state), encoding="utf-8")

    result = issue_workflow_store.purge_workflow_records_for_jobs(
        ["job-del"],
        slot_ids=["slot-del"],
    )
    assert result["changed"] is True
    assert result["removed_issues"] == 1
    assert result["removed_events"] == 1
    assert result["removed_locks"] == 2
    assert result["removed_packages"] == 1
    assert result["updated_packages"] == 1

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert list(saved["issues"].keys()) == ["job-keep::1"]
    assert [ev["id"] for ev in saved["decision_events"]] == ["ev-2"]
    assert list(saved["review_locks"].keys()) == ["job-keep"]
    assert list(saved["legacy_ignore_migrations"].keys()) == ["job-keep"]
    assert len(saved["packages"]) == 1
    assert saved["packages"][0]["id"] == "pkg-shared"
    assert saved["packages"][0]["job_ids"] == ["job-keep"]
    assert saved["packages"][0]["issue_keys"] == ["job-keep::1"]
    assert list(saved["packages"][0]["report_snapshots"].keys()) == ["job-keep"]


@pytest.mark.asyncio
async def test_delete_material_reports_sets_guard_and_resets_baseline_slots(tmp_path, monkeypatch):
    import uuid
    from api import runtime
    from src.services import issue_workflow_store
    from src.services.material_report_deletion import delete_material_reports

    normal_slot = uuid.UUID("11111111-1111-1111-1111-111111111111")
    baseline_slot = uuid.UUID("22222222-2222-2222-2222-222222222222")

    upload_root = tmp_path / "uploads"
    (upload_root / "job-normal").mkdir(parents=True)
    (upload_root / "job-base").mkdir(parents=True)
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)
    monkeypatch.setattr(runtime, "get_job_queue", lambda: None)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", False)

    purged = []

    async def fake_purge(jobs, *, slot_ids=()):
        purged.append((list(jobs), list(slot_ids)))
        return {"changed": False}

    monkeypatch.setattr(issue_workflow_store, "purge_and_persist_workflow_records_for_jobs", fake_purge)

    class _FakeConn:
        def __init__(self):
            self.queries = []

        def is_in_transaction(self):
            return True

        async def fetch(self, sql, *args):
            self.queries.append((sql, args))
            if "DELETE FROM fiscal_document_versions" in sql:
                return [{"id": 101}, {"id": 102}]
            if "DELETE FROM fiscal_documents" in sql:
                return [{"id": 11}, {"id": 12}]
            if "FROM information_schema.tables" in sql:
                return [
                    {"table_name": name}
                    for name in args[0]
                ]
            if "SELECT id, current_document_version_id\n            FROM material_slots" in sql:
                return [
                    {"id": normal_slot, "current_document_version_id": 101},
                    {"id": baseline_slot, "current_document_version_id": 102},
                ]
            if "SELECT id\n            FROM fiscal_document_versions" in sql:
                return [{"id": 101}, {"id": 102}]
            if "SELECT slot_id\n            FROM fiscal_document_versions" in sql:
                return [{"slot_id": normal_slot}, {"slot_id": baseline_slot}]
            if "SELECT id\n            FROM material_slots" in sql:
                return [{"id": normal_slot}, {"id": baseline_slot}]
            if "applicability_status\n                FROM material_slots" in sql:
                return [
                    {
                        "id": normal_slot,
                        "slot_key": "k1",
                        "current_document_version_id": 101,
                        "due_at": None,
                        "expected_source_url": None,
                        "applicability_status": "applicable",
                    },
                    {
                        "id": baseline_slot,
                        "slot_key": "k2",
                        "current_document_version_id": 102,
                        "due_at": "2026-08-01",
                        "expected_source_url": None,
                        "applicability_status": "applicable",
                    },
                ]
            if "SELECT id, document_id, slot_id\n                FROM fiscal_document_versions" in sql:
                return [
                    {"id": 101, "document_id": 11, "slot_id": normal_slot},
                    {"id": 102, "document_id": 12, "slot_id": baseline_slot},
                ]
            if "FROM analysis_jobs" in sql and "job_uuid" in sql:
                return [{"job_uuid": "job-normal"}, {"job_uuid": "job-base"}]
            if "FROM review_sessions" in sql and "FOR UPDATE" in sql:
                return [{"id": uuid.UUID("33333333-3333-3333-3333-333333333333")}]
            if "SELECT analysis_job_uuid" in sql and "FROM review_sessions" in sql:
                return []
            if "FROM parse_runs" in sql:
                return [{"id": 201}]
            if "FROM published_fact_projections" in sql:
                return [{"document_id": 11, "shared_report_id": "44444444-4444-4444-4444-444444444444"}]
            if "FROM analysis_fact_snapshots" in sql:
                return [{"id": 301}]
            return []

        async def fetchrow(self, sql, *args):
            self.queries.append((sql, args))
            return None

        async def fetchval(self, sql, *args):
            self.queries.append((sql, args))
            return False

        async def execute(self, sql, *args):
            self.queries.append((sql, args))
            return "DELETE 1"

    from src.services.material_slot_service import MaterialSlotService

    refreshed = []

    async def fake_refresh(self, slot_uuid, **kwargs):
        refreshed.append((str(slot_uuid), kwargs))

    monkeypatch.setattr(MaterialSlotService, "refresh_status", fake_refresh)

    conn = _FakeConn()
    summary = await delete_material_reports(
        conn,
        slot_ids=[str(normal_slot), str(baseline_slot)],
        actor="admin",
    )

    assert summary["deleted_slot_ids"] == [str(normal_slot)]
    assert summary["reset_baseline_slot_ids"] == [str(baseline_slot)]
    assert summary["deleted_version_ids"] == [101, 102]
    assert summary["deleted_document_ids"] == [11, 12]
    assert sorted(summary["deleted_job_ids"]) == ["job-base", "job-normal"]
    assert summary["deleted_review_session_count"] == 1
    assert not (upload_root / "job-normal").exists()
    assert not (upload_root / "job-base").exists()
    assert len(refreshed) == 1 and refreshed[0][0] == str(baseline_slot)
    assert len(purged) == 1

    sql_list = [q[0] for q in conn.queries]
    assert "SET LOCAL app.allow_admin_report_delete = 'on'" in sql_list
    for table_fragment in (
        "DELETE FROM supervision_rounds",
        "DELETE FROM review_obligation_decisions",
        "DELETE FROM review_sessions",
        "DELETE FROM document_writing_scope_decisions",
        "DELETE FROM document_scope_decisions",
        "DELETE FROM published_fact_projections",
        "DELETE FROM legacy_projection_archives",
        "DELETE FROM analysis_revision_results",
        "DELETE FROM analysis_fact_snapshots",
        "DELETE FROM parse_runs",
        "DELETE FROM workflow_issue_records",
        "DELETE FROM analysis_jobs",
        "DELETE FROM jobs",
        "DELETE FROM fiscal_document_versions",
        "DELETE FROM fiscal_documents",
        "DELETE FROM org_dept_annual_report",
        "DELETE FROM material_slots WHERE id = $1",
    ):
        assert any(table_fragment in s for s in sql_list), f"missing {table_fragment}"

    guard_idx = sql_list.index("SET LOCAL app.allow_admin_report_delete = 'on'")
    lock_indices = [
        next(i for i, s in enumerate(sql_list) if "FROM material_slots" in s and "FOR UPDATE" in s),
        next(i for i, s in enumerate(sql_list) if "FROM fiscal_document_versions" in s and "FOR UPDATE" in s),
        next(i for i, s in enumerate(sql_list) if "FROM review_sessions" in s and "FOR UPDATE" in s),
        next(i for i, s in enumerate(sql_list) if "FROM analysis_jobs" in s and "FOR UPDATE" in s),
    ]
    assert lock_indices == sorted(lock_indices)
    assert lock_indices[-1] < guard_idx
