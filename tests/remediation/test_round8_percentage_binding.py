"""Synthetic production-rule regressions for full denominator and owner binding.

These are boundary tests, not independently frozen business truth or OCR proof.
"""

import asyncio
from decimal import Decimal

import pytest

from src.engine.check_obligations import build_check_receipts, build_obligation_ledger
from src.engine.fact_context import FactContext
from src.engine.pipeline import build_issues_payload
from src.engine.rule_registry import select_rules
from src.engine.rules_v33 import (
    R33103_Income_vs_Text,
    R33104_Expense_vs_Text,
    _resolve_indicator_percentage,
    build_document,
)
from src.schemas.parse_artifact import FiscalFact
from src.services.document_profile_resolver import resolve_document_profile


def make_document(kind, body, *, fiscal=600, owner="测试局", declaration=""):
    if kind == "income":
        title, table_code = "收入决算表", "FIN_02_income"
        heading = "二、收入决算情况说明"
        rows = [
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
            ["", "合计", "1000", str(fiscal), "0", "0", "0", "0", str(1000 - fiscal)],
        ]
        operands = {"total_actual": 1000, "fiscal_allocation": fiscal}
        rule, companion_id, obligation = R33103_Income_vs_Text(), "V33-226", "OBL-TXT-T2"
    else:
        title, table_code = "支出决算表", "FIN_03_expenditure"
        heading = "三、支出决算情况说明"
        rows = [
            [
                "科目编码",
                "科目名称",
                "本年支出合计",
                "基本支出",
                "项目支出",
                "上缴上级支出",
                "经营支出",
                "对附属单位补助支出",
            ],
            ["", "合计", "100", "60", "40", "0", "0", "0"],
        ]
        operands = {"total_actual": 100, "basic_actual": 60, "project_actual": 40}
        rule, companion_id, obligation = R33104_Expense_vs_Text(), "V33-220", "OBL-TXT-T3"
    text = (
        f"2025年决算公开\n发布单位：{owner}\n{declaration}\n{heading}\n{body}\n{title}\n单位：万元"
    )
    doc = build_document(f"{owner}2025年决算.pdf", [text], [[rows]], 100)
    doc.fact_context = FactContext(
        facts=[
            FiscalFact(
                fact_uid=f"synthetic-{indicator}",
                fiscal_year=2025,
                budget_stage='决算',
                indicator=indicator,
                raw_value=str(value),
                unit="万元",
                normalized_value=Decimal(value),
                subject_name="合计",
                table_code=table_code,
                recognition_status="verified",
                confidence=0.99,
                evidence_ids=[f"synthetic-evidence-{indicator}"],
            )
            for indicator, value in operands.items()
        ],
        fiscal_year=2025,
        report_kind="final",
    )
    doc.document_profile = resolve_document_profile(
        explicit_report_kind="final",
        filename=doc.path,
        page_texts=doc.page_texts,
        cover_org_name=owner,
        cover_org_label="单位",
    )
    companion = next(item for item in select_rules("final") if item.code == companion_id)
    return doc, rule, companion, obligation


def execute(kind, body, **kwargs):
    doc, rule, companion, obligation_id = make_document(kind, body, **kwargs)
    payload = build_issues_payload(doc, False, report_kind="final", rules=[rule, companion])
    ledger = build_obligation_ledger(
        doc.document_profile,
        report_kind="final",
        rule_execution_summary=payload["rule_execution_summary"],
    )
    receipts = build_check_receipts(
        payload,
        report_kind="final",
        rule_execution_summary=payload["rule_execution_summary"],
        obligation_coverage=ledger,
        fact_receipts=doc.fact_receipts,
    )
    pair = next(item for item in receipts if item["sub_check_id"] == f"{obligation_id}:{rule.code}")
    relations = {
        item["sub_check_id"]: item for item in receipts if "share_of" in item["sub_check_id"]
    }
    issues = [item for item in payload["issues"]["all"] if "占比" in item["message"]]
    return pair, relations, issues


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("占60%", "pass"),
        ("占本年收入合计60%", "pass"),
        ("占本年收入总额的60%", "pass"),
        ("占收入合计60%", "pass"),
        ("占本年收入合计59.90%", "pass"),
        ("占本年收入合计59.89%", "fail"),
        ("占比59.89%", "fail"),
        ("占财政拨款收入合计100%", "insufficient_data"),
        ("占一般公共预算财政拨款收入合计100%", "insufficient_data"),
        ("占财政拨款收入总额100%", "insufficient_data"),
        ("占上年收入合计60%", "insufficient_data"),
        ("占本年收入合计和事业收入60%", "insufficient_data"),
        ("占本年收入合计或总收入60%", "insufficient_data"),
        ("占全部经费60%", "insufficient_data"),
        ("占本年收入合计1000万元的60%", "insufficient_data"),
        ("占教育局本年收入合计60%", "insufficient_data"),
    ],
)
def test_complete_income_denominator_on_rules_receipts_and_ledger(claim, expected):
    pair, relations, issues = execute(
        "income", f"本年收入合计1000万元，财政拨款收入600万元，{claim}。"
    )
    relation = relations["T2:fiscal_allocation:share_of_income_total"]
    assert relation["status"] == pair["status"] == expected
    assert bool(issues) == (expected == "fail")
    if expected == "insufficient_data":
        assert "显式分母" in relation["incomplete_reason"]
        assert claim in relation["incomplete_reason"]
    if issues:
        assert (
            issues[0]["check_relation_id"] == "V33-103:T2:fiscal_allocation:share_of_income_total"
        )
        assert set(issues[0]["fact_ids"]) == {
            "synthetic-total_actual",
            "synthetic-fiscal_allocation",
        }


