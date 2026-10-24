"""组织导入的事务、旧编号兼容及真实 HTTP 结果回归。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import organizations as routes
from src.services import org_storage as storage_module


@pytest.fixture
def org_api(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage_module, "ORG_FILE", tmp_path / "organizations.json")
    monkeypatch.setattr(storage_module, "LINKS_FILE", tmp_path / "job_org_links.json")
    store = storage_module.OrganizationStorage()
    events = []
    monkeypatch.setattr(routes.runtime, "require_org_storage", lambda: store)
    monkeypatch.setattr(routes, "require_admin", lambda request: (None, "", {"username": "test-admin", "is_admin": True}))
    monkeypatch.setattr(routes, "require_login", routes.require_admin)
    monkeypatch.setattr(routes, "user_can_access_org", lambda user, org_id: True)
    monkeypatch.setattr(routes, "append_audit_event", lambda **event: events.append(event))
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), store, events


def seed(org_api, organizations, links=None):
    storage_module.ORG_FILE.write_text(json.dumps({"organizations": organizations}), encoding="utf-8")
    if links is not None:
        storage_module.LINKS_FILE.write_text(json.dumps({"links": links}), encoding="utf-8")
    # get_all 会检测文件签名，重新读入历史目录。
    org_api[1].get_all()


def upload(client, text, clear=False):
    return client.post("/api/organizations/import", data={"clear_existing": str(clear).lower()},
                       files={"file": ("organizations.csv", text.encode("utf-8-sig"), "text/csv")})


def legacy_catalog():
    return [
        {"id": "legacy-city", "name": "上海市", "level": "city"},
        {"id": "legacy-district", "name": "普陀区", "level": "department"},
        {"id": "legacy-finance", "name": "财政局", "level": "department", "parent_id": "legacy-district"},
    ]


CSV = "name,level,parent\n上海市,city,\n新部门,department,上海市\n新单位,unit,新部门\n"


def test_existing_invalid_hierarchy_returns_422_zero_committed_and_failure_audit(org_api):
    client, store, events = org_api
    seed(org_api, legacy_catalog())
    before = storage_module.ORG_FILE.read_bytes()
    response = upload(client, CSV)
    assert response.status_code == 422
    payload = response.json()
    assert payload["success"] is False
    assert payload["imported"] == 0 and payload["attempted"] == 2
    assert any("财政局" in error and "普陀区" in error for error in payload["errors"])
    assert storage_module.ORG_FILE.read_bytes() == before
    assert len(store.get_all()) == 3
    assert client.get("/api/organizations/list").json()["total"] == 3
    assert events[-1]["result"] == "failure"
    assert events[-1]["details"]["imported"] == 0


def test_repair_and_import_preserve_legacy_ids_links_and_repeat_is_noop(org_api):
    client, store, _ = org_api
    seed(org_api, legacy_catalog(), [
        {"job_id": "existing-report", "org_id": "legacy-finance", "match_type": "manual"},
    ])
    links_before = storage_module.LINKS_FILE.read_bytes()
    repaired = client.put("/api/organizations/legacy-district", json={
        "name": "普陀区", "level": "district", "parent_id": "legacy-city",
    })
    assert repaired.status_code == 200 and repaired.json()["id"] == "legacy-district"
    first = upload(client, CSV)
    assert first.status_code == 200
    assert first.json()["imported"] == 2 and first.json()["skipped"] == 1
    assert {org.id for org in store.get_all()} >= {"legacy-city", "legacy-district", "legacy-finance"}
    assert len([org for org in store.get_all() if org.name == "上海市"]) == 1
    after_first = storage_module.ORG_FILE.read_bytes()
    repeated = upload(client, CSV)
    assert repeated.status_code == 200 and repeated.json()["success"] is True
    assert repeated.json()["imported"] == 0 and repeated.json()["skipped"] == 3
    assert storage_module.ORG_FILE.read_bytes() == after_first
    assert storage_module.LINKS_FILE.read_bytes() == links_before
    assert storage_module.OrganizationStorage().validate_hierarchy()["valid"]


def test_missing_parent_rolls_back_other_valid_rows(org_api):
    client, store, _ = org_api
    seed(org_api, [{"id": "existing", "name": "原部门", "level": "department"}])
    before = storage_module.ORG_FILE.read_bytes()
    response = upload(client, "name,level,parent\n新增部门,department,\n孤儿单位,unit,不存在部门\n")
    assert response.status_code == 422 and response.json()["imported"] == 0
    assert response.json()["attempted"] == 1
    assert any("孤儿单位" in error and "不存在部门" in error for error in response.json()["errors"])
    assert all("parent_name" not in error for error in response.json()["errors"])
    assert storage_module.ORG_FILE.read_bytes() == before
    assert len(store.get_all()) == 1


def test_ambiguous_parent_never_picks_first_matching_name(org_api):
    client, _, _ = org_api
    seed(org_api, [
        {"id": "a", "name": "甲区", "level": "district"},
        {"id": "b", "name": "乙区", "level": "district"},
        {"id": "dept-a", "name": "同名部门", "level": "department", "parent_id": "a"},
        {"id": "dept-b", "name": "同名部门", "level": "department", "parent_id": "b"},
    ])
    before = storage_module.ORG_FILE.read_bytes()
    response = upload(client, "name,level,parent\n单位,unit,同名部门\n")
    assert response.status_code == 422
    assert any("多个同名候选" in error for error in response.json()["errors"])
    assert storage_module.ORG_FILE.read_bytes() == before


def test_create_and_update_reject_invalid_hierarchy_without_mutation(org_api):
    client, store, _ = org_api
    seed(org_api, [{"id": "dept", "name": "部门", "level": "department"}])
    before = storage_module.ORG_FILE.read_bytes()
    assert client.post("/api/organizations", json={
        "name": "下级部门", "level": "department", "parent_id": "dept",
    }).status_code == 422
    assert client.post("/api/organizations", json={"name": "孤儿单位", "level": "unit"}).status_code == 422
    assert storage_module.ORG_FILE.read_bytes() == before
    unit = client.post("/api/organizations", json={"name": "直属单位", "level": "unit", "parent_id": "dept"})
    assert unit.status_code == 200
    after_unit = storage_module.ORG_FILE.read_bytes()
    assert client.put("/api/organizations/dept", json={
        "name": "部门", "level": "department", "parent_id": unit.json()["id"],
    }).status_code == 422
    assert storage_module.ORG_FILE.read_bytes() == after_unit
    assert store.get_by_id("dept").parent_id is None
    assert client.post("/api/organizations", json={
        "id": "different-id", "name": "部门", "level": "department",
    }).status_code == 422


def test_clear_existing_cannot_orphan_report_links(org_api):
    client, _, _ = org_api
    seed(org_api, [{"id": "old-dept", "name": "旧部门", "level": "department"}], [
        {"job_id": "report", "org_id": "old-dept", "match_type": "manual"},
    ])
    before = storage_module.ORG_FILE.read_bytes()
    links_before = storage_module.LINKS_FILE.read_bytes()
    response = upload(client, "name,level,parent\n新部门,department,\n", clear=True)
    assert response.status_code == 422 and response.json()["imported"] == 0
    assert any("报告关联失效" in error for error in response.json()["errors"])
    assert storage_module.ORG_FILE.read_bytes() == before
    assert storage_module.LINKS_FILE.read_bytes() == links_before


@pytest.mark.parametrize("text", [
    "wrong_header\n没有模板\n",
    "name,level,parent\n缺名称行前的正确记录,department,\n,department,\n",
    "name,level,parent\n多列,department,,意外值\n",
    "name,level,parent\n",
])
def test_invalid_csv_never_writes_partial_catalog(org_api, text):
    client, store, _ = org_api
    response = upload(client, text)
    assert response.status_code == 422
    assert store.get_all() == []
    assert not storage_module.ORG_FILE.exists()


def test_parent_rows_can_follow_children(org_api):
    client, store, _ = org_api
    response = upload(client, "name,level,parent\n单位,unit,部门\n部门,department,城市\n城市,city,\n")
    assert response.status_code == 200 and response.json()["imported"] == 3
    assert store.validate_hierarchy()["valid"]


def test_later_same_name_parent_cannot_silently_capture_an_earlier_child(org_api):
    client, store, _ = org_api
    response = upload(client, "name,level,parent\n单位,unit,同名部门\n甲区,district,\n"
                      "同名部门,department,甲区\n乙区,district,\n同名部门,department,乙区\n")
    assert response.status_code == 422
    assert any("多个同名候选" in error for error in response.json()["errors"])
    assert store.get_all() == []


def test_failed_save_restores_in_memory_catalog(org_api, monkeypatch):
    _, store, _ = org_api
    seed(org_api, [{"id": "existing", "name": "原部门", "level": "department"}])
    before = storage_module.ORG_FILE.read_bytes()
    def fail_save():
        raise OSError("simulated disk failure")
    monkeypatch.setattr(store, "_save_organizations", fail_save)
    with pytest.raises(OSError):
        store.import_from_list([{"name": "新增", "level": "department"}])
    assert [org.id for org in store.get_all()] == ["existing"]
    assert storage_module.ORG_FILE.read_bytes() == before
