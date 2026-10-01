"""ZC-06 conservative merge and offline AI candidate contract tests."""

from __future__ import annotations

from src.schemas.issues import AnalysisConfig, IssueItem
from src.services.ai_candidate_contract import validate_ai_candidate_batch
from src.services.merge_findings import FindingsMerger
from src.schemas.review_report import build_review_report
from src.services.review_problem_set import collect_review_problems


def _finding(
    *,
    issue_id: str,
    source: str,
    severity: str = "high",
    value: str = "100.00",
    check_relation_id: str | None = "D-005:expense-total",
    fact_ids: list[str] | None = None,
    title: str = "一般公共预算支出不一致",
    page: int = 2,
) -> IssueItem:
    return IssueItem(
        id=issue_id,
        source=source,
        rule_id="D-005",
        severity=severity,
        title=title,
        message=f"金额核对 {value}",
        evidence=[{"evidence_id": f"ev-{issue_id}", "page": page, "text": f"合计 {value}"}],
        evidence_ids=[f"ev-{issue_id}"],
        location={"page": page},
        metrics={"total": value},
        check_relation_id=check_relation_id,
        fact_ids=fact_ids if fact_ids is not None else ["fact-total-2025"],
    )


def test_zc06_exact_check_and_fact_identity_is_the_only_merge_key() -> None:
    ai = _finding(issue_id="ai-1", source="ai", title="AI 的不同标题", page=9)
    rule = _finding(issue_id="rule-1", source="rule", title="规则标题", page=2)
    summary = FindingsMerger(AnalysisConfig()).merge_findings([ai], [rule])

    assert summary.merged_ids == ["rule-1"]
    assert len(summary.agreements) == 1
    assert summary.relations[0]["status"] == "agreement"
    assert summary.relations[0]["ai_issue_ids"] == ["ai-1"]
    assert summary.relations[0]["rule_issue_ids"] == ["rule-1"]


def test_zc06_same_title_page_and_amount_without_fact_ids_never_merge() -> None:
    ai = _finding(
        issue_id="ai-no-identity",
        source="ai",
        check_relation_id=None,
        fact_ids=[],
    )
    rule = _finding(
        issue_id="rule-no-identity",
        source="rule",
        check_relation_id=None,
        fact_ids=[],
    )
    summary = FindingsMerger(AnalysisConfig()).merge_findings([ai], [rule])

    assert summary.merged_ids == ["rule-no-identity"]
    assert summary.agreements == []
    assert summary.totals["ai_only"] == 1
    assert summary.candidate_issue_ids == ["ai-no-identity"]
    assert all(item["status"] != "agreement" for item in summary.relations)


def test_zc06_value_and_severity_conflicts_preserve_both_originals_and_rule_rep() -> None:
    ai = _finding(issue_id="ai-conflict", source="ai", severity="medium", value="100.00")
    rule = _finding(issue_id="rule-conflict", source="rule", severity="high", value="90.00")
    summary = FindingsMerger(AnalysisConfig()).merge_findings([ai], [rule])

    assert summary.merged_ids == ["rule-conflict"]
    assert summary.agreements == []
    assert summary.totals["conflicts"] == 1
    conflict = summary.conflicts[0]
    assert conflict.key == summary.relations[0]["relation_id"]
    assert set(conflict.reasons) == {"value-mismatch", "severity-mismatch"}
    assert conflict.ai_finding.id == "ai-conflict"
    assert conflict.rule_finding.id == "rule-conflict"
    assert summary.relations[0]["status"] == "conflict"


def test_zc06_duplicate_relation_is_ambiguous_and_not_greedily_paired() -> None:
    ai_one = _finding(issue_id="ai-1", source="ai")
    ai_two = _finding(issue_id="ai-2", source="ai")
    rule = _finding(issue_id="rule-1", source="rule")
    summary = FindingsMerger(AnalysisConfig()).merge_findings([ai_one, ai_two], [rule])

    assert summary.totals["conflicts"] == 1
    assert summary.relations[0]["status"] == "ambiguous"
    assert set(summary.relations[0]["ai_issue_ids"]) == {"ai-1", "ai-2"}
    assert summary.merged_ids == []
    assert set(summary.manual_review_issue_ids) == {"ai-1", "ai-2", "rule-1"}