@pytest.mark.parametrize(
    ("body", "basic", "project"),
    [
        ("基本支出60万元，占60%，项目支出40万元，占40%。", "pass", "pass"),
        ("基本支出60万元，占本年支出合计60%，项目支出40万元，占支出合计40%。", "pass", "pass"),
        ("基本支出60万元，占55%，项目支出40万元，占45%。", "fail", "fail"),
        (
            "基本支出60万元，占基本支出合计100%，项目支出40万元，占40%。",
            "insufficient_data",
            "pass",
        ),
        (
            "基本支出60万元，占60%，项目支出40万元，占项目支出合计100%。",
            "pass",
            "insufficient_data",
        ),
        (
            "基本支出60万元，占基本支出合计100%，项目支出40万元，占45%。",
            "insufficient_data",
            "fail",
        ),
        (
            "基本支出60万元，占基本支出及项目支出合计60%，项目支出40万元，占40%。",
            "insufficient_data",
            "pass",
        ),
        ("基本支出60万元，占60%，项目支出40万元。", "pass", "not_applicable"),
    ],
)
def test_expense_qualified_denominator_preserves_independent_relations(body, basic, project):
    _pair, relations, issues = execute("expense", "本年支出合计100万元，" + body)
    assert relations["T3:basic_share_of_expense_total"]["status"] == basic
    assert relations["T3:project_share_of_expense_total"]["status"] == project
    assert {item["check_relation_id"] for item in issues} == {
        f"V33-104:T3:{name}_share_of_expense_total"
        for name, status in (("basic", basic), ("project", project))
        if status == "fail"
    }


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        ("教育局财政拨款收入50万元，占50%。", "insufficient_data"),
        ("教育局50万元，占50%。", "insufficient_data"),
        ("教育局的财政拨款收入50万元，占50%。", "insufficient_data"),
        ("教育局：财政拨款收入50万元，占50%。", "insufficient_data"),
        ("教育局事业收入50万元，财政拨款收入50万元，占50%。", "insufficient_data"),
        ("测试局下属教育局财政拨款收入50万元，占50%。", "insufficient_data"),
        ("不明机构财政拨款收入50万元，占50%。", "insufficient_data"),
        ("测试局财政拨款收入1000万元，占100%。", "pass"),
        ("本单位财政拨款收入1000万元，占100%。", "pass"),
        ("测试局2025年财政拨款收入1000万元，占99.89%。", "fail"),
        ("2025年测试局财政拨款收入1000万元，占99.89%。", "fail"),
        ("2024年测试局财政拨款收入1000万元，占80%。", "insufficient_data"),
        ("财政拨款收入1000万元，占100%，教育局财政拨款收入50万元，占50%。", "pass"),
        ("财政拨款收入1000万元，占99.89%，教育局财政拨款收入50万元，占50%。", "fail"),
        ("教育局财政拨款收入50万元，占50%，本单位财政拨款收入1000万元，占100%。", "pass"),
    ],
)
def test_indicator_preceding_subject_is_bound_before_current_facts(tail, expected):
    pair, relations, issues = execute(
        "income",
        "本年收入合计1000万元，财政拨款收入1000万元，" + tail,
        fiscal=1000,
    )
    relation = relations["T2:fiscal_allocation:share_of_income_total"]
    assert relation["status"] == pair["status"] == expected
    assert bool(issues) == (expected == "fail")
    if expected == "insufficient_data":
        assert "原文：" in relation["incomplete_reason"]
        assert "教育局" in relation["incomplete_reason"] or "主体" in relation["incomplete_reason"]
    if expected == "fail" and "教育局" in tail:
        assert "教育局" not in issues[0]["evidence"][0]["text"]
        assert "99.89%" in issues[0]["evidence"][0]["text"]


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        ("测试局（以下简称‘测局’）。", "pass"),
        ("测试局（简称“测局”）。", "pass"),
        ("教育局（以下简称‘测局’）。", "insufficient_data"),
        ("2024年测试局（以下简称‘测局’）。", "insufficient_data"),
        ("测试局下属教育局（以下简称‘测局’）。", "insufficient_data"),
        ("测试局（以下简称‘测局’）。教育局（以下简称‘测局’）。", "insufficient_data"),
    ],
)
def test_profile_owner_alias_requires_current_declaration_and_scope(declaration, expected):
    _pair, relations, issues = execute(
        "income",
        "本年收入合计1000万元，财政拨款收入1000万元，测局财政拨款收入1000万元，占100%。",
        fiscal=1000,
        declaration=declaration,
    )
    assert relations["T2:fiscal_allocation:share_of_income_total"]["status"] == expected
    assert not issues


