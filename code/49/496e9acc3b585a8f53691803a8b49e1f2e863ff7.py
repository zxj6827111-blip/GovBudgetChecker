"""复核清单的当前版本、代际、授权与未知计数边界。"""

from datetime import datetime, timezone

import pytest

from src.services import review_lifecycle_service as lifecycle
from src.services.review_context_query import ReviewAnalysis
from src.services.review_worklist_service import load_review_worklist


def analysis(**updates):
    values = dict(job_uuid="current", analysis_revision=2, document_version_id=12, completed=True,
                  result_row={"ai_findings": [], "rule_findings": [], "merged_result": {}},
                  result_meta={}, structured_ingest={})
    values.update(updates)
    return ReviewAnalysis(**values)


def session(**updates):
    values = dict(slot_id="slot", review_session_id="session", document_version_id=12,
                  analysis_job_uuid="current", analysis_basis_token="current:2", status="completed")
    values.update(updates)
    return values


@pytest.fixture(autouse=True)
def isolated_decisions(monkeypatch):
    monkeypatch.setattr(lifecycle, "_issue_decision_snapshot", lambda _: {"decisions": {}, "ignored_issue_ids": []})


@pytest.mark.parametrize("history,expected", [
    ([], "pending"), ([session()], "completed"), ([session(status="in_progress")], "in_progress"),
    ([session(analysis_basis_token="current:1")], "invalidated"),
    ([session(document_version_id=11)], "invalidated"),
    ([session(analysis_job_uuid="other")], "invalidated"),
    ([session(status="invalidated")], "invalidated"),
])
def test_current_review_status_is_bound_to_version_and_generation(history, expected):
    result = lifecycle.build_worklist_review_summary({"slot_id": "slot"}, analysis(), history, [])
    assert result["review_status"] == expected
    assert result["pending_checks"] is None


def test_unfinished_analysis_does_not_inherit_completed_review_or_zero_counts():
    result = lifecycle.build_worklist_review_summary({"slot_id": "slot"}, analysis(completed=False), [session()], [])
    assert result["review_status"] == "waiting_analysis"
    assert result["pending_findings"] is None
    assert result["pending_checks"] is None


@pytest.mark.asyncio
async def test_batch_worklist_deduplicates_runs_and_keeps_only_unlinked_history():
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    base = dict(slot_id="slot", current_document_version_id=12, document_version_id=12,
                subject_org_id="unit", subject_org_name="单位", department_name="部门", fiscal_year=2026,
                report_kind="budget", original_filename="预算.pdf", metadata={}, status="done", result_id=1,
                ai_findings=[], rule_findings=[], merged_result={}, history=[], started_at=now,
                completed_at=now, created_at=now, updated_at=now)
    calls = []

    class Connection:
        async def fetch(self, sql, *params):
            calls.append((sql, params))
            if "WITH visible_slots" in sql:
                assert params == (["unit"],)
                assert "subject_org_id = ANY" in sql
                assert "current_document_version_id" in sql
                return [dict(base, id=1, job_uuid="old", analysis_revision=1), dict(base, id=2, job_uuid="current", analysis_revision=2)]
            if "WHERE j.job_uuid = ANY" in sql:
                return [{"job_uuid": "old"}, {"job_uuid": "current"}]
            raise AssertionError("Unexpected query")

    data = await load_review_worklist(Connection(), jobs=[
        {"job_id": "old", "status": "done"}, {"job_id": "current", "status": "review_required"},
        {"job_id": "legacy", "status": "done"}, {"job_id": "failed", "status": "error"},
    ], visible_org_ids=["unit"], job_accessible=lambda _: True, viewer="reviewer")
    assert [(item.job_id, item.review_status) for item in data.items] == [("current", "pending"), ("legacy", "history_only")]
    assert data.pending_total == 1
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_job_denial_excludes_slot_even_when_material_is_visible():
    class Connection:
        async def fetch(self, sql, *params):
            return [dict(slot_id="slot", id=1, job_uuid="denied", history=[])]
    data = await load_review_worklist(Connection(), jobs=[], visible_org_ids=None,
                                    job_accessible=lambda _: False, viewer="reviewer")
    assert data.items == []
    assert data.pending_total == 0
