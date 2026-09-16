"""Tests for finding & incomplete check coexistence (Contract C1).

Verifies that:
- Partial issues and unresolved statuses coexist without suppressing confirmed findings.
- Subcheck order does not matter:
    1) Error discovered first, then missing data encountered;
    2) Missing data encountered first, then error discovered in subsequent subcheck.
- Rule totals (total_rules) are conserved (no double counting).
- Dual reasons are preserved when a partial issue fails conversion.
- All three entry points (pipeline, engine_rule_runner, structured_rules) behave consistently.
- Backward compatibility with legacy rules lacking partial_issues is preserved.
"""

from __future__ import annotations

import pytest
from typing import Any, List
from unittest.mock import MagicMock

from src.engine.rule_outcome import (
    RuleOutcome,
    RuleOutcomeSignal,
    RuleDeferred,
    RuleNotApplicable,
    STATUS_PASS,
    STATUS_FAIL,
    STATUS_INSUFFICIENT_DATA,
    STATUS_PARSE_ERROR,
    STATUS_EXECUTION_ERROR,
    summarize_rule_outcomes,
)
from src.engine.rules_v33 import Issue, Document, build_document
from src.engine import pipeline as engine_pipeline
from src.engine import structured_rules as struct_rules
from src.services.engine_rule_runner import EngineRuleRunner
from src.schemas.issues import JobContext, AnalysisConfig


class _SubcheckErrorFirstRule:
    """Subcheck 1 finds an error; Subcheck 2 encounters missing data."""
    code = "TEST-ERR-FIRST"
    desc = "Subcheck error first then missing data"
    severity = "error"

    def apply(self, doc: Any) -> List[Issue]:
        findings: List[Issue] = [
            Issue(
                rule=self.code,
                severity="error",
                message="Subcheck 1 found confirmed mismatch: expected 100, got 200",
                location={"page": 1, "table": "T1"},
                evidence_text="表1: 200",
            )
        ]
        # Subcheck 2 encounters missing data -> raise RuleDeferred carrying confirmed finding
        raise RuleDeferred(
            self.code,
            detail="Subcheck 2 missing table BUD_T2",
            partial_issues=findings,
            unresolved_reasons=["Subcheck 2 missing table BUD_T2"],
        )


class _SubcheckMissingFirstRule:
    """Subcheck 1 encounters missing data; Subcheck 2 still executes and finds an error."""
    code = "TEST-MISSING-FIRST"
    desc = "Subcheck missing data first then error in independent subcheck"
    severity = "error"

    def apply(self, doc: Any) -> List[Issue]:
        findings: List[Issue] = []
        unresolved: List[str] = []

        # Independent subcheck 1: missing data
        unresolved.append("Subcheck 1 missing optional detail column")

        # Independent subcheck 2: executes and finds error
        findings.append(
            Issue(
                rule=self.code,
                severity="warn",
                message="Subcheck 2 found formula discrepancy: basic + project != total",
                location={"page": 2, "table": "T3"},
                evidence_text="合计: 100, 分项之和: 90",
            )
        )

        # All independent checks finished, unified return/signal
        raise RuleDeferred(
            self.code,
            detail="; ".join(unresolved),
            partial_issues=findings,
            unresolved_reasons=unresolved,
        )


class _PureDeferredRule:
    """Rule with no findings that encounters insufficient data."""
    code = "TEST-PURE-DEFERRED"
    desc = "Pure missing data"
    severity = "medium"

    def apply(self, doc: Any) -> List[Issue]:
        raise RuleDeferred(self.code, detail="Table not found")


class _PureErrorRule:
    """Rule with normal confirmed finding."""
    code = "TEST-PURE-ERROR"
    desc = "Pure error"
    severity = "high"

    def apply(self, doc: Any) -> List[Issue]:
        return [
            Issue(
                rule=self.code,
                severity="high",
                message="Pure confirmed error",
                location={"page": 1},
            )
        ]


def _make_dummy_doc() -> Document:
    return build_document("test.pdf", ["决算 页面一", "页面二"], [[[]], [[]]], 1000)


