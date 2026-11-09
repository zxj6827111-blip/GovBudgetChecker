"""石泉完成率：恢复原文软折行身份，保留缺预算与决算冲突边界。

公共法律服务与职业年金条目直接裁自冻结真实夹具，保留物理页码及折行；
99.30% 为显式变造负控，其余关键字折行用例为合成合同，不冒充原件。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33NarrativeIndicatorRepeat,
    R33TrendCompletionRate,
    build_document,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/shiquan_narrative_truth_page_data.json"
FIXTURE_SHA = "be8abd4685bc0de3fc2b87b91e0f3d32e79a50e369c9e4dc7aa3cfe5c28272d7"
SOURCE_SHA = "e8315830f800e58038b19bfd0f1e36f5930d9da72a22c3e79f781036bd5289a4"
SECTION = "五、一般公共预算财政拨款支出决算情况说明"
LEGAL_HEAD = "公共安全支出（类）司法（款）公共法律服务（项）”27.09万元，主要用于法律援助。"


@pytest.fixture(scope="module")
def real_pages():
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA
    payload = json.loads(raw)
    assert payload["source_pdf_sha256"] == SOURCE_SHA
    return payload["page_texts"]


def document(*clauses):
    return build_document(
        "contract-soft-wrap-completion.pdf",
        ["某部门2025年度部门决算", SECTION + "\n" + "\n".join(clauses)],
        [],
        0,
    )


def real_slice(real_pages, selected):
    # 空页只用于保留原件页码，不作为结构检查输入。
    pages = [""] * len(real_pages)
    pages[0] = real_pages[0]
    pages[27] = SECTION
    for physical_page, excerpt in selected.items():
        pages[physical_page - 1] = excerpt
    return build_document("shiquan-real-narrative-slice.pdf", pages, [], 0)


def legal_excerpt(real_pages):
    page = real_pages[29]
    start = page.index("2. “公共安全支出（类）司法（款）公共法律服务（项）”")
    stop = page.index("“公共安全支出（类）司法（款）其他公共安全支出（项）”", start)
    excerpt = page[start:stop]
    assert "年\n初预算为 30 万元" in excerpt
    assert "27.09 万元" in excerpt and "90.30%" in excerpt
    return excerpt


def run(doc):
    return list(R33TrendCompletionRate().apply(doc))


def assert_wrong_legal_rate(issues, *, denominator_type="年初预算", page=2):
    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule == "V33-TREND-COMPLETION-RATE"
    assert issue.severity == "error"
    location = issue.location
    assert location["indicator"] == "公共法律服务"
    assert location["denominator_type"] == denominator_type
    assert Decimal(location["denominator_amount"]) == Decimal("30")
    assert Decimal(location["actual_amount"]) == Decimal("27.09")
    assert Decimal(location["declared_completion_rate"]) == Decimal("99.30")
    assert Decimal(location["recomputed_completion_rate"]) == Decimal("90.30")
    assert location["page"] == page


def test_real_legal_service_year_initial_soft_wrap_is_checkable(real_pages):
    """REAL：p30 年/初拆行，30→27.09→90.30% 自洽，必须可核对。"""
    assert run(real_slice(real_pages, {30: legal_excerpt(real_pages)})) == []


def test_real_legal_service_wrong_rate_is_not_silently_skipped(real_pages):
    """变造负控：仅改百分比，防止通过不识别声明/分母获得假通过。"""
    excerpt = legal_excerpt(real_pages).replace("90.30%", "99.30%")
    assert_wrong_legal_rate(run(real_slice(real_pages, {30: excerpt})), page=30)


@pytest.mark.parametrize(
    "role,canonical",
    [("年\n初预\n算", "年初预算"), ("全\n年预\n算", "全年预算"),
     ("调\n整后预\n算", "调整预算")],
)
def test_disclosed_budget_role_survives_internal_soft_wrap(role, canonical):
    """合同：只恢复已披露角色字素，不把泛预算假定成年初预算。"""
    claim_role = "调整后预算" if canonical == "调整预算" else canonical
    doc = document(LEGAL_HEAD, f"{role}为30万元，支出决算为27.09万元，完成{claim_role}的99.30%。")
    assert_wrong_legal_rate(run(doc), denominator_type=canonical)


@pytest.mark.parametrize("claim", ["完\n成年\n初预\n算的99.30%", "年\n初预\n算完\n成\n率为99.30%"])
def test_verb_and_noun_claims_survive_internal_soft_wrap(claim):
    doc = document(LEGAL_HEAD, f"年初预算为30万元，支出决算为27.09万元，{claim}。")
    assert_wrong_legal_rate(run(doc))


def test_missing_budget_is_never_inverted_from_declared_rate():
    """27.09与90.30%可以反推30，但没有原文预算就必须未完成。"""
    doc = document(LEGAL_HEAD, "支出决算为27.09万元，完成年初预算的90.30%。")
    with pytest.raises(RuleDeferred) as caught:
        run(doc)
    assert "公共法律服务" in caught.value.detail and "预算分母" in caught.value.detail
    assert caught.value.partial_issues == []


def test_neighboring_item_budget_cannot_fill_legal_service_denominator():
    doc = document(
        "公共安全支出（类）司法（款）其他司法支出（项）”30万元，主要用于司法。"
        "年初预算为30万元，支出决算为30万元，完成年初预算的100%。",
        LEGAL_HEAD + "支出决算为27.09万元，完成年初预算的90.30%。",
    )
    with pytest.raises(RuleDeferred) as caught:
        run(doc)
    assert "公共法律服务" in caught.value.detail and "预算分母" in caught.value.detail
    assert caught.value.partial_issues == []


def test_nonzero_budget_without_unit_is_not_assumed_to_be_wan():
    doc = document(LEGAL_HEAD, "年\n初预算为30，支出决算为27.09万元，完成年初预算的90.30%。")
    with pytest.raises(RuleDeferred) as caught:
        run(doc)
    assert "未显式金额单位" in caught.value.detail
    assert caught.value.partial_issues == []


def test_real_annuity_conflict_remains_unresolved_with_both_original_values(real_pages):
    """REAL：不能为了复算93.57%而选257.14，也不能选245.53制造完成率错报。"""
    head = "“社会保障和就业支出（类）行政事业单位离退休（款）"
    start = real_pages[31].rindex(head)
    stop = real_pages[32].index(head)
    doc = real_slice(real_pages, {32: real_pages[31][start:], 33: real_pages[32][:stop]})
    with pytest.raises(RuleDeferred) as caught:
        run(doc)
    assert "机关事业单位职业年金缴费支出" in caught.value.detail
    assert "parse_ambiguity" in caught.value.detail
    assert "首句金额" in caught.value.detail
    assert caught.value.partial_issues == []
    conflicts = list(R33NarrativeIndicatorRepeat().apply(doc))
    assert len(conflicts) == 1
    assert conflicts[0].severity == "error"
    location = conflicts[0].location
    assert {location["value_a"], location["value_b"]} == {"257.14", "245.53"}
    assert location["difference"] == "11.61"
    assert location["pages"] == [32, 33]
