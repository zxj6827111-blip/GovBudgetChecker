"""Integration tests for minimal quality gate & result boundary coexistence (Card A06, Contract C1, Matrix T10).

Verifies that:
- Partial issues and unresolved rule execution summaries survive actual result
  conversion and IPC / serialization boundaries (Pipe, Pickle, JSON).
- Confirmed findings remain readable in the issues payload with all metadata intact.
- Quality gate evaluates to status="review_required", quality_status="review_required",
  analysis_conclusion="incomplete", with reason "rules_insufficient_data" carrying
  the exact unresolved rule and reasons.
- Neither confirmed findings nor incomplete statuses hide each other.
- Dual entry point coverage: pipeline build_issues_payload + EngineRuleRunner.
- Pure pass vs pure error vs partial coexistence contrasts.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import pickle
from pathlib import Path
from typing import Any, List
import pytest

from api.main import _evaluate_quality_gate, _count_result_findings
from api.runtime import write_json_file, read_json_file
from src.engine import pipeline as engine_pipeline
from src.engine.rule_outcome import (
    RuleDeferred,
    RuleOutcome,
    STATUS_INSUFFICIENT_DATA,
    summarize_rule_outcomes,
)
from src.engine.rules_v33 import Issue, Document, build_document
from src.schemas.issues import JobContext, AnalysisConfig
from src.services.engine_rule_runner import EngineRuleRunner
from src.services.rule_process import _rule_worker, run_rules_in_process


GOOD_PAGE_ASSESSMENT = {
    "page_count": 2,
    "text_page_count": 2,
    "low_text_pages": [],
    "low_text_page_count": 0,
    "scanned_pages": [],
    "scanned_page_count": 0,
    "page_coverage": 1.0,
}


class _CoexistenceMockRule:
    """Rule that finds a confirmed issue in subcheck 1, then encounters missing data in subcheck 2."""
    code = "BUD-TEST-COEXIST"
    desc = "Coexistence rule: confirmed finding + missing data"
    severity = "error"

    def apply(self, doc: Any) -> List[Issue]:
        confirmed = Issue(
            rule=self.code,
            severity="error",
            message="【预算数不一致】收支总表一般公共预算支出(100.00万元)与功能分类表合计(120.00万元)不一致",
            location={"page": 5, "table": "收支总表"},
            evidence_text="一般公共预算支出: 100.00 万元",
        )
        raise RuleDeferred(
            self.code,
            detail="政府性基金预算支出表缺失，无法核验基金勾稽关系",
            partial_issues=[confirmed],
            unresolved_reasons=["政府性基金预算支出表缺失，无法核验基金勾稽关系"],
        )


class _CleanPassMockRule:
    """Rule that passes completely."""
    code = "TEST-CLEAN-PASS"
    desc = "Clean pass rule"
    severity = "info"

    def apply(self, doc: Any) -> List[Issue]:
        return []


def _make_dummy_doc() -> Document:
    return build_document(
        path="test_coexist.pdf",
        page_texts=["预算 部门预算公开", "第二页内容"],
        page_tables=[[[]], [[]]],
        filesize=2048,
    )


def test_t10_pipeline_payload_boundary_and_quality_gate(monkeypatch: pytest.MonkeyPatch):
    """T10: Partial completion through pipeline build_issues_payload -> Pickle/JSON -> quality gate."""
    rule = _CoexistenceMockRule()
    monkeypatch.setattr(engine_pipeline, "ALL_COMMON_RULES", [rule])
    monkeypatch.setattr(engine_pipeline, "ALL_BUDGET_RULES", [])
    monkeypatch.setattr(engine_pipeline, "FINAL_ALL_RULES", [])

    doc = _make_dummy_doc()

    # 1. Generate payload through real pipeline conversion
    payload = engine_pipeline.build_issues_payload(doc, False, report_kind="budget")

    # 2. Simulate IPC boundary (pickle)
    pickled_payload = pickle.loads(pickle.dumps(payload))

    # 3. Simulate API / HTTP serialization boundary (JSON)
    json_payload = json.loads(json.dumps(pickled_payload))

    # Assert findings are readable and not dropped
    all_issues = json_payload["issues"]["all"]
    assert len(all_issues) == 1
    finding = all_issues[0]
    assert finding["rule_id"] == "BUD-TEST-COEXIST"
    assert finding["severity"] == "error"
    assert "收支总表一般公共预算支出" in finding["message"]
    assert finding["location"]["page"] == 5

    # Assert summary is intact
    summary = json_payload["rule_execution_summary"]
    assert summary["total_rules"] == 1
    assert summary["insufficient_data"] == 1
    assert summary["partial_findings_total"] == 1
    assert len(summary["unresolved_rules"]) == 1
    assert summary["unresolved_rules"][0]["rule_id"] == "BUD-TEST-COEXIST"
    assert "政府性基金预算支出表缺失" in summary["unresolved_rules"][0]["unresolved_reasons"][0]

    # 4. Feed into quality gate
    gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGE_ASSESSMENT,
        report_kind="budget",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=_count_result_findings({"issues": json_payload["issues"]}),
        rule_execution_summary=json_payload["rule_execution_summary"],
    )

    # 5. Assert quality gate verdicts: incomplete + review_required
    assert gate["status"] == "review_required"
    assert gate["quality_status"] == "review_required"
    assert gate["analysis_conclusion"] == "incomplete"

    reason_codes = [r["code"] for r in gate["review_reasons"]]
    assert "rules_insufficient_data" in reason_codes

    insufficient_reason = next(r for r in gate["review_reasons"] if r["code"] == "rules_insufficient_data")
    assert len(insufficient_reason["unresolved_rules"]) == 1
    unresolved_item = insufficient_reason["unresolved_rules"][0]
    assert unresolved_item["rule_id"] == "BUD-TEST-COEXIST"
    assert unresolved_item["status"] == STATUS_INSUFFICIENT_DATA
    assert "政府性基金预算支出表缺失" in unresolved_item["unresolved_reasons"][0]


@pytest.mark.asyncio
async def test_t10_engine_rule_runner_boundary_and_quality_gate(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    """T10: EngineRuleRunner converts partial issues and passes rule_execution_summary to quality gate."""
    runner = EngineRuleRunner()
    pdf_file = tmp_path / "dummy.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 dummy")

    job_context = JobContext(
        job_id="test_runner_coexist",
        pdf_path=str(pdf_file),
        page_texts=["预算 部门预算公开", "第二页内容"],
        page_tables=[[[]], [[]]],
        pages=2,
        meta={"report_kind": "budget"},
    )
    config = AnalysisConfig(rules_version="2026.09.14")

    rule = _CoexistenceMockRule()
    monkeypatch.setattr(runner, "_select_rule_set", lambda ctx, doc: [rule])

    findings = await runner.run_rules(job_context, [], config)
    summary = runner.get_rule_execution_summary()

    # Finding must be present as IssueItem (EngineRuleRunner normalizes "error" -> "high")
    assert len(findings) == 1
    assert findings[0].rule_id == "BUD-TEST-COEXIST"
    assert findings[0].severity == "high"
    assert "收支总表一般公共预算支出" in findings[0].message
    assert findings[0].rule_version == "2026.09.14"

    # Summary has insufficient_data and unresolved_reasons
    assert summary["total_rules"] == 1
    assert summary["insufficient_data"] == 1
    assert summary["partial_findings_total"] == 1

    # Quality gate evaluation
    gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGE_ASSESSMENT,
        report_kind="budget",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=len(findings),
        rule_execution_summary=summary,
    )

    assert gate["status"] == "review_required"
    assert gate["quality_status"] == "review_required"
    assert gate["analysis_conclusion"] == "incomplete"
    assert any(r["code"] == "rules_insufficient_data" for r in gate["review_reasons"])


def test_t10_contrast_clean_pass_vs_partial_coexistence(monkeypatch: pytest.MonkeyPatch):
    """Contrast: clean complete pass yields done/no_findings, whereas coexistence yields review_required/incomplete."""
    doc = _make_dummy_doc()

    # Case A: Clean rule execution
    clean_rule = _CleanPassMockRule()
    monkeypatch.setattr(engine_pipeline, "ALL_COMMON_RULES", [clean_rule])
    monkeypatch.setattr(engine_pipeline, "ALL_BUDGET_RULES", [])
    monkeypatch.setattr(engine_pipeline, "FINAL_ALL_RULES", [])

    clean_payload = engine_pipeline.build_issues_payload(doc, False, report_kind="budget")
    clean_gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGE_ASSESSMENT,
        report_kind="budget",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=_count_result_findings({"issues": clean_payload["issues"]}),
        rule_execution_summary=clean_payload["rule_execution_summary"],
    )
    assert clean_gate["status"] == "done"
    assert clean_gate["quality_status"] == "complete"
    assert clean_gate["analysis_conclusion"] == "no_findings"
    assert clean_gate["review_reasons"] == []

    # Case B: Mixed clean + coexist rule -> must fail closed to review_required
    coexist_rule = _CoexistenceMockRule()
    monkeypatch.setattr(engine_pipeline, "ALL_COMMON_RULES", [clean_rule, coexist_rule])

    mixed_payload = engine_pipeline.build_issues_payload(doc, False, report_kind="budget")
    mixed_gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGE_ASSESSMENT,
        report_kind="budget",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=_count_result_findings({"issues": mixed_payload["issues"]}),
        rule_execution_summary=mixed_payload["rule_execution_summary"],
    )
    assert mixed_gate["status"] == "review_required"
    assert mixed_gate["quality_status"] == "review_required"
    assert mixed_gate["analysis_conclusion"] == "incomplete"
    # Finding from coexist_rule is preserved
    assert len(mixed_payload["issues"]["all"]) == 1
    # Total rules count is conserved: 2 rules
    assert mixed_payload["rule_execution_summary"]["total_rules"] == 2
    assert mixed_payload["rule_execution_summary"]["pass"] == 1
    assert mixed_payload["rule_execution_summary"]["insufficient_data"] == 1
    assert mixed_payload["rule_execution_summary"]["partial_findings_total"] == 1


def test_t10_real_pipe_ipc_worker_cross_process_spawn():
    """T10: Real cross-process worker spawn executing coexistence rule and transferring payload across IPC Pipe."""
    doc = _make_dummy_doc()

    start_method = "spawn" if os.name == "nt" else "fork"
    ctx = multiprocessing.get_context(start_method)
    parent_conn, child_conn = ctx.Pipe(duplex=False)

    # Spawn real worker process with deterministic coexistence test rule
    proc = ctx.Process(
        target=_rule_worker,
        args=(child_conn, doc, False, "budget", [_CoexistenceMockRule()]),
        daemon=True,
    )
    proc.start()
    child_conn.close()

    try:
        # Assert child process PID is distinct from current process (proves real cross-process execution)
        assert proc.pid is not None
        assert proc.pid != os.getpid()

        status, payload = parent_conn.recv()
        proc.join(timeout=10)

        assert status == "ok"
        assert isinstance(payload, dict)

        # Verify confirmed finding survived real cross-process IPC boundary
        assert len(payload["issues"]["all"]) == 1
        finding = payload["issues"]["all"][0]
        assert finding["rule"] == "BUD-TEST-COEXIST"
        assert finding["rule_id"] == "BUD-TEST-COEXIST"
        assert finding["severity"] == "error"
        assert finding["location"]["table"] == "收支总表"
        assert finding["location"]["page"] == 5
        assert "一般公共预算支出" in finding["evidence"][0]["text_snippet"]

        # Verify unresolved rule execution summary survived real cross-process IPC boundary
        summary = payload["rule_execution_summary"]
        assert summary["total_rules"] == 1
        assert summary["insufficient_data"] == 1
        assert summary["partial_findings_total"] == 1
        assert len(summary["unresolved_rules"]) == 1
        unresolved_item = summary["unresolved_rules"][0]
        assert unresolved_item["rule_id"] == "BUD-TEST-COEXIST"
        assert unresolved_item["status"] == STATUS_INSUFFICIENT_DATA
        assert "政府性基金预算支出表缺失" in unresolved_item["unresolved_reasons"][0]

        # Feed the cross-process payload directly into quality gate
        gate = _evaluate_quality_gate(
            page_assessment=GOOD_PAGE_ASSESSMENT,
            report_kind="budget",
            report_year=2025,
            ai_requested=False,
            ai_degraded=False,
            issue_total=_count_result_findings({"issues": payload["issues"]}),
            rule_execution_summary=summary,
        )
        # Strict quality gate assertions: coexistence of confirmed finding + unresolved data
        # MUST evaluate to review_required and incomplete (never done / complete)
        assert gate["status"] == "review_required"
        assert gate["quality_status"] == "review_required"
        assert gate["analysis_conclusion"] == "incomplete"
        reasons = gate.get("review_reasons") or []
        assert any(r.get("code") == "rules_insufficient_data" for r in reasons)
        insufficient_reason = next(r for r in reasons if r.get("code") == "rules_insufficient_data")
        assert any(
            item.get("rule_id") == "BUD-TEST-COEXIST"
            for item in insufficient_reason.get("unresolved_rules", [])
        )
    finally:
        parent_conn.close()
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)


@pytest.mark.asyncio
async def test_t10_production_run_rules_in_process_and_disk_storage_boundary(tmp_path: Any):
    """T10: Production run_rules_in_process pipeline through disk storage boundary and quality gate."""
    doc = _make_dummy_doc()

    # 1. Execute coexistence rule through production process-isolated entry point
    payload = await run_rules_in_process(
        doc, False, "budget", timeout_seconds=30, rules=[_CoexistenceMockRule()]
    )
    assert isinstance(payload, dict)
    assert len(payload["issues"]["all"]) == 1
    assert payload["rule_execution_summary"]["partial_findings_total"] == 1
    assert payload["rule_execution_summary"]["insufficient_data"] == 1

    # 2. Assemble task status payload matching api/main.py production format
    status_payload = {
        "status": "pending",
        "stage": "rules_completed",
        "issues": payload["issues"],
        "meta": {
            "pages": len(doc.page_texts),
            "filesize": doc.filesize,
            "job_id": "test_job_disk_boundary",
            "report_kind": "budget",
            "report_year": 2025,
            "rule_execution_summary": payload["rule_execution_summary"],
            "ai_execution": {"status": "not_run"},
        },
    }

    # 3. Persist to disk using project's actual atomic write_json_file helper
    status_file = Path(tmp_path) / "status.json"
    write_json_file(status_file, status_payload)

    # 4. Re-read from disk using project's actual read_json_file helper
    loaded_status = read_json_file(status_file)

    # Assert findings and coexistence summary are fully intact after disk roundtrip
    assert len(loaded_status["issues"]["all"]) == 1
    assert loaded_status["issues"]["all"][0]["rule_id"] == "BUD-TEST-COEXIST"
    loaded_summary = loaded_status["meta"]["rule_execution_summary"]
    assert loaded_summary["partial_findings_total"] == 1
    assert loaded_summary["insufficient_data"] == 1
    assert loaded_summary["unresolved_rules"][0]["rule_id"] == "BUD-TEST-COEXIST"

    # 5. Evaluate quality gate on re-read result with strict assertions
    gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGE_ASSESSMENT,
        report_kind="budget",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=_count_result_findings(loaded_status),
        rule_execution_summary=loaded_summary,
    )
    assert gate["status"] == "review_required"
    assert gate["quality_status"] == "review_required"
    assert gate["analysis_conclusion"] == "incomplete"
    reasons = gate.get("review_reasons") or []
    assert any(r.get("code") == "rules_insufficient_data" for r in reasons)