def test_t05_error_first_then_missing_data_in_pipeline(monkeypatch: pytest.MonkeyPatch):
    """T05: First discover error, then encounter missing data -> finding and incomplete reason both preserved."""
    rule = _SubcheckErrorFirstRule()
    monkeypatch.setattr(engine_pipeline, "ALL_COMMON_RULES", [rule])
    monkeypatch.setattr(engine_pipeline, "ALL_BUDGET_RULES", [])
    monkeypatch.setattr(engine_pipeline, "FINAL_ALL_RULES", [])

    doc = _make_dummy_doc()
    issues, outcomes = engine_pipeline.run_rules_with_outcomes(doc, False, report_kind="final")

    # 1. Finding must NOT be discarded
    assert len(issues) == 1
    assert issues[0].rule == "TEST-ERR-FIRST"
    assert "Subcheck 1 found confirmed mismatch" in issues[0].message

    # 2. Outcome must preserve insufficient_data status and reasons
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.rule_id == "TEST-ERR-FIRST"
    assert outcome.status == STATUS_INSUFFICIENT_DATA
    assert outcome.partial_findings_count == 1
    assert "Subcheck 2 missing table BUD_T2" in outcome.detail
    assert "Subcheck 2 missing table BUD_T2" in outcome.unresolved_reasons

    # 3. Rule execution summary
    summary = summarize_rule_outcomes(outcomes)
    assert summary["total_rules"] == 1
    assert summary["insufficient_data"] == 1
    assert summary["partial_findings_total"] == 1
    assert summary["fail"] == 0  # not classified as fail, but finding is exposed
    assert len(summary["unresolved_rules"]) == 1


def test_t06_missing_first_then_error_in_pipeline(monkeypatch: pytest.MonkeyPatch):
    """T06: First encounter missing data, subsequent independent subcheck finds error -> both preserved."""
    rule = _SubcheckMissingFirstRule()
    monkeypatch.setattr(engine_pipeline, "ALL_COMMON_RULES", [rule])
    monkeypatch.setattr(engine_pipeline, "ALL_BUDGET_RULES", [])
    monkeypatch.setattr(engine_pipeline, "FINAL_ALL_RULES", [])

    doc = _make_dummy_doc()
    issues, outcomes = engine_pipeline.run_rules_with_outcomes(doc, False, report_kind="final")

    # Finding from subcheck 2 is preserved
    assert len(issues) == 1
    assert issues[0].rule == "TEST-MISSING-FIRST"
    assert "Subcheck 2 found formula discrepancy" in issues[0].message

    # Incomplete status and reason from subcheck 1 is preserved
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.status == STATUS_INSUFFICIENT_DATA
    assert outcome.partial_findings_count == 1
    assert "Subcheck 1 missing optional detail column" in outcome.unresolved_reasons

    summary = summarize_rule_outcomes(outcomes)
    assert summary["total_rules"] == 1
    assert summary["insufficient_data"] == 1
    assert summary["partial_findings_total"] == 1


def test_t08_total_rules_conserved_with_mixed_rules(monkeypatch: pytest.MonkeyPatch):
    """T08: Total rules count is strictly conserved, findings count is separate."""
    rules = [
        _SubcheckErrorFirstRule(),
        _SubcheckMissingFirstRule(),
        _PureDeferredRule(),
        _PureErrorRule(),
    ]
    monkeypatch.setattr(engine_pipeline, "ALL_COMMON_RULES", rules)
    monkeypatch.setattr(engine_pipeline, "ALL_BUDGET_RULES", [])
    monkeypatch.setattr(engine_pipeline, "FINAL_ALL_RULES", [])

    doc = _make_dummy_doc()
    issues, outcomes = engine_pipeline.run_rules_with_outcomes(doc, False, report_kind="final")

    # Total 3 findings: 1 from err_first, 1 from missing_first, 1 from pure_error
    assert len(issues) == 3

    # Total 4 outcomes (1 per rule, no double counting)
    assert len(outcomes) == 4

    summary = summarize_rule_outcomes(outcomes)
    assert summary["total_rules"] == 4
    assert summary["insufficient_data"] == 3  # 2 partial + 1 pure
    assert summary["fail"] == 1               # 1 pure error
    assert summary["partial_findings_total"] == 2


def test_t09_structured_rules_entrypoint_preserves_partial_issues(monkeypatch: pytest.MonkeyPatch):
    """T09: Structured rules entry point preserves partial issues and unresolved reasons."""
    doc = _make_dummy_doc()
    # Mock migrated rules in structured_rules
    rule = _SubcheckErrorFirstRule()
    monkeypatch.setattr(struct_rules, "STRUCTURED_MIGRATED_RULES", [rule])

    issues, outcomes = struct_rules.run_structured_rules(doc, report_kind="final", rules=[rule])

    assert len(issues) == 1
    assert issues[0].rule == "TEST-ERR-FIRST"

    matching_outcomes = [o for o in outcomes if o.rule_id == "TEST-ERR-FIRST"]
    assert len(matching_outcomes) == 1
    assert matching_outcomes[0].status == STATUS_INSUFFICIENT_DATA
    assert matching_outcomes[0].partial_findings_count == 1
    assert "Subcheck 2 missing table BUD_T2" in matching_outcomes[0].unresolved_reasons


