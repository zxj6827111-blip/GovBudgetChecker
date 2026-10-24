"""ZC-03 acceptance tests: canonical registry, portable policy and receipts."""

from decimal import Decimal

import pytest

from src.engine.amount_math import classify_amount_diff
from src.engine.check_obligations import build_check_receipts, obligation_ids_for_rule
from src.engine.rule_policy import classify_percentage_point_difference
from src.engine.fact_context import FactContext
from src.engine.rules_v33 import (
    R33103_Income_vs_Text,
    R33104_Expense_vs_Text,
    R33214_T1_TotalBalance,
    build_document,
)
from src.engine.rule_registry import (
    RULE_REGISTRY_VERSION,
    all_rule_ids,
    rule_catalog,
    select_rules,
)
from src.schemas.issues import AnalysisConfig
from src.schemas.parse_artifact import FiscalFact
from src.services.engine_rule_runner import EngineRuleRunner


def test_canonical_registry_routes_every_production_family() -> None:
    catalog = rule_catalog()
    assert catalog["version"] == RULE_REGISTRY_VERSION
    # 2026-10-22：新增 BUD-114（主要内容逐项金额）、BUD-115（三公数量要素）；
    # 检查点清单全系统默认：CMM-008~026（19 条通用）、BUD-116~123（8 条）、V33-251/252。
    assert catalog["total"] == 116
    assert len(all_rule_ids()) == 116
    assert len(select_rules("budget")) == 54
    assert len(select_rules("final")) == 89
    assert len(select_rules("unknown")) == 27
    # AC-01：已迁移规则以统一事实为输入（fact_inputs 非空、migration=complete），
    # 其余规则仍是 legacy_document/pending——目录必须如实区分两种口径。
    migrated = {
        "BUD-107",
        "CMM-001",
        "V33-103",
        "V33-104",
        "V33-214",
        "V33-248",
        "V33-249",
    }
    for item in catalog["rules"]:
        if item["rule_id"] in migrated:
            assert item["evaluation_basis"] == "fiscal_fact", item
            assert item["fact_migration"] == "complete", item
            assert item["fact_inputs"], item
        else:
            assert item["evaluation_basis"] == "legacy_document", item
            assert item["fact_migration"] == "pending", item
            assert item["fact_inputs"] == [], item
    assert all("implementation" in item and "obligation_ids" in item for item in catalog["rules"])


def test_portable_amount_and_percentage_tolerance_boundaries() -> None:
    # 2026-10-22 口径决议①：政策容差 0.01 万元，显示舍入包络照旧保留。
    assert classify_amount_diff(100, Decimal("100.02"), n_children=3)[0] == "rounding_hint"
    assert classify_amount_diff(100, Decimal("100.008"), envelope=Decimal("0.005"))[0] == "within_policy_tolerance"
    assert classify_amount_diff(100, Decimal("100.1"), n_children=3)[0] == "mismatch"
    assert classify_amount_diff(100, Decimal("100.5"), n_children=3)[0] == "mismatch"

    assert classify_percentage_point_difference("3.0", "3.1")[0] == "within_policy_tolerance"
    assert classify_percentage_point_difference("3.0", "3.1001")[0] == "mismatch"


def test_receipts_are_linked_and_mark_legacy_fact_migration_pending() -> None:
    coverage = {
        "instances": [
            {
                "obligation_id": "OBL-TEST",
                "status": "completed",
                "checkers": ["CMM-002"],
                "missing_checkers": [],
            },
            {
                "obligation_id": "OBL-MISSING",
                "status": "not_executed",
                "checkers": ["CMM-002"],
                "missing_checkers": ["RULE-NOT-REGISTERED"],
            },
        ]
    }
    result = {
        "issues": {
            "all": [
                {
                    "id": "finding-1",
                    "rule_id": "CMM-002",
                    "obligation_ids": ["OBL-TEST"],
                }
            ]
        }
    }
    receipts = build_check_receipts(
        result,
        report_kind="budget",
        rule_execution_summary={"rule_statuses": {"CMM-002": "fail"}},
        obligation_coverage=coverage,
    )
    by_id = {item["sub_check_id"]: item for item in receipts}

    linked = by_id["OBL-TEST:CMM-002"]
    assert linked["status"] == "fail"
    assert linked["issue_refs"] == ["finding-1"]
    assert linked["required_fact_ids"] == []
    assert linked["operand_fact_ids"] == []
    assert linked["evaluation_basis"] == "legacy_document"
    assert linked["fact_migration_status"] == "pending"
    assert linked["tolerance"]["configured_amount_tolerance_wan"] == "0.01"

    missing = by_id["OBL-MISSING:RULE-NOT-REGISTERED"]
    assert missing["status"] == "insufficient_data"
    assert missing["incomplete_reason"]


