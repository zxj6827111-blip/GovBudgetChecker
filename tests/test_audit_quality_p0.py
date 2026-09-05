"""审查质量整改 P0 契约测试（fix/audit-quality-remediation-20260905）。

覆盖 docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md §3.5/§3.6 的修复：
1. AI 执行状态机：请求 AI 后只有 succeeded 可以通过质量门；
2. 分析请求契约：legacy/structured 不请求 AI，冲突参数 422；
3. 质量门新增原因码：missing_required_table / ambiguous_table_schema /
   rule_evidence_incomplete / rule_execution_error / report_type_mismatch /
   rules_not_executed；
4. 报告类型归一化：dept_final/unit_final → FINAL，未知类型禁止默认 BUDGET；
5. RuleOutcome：只有 fail 能生成 finding，其余状态进运行摘要。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest
from fastapi import HTTPException

from api import runtime
from api.main import _evaluate_quality_gate
from config.settings import get_settings
from src.engine import pipeline as engine_pipeline
from src.engine.rule_outcome import (
    RuleDeferred,
    RuleOutcome,
    summarize_rule_outcomes,
)
from src.services.ai_execution import (
    AI_STATE_DEGRADED,
    AI_STATE_FAILED,
    AI_STATE_NOT_REQUESTED,
    AI_STATE_NOT_RUN,
    AI_STATE_SUCCEEDED,
    ai_execution_is_successful,
    build_ai_execution,
    quality_gate_ai_reasons,
)
from src.services.ps_schema_sync import PSSharedSchemaSync

GOOD_PAGES = {
    "page_count": 3,
    "text_page_count": 3,
    "low_text_pages": [],
    "low_text_page_count": 0,
    "scanned_pages": [],
    "scanned_page_count": 0,
    "page_coverage": 1.0,
}

FULL_EXECUTED_SUMMARY = {
    "total_rules": 10,
    "executed": 10,
    "pass": 10,
    "fail": 0,
    "not_applicable": 0,
    "insufficient_data": 0,
    "parse_error": 0,
    "execution_error": 0,
    "unresolved_total": 0,
    "failed_rules": [],
    "unresolved_rules": [],
}


def _reason_codes(gate) -> List[str]:
    return [reason["code"] for reason in gate["review_reasons"]]


# ---------------------------------------------------------------------------
# 1. AI 执行状态机
# ---------------------------------------------------------------------------


def test_ai_execution_not_requested():
    record = build_ai_execution(False)
    assert record["state"] == AI_STATE_NOT_REQUESTED
    assert record["requested"] is False
    assert ai_execution_is_successful(record) is True


def test_ai_execution_succeeded_with_call_evidence():
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "prompt_version": "v1",
            "finish_reason": "stop",
            "token_usage": {"total_tokens": 1234},
            "content": '[{"problem_type":"ratio_recalc"}]',
            "error": None,
        }
    ]
    record = build_ai_execution(True, call_ledger=ledger)
    assert record["state"] == AI_STATE_SUCCEEDED
    assert record["provider"] == "zhipu"
    assert record["model"] == "glm-4.5-flash"
    assert record["token_usage"] == {"total_tokens": 1234}
    assert ai_execution_is_successful(record) is True


def test_ai_execution_truncated_response_is_failed():
    """finish_reason=length（token 截断）按失败处理，不得解释为未发现问题。"""
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "finish_reason": "length",
            "content": '{"partial"',
            "error": None,
        }
    ]
    record = build_ai_execution(True, call_ledger=ledger)
    assert record["state"] == AI_STATE_FAILED


def test_ai_execution_empty_content_is_failed():
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "finish_reason": "stop",
            "content": "",
            "error": None,
        }
    ]
    record = build_ai_execution(True, call_ledger=ledger)
    assert record["state"] == AI_STATE_FAILED


def test_ai_execution_window_limit_is_degraded():
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "finish_reason": "stop",
            "content": "[]",
            "error": None,
        }
    ]
    record = build_ai_execution(
        True,
        call_ledger=ledger,
        window_errors=[{"type": "audit_window_limit_reached"}],
    )
    assert record["state"] == AI_STATE_DEGRADED
    assert ai_execution_is_successful(record) is False


def test_ai_execution_all_calls_failed():
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "error": "timeout",
        }
    ]
    record = build_ai_execution(True, call_ledger=ledger)
    assert record["state"] == AI_STATE_FAILED
    assert record["error_code"] == "timeout"


def test_ai_execution_no_evidence_is_not_run():
    """没有任何调用留痕时禁止呈现为已执行——这是假完成的核心判据。"""
    record = build_ai_execution(True, ai_error="")
    assert record["state"] == AI_STATE_NOT_RUN
    assert ai_execution_is_successful(record) is False
    record_with_error = build_ai_execution(True, ai_error="AI分析超时(90s)")
    assert record_with_error["state"] == AI_STATE_FAILED
    assert record_with_error["error_code"] == "ai_timeout"


def test_ai_execution_fallback_counts_as_degraded():
    ledger = [
        {
            "provider": "zhipu",
            "model": "glm-4.5-flash",
            "finish_reason": "stop",
            "content": "[]",
            "error": None,
        }
    ]
    record = build_ai_execution(True, call_ledger=ledger, fallback="engine_only")
    assert record["state"] == AI_STATE_DEGRADED


def test_ai_gate_reasons_not_run_and_failed():
    not_run = {"requested": True, "state": AI_STATE_NOT_RUN, "error_code": "legacy_mode_no_ai"}
    assert [r["code"] for r in quality_gate_ai_reasons(not_run)] == ["ai_not_run"]
    failed = {"requested": True, "state": AI_STATE_FAILED, "error_code": "ai_timeout"}
    assert [r["code"] for r in quality_gate_ai_reasons(failed)] == ["ai_failed"]
    succeeded = {"requested": True, "state": AI_STATE_SUCCEEDED}
    assert quality_gate_ai_reasons(succeeded) == []
    assert quality_gate_ai_reasons({"requested": False, "state": AI_STATE_NOT_REQUESTED}) == []


@pytest.mark.asyncio
async def test_legacy_pipeline_records_ai_not_run_for_stale_ai_flag(tmp_path, monkeypatch):
    """旧任务 status.json 残留 legacy + use_ai_assist=true：如实记 not_run 并转复核。"""

    from api import main as pipeline_mod
    from unittest.mock import AsyncMock

    job_dir = tmp_path / "job-legacy-ai"
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": True,  # 旧默认值残留
            "report_year": 2025,
            "report_kind": "budget",
        },
    )
    full_text = "一般公共预算财政拨款支出预算表" * 10

    class _FakePdf:
        pages = [object()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(pipeline_mod.pdfplumber, "open", lambda _path: _FakePdf())
    monkeypatch.setattr(pipeline_mod, "_extract_visible_text_from_page", lambda _page: full_text)
    monkeypatch.setattr(pipeline_mod, "_extract_tables_from_page", lambda _page: [])
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(
            return_value={
                "issues": {"all": [], "error": [], "warn": [], "info": []},
                "rule_execution_summary": dict(FULL_EXECUTED_SUMMARY),
            }
        ),
    )
    monkeypatch.setattr(pipeline_mod, "persist_analysis_job_snapshot", AsyncMock(return_value=True))
    monkeypatch.setattr(
        pipeline_mod,
        "run_structured_ingest",
        AsyncMock(return_value={"status": "skipped", "review_item_count": 0, "review_items": []}),
    )
    monkeypatch.setattr(pipeline_mod.settings, "is_dual_mode_enabled", lambda: False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    ai_execution = payload["result"]["meta"]["ai_execution"]
    assert ai_execution["requested"] is True
    assert ai_execution["state"] == AI_STATE_NOT_RUN
    assert ai_execution["error_code"] == "legacy_mode_no_ai"
    assert payload["status"] == "review_required"
    assert "ai_not_run" in [r["code"] for r in payload["review_reasons"]]


# ---------------------------------------------------------------------------
# 2. 分析请求契约
# ---------------------------------------------------------------------------


class _DummyQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


async def _dummy_runner(_job_dir):
    return None


def _prepare_job(tmp_path, name="job-contract"):
    job_dir = tmp_path / name
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    return job_dir


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body, detail_fragment",
    [
        ({"mode": "legacy", "use_ai_assist": True}, "conflicting analysis parameters"),
        ({"mode": "structured", "use_ai_assist": True}, "conflicting analysis parameters"),
        ({"mode": "legacy", "use_local_rules": False}, "use_local_rules"),
        ({"mode": "auto"}, "invalid mode"),
        ({"mode": "dual", "use_ai_assist": True, "_disable_dual": True}, "dual mode is disabled"),
    ],
)
async def test_start_analysis_rejects_conflicting_requests(
    tmp_path, monkeypatch, body, detail_fragment
):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)
    if body.pop("_disable_dual", False):
        monkeypatch.setattr(
            runtime, "get_settings", lambda: _settings_with_dual(False)
        )
    else:
        monkeypatch.setattr(
            runtime, "get_settings", lambda: _settings_with_dual(True)
        )

    _prepare_job(tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        await runtime.start_analysis("job-contract", body)
    assert excinfo.value.status_code == 422
    assert detail_fragment in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_start_analysis_legacy_defaults_to_no_ai(tmp_path, monkeypatch):
    """legacy 请求默认不请求 AI：status.json 的 use_ai_assist 必须为 False。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings_with_dual(True))

    _prepare_job(tmp_path)
    await runtime.start_analysis("job-contract", {})
    status = runtime.read_json_file(tmp_path / "job-contract" / "status.json")
    assert status["mode"] == "legacy"
    assert status["use_ai_assist"] is False


