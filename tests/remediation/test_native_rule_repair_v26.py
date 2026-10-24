"""新增九份的三条规则误报固定回归，原页裁切与合成差错变体分开。"""

from copy import deepcopy
from pathlib import Path

import pdfplumber
import pytest

from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33106_GeneralBudgetStruct,
    R33234_NarrativePercentConsistency,
    R33235_NarrativeAmountConsistency,
    R33246_DomesticReceptionDisclosure,
    build_document,
)
from src.services.pdf_parser import PDFParser


FIXTURES = Path(__file__).parents[1] / "fixtures" / "native_rule_repair_v26"
SG = "七、财政拨款“三公”经费支出决算情况说明\n"


@pytest.fixture(scope="module")
def original_documents():
    documents = {}
    for doc_id in ("SH2024N9_03", "SH2024N9_02", "SH2024N9_04"):
        path = FIXTURES / f"{doc_id}.pdf"
        artifact = PDFParser(None).build_parse_artifact(str(path))
        with pdfplumber.open(path) as pdf:
            texts = [page.extract_text() or "" for page in pdf.pages]
        tables = [[] for _ in texts]
        for fragment in artifact.physical_fragments:
            if fragment.extraction_method != "page_text_fallback":
                tables[fragment.page_number - 1].append(fragment.rows)
        doc = build_document("固定回归单位2024年度部门决算.pdf", texts, tables, path.stat().st_size)
        doc.parse_artifact = artifact
        doc.fact_context = build_fact_context_from_artifact(
            artifact, org_name="固定回归单位", fiscal_year=2024, report_kind="final"
        )
        documents[doc_id] = doc
    return documents


def narrative(*pages):
    return build_document("固定回归2024年度部门决算.pdf", list(pages), [], 0)


def collected(rule, doc):
    try:
        return rule.apply(doc)
    except RuleDeferred as deferred:
        return deferred.partial_issues


def test_original_chapter_boundary_does_not_bind_three_public_amounts_to_housing(original_documents):
    # 原研究室20→21页：购房项后同页出现六、七主章节；原因在次页原文。
    doc = deepcopy(original_documents["SH2024N9_03"])
    assert "年初预算为 29.98 万元" in doc.page_texts[0]
    assert "团组减少" in doc.page_texts[1]
    issues = collected(R33234_NarrativePercentConsistency(), doc)
    assert not any("未说明原因" in issue.message for issue in issues)
    assert not any("购房补贴" in issue.message and "29.98" in issue.message for issue in issues)


def test_real_housing_completion_without_reason_is_not_excused_by_next_chapter(original_documents):
    doc = deepcopy(original_documents["SH2024N9_03"])
    before = "支出决算为 179.48 万元。决算数小于预算数的主要原因：人员\n减少。"
    assert before in doc.page_texts[0]
    doc.page_texts[0] = doc.page_texts[0].replace(
        before, "支出决算为 179.48 万元，完成预算的88.45%。"
    )
    issues = collected(R33234_NarrativePercentConsistency(), doc)
    hits = [issue for issue in issues if "未说明原因" in issue.message and "购房补贴" in issue.message]
    assert len(hits) == 1
    assert "202.92" in hits[0].message and "179.48" in hits[0].message
    assert "三公" not in hits[0].evidence_text


def test_same_item_completion_reason_can_continue_on_next_page():
    doc = narrative(
        "1、行政运行。年初预算为100万元，支出决算为80万元，完成年初预算的80%。",
        "主要原因：项目结转下年支付。",
    )
    assert not any("未说明原因" in issue.message for issue in collected(R33234_NarrativePercentConsistency(), doc))


def test_original_domestic_reception_times_and_people_are_complete(original_documents):
    doc = deepcopy(original_documents["SH2024N9_02"])
    assert "国内公务接待共 18 次、114 人次" in doc.page_texts[-1]
    assert R33246_DomesticReceptionDisclosure().apply(doc) == []