def test_unresolved_profile_owner_is_not_guessed_from_ratio_subject():
    doc, rule, _companion, _obligation = make_document(
        "income",
        "本年收入合计1000万元，财政拨款收入1000万元，教育局财政拨款收入50万元，占50%。",
        fiscal=1000,
    )
    doc.document_profile.organization_name.value = None
    payload = build_issues_payload(doc, False, report_kind="final", rules=[rule])
    assert (
        next(r for r in doc.fact_receipts if "share_of" in r["sub_check_id"])["status"]
        == "insufficient_data"
    )
    assert not [item for item in payload["issues"]["all"] if "占比" in item["message"]]


def test_rejected_claim_quote_keeps_subject_and_complete_denominator():
    text = "财政拨款收入600万元，教育局财政拨款收入50万元，占财政拨款收入合计100%。"
    claim = _resolve_indicator_percentage(
        text,
        ("财政拨款收入",),
        ("本年收入合计", "收入合计"),
        organization_name="测试局",
    )
    assert claim.status == "ambiguous"
    assert claim.quotes == ("教育局财政拨款收入50万元，占财政拨款收入合计100%",)
    assert tuple(text[start:end] for start, end in claim.spans) == claim.quotes
    assert claim.denominators == ("财政拨款收入合计",) and claim.subjects == ("教育局",)


def test_profile_owner_survives_spawned_rule_process(monkeypatch):
    from src.services.rule_process import run_rules_in_process

    monkeypatch.setenv("RULES_PROCESS_START_METHOD", "spawn")
    doc, rule, _companion, _obligation = make_document(
        "income",
        "本年收入合计1000万元，财政拨款收入1000万元，测局财政拨款收入1000万元，占99.89%。",
        fiscal=1000,
        declaration="测试局（以下简称‘测局’）。",
    )
    payload = asyncio.run(run_rules_in_process(doc, False, "final", 30, [rule]))
    relation = next(r for r in payload["fact_receipts"] if "share_of" in r["sub_check_id"])
    assert relation["status"] == "fail"
    assert "99.89%" in next(
        item["message"] for item in payload["issues"]["all"] if "占比" in item["message"]
    )


@pytest.mark.parametrize(("share", "expected"), [("100", "pass"), ("99.89", "fail")])
def test_engine_runner_consumes_same_profile_snapshot(tmp_path, monkeypatch, share, expected):
    import fitz

    from src.schemas.issues import AnalysisConfig, JobContext
    from src.services.engine_rule_runner import EngineRuleRunner

    owner = "上海市测试区建设和管理委员会"
    doc, rule, _companion, _obligation = make_document(
        "income",
        f"本年收入合计1000万元，财政拨款收入1000万元，建管委财政拨款收入1000万元，占{share}%。",
        fiscal=1000,
        owner=owner,
        declaration=f"{owner}（以下简称‘建管委’）。",
    )
    # Cover metadata is provided separately; page text cannot establish the owner.
    page_texts = [doc.page_texts[0].split("\n", 2)[2]]
    assert resolve_document_profile(
        explicit_report_kind="final", filename="input.pdf", page_texts=page_texts
    ).organization_name.value != owner
    path = tmp_path / "input.pdf"
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.save(path)
    context = JobContext(
        job_id="synthetic-profile-transport",
        pdf_path=str(path),
        page_texts=page_texts,
        page_tables=doc.page_tables,
        fact_context=doc.fact_context.to_dict(),
        meta={"report_kind": "final", "document_profile": doc.document_profile.model_dump()},
    )
    runner = EngineRuleRunner()
    monkeypatch.setattr(runner, "_select_rule_set", lambda *_args: [rule])
    findings = asyncio.run(runner.run_rules(context, [], AnalysisConfig()))
    relation = next(
        r for r in runner.get_stats()["fact_receipts"] if "share_of" in r["sub_check_id"]
    )
    assert relation["status"] == expected
    assert any("占比" in item.message for item in findings) == (expected == "fail")