@pytest.mark.asyncio
async def test_start_analysis_dual_defaults_to_ai_requested(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings_with_dual(True))

    _prepare_job(tmp_path)
    await runtime.start_analysis("job-contract", {"mode": "dual"})
    status = runtime.read_json_file(tmp_path / "job-contract" / "status.json")
    assert status["mode"] == "dual"
    assert status["use_ai_assist"] is True


class _SettingsStub:
    def __init__(self, dual_enabled: bool):
        self._dual_enabled = dual_enabled

    def is_dual_mode_enabled(self) -> bool:
        return self._dual_enabled


def _settings_with_dual(enabled: bool) -> _SettingsStub:
    return _SettingsStub(enabled)


async def _fake_persist(_payload, include_results: bool = False):
    return None


# ---------------------------------------------------------------------------
# 3. 质量门新增原因码
# ---------------------------------------------------------------------------


def _base_gate_kwargs(**overrides):
    kwargs = dict(
        page_assessment=GOOD_PAGES,
        report_kind="final",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=0,
        evidence_degraded_count=0,
    )
    kwargs.update(overrides)
    return kwargs


def test_gate_missing_required_table_reason():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            structured_ingest={
                "review_items": [
                    {
                        "id": "FIN_05_general_public_expenditure:missing",
                        "type": "missing_core_table",
                        "table_code": "FIN_05_general_public_expenditure",
                        "severity": "warn",
                    }
                ]
            }
        )
    )
    assert gate["status"] == "review_required"
    assert "missing_required_table" in _reason_codes(gate)


