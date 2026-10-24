"""主管端取数：只为当前运行补取结论，raw_response 只投影人工判定要读的位置。"""

from contextlib import nullcontext
from datetime import datetime, timezone

import pytest

from src.services import issue_workflow_store
from src.services import supervision_query as sq


def _slim(payload):
    """与 ``_DECISION_PAYLOAD_PROJECTION`` 同义的 Python 版：缺失或类型不符的路径为 None。"""
    out = {}
    for path in issue_workflow_store.DECISION_PAYLOAD_PATHS:
        target, source = out, payload
        for key in path[:-1]:
            target = target.setdefault(key, {})
            source = source.get(key) if isinstance(source, dict) else None
        target[path[-1]] = source.get(path[-1]) if isinstance(source, dict) else None
    return out


def _issue(issue_id, **extra):
    return {"id": issue_id, "title": f"问题 {issue_id}", "severity": "high", "message": "说明", **extra}


REVISION = "rev-20261022"
FULL_PAYLOAD = {
    "job_id": "job-a",
    "analysis_revision": REVISION,
    "ai_findings": [_issue("top-ai")],
    "rule_findings": [_issue("top-rule")],
    "issues": {"error": [_issue("top-issue")], "warn": []},
    "result": {
        "ai_findings": [_issue("result-ai")],
        "rule_findings": [_issue("result-rule")],
        "issues": [_issue("result-issue")],
        "merged": {"large": "x" * 5000},
    },
    "structured_ingest": {
        "review_items": [{"id": "structured-1", "type": "review", "table_code": "T1", "page": 2, "title": "结构化识别待复核"}],
        "fact_receipts": ["x" * 5000],
    },
    "review_report": {"manual_review": [{"record_type": "review_reason", "code": "ai_failed", "id": "reason-1"}],
                      "summary": "x" * 5000},
    "check_coverage": {"large": "x" * 5000},
}
DECIDED = ["top-ai", "top-rule", "top-issue", "result-ai", "result-rule", "result-issue", "structured-1", "reason-1"]


def _state(issue_ids, job_id="job-a"):
    return {"decision_events": [
        {"job_id": job_id, "issue_id": issue_id, "status": "confirmed", "analysis_revision": REVISION,
         "decision_revision": index + 1, "updated_at": "2026-10-22T00:00:00Z"}
        for index, issue_id in enumerate(issue_ids)
    ]}


@pytest.mark.parametrize("payload", [
    FULL_PAYLOAD,
    # 父节点类型不符或缺失：数据库投影得到 null，查找结果必须与完整报告一致。
    {**FULL_PAYLOAD, "result": ["not", "a", "dict"], "review_report": None},
    {key: value for key, value in FULL_PAYLOAD.items() if key not in {"structured_ingest", "analysis_revision"}},
])
def test_projected_payload_yields_the_same_decisions(payload):
    state = _state(DECIDED + ["missing-issue"])
    full = issue_workflow_store.project_job_issue_decisions("job-a", payload, _state=state)
    slim = issue_workflow_store.project_job_issue_decisions("job-a", _slim(payload), _state=state)
    assert slim == full


def test_projection_covers_every_lookup_location():
    decisions = issue_workflow_store.project_job_issue_decisions("job-a", _slim(FULL_PAYLOAD), _state=_state(DECIDED))
    assert sorted(item["issue_id"] for item in decisions) == sorted(DECIDED), "每个查找位置都必须投影出来"


def test_sql_projection_renders_every_path_from_the_shared_list():
    sql = sq._DECISION_PAYLOAD_PROJECTION
    for path in issue_workflow_store.DECISION_PAYLOAD_PATHS:
        source = "r.raw_response" + "".join(f" -> '{key}'" for key in path)
        assert f"'{path[-1]}', {source}" in sql
    assert "'result', jsonb_build_object('ai_findings', r.raw_response -> 'result' -> 'ai_findings'" in sql


@pytest.mark.asyncio
async def test_build_items_fetches_details_only_for_current_runs(monkeypatch):
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    earlier = datetime(2026, 10, 1, tzinfo=timezone.utc)
    slot = dict(subject_org_id="unit", subject_org_name="单位", subject_kind="unit", department_org_id="dept",
                department_name="部门", fiscal_year=2025, report_kind="final", slot_status="uploaded",
                status_reason=None, applicability_status="applicable", due_at=None, current_document_version_id=12,
                mapping_key=None, caliber_conflict_candidate=False, slot_updated_at=now, original_filename="决算.pdf")
    run = dict(status="processing", analysis_revision=1, created_at=now, updated_at=now, started_at=now,
               completed_at=now, result_id=None)
    rows = [
        dict(slot, slot_id="a", id=1, job_uuid="old", **{**run, "completed_at": earlier, "updated_at": earlier}),
        dict(slot, slot_id="a", id=2, job_uuid="current", **run),
        dict(slot, slot_id="b", id=3, job_uuid="gone", **run),
    ]
    calls = []

    class Connection:
        async def fetch(self, sql, *params):
            calls.append((sql, params))
            if "FROM analysis_jobs j LEFT JOIN analysis_results r" in sql:
                assert sorted(params[0]) == [2, 3], "只补取各材料的当前运行"
                assert params[1] == ["current"], "raw_response 只为有判定记录的任务投影"
                return [dict(id=2, status="done", analysis_revision=3, metadata="{}", result_id=9,
                             ai_findings="[]", rule_findings="[]", merged_result="{}", raw_response=None)]
            return []

    monkeypatch.setattr(issue_workflow_store, "_state_lock", nullcontext)
    monkeypatch.setattr(issue_workflow_store, "_read_state",
                        lambda: {"decision_events": [{"job_id": "current", "issue_id": "x"}, {"job_id": ""}]})
    items = {item["slot_id"]: item for item in await sq.build_items(Connection(), rows)}
    assert items["a"]["job_id"] == "current" and items["a"]["analyzed"] is True
    assert items["a"]["basis_token"].startswith("current:3"), "代际取自第二段，不与第一段拼接"
    assert items["b"]["job_id"] is None and items["b"]["review_status"] == "waiting_analysis", "两段之间被删的运行按未分析处理"
    assert sum("FROM analysis_jobs j LEFT JOIN analysis_results r" in sql for sql, _ in calls) == 1


@pytest.mark.asyncio
async def test_load_rows_selects_only_light_columns():
    captured = []

    class Connection:
        async def fetch(self, sql, *params):
            captured.append(sql)
            return []

    await sq.load_rows(Connection(), visible_org_ids=None, fiscal_year=2025, report_kind="final")
    sql = captured[0]
    for heavy in ("metadata", "raw_response", "ai_findings", "rule_findings", "merged_result"):
        assert heavy not in sql
    assert "j.structured_document_version_id = v.id::numeric" in sql
