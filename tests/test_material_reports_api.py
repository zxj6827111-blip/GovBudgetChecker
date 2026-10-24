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
    app=FastAPI();app.include_router(materials.router)
    with TestClient(app) as client:
        assert client.get("/api/materials/reports?org=secret").status_code==403


def test_report_uses_task_authorization_instead_of_stale_database_owner(monkeypatch):
    from api.routes import reviews
    calls=[]
    monkeypatch.setattr(reviews,"_job_access_ok",lambda user,job:(calls.append(job) or False))
    assert not materials._report_job_is_accessible({"username":"reader"},{"job_id":"job","organization_id":"stale-visible"})
    assert calls==["job"]


def test_download_checks_requested_file_hash_after_authorization(tmp_path,monkeypatch):
    path=tmp_path/"original.pdf";path.write_bytes(b"original pdf bytes")
    calls=[]
    monkeypatch.setattr(files,"require_job_access",lambda *args,**kwargs:calls.append("authorized"))
    monkeypatch.setattr(files,"_resolve_source_pdf",lambda _:path)
    app=FastAPI();app.include_router(files.router)
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
    app = FastAPI(); app.include_router(materials.router)
    with TestClient(app) as client:
        assert client.get(f"/api/materials/reports?org=secret&report_category={category}").status_code == 403
        assert client.get("/api/materials/reports?report_category=invalid").status_code == 422
