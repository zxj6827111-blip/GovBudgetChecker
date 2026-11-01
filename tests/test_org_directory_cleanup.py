import io
import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from api import runtime
from scripts import clean_org_directory, report_org_duplicates
from scripts.clean_org_directory import plan_directory, rewrite_ids
from tests.test_upload_auto_match import _patch_runtime_state, _create_org, _headers


def test_strict_duplicate_formats_are_rejected_by_api(tmp_path, monkeypatch):
    _patch_runtime_state(tmp_path, monkeypatch)
    _create_org("甲市财政局（部门）")
    response = TestClient(app).post(
        "/api/organizations",
        headers=_headers(),
        json={"name": "甲市财政局(部门)", "level": "department"},
    )
    assert response.status_code == 422
    assert "重复组织" in response.text


def test_tail_warning_requires_explicit_confirmation_and_does_not_block_internal_budget_word(
    tmp_path, monkeypatch
):
    _patch_runtime_state(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/organizations",
        headers=_headers(),
        json={"name": "甲市管理局2022", "level": "department"},
    )
    assert response.status_code == 422
    assert "confirmation_required" in response.text
    assert (
        client.post(
            "/api/organizations",
            headers=_headers(),
            json={"name": "甲市预算编审中心", "level": "department"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/organizations",
            headers=_headers(),
            json={"name": "甲市管理局2022", "level": "department", "confirm_name_warning": True},
        ).status_code
        == 200
    )


def test_copy_plan_merges_only_strict_duplicates_and_rewrites_all_known_id_fields():
    orgs = [
        {"id": "a", "name": "甲市局（本级）", "level": "unit", "parent_id": "d"},
        {"id": "b", "name": "甲市局(本级)", "level": "unit", "parent_id": "d"},
        {"id": "c", "name": "甲市局本部", "level": "unit", "parent_id": "d"},
    ]
    cleaned, mapping, review = plan_directory({"organizations": orgs})
    assert mapping == {"b": "a"}
    assert len(cleaned["organizations"]) == 2
    assert review
    assert rewrite_ids(
        {"organization_id": "b", "nested": {"org_id": "b"}, "text": "b"}, mapping
    ) == {"organization_id": "a", "nested": {"org_id": "a"}, "text": "b"}


def test_external_review_group_never_authorizes_identity_merge():
    orgs = [{"id": "a", "name": "甲市局（本级）", "level": "unit", "parent_id": "d"},
            {"id": "b", "name": "甲市局（机关）", "level": "unit", "parent_id": "d"}]
    original = {"organizations": orgs}
    cleaned, mapping, review = plan_directory(original, [[org["name"] for org in orgs]])
    assert cleaned == original and mapping == {}
    assert {r["id"] for r in review if r["kind"] == "external_group_review"} == {"a", "b"}


def test_approved_variant_group_merges_only_when_written_forms_differ():
    orgs = [
        {"id": "a", "name": "甲市局（本级）", "level": "unit", "parent_id": "d"},
        {"id": "b", "name": "甲市局(本级)", "level": "unit", "parent_id": "d"},
        {"id": "c", "name": "甲市局本级", "level": "unit", "parent_id": "d"},
    ]
    cleaned, mapping, review = plan_directory(
        {"organizations": orgs},
        approve_groups=[["甲市局本级", "甲市局（本级）", "甲市局(本级)"]],
    )
    assert mapping == {"b": "a", "c": "a"}
    assert [org["id"] for org in cleaned["organizations"]] == ["a"]


def test_approved_group_with_conflicting_identity_markers_is_rejected():
    orgs = [{"id": "a", "name": "甲市局（本级）", "level": "unit", "parent_id": "d"},
            {"id": "b", "name": "甲市局（机关）", "level": "unit", "parent_id": "d"}]
    try:
        plan_directory({"organizations": orgs}, approve_groups=[["甲市局（本级）", "甲市局（机关）"]])
    except ValueError as exc:
        assert "身份不一致" in str(exc)
    else:
        raise AssertionError("机关与本级不能被自动合并")


def test_keeper_prefers_records_that_still_hold_materials():
    orgs = [
        {"id": "old", "name": "甲市局（本级）", "level": "unit", "parent_id": "d", "created_at": 1},
        {"id": "used", "name": "甲市局本级", "level": "unit", "parent_id": "d", "created_at": 9},
    ]
    _cleaned, mapping, _review = plan_directory(
        {"organizations": orgs},
        approve_groups=[["甲市局本级", "甲市局（本级）"]],
        referenced_ids={"used"},
    )
    assert mapping == {"old": "used"}


def test_reporter_separates_strict_duplicates_from_written_variants():
    orgs = [
        {"id": "a", "name": "甲市局（本级）", "level": "unit", "parent_id": "d", "created_at": 1},
        {"id": "b", "name": "甲市局(本级)", "level": "unit", "parent_id": "d", "created_at": 2},
        {"id": "c", "name": "甲市局本级", "level": "unit", "parent_id": "d", "created_at": 3},
        {"id": "d1", "name": "乙市局（本级）", "level": "unit", "parent_id": "d", "created_at": 4},
        {"id": "e1", "name": "乙市局(本级)", "level": "unit", "parent_id": "d", "created_at": 5},
    ]
    strict, variant = report_org_duplicates.classify(orgs)
    assert [[org["id"] for org in group] for group in strict] == [["d1", "e1"]]
    assert [[org["id"] for org in group] for group in variant] == [["a", "b", "c"]]


def test_database_reference_scan_matches_any_org_id_column():
    sql = clean_org_directory.ORG_REFERENCE_COLUMN_SQL
    # 用通配而非逐个列名列举：material_slots 的 subject_org_id 一类列才不会被漏掉。
    assert "column_name LIKE '%org_id%'" in sql
    assert "table_schema='public'" in sql
