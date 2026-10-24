"""R16: reject unnormalized/foreign facts without reviving legacy operands."""

import dataclasses
from decimal import Decimal

import pytest

from src.engine.fact_context import (
    FactContext, FactOverlay, apply_fact_overlay, build_fact_context_from_artifact, resolve_operand,
)
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rules_v33 import R33103_Income_vs_Text, build_document
from src.services.pdf_parser import PDFParser
from tests.remediation.test_rc_batch1 import _make_fragment, _artifact_with


def native_contract(table_owner="测试甲局", unit="万元", raw="900", document_owner="测试甲局"):
    rows = [["科目名称", "本年收入合计", "财政拨款收入"], ["合计", raw, raw]]
    fragment = _make_fragment(
        "FIN_02_income", rows, title=f"{table_owner}收入决算表", unit_hint=unit, page=2,
    )
    instance = PDFParser(None)._build_logical_instance(fragment)
    context = build_fact_context_from_artifact(
        _artifact_with([fragment], [instance]), org_name=document_owner, fiscal_year=2025,
        report_kind="final",
    )
    doc = build_document(
        "测试甲局2025年决算.pdf", [
            "测试甲局2025年部门决算\n二、收入决算情况说明\n"
            "本年收入合计100万元，财政拨款收入100万元，占100%。",
            fragment.title + ("\n单位："+unit if unit else ""),
        ], [[], [rows]], 100,
    )
    doc.fact_context = context
    return context, doc


@pytest.mark.parametrize("unit,raw", [(None, "900"), ("未知单位", "900"), ("万元", "NaN")])
def test_unusable_normalization_never_enters_operand_views_or_legacy_fallback(unit, raw):
    context, doc = native_contract(unit=unit, raw=raw)
    assert context.facts and not context.definite_facts()
    assert context.non_operand_facts() == context.facts
    assert context.summary()["operand_count"] == 0
    resolution = resolve_operand(context, "total_actual", table_code="FIN_02_income")
    assert resolution.status == "blocked" and "归一" in resolution.reason
    issues, outcomes = run_rules_with_outcomes(doc, report_kind="final", rules=[R33103_Income_vs_Text()])
    assert not issues and outcomes[0].status == "insufficient_data"
    assert "归一" in " ".join(outcomes[0].unresolved_reasons)
    assert all(item["status"] == "insufficient_data" for item in doc.fact_receipts)


@pytest.mark.parametrize("raw", ["100", "900"])
def test_known_foreign_owner_is_blocked_even_if_amounts_match_or_legacy_table_exists(raw):
    context, doc = native_contract(table_owner="测试乙局", raw=raw)
    assert all(fact.org_name == "测试乙局" for fact in context.facts)
    assert not context.definite_facts()
    resolution = resolve_operand(context, "total_actual", table_code="FIN_02_income")
    assert resolution.status == "blocked" and "主体" in resolution.reason
    assert context.best_value("total_actual")[0] is None
    issues, outcomes = run_rules_with_outcomes(doc, report_kind="final", rules=[R33103_Income_vs_Text()])
    assert not issues and outcomes[0].status == "insufficient_data"
    assert "主体" in " ".join(outcomes[0].unresolved_reasons)
    assert all(item["status"] == "insufficient_data" for item in doc.fact_receipts)


def test_unique_current_owner_wins_over_other_units_total():
    current, _ = native_contract(raw="100")
    foreign, _ = native_contract(table_owner="测试乙局", raw="900")
    mixed = dataclasses.replace(current, facts=[*foreign.facts, *current.facts], version_token="")
    result = resolve_operand(mixed, "total_actual", table_code="FIN_02_income")
    assert result.status == "ok" and result.value == Decimal("100")
    assert result.fact.org_name == "测试甲局"
    assert len(mixed.definite_facts()) == 2