@pytest.mark.asyncio
async def test_t09_and_t07_engine_rule_runner_preserves_partial_issues_and_dual_reasons(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch
):
    """T09 & T07: EngineRuleRunner preserves partial findings and handles dual reasons if conversion fails."""
    runner = EngineRuleRunner()

    pdf_file = tmp_path / "dummy.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 dummy content")

    job_context = JobContext(
        job_id="test_job_123",
        pdf_path=str(pdf_file),
        meta={"report_kind": "final"},
    )
    config = AnalysisConfig(rules_version="2026.09.14")

    # Case 1: normal conversion of partial issue
    rule = _SubcheckErrorFirstRule()
    monkeypatch.setattr(runner, "_select_rule_set", lambda ctx, doc: [rule])

    findings = await runner.run_rules(job_context, [], config)

    assert len(findings) == 1
    assert findings[0].rule_id == "TEST-ERR-FIRST"
    assert "Subcheck 1 found confirmed mismatch" in findings[0].message

    summary = runner.get_rule_execution_summary()
    assert summary["total_rules"] == 1
    assert summary["insufficient_data"] == 1
    assert summary["partial_findings_total"] == 1

    # Case 2: T07 conversion failure on a partial issue alongside a valid partial issue
    # -> Valid issue is preserved, failing issue causes parse_error, and dual reasons are preserved!
    class _FailingConversionIssue:
        rule = "TEST-DUAL"
        # Invalid location type that causes _issue_to_finding or bbox locator to raise
        location = "INVALID_LOCATION_TYPE"
        message = "Partial issue that will fail conversion"

    class _DualReasonRule:
        code = "TEST-DUAL"
        desc = "Dual reason test with mixed valid and invalid partial issues"
        severity = "error"

        def apply(self, doc: Any) -> List[Issue]:
            valid_issue = Issue(
                rule=self.code,
                severity="error",
                message="Valid confirmed finding that converts successfully",
                location={"page": 1, "table": "T1"},
                evidence_text="Valid evidence text",
            )
            failing_issue = _FailingConversionIssue()
            raise RuleDeferred(
                self.code,
                detail="Original missing table data",
                partial_issues=[valid_issue, failing_issue],
                unresolved_reasons=["Original missing table data"],
            )

    runner2 = EngineRuleRunner()
    monkeypatch.setattr(runner2, "_select_rule_set", lambda ctx, doc: [_DualReasonRule()])

    findings2 = await runner2.run_rules(job_context, [], config)
    # T07 contract: The valid issue was converted and preserved!
    assert len(findings2) == 1
    assert findings2[0].rule_id == "TEST-DUAL"
    assert "Valid confirmed finding" in findings2[0].message

    summary2 = runner2.get_rule_execution_summary()
    assert summary2["total_rules"] == 1
    # Priority: parse_error (40) > insufficient_data (30) > fail (20)
    assert summary2["parse_error"] == 1
    assert summary2["partial_findings_total"] == 1
    unresolved_rules = summary2["unresolved_rules"]
    assert len(unresolved_rules) == 1
    reasons = unresolved_rules[0]["unresolved_reasons"]
    # Both reasons are preserved!
    assert any("Original missing table data" in r for r in reasons)
    assert any("转换失败" in r for r in reasons)


