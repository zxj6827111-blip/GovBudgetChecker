"""主管端口径与权限反例；全部账号和文件在临时目录。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.auth_utils import enforce_read_only, user_can_access_job
from api.routes.supervision import IssueVerification, verify_comparisons
from src.services.supervision_metrics import category, overdue, paginate, summarize
from src.services.supervision_query import confirmed_snapshot
from tests.test_workflow_rbac import client, _headers, _login, _create_org


def item(id="slot-a", **changes):
    base = {"slot_id": id, "organization_id": "unit-a", "organization_name": "单位", "department_id": "dept-a", "department_name": "同名部门", "subject_kind": "unit",
        "uploaded": True, "applicability_status": "applicable", "confirmed_count": 2, "pending_count": 1,
        "review_status": "completed", "analyzed": True, "status": "completed", "overdue": False,
        "rounds_count": 0, "history_incomplete": False,
        "findings": [{"issue_id": "a", "title": "金额勾稽不一致", "severity": "high"}, {"issue_id": "b", "title": "占比口径不一致", "severity": "medium"}]}
    return {**base, **changes}


def test_no_baseline_is_unknown_not_zero():
    summary, departments = summarize([item(uploaded=False, confirmed_count=None, findings=[], analyzed=False, review_status="waiting_analysis")], None)
    assert summary["expected"] is None and summary["missing"] is None
    assert summary["empty_departments"] is None and departments[0]["problem_rate"] is None
    assert summary["unknown_analysis"] == 1


def test_specific_issue_categories_precede_generic_amount_words():
    assert category("预算完成率分母为0") == "比例与同比口径"
    assert category("功能分类名称与表格不一致") == "文字与科目表述"
    assert category("支出决算表缺失") == "公开要素完整性"
    assert category("金额合计不一致") == "表内表间勾稽"


def test_confirmation_notes_reject_whitespace_only():
    from pydantic import ValidationError
    from api.routes.supervision import RoundRequest

    with pytest.raises(ValidationError):
        RoundRequest(document_version_id=1, note="     ")


def test_unfinished_checks_do_not_count_as_confirmed_issues():
    summary, _ = summarize([item(confirmed_count=0, findings=[], pending_count=0, pending_checks=5), item("b", pending_checks=None)], None)
    assert summary["pending_checks"] == 5
    assert summary["unknown_coverage"] == 1
    assert summary["confirmed"] == 2


def test_baseline_department_partial_empty_and_not_applicable():
    values = [item(), item("b", uploaded=False, confirmed_count=None, findings=[], review_status="waiting_analysis"),
        item("c", department_id="dept-b", uploaded=False, confirmed_count=None, findings=[], review_status="waiting_analysis"),
        item("d", department_id="dept-c", uploaded=False, applicability_status="not_applicable", confirmed_count=None, findings=[], review_status="waiting_analysis")]
    summary, departments = summarize(values, {i["slot_id"] for i in values})
    assert (summary["expected"], summary["received_expected"], summary["missing"]) == (3, 1, 2)
    assert (summary["empty_departments"], summary["partial_departments"]) == (1, 1)
    assert len(departments) == 3, "同名部门按组织 ID 分开"
    assert summary["confirmed"] == 2


def test_rounds_are_registered_not_inferred_from_versions():
    summary, _ = summarize([item(history_incomplete=True), item("b", rounds_count=3)], None)
    assert summary["repeat_materials"] == 1
    assert summary["unregistered_history"] == 1


def test_only_reviewed_materials_form_problem_rate_denominator():
    _, departments = summarize([item(), item("b", review_status="pending", confirmed_count=0, findings=[])], None)
    assert departments[0]["reviewed"] == 1 and departments[0]["problem_rate"] == 1
    summary, _ = summarize([item(confirmed_count=5)], None)
    assert sum(c["count"] for c in summary["categories"]) == 5
    assert any(c["name"] == "历史确认问题未细分" for c in summary["categories"])


def test_no_due_date_no_overdue_and_large_pagination():
    assert not overdue(None, False, "applicable")
    assert not overdue(datetime.now(), False, "applicable")
    past = datetime.now(timezone.utc) - timedelta(days=2)
    assert overdue(past, False, "applicable")
    assert not overdue(past, False, "not_applicable")
    result = paginate([{"id": i} for i in range(351)], 18, 20)
    assert result["total_pages"] == 18 and len(result["items"]) == 11


@pytest.mark.parametrize("path", ["/api/workflow", "/api/analyze/job-a", "/api/reviews/slot-a/complete", "/api/materials/expected/import", "/api/supervision/baselines"])
def test_readonly_rejects_all_mutations(path):
    with pytest.raises(HTTPException) as exc:
        enforce_read_only(SimpleNamespace(method="POST", url=SimpleNamespace(path=path)), {"is_read_only": True})
    assert exc.value.status_code == 403


def test_readonly_auth_operations_and_owner_fallback():
    for method, path in [("GET", "/api/supervision"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/change-password")]:
        enforce_read_only(SimpleNamespace(method=method, url=SimpleNamespace(path=path)), {"is_read_only": True})
    assert not user_can_access_job({"username": "viewer", "is_read_only": True}, {"created_by": "viewer"})


def test_snapshot_freezes_only_confirmed_problem_identity():
    from src.services.review_problem_set import ReviewProblem
    problems = [ReviewProblem("a", "finding", "真实确认问题", "high", True), ReviewProblem("b", "finding", "未确认线索", "high", True)]
    result = confirmed_snapshot(SimpleNamespace(problems=problems, decisions={"a": "confirmed"}, obligation_decisions={"OBL": {"decision": "verified_issue", "note": "原文缺少说明", "evidence_reference": "原文章节"}}))
    assert {r["issue_id"] for r in result} == {"a", "obligation:OBL"}


def test_issue_disappearance_does_not_automatically_mean_fixed():
    previous, current = [{"issue_id": "old"}], [{"issue_id": "new"}]
    with pytest.raises(HTTPException):
        verify_comparisons(previous, current, [])
    result = verify_comparisons(previous, current, [IssueVerification(previous_issue_id="old", resolution="remains", current_issue_id="new")])
    assert (result["fixed"], result["remaining"], result["new"]) == (0, 1, 0)
    result = verify_comparisons(previous, current, [IssueVerification(previous_issue_id="old", resolution="fixed")])
    assert (result["fixed"], result["new"]) == (1, 1)
    with pytest.raises(HTTPException):
        verify_comparisons(previous, current, [IssueVerification(previous_issue_id="old", resolution="remains", current_issue_id="fabricated")])


def test_supervisor_account_persistence_and_write_auth(client):  # noqa: F811 -- 复用导入的 pytest fixture
    org_id = _create_org("主管账号测试单位")
    admin = _login(client, "admin", "AdminPass123")
    response = client.post("/api/users", headers=_headers(admin), json={"username": "supervisor", "password": "TestUser123", "is_read_only": True, "organization_ids": [org_id]})
    assert response.status_code == 200 and response.json()["is_read_only"] is True
    token = _login(client, "supervisor", "TestUser123")
    assert client.get("/api/auth/me", headers=_headers(token)).json()["user"]["is_read_only"] is True
    assert client.post("/api/workflow", headers=_headers(token), json={"action": "update_issue", "job_id": "j", "issue_id": "i", "status": "confirmed"}).status_code == 403
    assert client.post("/api/users", headers=_headers(admin), json={"username": "noscope", "password": "TestUser123", "is_read_only": True}).status_code == 400
    assert client.post("/api/users", headers=_headers(admin), json={"username": "mixed", "password": "TestUser123", "is_read_only": True, "is_admin": True, "organization_ids": [org_id]}).status_code == 400


def test_switching_to_readonly_revokes_existing_session(client):  # noqa: F811 -- 复用导入的 pytest fixture
    org_id = _create_org("切换权限测试单位")
    admin = _login(client, "admin", "AdminPass123")
    assert client.post("/api/users", headers=_headers(admin), json={"username": "worker", "password": "TestUser123", "organization_ids": [org_id]}).status_code == 200
    old = _login(client, "worker", "TestUser123")
    assert client.patch("/api/users/worker", headers=_headers(admin), json={"is_read_only": True}).status_code == 200
    assert client.get("/api/auth/me", headers=_headers(old)).status_code == 401


def test_supervisor_scope_filter_cannot_open_sibling(client):  # noqa: F811 -- 复用导入的 pytest fixture
    allowed, sibling = _create_org("授权单位"), _create_org("未授权单位")
    admin = _login(client, "admin", "AdminPass123")
    assert client.post("/api/users", headers=_headers(admin), json={"username": "scoped", "password": "TestUser123", "is_read_only": True, "organization_ids": [allowed]}).status_code == 200
    token = _login(client, "scoped", "TestUser123")
    response = client.get("/api/supervision", headers=_headers(token), params={"fiscal_year": 2026, "scope_id": sibling})
    assert response.status_code == 403
    assert "未授权单位" not in response.text
