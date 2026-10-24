"""材料列表的版本、复核身份、计数与权限反例。"""
import json
from datetime import datetime, timezone
import pytest
from src.services.material_report_query import project_material, report_page, load_report_rows, review_summary


def row(**changes):
    value = dict(slot_id="slot",subject_org_id="org",subject_org_name="医保局",subject_kind="department",
        material_scope="department_summary",fiscal_year=2025,report_kind="final",slot_status="review_required",
        current_document_version_id=132,document_version_id=132,version_number=1,original_filename="医保局.pdf",
        file_hash="a"*64,version_created_at=datetime(2026,1,1,tzinfo=timezone.utc),id=1,
        job_uuid="old",job_visible=True,status="review_required",analysis_revision=1,result_id=1,
        created_at=datetime(2026,1,1,tzinfo=timezone.utc),history=[])
    value.update(changes)
    return value


def session(**changes):
    value=dict(slot_id="slot",document_version_id=132,analysis_job_uuid="current",
        analysis_basis_token="current:2",status="completed",completed_by="admin",
        completed_at="2026-10-23T11:08:18+00:00",issue_counts={"confirmed":6})
    value.update(changes)
    return value


def test_same_file_two_tasks_is_one_material_and_one_version():
    material=project_material([row(),row(id=2,job_uuid="current",analysis_revision=2)])
    assert material["version_count"]==1
    assert material["analysis_task_count"]==2
    assert material["current_version"]["version_number"]==1  # 不能把数据库132号显示成第132版。
    assert material["current_version"]["current_run"]["job_id"]=="current"


@pytest.mark.parametrize("change",[{"document_version_id":131},{"analysis_job_uuid":"old"},
    {"analysis_basis_token":"current:1"},{"slot_id":"other"},{"status":"invalidated"}])
def test_review_does_not_cross_identity(change):
    material=project_material([row(job_uuid="current",analysis_revision=2,history=[session(**change)])])
    assert material["review"]["status"]!="completed"
    assert material["review"]["confirmed_count"] is None


def test_completed_count_is_frozen_and_unknown_is_not_zero():
    material=project_material([row(job_uuid="current",analysis_revision=2,history=json.dumps([session()]))])
    assert material["review"]["confirmed_count"]==6
    assert review_summary("slot",132,row(job_uuid="current",analysis_revision=2),[session(issue_counts={})])["confirmed_count"] is None


def test_packaged_confirmed_issues_do_not_turn_into_zero_issues():
    material=project_material([row(job_uuid="current",analysis_revision=2,history=[session(issue_counts={"confirmed":2,"in_package":4})])])
    assert material["review"]["confirmed_count"]==6
    assert material["versions"][0]["reviews"][0]["confirmed_count"]==6


def test_new_file_without_analysis_keeps_old_completed_history():
    history=[session(analysis_job_uuid="old",analysis_basis_token="old:1")]
    material=project_material([row(current_document_version_id=133,history=history),
        row(document_version_id=133,current_document_version_id=133,version_number=2,id=None,
            job_uuid=None,job_visible=False,result_id=None,history=history)])
    assert material["review"]["status"]=="waiting_analysis"
    assert material["review"]["confirmed_count"] is None
    assert material["previous_review"]["document_version_id"]==132


def test_hidden_current_run_is_not_replaced_by_visible_old_run():
    material=project_material([row(),row(id=2,job_uuid="secret",job_visible=False)])
    assert material["current_version"]["current_run"] is None
    assert material["analysis_task_count"]==1
    assert material["current_version"]["analysis_unavailable"]


def test_page_filters_after_material_dedup_and_keeps_orphans():
    material=project_material([row(),row(id=2,job_uuid="current")])
    orphan={**material,"item_id":"slot:orphan","organization_id":"old-org"}
    data=report_page([material,orphan],organization_ids=None,unassigned=False,catalog_ids={"org"},
        year="",kind="all",keyword="",review="all",page=5,page_size=1)
    assert (data["total"],data["page"],data["page_count"])==(2,2,2)
    assert data["organization_report_counts"]=={"org":1}
    assert data["unassigned_total"]==1
    assert "versions" not in data["items"][0]
    assert "runs" not in data["items"][0]["current_version"]


@pytest.mark.asyncio
async def test_batch_query_never_selects_findings_and_filters_jobs_before_counts():
    class Conn:
        calls=0
        async def fetch(self,sql,*params):
            self.calls+=1
            assert "ai_findings" not in sql and "j.metadata" not in sql
            assert "ANY($1::text[])" in sql
            assert params==(["org"],)
            return [row(),row(id=2,job_uuid="secret")]
    conn=Conn()
    items=await load_report_rows(conn,visible_org_ids=["org"],job_accessible=lambda p:p["job_uuid"]!="secret")
    assert conn.calls==1
    assert items[0]["analysis_task_count"]==1