def test_gate_ambiguous_table_schema_reason():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            structured_ingest={
                "review_items": [
                    {"id": "TBL_X:unknown", "type": "unknown_table", "table_code": "TBL_X"},
                    {"id": "TBL_Y:low_confidence", "type": "low_confidence_table"},
                ]
            }
        )
    )
    assert "ambiguous_table_schema" in _reason_codes(gate)


def test_gate_rule_evidence_incomplete_reason():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(evidence_completeness={"incomplete": 12})
    )
    assert "rule_evidence_incomplete" in _reason_codes(gate)
    assert gate["status"] == "review_required"


def test_gate_rule_execution_error_reason():
    summary = dict(FULL_EXECUTED_SUMMARY)
    summary.update(
        {
            "executed": 8,
            "pass": 8,
            "execution_error": 1,
            "parse_error": 1,
            "unresolved_total": 2,
            "unresolved_rules": [
                {"rule_id": "V33-115", "status": "parse_error"},
                {"rule_id": "V33-120", "status": "execution_error"},
            ],
        }
    )
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(issue_total=3, rule_execution_summary=summary)
    )
    assert "rule_execution_error" in _reason_codes(gate)


def test_gate_no_findings_blocked_when_rules_not_executed():
    summary = dict(FULL_EXECUTED_SUMMARY)
    summary.update(
        {
            "executed": 6,
            "pass": 6,
            "insufficient_data": 4,
            "unresolved_total": 4,
        }
    )
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(rule_execution_summary=summary)
    )
    assert gate["status"] == "review_required"
    assert "rules_not_executed" in _reason_codes(gate)
    assert gate["analysis_conclusion"] == "incomplete"


def test_gate_no_findings_allowed_when_all_rules_executed():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(rule_execution_summary=dict(FULL_EXECUTED_SUMMARY))
    )
    assert gate["status"] == "done"
    assert gate["analysis_conclusion"] == "no_findings"


def test_gate_report_type_mismatch_doc_type_vs_content():
    gate = _evaluate_quality_gate(**_base_gate_kwargs(doc_type="dept_final", report_kind="budget"))
    assert "report_type_mismatch" in _reason_codes(gate)


def test_gate_report_type_mismatch_ps_sync_budget_misclass():
    """决算材料被写库为 BUDGET：必须被发现（样张实测缺陷）。"""
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            doc_type="dept_final",
            report_kind="final",
            structured_ingest={
                "ps_sync": {"status": "done", "report_type": "BUDGET"},
                "review_items": [],
            },
        )
    )
    assert "report_type_mismatch" in _reason_codes(gate)


def test_gate_report_type_mismatch_ps_sync_skipped():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            doc_type="dept_final",
            report_kind="final",
            structured_ingest={
                "ps_sync": {"status": "skipped", "reason": "unknown_report_type"},
                "review_items": [],
            },
        )
    )
    assert "report_type_mismatch" in _reason_codes(gate)


def test_gate_ai_requested_but_not_run_forces_review():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            ai_requested=True,
            ai_execution={"requested": True, "state": AI_STATE_NOT_RUN, "error_code": "legacy_mode_no_ai"},
        )
    )
    assert gate["status"] == "review_required"
    assert "ai_not_run" in _reason_codes(gate)