@pytest.mark.parametrize("unit,raw", [
    ("万元", "100"), ("元", "1000000"), ("亿元", "0.01"),
    (None, "100万元"), (None, "1000000元"),
])
def test_same_owner_explicit_and_embedded_units_stay_normalized_and_pass(unit, raw):
    context, doc = native_contract(unit=unit, raw=raw)
    assert len(context.definite_facts()) == 2
    assert all(fact.normalized_value == Decimal("100") for fact in context.facts)
    assert all(fact.unit in {"万元", "元", "亿元"} for fact in context.facts)
    issues, outcomes = run_rules_with_outcomes(doc, report_kind="final", rules=[R33103_Income_vs_Text()])
    assert not issues and outcomes[0].status == "pass"


@pytest.mark.parametrize("raw", ["0", "7", "746.62"])
def test_legal_zero_integer_and_original_amount_remain_operands(raw):
    context, _ = native_contract(raw=raw)
    result = resolve_operand(context, "total_actual", table_code="FIN_02_income")
    assert result.status == "ok" and result.value == Decimal(raw)
    assert len(context.definite_facts()) == 2


@pytest.mark.parametrize("prefix,suffix", [
    ("2025年", ""), ("", "2025年"), ("", "年初预算"), ("", "调整后预算"), ("", "全年预算"),
])
def test_original_full_table_owner_can_bind_a_shortened_legacy_title_extraction(prefix, suffix):
    owner = "金华市福利彩票发行中心"
    context, _ = native_contract(table_owner=prefix+owner+suffix, document_owner=owner, raw="100")
    assert any(fact.org_name != owner for fact in context.facts)
    assert all(fact.extra_dims["source_table_title"].startswith(prefix+owner) for fact in context.facts)
    assert len(context.definite_facts()) == 2


@pytest.mark.parametrize("suffix", ["下属乙中心", "预算管理中心"])
def test_parent_name_inside_subordinate_title_cannot_borrow_parent_identity(suffix):
    owner = "测试甲局"
    context, _ = native_contract(table_owner=owner+suffix, document_owner=owner, raw="100")
    assert not context.definite_facts()
    assert resolve_operand(context, "total_actual", table_code="FIN_02_income").status == "blocked"


def test_implicit_table_owner_and_absent_document_owner_are_not_blanket_rejected():
    inherited, _ = native_contract(table_owner="", raw="100")
    assert len(inherited.definite_facts()) == 2
    unknown, _ = native_contract(table_owner="测试乙局", document_owner=None, raw="100")
    assert len(unknown.definite_facts()) == 2


def test_unit_correction_recomputes_relations_while_original_evidence_stays_at_original_cell():
    context, doc = native_contract(unit=None, raw="1000000")
    original = context.to_dict()
    original_refs = context.evidence_refs()
    assert not context.definite_facts()
    corrected = apply_fact_overlay(context, [FactOverlay(fact_uid=fact.fact_uid, unit="元") for fact in context.facts])
    assert len(corrected.definite_facts()) == 2
    assert context.to_dict() == original
    assert corrected.evidence_refs() == original_refs
    assert corrected.version_token != context.version_token
    doc.fact_context = corrected
    issues, outcomes = run_rules_with_outcomes(doc, report_kind="final", rules=[R33103_Income_vs_Text()])
    assert not issues and outcomes[0].status == "pass"
    assert {uid for item in doc.fact_receipts for uid in item["operand_fact_ids"]} == {
        fact.fact_uid for fact in corrected.facts
    }


def test_raw_amount_correction_does_not_validate_a_different_units_identity():
    context, _ = native_contract(table_owner="测试乙局")
    corrected = apply_fact_overlay(context, [FactOverlay(fact_uid=fact.fact_uid, raw_value="100") for fact in context.facts])
    assert all(fact.recognition_status == "verified" for fact in corrected.facts)
    assert not corrected.definite_facts()
    assert resolve_operand(corrected, "total_actual", table_code="FIN_02_income").status == "blocked"