def test_mixed_relation_receipts_do_not_promote_legacy_passes_to_fact_complete() -> None:
    coverage = {
        "instances": [
            {
                "obligation_id": "OBL-TXT-BUDGET",
                "status": "completed",
                "checkers": ["BUD-107"],
                "missing_checkers": [],
            }
        ]
    }
    relations = [
        {
            "rule_id": "BUD-107",
            "sub_check_id": "BUD-107:table-text:fact",
            "obligation_id": "OBL-TXT-BUDGET",
            "status": "pass",
            "evaluation_basis": "fiscal_fact",
            "fact_migration_status": "complete",
            "operand_fact_ids": ["fact-1"],
            "required_fact_ids": ["fact-1"],
            "tolerance": {
                "configured_amount_tolerance_wan": "0.5",
                "rounding_envelope_wan": "0.2",
                "effective_amount_threshold_wan": "0.5",
            },
        },
        {
            "rule_id": "BUD-107",
            "sub_check_id": "BUD-107:legacy-text:pass",
            "obligation_id": "OBL-TXT-BUDGET",
            "status": "pass",
            "evaluation_basis": "legacy_document",
            "fact_migration_status": "pending",
            "operand_fact_ids": [],
            "required_fact_ids": [],
            "issue_refs": ["legacy-finding"],
            "tolerance": {
                "configured_amount_tolerance_wan": "0.5",
                "rounding_envelope_wan": "0",
                "effective_amount_threshold_wan": "0.5",
            },
        },
    ]
    receipts = build_check_receipts(
        {"issues": {"all": [{"id": "legacy-finding", "rule_id": "BUD-107"}]}},
        report_kind="budget",
        rule_execution_summary={"rule_statuses": {"BUD-107": "pass"}},
        obligation_coverage=coverage,
        fact_receipts=relations,
    )
    aggregate = next(item for item in receipts if item["sub_check_id"] == "OBL-TXT-BUDGET:BUD-107")
    assert aggregate["status"] == "pass"
    assert aggregate["evaluation_basis"] == "legacy_document"
    assert aggregate["fact_migration_status"] == "pending"
    assert aggregate["operand_fact_ids"] == []
    assert aggregate["issue_refs"] == ["legacy-finding"]


def _final_fact(
    fact_uid: str,
    indicator: str,
    value: str,
    *,
    table_code: str,
    subject_name: str = "合计",
) -> FiscalFact:
    return FiscalFact(
        fiscal_year=2025,
        budget_stage='决算',
        fact_uid=fact_uid,
        indicator=indicator,
        raw_value=value,
        unit="万元",
        normalized_value=Decimal(value),
        subject_name=subject_name,
        table_code=table_code,
        recognition_status="verified",
        confidence=0.99,
        evidence_ids=[f"evidence-{fact_uid}"],
    )


def _convert_and_link(doc, rule_code: str, issues):
    runner = EngineRuleRunner()
    findings = [
        runner._issue_to_finding(issue, rule_id=rule_code, document=doc)
        for issue in issues
    ]
    runner._link_relation_receipts(doc, rule_code, 0, findings)
    return findings


@pytest.mark.parametrize(
    ("ratio_claim", "expected_status"),
    [
        ("占99.89%", "fail"),
        ("占比99.89%", "fail"),
        ("占100%", "pass"),
        ("占比99.90%", "pass"),
    ],
)
def test_r5_01_v33_103_checks_common_ratio_claims_and_links_failure(
    ratio_claim: str, expected_status: str
) -> None:
    text = (
        "二、收入决算情况说明\n"
        f"本年收入合计1000万元，财政拨款收入1000万元，{ratio_claim}。\n"
        "收入决算表\n单位：万元\n"
    )
    doc = build_document("unit-2025-final.pdf", [text], [[]], 100)
    doc.fact_context = FactContext(
        facts=[
            _final_fact(
                "fact-income-total", "total_actual", "1000",
                table_code="FIN_02_income",
            ),
            _final_fact(
                "fact-fiscal-income", "fiscal_allocation", "1000",
                table_code="FIN_02_income",
            ),
        ],
        fiscal_year=2025,
        report_kind="final",
    )

    issues = R33103_Income_vs_Text().apply(doc)
    ratio_receipt = next(
        item for item in doc.fact_receipts
        if item["sub_check_id"] == "T2:fiscal_allocation:share_of_income_total"
    )
    assert ratio_receipt["status"] == expected_status
    if expected_status == "pass":
        assert issues == []
        return

    ratio_issue = next(item for item in issues if "占比" in item.message)
    assert ratio_issue.check_relation_id == "V33-103:T2:fiscal_allocation:share_of_income_total"
    assert set(ratio_issue.fact_ids) == {"fact-fiscal-income", "fact-income-total"}
    assert set(ratio_issue.evidence_ids) == {"evidence-fact-fiscal-income", "evidence-fact-income-total"}

    findings = _convert_and_link(doc, "V33-103", [ratio_issue])
    linked_receipt = next(
        item for item in doc.fact_receipts
        if item["sub_check_id"] == "T2:fiscal_allocation:share_of_income_total"
    )
    assert linked_receipt["issue_refs"] == [findings[0].id]
    obligation_ids = obligation_ids_for_rule("V33-103")
    final_receipts = build_check_receipts(
        {"issues": {"all": [finding.model_dump() for finding in findings]}, "meta": {"check_receipts": doc.fact_receipts}},
        report_kind="final",
        rule_execution_summary={"rule_statuses": {"V33-103": "fail"}},
        obligation_coverage={
            "instances": [
                {
                    "obligation_id": obligation_id,
                    "status": "completed",
                    "checkers": ["V33-103"],
                    "missing_checkers": [],
                }
                for obligation_id in obligation_ids
            ]
        },
        fact_receipts=doc.fact_receipts,
    )
    ratio_aggregate = next(
        item for item in final_receipts
        if item["sub_check_id"].endswith(":V33-103")
        and item.get("obligation_id") in obligation_ids
    )
    assert ratio_aggregate["status"] == "fail"
    assert findings[0].id in ratio_aggregate["issue_refs"]