def test_gate_ai_succeeded_passes():
    gate = _evaluate_quality_gate(
        **_base_gate_kwargs(
            ai_requested=True,
            ai_execution={
                "requested": True,
                "state": AI_STATE_SUCCEEDED,
                "provider": "zhipu",
                "model": "glm-4.5-flash",
            },
            issue_total=2,
        )
    )
    assert gate["status"] == "done"
    assert gate["review_reasons"] == []


# ---------------------------------------------------------------------------
# 4. 报告类型归一化
# ---------------------------------------------------------------------------


def test_normalize_report_type_maps_doc_type_values():
    sync = PSSharedSchemaSync(None)  # type: ignore[arg-type]

    assert sync._normalize_report_type("budget") == "BUDGET"
    assert sync._normalize_report_type("预算") == "BUDGET"
    assert sync._normalize_report_type("dept_budget") == "BUDGET"
    assert sync._normalize_report_type("unit_budget") == "BUDGET"
    assert sync._normalize_report_type("final") == "FINAL"
    assert sync._normalize_report_type("决算") == "FINAL"
    assert sync._normalize_report_type("dept_final") == "FINAL"
    assert sync._normalize_report_type("unit_final") == "FINAL"
    # 未知类型禁止默认写成 BUDGET（样张 dept_final 被错分 BUDGET 的教训）
    assert sync._normalize_report_type("unknown") is None
    assert sync._normalize_report_type("") is None
    assert sync._normalize_report_type("something_else") is None


# ---------------------------------------------------------------------------
# 5. RuleOutcome
# ---------------------------------------------------------------------------


def test_summarize_rule_outcomes_counts_all_statuses():
    outcomes = [
        RuleOutcome(rule_id="V33-001", status="fail"),
        RuleOutcome(rule_id="V33-001", status="fail"),
        RuleOutcome(rule_id="CMM-001", status="pass"),
        RuleOutcome(rule_id="CMM-004", status="not_applicable", detail="域不同不可比"),
        RuleOutcome(rule_id="V33-115", status="insufficient_data", detail="列归属不明"),
        RuleOutcome(rule_id="V33-120", status="parse_error"),
        RuleOutcome(rule_id="V33-202", status="execution_error"),
    ]
    summary = summarize_rule_outcomes(outcomes)
    assert summary["total_rules"] == 7
    assert summary["fail"] == 2
    assert summary["pass"] == 1
    assert summary["not_applicable"] == 1
    assert summary["insufficient_data"] == 1
    assert summary["parse_error"] == 1
    assert summary["execution_error"] == 1
    assert summary["executed"] == 4
    assert summary["unresolved_total"] == 3
    assert summary["failed_rules"] == ["V33-001"]


class _DeferredRule:
    code, severity = "V33-TEST-DEFERRED", "warn"

    def apply(self, doc):
        raise RuleDeferred(self.code, "无法可靠确定列归属")


class _BoomRule:
    code, severity = "V33-TEST-BOOM", "warn"

    def apply(self, doc):
        raise ValueError("boom")


class _HitRule:
    code, severity = "V33-TEST-HIT", "warn"

    def apply(self, doc):
        from src.engine.rules_v33 import Issue

        return [Issue(rule=self.code, severity="warn", message="hit", location={"page": 1})]


class _QuietRule:
    code, severity = "V33-TEST-QUIET", "warn"

    def apply(self, doc):
        return []


def test_run_rules_with_outcomes_only_fail_produces_findings(monkeypatch):
    """insufficient_data / execution_error 不得产出 finding，只进运行摘要。"""

    monkeypatch.setattr(
        engine_pipeline, "ALL_COMMON_RULES", [_DeferredRule(), _BoomRule(), _HitRule(), _QuietRule()]
    )
    monkeypatch.setattr(engine_pipeline, "ALL_BUDGET_RULES", [])
    monkeypatch.setattr(engine_pipeline, "FINAL_ALL_RULES", [])

    class _Doc:
        pages = 1
        path = "x.pdf"
        page_texts = ["决算"]
        years_per_page: list = []
        units_per_page: list = []
        anchors: dict = {}

        def __init__(self):
            self.page_tables = [[]]

    issues, outcomes = engine_pipeline.run_rules_with_outcomes(_Doc(), False, report_kind="final")
    rule_ids = {issue.rule for issue in issues}
    assert "V33-TEST-DEFERRED" not in rule_ids
    assert "V33-TEST-BOOM" not in rule_ids
    assert "V33-TEST-HIT" in rule_ids

    summary = summarize_rule_outcomes(outcomes)
    assert summary["insufficient_data"] == 1
    assert summary["execution_error"] == 1
    assert summary["fail"] == 1
    assert summary["pass"] == 1
