"""市级与区级范围必须同时约束材料、年份和计数，且不得替代原授权。"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.material_access import MaterialAccessScope
from api.routes import materials
from src.services import material_report_query as query


def catalog():
    return [SimpleNamespace(id=id_, level=level, parent_id=parent) for id_, level, parent in [
        ("city", "city", None), ("city-dept", "department", "city"),
        ("city-unit", "unit", "city-dept"), ("district", "district", "city"),
        ("district-dept", "department", "district"), ("district-unit", "unit", "district-dept"),
        ("other-district", "district", "city"), ("other-dept", "department", "other-district"),
    ]]


def test_management_uses_ancestors_instead_of_names_or_depth():
    records = catalog()
    assert query.management_organization_ids(records, "city") == {"city", "city-dept", "city-unit"}
    assert query.management_organization_ids(records, "district") == {"district", "district-dept", "district-unit", "other-district", "other-dept"}
    records += [SimpleNamespace(id="orphan", level="unit", parent_id="unknown"),
                SimpleNamespace(id="cycle", level="unit", parent_id="cycle")]
    assert "orphan" not in query.management_organization_ids(records, "city")
    assert "cycle" not in query.management_organization_ids(records, "city")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(materials, "require_login", lambda _: (None, "", {"username": "tester"}))
    monkeypatch.setattr(materials, "_build_access_scope", lambda _: MaterialAccessScope(
        visible_org_ids=["city-dept", "city-unit", "district-dept", "district-unit"],
        container_org_ids={"city", "city-dept", "city-unit", "district", "district-dept", "district-unit"}))
    monkeypatch.setattr(materials.runtime, "require_org_storage", lambda: SimpleNamespace(get_all=catalog))

    async def load_rows(conn, *, visible_org_ids, **kwargs):
        return [dict(item_id=id_, slot_id=id_, organization_id=id_, fiscal_year=year,
                     report_year=year, report_kind="budget", material_scope="unit_self" if "unit" in id_ else "department_summary", review={"status": "pending"},
                     updated_at="", current_version=None) for id_, year in [
            ("city-dept", 2026), ("city-unit", 2025), ("district-dept", 2024),
            ("district-unit", 2023), ("other-dept", 2022)] if id_ in visible_org_ids]

    async def unlinked(conn, **kwargs):
        return [dict(item_id="legacy", slot_id=None, organization_id=None, fiscal_year=None,
                     report_kind="unknown", review={"status": "history_only"}, updated_at="")]

    async def connection(callback):
        return await callback(None)

    monkeypatch.setattr(query, "load_report_rows", load_rows)
    monkeypatch.setattr(query, "load_unlinked_reports", unlinked)
    monkeypatch.setattr(materials, "_with_connection", connection)
    app = FastAPI()
    app.include_router(materials.router)
    with TestClient(app) as result:
        yield result


def test_city_list_years_and_counts_exclude_districts(client):
    response = client.get("/api/materials/reports?management_scope=city&management_region=city&org=city&org_scope=descendants")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == data["material_total"] == 2
    assert data["years"] == [2026, 2025]
    assert data["organization_report_counts"] == {"city-dept": 1, "city-unit": 1}
    assert data["unassigned_total"] == 1, "无法识别市/区身份的历史材料应保留独立入口"
    assert data["legacy_total"] == 0


def test_district_scope_is_limited_to_selected_region(client):
    data = client.get("/api/materials/reports?management_scope=district&management_region=district").json()["data"]
    assert {item["organization_id"] for item in data["items"]} == {"district-dept", "district-unit"}
    assert data["years"] == [2024, 2023]
    assert data["material_total"] == 2


def test_management_filter_cannot_expand_authorization(client):
    assert client.get("/api/materials/reports?management_scope=district&management_region=other-district").status_code == 403
    assert client.get("/api/materials/reports?management_scope=city&org=other-dept").status_code == 403
    assert client.get("/api/materials/reports?management_scope=invalid").status_code == 422
    assert client.get("/api/materials/reports").json()["data"]["total"] == 5
    unresolved = client.get("/api/materials/reports?org=unassigned").json()["data"]
    assert unresolved["total"] == 1
    assert unresolved["items"][0]["item_id"] == "legacy"


@pytest.mark.parametrize("category,expected,year", [("department", "city-dept", 2026), ("units", "city-unit", 2025)])
def test_report_category_passes_through_api_without_changing_management_counts(client, category, expected, year):
    data = client.get(f"/api/materials/reports?management_scope=city&management_region=city&org=city-dept&org_scope=descendants&report_category={category}").json()["data"]
    assert [item["organization_id"] for item in data["items"]] == [expected]
    assert data["total"] == data["material_total"] == 1
    assert data["years"] == [year]
    assert data["organization_report_counts"] == {"city-dept": 1, "city-unit": 1}
    assert data["organization_unit_report_counts"] == {"city-unit": 1}
