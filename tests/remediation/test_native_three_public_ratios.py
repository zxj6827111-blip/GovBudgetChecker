from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33TrendCompletionRate, R33224_Narrative7_T7, R33245_ThreePublicDirectionContradiction
from src.schemas.parse_artifact import FiscalFact


def document(body, budget="67.30", actual="43.34", total="244.48"):
    doc = build_document("completion.pdf", ["甲局2025年度部门决算",
        "七、财政拨款“三公”经费支出决算情况说明\n" + body], [], 0)
    facts = []
    for indicator, stage, value in (("vehicle_purchase_operation", "年初预算", budget),
                                    ("vehicle_purchase_operation", "决算", actual),
                                    ("total_actual", "决算", total)):
        facts.append(FiscalFact(fact_uid=f"{indicator}:{stage}", indicator=indicator,
            raw_value=value, unit="万元", normalized_value=Decimal(value), org_name="甲局",
            fiscal_year=2025, period="本年", budget_stage=stage, subject_code="total",
            subject_name="合计", table_code="FIN_07_three_public", instance_uid="table",
            extra_dims={"evidence_refs": [{"physical_page": 1}]}))
    doc.fact_context = FactContext(facts, "a"*64, "run", org_name="甲局", fiscal_year=2025)
    doc.parse_artifact = SimpleNamespace(source_file_hash="a"*64, parse_run_uid="run")
    return doc


def test_native_item_rate_uses_its_budget_and_preserves_original_claim():
    doc = document("公务用车购置及运行维护费支出决算43.34万元，完成预算的95.26%。")
    findings = R33TrendCompletionRate().apply(doc)
    assert len(findings) == 1
    item = findings[0]
    assert "64.40%" in item.message
    assert item.location["pages"] == [1, 2]
    assert {ref["value"] for ref in item.location["operand_refs"]} == {"67.30", "43.34"}
    assert "公务用车" in item.location["table_refs"][0]["span"]
    assert doc.fact_receipts[0]["status"] == "fail"
    assert doc.structured_consumption[0]["fact_version"] == doc.fact_context.version_token


@pytest.mark.parametrize("conflict", ["year", "organization", "ambiguous", "instance", "missing", "stage",
                                      "period", "fund_scope", "org_scope", "parse_version"])
def test_unbound_or_incompatible_operand_never_silently_passes(conflict):
    doc = document("公务用车购置及运行维护费支出决算43.34万元，完成年初预算的95.26%。")
    budget = doc.fact_context.facts[0]
    if conflict == "year":
        budget.fiscal_year = 2024
    elif conflict == "organization":
        budget.org_name = "乙局"
    elif conflict == "ambiguous":
        budget.recognition_status = "ambiguous"
    elif conflict == "instance":
        other = deepcopy(budget)
        other.fact_uid, other.instance_uid = "other", "other"
        doc.fact_context.facts.append(other)
    elif conflict == "stage":
        budget.budget_stage = "全年预算"
    elif conflict == "period":
        budget.period = "上年"
    elif conflict in ("fund_scope", "org_scope"):
        setattr(budget, conflict, "different")
    elif conflict == "parse_version":
        doc.parse_artifact.parse_run_uid = "older_run"
    else:
        doc.fact_context.facts.remove(budget)
    with pytest.raises(RuleDeferred) as error:
        R33TrendCompletionRate().apply(doc)
    assert error.value.partial_issues == []
    assert doc.fact_receipts[0]["status"] == "insufficient_data"


def test_correction_recomputes_current_value_without_reusing_old_amount():
    doc = document("公务用车购置及运行维护费支出决算43.34万元，完成预算的95.26%。")
    doc.fact_context.facts[0].normalized_value = Decimal("45.495")
    assert R33TrendCompletionRate().apply(doc) == []
    assert doc.fact_receipts[0]["status"] == "pass"


def test_confirmed_zero_denominator_is_undefined_even_without_numerator():
    doc = document("公务用车购置及运行维护费完成年初预算的50%。", budget="0")
    doc.fact_context.facts.pop(1)
    findings = R33TrendCompletionRate().apply(doc)
    assert len(findings) == 1 and "没有数学定义" in findings[0].message
    assert findings[0].location["actual_amount"] is None
    assert len(findings[0].location["operand_refs"]) == 1


def test_share_is_decision_part_over_decision_total_not_budget():
    doc = document("公务用车购置及运行维护费支出决算17.36万元，占62.00%。",
                   budget="28", actual="17.36", total="44.22")
    from src.engine.native_three_public_ratios import check_shares
    issues, unresolved = check_shares(R33224_Narrative7_T7(), doc, doc.page_texts[1], 2025)
    assert unresolved == []
    assert len(issues) == 1
    assert "39.26%" in issues[0].message
    assert issues[0].location["denominator_type"] == "决算"


@pytest.mark.parametrize("qualifier, expected", [("基本", False), ("大体", False), ("", True)])
def test_qualitative_flat_does_not_imply_zero_growth(qualifier, expected):
    doc = document(f"公务用车支出增加1.11万元，与上年{qualifier}持平。")
    assert bool(R33245_ThreePublicDirectionContradiction().apply(doc)) is expected


def test_other_explicit_share_denominator_is_not_replaced_by_three_public_total():
    doc = document("公务用车购置及运行维护费支出决算17.36万元，占一般公共预算支出的62.00%。",
                   budget="28", actual="17.36", total="44.22")
    from src.engine.native_three_public_ratios import check_shares
    assert check_shares(R33224_Narrative7_T7(), doc, doc.page_texts[1], 2025) == ([], [])