def test_t05_fail_beats_not_applicable_when_partial_issues_present(monkeypatch: pytest.MonkeyPatch):
    """Item 5: Subcheck A fails, Subcheck B not applicable -> overall rule MUST be fail, not not_applicable.

    Verified across all three entry points:
    1. engine pipeline
    2. structured_rules
    3. EngineRuleRunner
    """
    class _PartialNotApplicableRule:
        code = "AUDIT-NA-PARTIAL"
        desc = "Subcheck A fails, Subcheck B not applicable"

        def apply(self, doc: Any) -> List[Issue]:
            raise RuleNotApplicable(
                self.code,
                detail="子检查 B 不适用（单位无此项业务）",
                partial_issues=[
                    Issue(
                        rule=self.code,
                        severity="error",
                        message="子检查 A 已确认金额勾稽不一致",
                        location={"page": 1, "table": "T1"},
                        evidence_text="收入合计不符",
                    )
                ],
                unresolved_reasons=["子检查 B 不适用（单位无此项业务）"],
            )

    rule = _PartialNotApplicableRule()
    doc = _make_dummy_doc()

    # 1. Pipeline entry point
    monkeypatch.setattr(engine_pipeline, "ALL_COMMON_RULES", [rule])
    monkeypatch.setattr(engine_pipeline, "ALL_BUDGET_RULES", [])
    monkeypatch.setattr(engine_pipeline, "FINAL_ALL_RULES", [])

    issues_p, outcomes_p = engine_pipeline.run_rules_with_outcomes(doc, False, report_kind="final")
    assert len(issues_p) == 1
    assert issues_p[0].rule == "AUDIT-NA-PARTIAL"
    assert len(outcomes_p) == 1
    assert outcomes_p[0].status == STATUS_FAIL  # fail beats not_applicable!
    assert outcomes_p[0].partial_findings_count == 1

    summary_p = summarize_rule_outcomes(outcomes_p)
    assert summary_p["total_rules"] == 1
    assert summary_p["fail"] == 1
    assert summary_p["not_applicable"] == 0  # not_applicable is overridden by confirmed fail
    assert summary_p["failed_rules"] == ["AUDIT-NA-PARTIAL"]
    assert summary_p["partial_findings_total"] == 1

    # 2. Structured rules entry point
    issues_s, outcomes_s = struct_rules.run_structured_rules(doc, report_kind="final", rules=[rule])
    assert len(issues_s) == 1
    assert len(outcomes_s) == 1
    assert outcomes_s[0].status == STATUS_FAIL
    assert outcomes_s[0].partial_findings_count == 1

    # 3. EngineRuleRunner entry point
    runner = EngineRuleRunner()
    monkeypatch.setattr(runner, "_select_rule_set", lambda ctx, d: [rule])
    job_context = JobContext(
        job_id="test_na_fail_job",
        pdf_path="dummy.pdf",
        page_texts=["决算 页面一", "页面二"],
        page_tables=[[[]], [[]]],
        pages=2,
        meta={"report_kind": "final"},
    )
    import asyncio
    findings_r = asyncio.run(runner.run_rules(job_context, [], AnalysisConfig()))
    assert len(findings_r) == 1
    assert findings_r[0].rule_id == "AUDIT-NA-PARTIAL"

    summary_r = runner.get_rule_execution_summary()
    assert summary_r["total_rules"] == 1
    assert summary_r["fail"] == 1
    assert summary_r["not_applicable"] == 0
    assert summary_r["failed_rules"] == ["AUDIT-NA-PARTIAL"]
    assert summary_r["partial_findings_total"] == 1


def test_t05_insufficient_data_beats_not_applicable():
    """Item 5: Subcheck A insufficient data, Subcheck B not applicable -> overall is insufficient_data."""
    from src.engine.rule_outcome import resolve_rule_status
    # insufficient_data (30) > not_applicable (10)
    resolved = resolve_rule_status(base_status=STATUS_INSUFFICIENT_DATA, has_findings=False)
    assert resolved == STATUS_INSUFFICIENT_DATA


def test_t05_parse_error_beats_insufficient_data_and_fail():
    """Item 5: Parse error (conversion error) beats insufficient data and fail in priority."""
    from src.engine.rule_outcome import resolve_rule_status
    # parse_error (40) > insufficient_data (30) > fail (20)
    resolved = resolve_rule_status(
        base_status=STATUS_INSUFFICIENT_DATA,
        has_findings=True,
        has_conversion_error=True,
    )
    assert resolved == STATUS_PARSE_ERROR



def test_t22_backward_compatibility_with_legacy_rules():
    """T22: Legacy rules without partial_issues continue to work with default empty lists."""
    sig = RuleOutcomeSignal("LEGACY-001", "some detail")
    assert sig.partial_issues == []
    assert sig.unresolved_reasons == []

    deferred = RuleDeferred("LEGACY-002", "some deferred detail")
    assert deferred.partial_issues == []
    assert deferred.unresolved_reasons == []

    outcome = RuleOutcome(rule_id="LEGACY-003", status="pass")
    assert outcome.partial_findings_count == 0
    assert outcome.unresolved_reasons == []
    d = outcome.to_dict()
    assert "partial_findings_count" not in d
    assert "unresolved_reasons" not in d