def test_r5_02_v33_104_and_v33_214_findings_keep_fact_evidence_and_relation_ids() -> None:
    expense_text = (
        "三、支出决算情况说明\n"
        "本年支出合计100万元，其中基本支出61万元，占61%；"
        "项目支出39万元，占39%。\n支出决算表\n单位：万元\n"
    )
    expense_doc = build_document("unit-2025-final.pdf", [expense_text], [[]], 100)
    expense_doc.fact_context = FactContext(
        facts=[
            _final_fact("fact-expense-total", "total_actual", "100", table_code="FIN_03_expenditure"),
            _final_fact("fact-basic", "basic_actual", "60", table_code="FIN_03_expenditure"),
            _final_fact("fact-project", "project_actual", "40", table_code="FIN_03_expenditure"),
        ],
        fiscal_year=2025,
        report_kind="final",
    )
    expense_issues = R33104_Expense_vs_Text().apply(expense_doc)
    assert expense_issues
    expense_findings = _convert_and_link(expense_doc, "V33-104", expense_issues)
    for finding in expense_findings:
        assert finding.check_relation_id
        assert finding.fact_ids
        assert finding.evidence_ids
        matching = next(
            item for item in expense_doc.fact_receipts
            if f"V33-104:{item['sub_check_id']}" == finding.check_relation_id
        )
        assert finding.id in matching["issue_refs"]

    total_doc = build_document("unit-2025-final.pdf", ["收入支出决算总表\n"], [[]], 100)
    total_doc.fact_context = FactContext(
        facts=[
            _final_fact("fact-t1-income", "income_actual", "100", table_code="FIN_01_income_expenditure_total"),
            _final_fact("fact-t1-expense", "expenditure_actual", "90", table_code="FIN_01_income_expenditure_total"),
        ],
        fiscal_year=2025,
        report_kind="final",
    )
    total_issues = R33214_T1_TotalBalance().apply(total_doc)
    total_findings = _convert_and_link(total_doc, "V33-214", total_issues)
    assert len(total_findings) == 1
    finding = total_findings[0]
    assert finding.check_relation_id == "V33-214:T1:income_expenditure_total:balance"
    assert set(finding.fact_ids) == {"fact-t1-income", "fact-t1-expense"}
    assert set(finding.evidence_ids) == {"evidence-fact-t1-income", "evidence-fact-t1-expense"}
    assert finding.id in total_doc.fact_receipts[0]["issue_refs"]


def test_fact_aggregate_retains_each_distinct_effective_tolerance() -> None:
    coverage = {
        "instances": [
            {
                "obligation_id": "OBL-TXT-BUDGET",
                "status": "completed",
                "checkers": ["BUD-107"],
                "missing_checkers": [],
            }
        ]
    }
    relations = []
    for relation_id, fact_id, envelope, threshold in (
        ("BUD-107:row-a", "fact-a", "0.2", "0.5"),
        ("BUD-107:row-b", "fact-b", "0.8", "0.8"),
    ):
        relations.append(
            {
                "rule_id": "BUD-107",
                "sub_check_id": relation_id,
                "obligation_id": "OBL-TXT-BUDGET",
                "status": "pass",
                "evaluation_basis": "fiscal_fact",
                "fact_migration_status": "complete",
                "operand_fact_ids": [fact_id],
                "required_fact_ids": [fact_id],
                "tolerance": {
                    "configured_amount_tolerance_wan": "0.5",
                    "rounding_envelope_wan": envelope,
                    "effective_amount_threshold_wan": threshold,
                },
            }
        )
    receipts = build_check_receipts(
        {"issues": {"all": []}},
        report_kind="budget",
        rule_execution_summary={"rule_statuses": {"BUD-107": "pass"}},
        obligation_coverage=coverage,
        fact_receipts=relations,
    )
    aggregate = next(item for item in receipts if item["sub_check_id"] == "OBL-TXT-BUDGET:BUD-107")
    assert aggregate["evaluation_basis"] == "fiscal_fact"
    assert aggregate["fact_migration_status"] == "complete"
    assert aggregate["tolerance"]["actual_envelope_source"] == "per_relation_receipts"
    assert aggregate["tolerance"]["relation_tolerances"]["BUD-107:row-b"]["effective_amount_threshold_wan"] == "0.8"