def test_department_and_units_partition_explicit_material_scope_even_with_same_org():
    def item(identifier, org, scope, year):
        return project_material([row(slot_id=identifier, subject_org_id=org,
            material_scope=scope, fiscal_year=year)])
    items = [item("summary", "org", "department_summary", 2026),
        item("own-unit", "org", "unit_self", 2025),
        item("child-unit", "child", "unit_self", 2024),
        item("unit-authored-summary", "child", "department_summary", 2023),
        item("unknown", "org", "unknown", 2022),
        item("other", "other", "unit_self", 2021)]
    args = dict(organization_ids={"org", "child"}, unassigned=False,
        catalog_ids={"org", "child", "other"}, year="", kind="all", keyword="",
        review="all", page=1, page_size=10)
    department = report_page(items, **args, report_category="department")
    units = report_page(items, **args, report_category="units")
    assert {i["slot_id"] for i in department["items"]} == {"summary", "unit-authored-summary"}
    assert {i["slot_id"] for i in units["items"]} == {"own-unit", "child-unit"}
    assert department["years"] == [2026, 2023]
    assert units["years"] == [2025, 2024]
    assert department["material_total"] == units["material_total"] == 2
    assert department["uncategorized_total"] == units["uncategorized_total"] == 1
    assert units["organization_unit_report_counts"] == {"org": 1, "child": 1, "other": 1}
    own = report_page(items, **{**args, "organization_ids": {"org"}}, report_category="units")
    assert [i["slot_id"] for i in own["items"]] == ["own-unit"]
    assert own["material_total"] == 1
    filtered = report_page(items, **{**args, "year": "2024"}, report_category="units")
    assert filtered["total"] == 1 and filtered["material_total"] == 2
    assert filtered["years"] == [2025, 2024]
    assert report_page(items, **args)["total"] == 5  # 待确认口径仍保留在全部材料中。


def test_directory_status_uses_count_scope_and_latest_year_per_kind():
    def item(identifier, org, scope, year, kind, review="pending", **changes):
        material = project_material([row(slot_id=identifier, subject_org_id=org, material_scope=scope,
            fiscal_year=year, report_kind=kind, **changes)])
        material["review"] = {"status": review, "confirmed_count": 9 if review == "completed" else None}
        return material
    items = [item("budget26", "org", "department_summary", 2026, "budget"),
        item("final25", "org", "department_summary", 2025, "final", "completed"),
        item("final24", "org", "department_summary", 2024, "final", "invalidated"),
        item("own-unit", "org", "unit_self", 2025, "final", "in_progress"),
        item("child", "child", "unit_self", 2024, "budget", "waiting_analysis", status="analyzing", result_id=None),
        item("conflict", "org", "department_summary", 2027, "budget", mapping_key="pending-map"),
        item("unknown-scope", "org", "unknown", 2028, "final")]
    orphan = {**item("orphan", "removed-org", "department_summary", 2026, "final")}
    data = report_page([*items, orphan], organization_ids={"org"}, unassigned=False,
        catalog_ids={"org", "child"}, year="", kind="all", keyword="", review="all", page=1, page_size=10)
    status = data["organization_review_status"]
    assert set(status) == {"org", "child"}, "未入组织目录或需确认归属的材料不进入目录状态"
    assert status["org"]["review_counts"] == {"pending": 2, "completed": 1, "invalidated": 1, "in_progress": 1}
    assert status["org"]["unit_review_counts"] == {"in_progress": 1}
    assert status["org"]["latest"] == {
        "department_summary:budget": {"fiscal_year": 2026, "review_status": "pending", "confirmed_count": None, "run_status": "review_required"},
        "department_summary:final": {"fiscal_year": 2025, "review_status": "completed", "confirmed_count": 9, "run_status": "review_required"},
        "unit_self:final": {"fiscal_year": 2025, "review_status": "in_progress", "confirmed_count": None, "run_status": "review_required"},
    }, "按口径取预算、决算各自最新年度；口径待确认的材料不进入最新状态"
    assert status["child"]["latest"]["unit_self:budget"]["run_status"] == "analyzing"
    assert sum(data["review_status_counts"].values()) == data["material_total"] == 6
    assert data["review_status_counts"] == {"pending": 3, "completed": 1, "invalidated": 1, "in_progress": 1}