def test_zc06_ai_contract_accepts_only_known_sources_and_keeps_good_items() -> None:
    result = validate_ai_candidate_batch(
        {
            "candidates": [
                {
                    "candidate_id": "candidate-good",
                    "check_relation_id": "D-005:expense-total",
                    "fact_ids": ["fact-total-2025"],
                    "evidence_ids": ["ev-1"],
                    "fact_candidates": [
                        {"field": "raw_value", "value": "100.00", "evidence_ids": ["ev-1"]}
                    ],
                    "semantic_uncertainties": ["需确认该数值属于本级口径"],
                },
                {
                    "candidate_id": "candidate-unknown-source",
                    "fact_ids": ["fact-not-in-input"],
                    "evidence_ids": ["ev-missing"],
                },
                {
                    "candidate_id": "candidate-with-severity",
                    "fact_ids": ["fact-total-2025"],
                    "evidence_ids": ["ev-1"],
                    "severity": "critical",
                },
            ],
            "uncertainties": ["模拟模型未进行数值判断"],
        },
        allowed_fact_ids=["fact-total-2025"],
        allowed_evidence_ids=["ev-1"],
    )

    assert [item["candidate_id"] for item in result["accepted"]] == ["candidate-good"]
    assert len(result["rejected"]) == 2
    assert result["rejected"][0]["error"] == "unknown_source_reference"
    assert result["batch_uncertainties"] == ["模拟模型未进行数值判断"]


def test_zc06_ai_contract_forbids_top_level_numeric_conclusion_fields() -> None:
    result = validate_ai_candidate_batch(
        {"candidates": [], "severity": "high"},
        allowed_fact_ids=[],
        allowed_evidence_ids=[],
    )
    assert result["accepted"] == []
    assert result["rejected"][0]["error"] == "unexpected_batch_fields"


def test_zc06_review_report_separates_ai_candidates_and_preserves_conflicts() -> None:
    ai = _finding(issue_id="ai-candidate", source="ai")
    rule = _finding(issue_id="rule-formal", source="rule")
    summary = FindingsMerger(AnalysisConfig()).merge_findings([ai], [rule])
    conflict = {
        "key": "relation-conflict",
        "reason": "severity-mismatch",
        "reasons": ["severity-mismatch"],
        "ai_issue": "ai-candidate",
        "rule_issue": "rule-formal",
        "details": {"severity": {"ai": "medium", "rule": "high"}},
    }
    ai_dict = ai.model_dump()
    rule_dict = rule.model_dump()
    payload = {
        "job_id": "job-zc06",
        "status": "review_required",
        "result": {
            "issues": {"all": [ai_dict, rule_dict]},
            "ai_findings": [ai_dict],
            "rule_findings": [rule_dict],
            "merged": {
                **summary.model_dump(),
                "conflicts": [conflict],
                "candidate_issue_ids": ["ai-candidate"],
            },
        },
    }

    report = build_review_report(payload)
    assert [item["id"] for item in report["issues"]] == ["rule-formal"]
    assert report["candidate_findings"][0]["issue_id"] == "ai-candidate"
    assert report["merge_conflicts"][0]["ai_issue"] == "ai-candidate"
    assert any(item["record_type"] == "merge_conflict" for item in report["manual_review"])


def test_zc06_review_lifecycle_does_not_count_ai_candidate_as_formal() -> None:
    ai = _finding(
        issue_id="ai-candidate",
        source="ai",
        check_relation_id=None,
        fact_ids=[],
    )
    rule = _finding(
        issue_id="rule-formal",
        source="rule",
        check_relation_id=None,
        fact_ids=[],
    )
    summary = FindingsMerger(AnalysisConfig()).merge_findings([ai], [rule])
    problems = collect_review_problems(
        job_uuid="job-zc06",
        ai_findings=[ai.model_dump()],
        rule_findings=[rule.model_dump()],
        merged_result=summary.model_dump(),
    )
    by_id = {item.issue_id: item for item in problems}
    assert by_id["ai-candidate"].is_formal is False
    assert by_id["rule-formal"].is_formal is True