@pytest.mark.parametrize("disclosure", [
    "国内公务接待共18次、114人次。",
    "国内公务接待18次、114人次。",
    "国内公务接待合计18次、114人次。",
    "国内公务接待共18次、114人次。其中接待外宾0次、0人次。",
])
def test_explicit_reception_subject_can_use_times(disclosure):
    doc = narrative(SG + "3、公务接待费支出2.95万元。" + disclosure)
    assert R33246_DomesticReceptionDisclosure().apply(doc) == []


@pytest.mark.parametrize("disclosure", [
    "国内公务接待共114人次。",
    "国内公务接待仅披露金额。其中接待外宾18次、114人次。",
    "国内公务接待114人次。另开展会议18次。",
])
def test_people_or_foreign_or_other_business_times_cannot_complete_domestic_counts(disclosure):
    doc = narrative(SG + "3、公务接待费支出2.95万元。" + disclosure)
    assert R33246_DomesticReceptionDisclosure().apply(doc)


def test_original_functional_parts_do_not_replace_an_undisclosed_overall_amount(original_documents):
    doc = deepcopy(original_documents["SH2024N9_04"])
    assert "一般公共预算财政拨款支出：" in doc.page_texts[-1]
    assert "672.47" in doc.page_texts[-1] and "740.24" in doc.page_texts[-1]
    with pytest.raises(RuleDeferred, match="说明未明示") as result:
        R33106_GeneralBudgetStruct().apply(doc)
    assert not any("672.47" in issue.message for issue in result.value.partial_issues)
    assert result.value.unresolved_reasons


@pytest.mark.parametrize("amount,expected", [("1,622.36", False), ("1,650.00", True)])
def test_direct_overall_disclosure_still_compares_original_table(original_documents, amount, expected):
    doc = deepcopy(original_documents["SH2024N9_04"])
    doc.page_texts[-1] = doc.page_texts[-1].replace(
        "一般公共预算财政拨款支出：", f"一般公共预算财政拨款支出决算为{amount}万元。"
    )
    issues = R33106_GeneralBudgetStruct().apply(doc)
    hits = [issue for issue in issues if "总体情况说明数字" in issue.message]
    assert bool(hits) is expected
    if expected:
        assert "1650.00" in hits[0].message


def test_explicit_overall_subsection_can_disclose_total_without_repeating_subject():
    table = [["科目名称", "合计", "基本支出"], ["合计", "100.00", "100.00"]]
    doc = build_document(
        "2024年度部门决算.pdf",
        ["一般公共预算财政拨款支出决算表",
         "五、一般公共预算财政拨款支出决算情况说明\n"
         "（一）支出决算总体情况\n年初预算为90万元，支出决算为100万元。\n"
         "（二）支出决算结构情况\n其他卫生健康支出科目60万元。"],
        [[table], []], 0,
    )
    assert R33106_GeneralBudgetStruct().apply(doc) == []


@pytest.mark.parametrize("amount", ["39.76万元", "397600元", "0.003976亿元"])
def test_amount_only_item_is_kept_without_borrowing_next_chapter_decision(amount):
    doc = narrative(
        SG + f"3、公务接待费支出{amount}。国内公务接待支出{amount}。\n"
        "八、政府性基金预算财政拨款收入支出决算情况说明\n"
        "年初预算为0万元，支出决算为420万元。"
    )
    assert R33235_NarrativeAmountConsistency().apply(doc) == []


def test_true_same_item_decision_conflict_is_kept_before_next_chapter():
    doc = narrative(
        SG + "3、公务接待费支出39.76万元。支出决算为40.76万元。\n"
        "八、政府性基金预算财政拨款收入支出决算情况说明\n"
        "支出决算为420万元。"
    )
    issues = R33235_NarrativeAmountConsistency().apply(doc)
    assert len(issues) == 1
    assert "40.76" in issues[0].message and "420" not in issues[0].evidence_text


def test_unitless_nonfinancial_item_remains_insufficient():
    doc = narrative("3、主要工作安排。年度重点事项18件。")
    with pytest.raises(RuleDeferred, match="未提取到"):
        R33235_NarrativeAmountConsistency().apply(doc)
