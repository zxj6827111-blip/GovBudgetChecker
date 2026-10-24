"""Round 6 counterexamples: actual rules/ledger, with legal and failing neighbours."""

from decimal import Decimal

import pytest

from src.engine.budget_rules import BUD107_TextTableConsistency, _document_org_aliases
from src.engine.check_obligations import build_check_receipts, build_obligation_ledger
from src.engine.fact_context import FactContext
from src.engine.pipeline import build_issues_payload, run_rules_with_outcomes
from src.engine.rule_registry import select_rules
from src.engine.rules_v33 import (
    R33103_Income_vs_Text,
    R33104_Expense_vs_Text,
    build_document,
    _resolve_indicator_percentage,
)
from src.schemas.parse_artifact import FiscalFact
from src.services.document_profile_resolver import resolve_document_profile


def fact(uid, indicator, value, table):
    return FiscalFact(
        fact_uid=uid,
        fiscal_year=2025,
        budget_stage='决算',
        indicator=indicator,
        raw_value=str(value),
        unit="万元",
        normalized_value=Decimal(str(value)),
        subject_name="合计",
        table_code=table,
        recognition_status="verified",
        confidence=0.99,
        evidence_ids=[f"ev-{uid}"],
    )


def income_document(body):
    text = f"2025年测试单位决算公开\n二、收入决算情况说明\n{body}\n收入决算表\n单位：万元"
    rows = [
        [
            [
                "科目编码",
                "科目名称",
                "本年收入合计",
                "财政拨款收入",
                "上级补助收入",
                "事业收入",
                "经营收入",
                "附属单位上缴收入",
                "其他收入",
            ],
            ["", "合计", "1000", "1000", "0", "0", "0", "0", "0"],
        ]
    ]
    doc = build_document("测试单位2025年决算.pdf", [text], [rows], 100)
    doc.fact_context = FactContext(
        facts=[
            fact("income", "total_actual", 1000, "FIN_02_income"),
            fact("fiscal", "fiscal_allocation", 1000, "FIN_02_income"),
        ],
        fiscal_year=2025,
        report_kind="final",
    )
    return doc


@pytest.mark.parametrize(
    ("clause", "expected"),
    [
        ("财政拨款收入1000万元，事业收入0万元，占0%。", "not_applicable"),
        ("财政拨款收入1000万元；事业收入0万元，占0%。", "not_applicable"),
        ("财政拨款收入1000万元，占100%，事业收入0万元，占0%。", "pass"),
        ("财政拨款收入1000万元，占本年收入合计100%。", "pass"),
        ("财政拨款收入1000万元，占99.89%。", "fail"),
        ("财政拨款收入1000万元，占比99.89%。", "fail"),
        ("财政拨款收入1000万元，占比99.90%。", "pass"),
        ("财政拨款收入1000万元，占100%。", "pass"),
        ("财政拨款收入1000万元，占事业收入100%。", "insufficient_data"),
        ("财政拨款收入1000万元，占100%。财政拨款收入1000万元，占80%。", "insufficient_data"),
        ("财政拨款收入1000万元，教育局50万元，占50%。", "insufficient_data"),
    ],
)
def test_income_ratio_ownership_and_real_multi_rule_ledger(clause, expected):
    doc = income_document("本年收入合计1000万元，其中" + clause)
    companion = next(rule for rule in select_rules("final") if rule.code == "V33-226")
    payload = build_issues_payload(
        doc, use_ai_assist=False, report_kind="final", rules=[R33103_Income_vs_Text(), companion]
    )
    relation = next(
        r
        for r in doc.fact_receipts
        if r["sub_check_id"] == "T2:fiscal_allocation:share_of_income_total"
    )
    assert relation["status"] == expected
    ratio_issues = [i for i in payload["issues"]["all"] if "占比" in i["message"]]
    assert bool(ratio_issues) == (expected == "fail")
    profile = resolve_document_profile(
        explicit_report_kind="final",
        filename=doc.path,
        page_texts=doc.page_texts,
        cover_org_name="测试单位",
        cover_org_label="单位",
    )
    ledger = build_obligation_ledger(
        profile, report_kind="final", rule_execution_summary=payload["rule_execution_summary"]
    )
    receipts = build_check_receipts(
        payload,
        report_kind="final",
        rule_execution_summary=payload["rule_execution_summary"],
        obligation_coverage=ledger,
        fact_receipts=doc.fact_receipts,
    )
    pair = next(r for r in receipts if r["sub_check_id"] == "OBL-TXT-T2:V33-103")
    assert pair["status"] == ("pass" if expected == "not_applicable" else expected)
    assert len([r for r in receipts if r["sub_check_id"] == relation["sub_check_id"]]) == 1
    if expected == "insufficient_data":
        assert relation["incomplete_reason"]


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["not_applicable", "not_applicable"], "not_applicable"),
        (["pass", "not_applicable"], "pass"),
        (["fail", "not_applicable"], "fail"),
        (["insufficient_data", "not_applicable"], "insufficient_data"),
        (["parse_error", "pass", "not_applicable"], "parse_error"),
    ],
)
def test_applicable_pair_aggregation_preserves_every_relation(states, expected):
    relations = [
        {
            "rule_id": "V33-103",
            "sub_check_id": f"relation-{i}",
            "status": state,
            "incomplete_reason": f"reason-{i}",
        }
        for i, state in enumerate(states)
    ]
    receipts = build_check_receipts(
        {},
        report_kind="final",
        rule_execution_summary={"rule_statuses": {"V33-103": expected}},
        obligation_coverage={
            "instances": [
                {"obligation_id": "OBL-TXT-T2", "status": "completed", "checkers": ["V33-103"]}
            ]
        },
        fact_receipts=relations,
    )
    pair = next(r for r in receipts if r["sub_check_id"] == "OBL-TXT-T2:V33-103")
    assert pair["status"] == expected
    for i, state in enumerate(states):
        relation = next(r for r in receipts if r["sub_check_id"] == f"relation-{i}")
        assert (relation["status"], relation["incomplete_reason"]) == (state, f"reason-{i}")


