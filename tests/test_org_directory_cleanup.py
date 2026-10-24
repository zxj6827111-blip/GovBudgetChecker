import io
import json

from fastapi.testclient import TestClient

from api.main import app
from api import runtime
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