def test_multiple_expense_metrics_keep_separate_ratio_failures():
    text = "三、支出决算情况说明\n本年支出合计100万元，基本支出60万元，占55%，项目支出40万元，占45%。\n支出决算表\n单位：万元"
    doc = build_document("测试单位2025年决算.pdf", [text], [[]], 100)
    doc.fact_context = FactContext(
        facts=[
            fact("total", "total_actual", 100, "FIN_03_expenditure"),
            fact("basic", "basic_actual", 60, "FIN_03_expenditure"),
            fact("project", "project_actual", 40, "FIN_03_expenditure"),
        ],
        fiscal_year=2025,
        report_kind="final",
    )
    issues = R33104_Expense_vs_Text().apply(doc)
    assert len(issues) == 2
    assert {i.check_relation_id for i in issues} == {
        "V33-104:T3:basic_share_of_expense_total",
        "V33-104:T3:project_share_of_expense_total",
    }


def test_ratio_source_span_belongs_to_owner():
    text = "财政拨款收入1000万元，占100%，事业收入0万元，占0%。"
    claim = _resolve_indicator_percentage(text, ("财政拨款收入",))
    assert claim.status == "ok" and claim.value == Decimal("100")
    assert [text[start:end] for start, end in claim.spans] == ["财政拨款收入1000万元，占100%"]


OWNER = "上海市普陀区建设和管理委员会"


@pytest.mark.parametrize(
    ("declaration", "body", "expected"),
    [
        (
            f"{OWNER}（以下简称‘建管委’）。",
            "建管委2026年收入预算100万元，支出预算100万元。",
            "pass",
        ),
        (f"{OWNER}（简称“建管委”）。", "建管委2026年收入预算90万元，支出预算100万元。", "fail"),
        (
            f"{OWNER}（以下简称建管委）。教育局2026年收入预算5万元。",
            "建管委2026年收入预算100万元，支出预算100万元。",
            "pass",
        ),
        (
            "上海市普陀区交通委员会（以下简称‘建管委’）。",
            "建管委2026年收入预算100万元，支出预算100万元。",
            "insufficient_data",
        ),
        (
            f"2025年{OWNER}（以下简称‘建管委’）。",
            "建管委2026年收入预算100万元，支出预算100万元。",
            "insufficient_data",
        ),
        (
            f"{OWNER}下属单位测试中心（以下简称‘建管委’）。",
            "建管委2026年收入预算100万元，支出预算100万元。",
            "insufficient_data",
        ),
        (
            f"{OWNER}（以下简称‘建管委’）。交通委员会（以下简称‘建管委’）。",
            "建管委2026年收入预算100万元，支出预算100万元。",
            "insufficient_data",
        ),
    ],
)
def test_explicit_alias_is_scoped_to_current_owner_and_year(declaration, body, expected):
    text = f"2026年部门财务收支预算总表\n单位：万元\n发布单位：{OWNER}\n{declaration}\n{body}"
    table = [[["项目", "预算数", "项目", "预算数"], ["收入总计", "100", "支出总计", "100"]]]
    doc = build_document(f"{OWNER}2026年部门预算.pdf", [text], [table], 100)
    _issues, _outcomes = run_rules_with_outcomes(
        doc, report_kind="budget", rules=[BUD107_TextTableConsistency()]
    )
    income = next(
        r for r in doc.fact_receipts if r["sub_check_id"] == "T1:income_budget:text_vs_table"
    )
    assert income["status"] == expected
    if expected == "pass":
        aliases = _document_org_aliases(text, OWNER, 2026)
        assert aliases and OWNER in aliases[0]["quote"] and aliases[0]["year"] == 2026
