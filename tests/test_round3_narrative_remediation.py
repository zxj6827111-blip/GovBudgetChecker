"""Round 3 remediation regression tests: narrative/table extractor bugs and V33-227 multi-clause coverage."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, List

import pytest

from src.engine import native_narrative_checks as nnc
from src.engine import native_table_text as ntt
from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.native_final_disclosure import (
    _DECISION,
    _FUND_ITEM_DECISION,
    _is_near_subject_name_candidate,
    _klass_mismatch_reason,
)
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33102_TotalSheet_vs_Text,
    R33107_BasicExpense_Check,
    R33220_Narrative3_T3,
    R33223_Narrative6_T6,
    R33TrendCompletionRate,
    _nar_repeat_sections,
    _row_value_v2,
    _txt_fund_merged_pages,
    _wp4e_role_key,
    build_document,
)
from src.schemas.material_detail import CoverageObligationItem
from src.services.obligation_finding_coverage import _v33227_name_mismatch_roots


def test_row_value_v2_and_r33220_accept_spaced_heji() -> None:
    table: List[List[Any]] = [
        ["项目", None, "合计", "基本支出", "项目支出"],
        ["合 计", None, "1,909.88", "1,836.21", "73.67"],
        ["201", "一般公共服务支出", "1,909.88", "1,836.21", "73.67"],
    ]
    assert _row_value_v2(table, ("合计",), prefer_cols=("合计",)) == pytest.approx(1909.88)

    page_t3 = "支出决算表\n单位：万元\n"
    page_s3 = (
        "三、支出决算情况说明\n"
        "本年支出合计1,909.88万元，其中：基本支出1,836.21万元，占96.14%；"
        "项目支出73.67万元，占3.86%。"
    )
    page: List[List[List[Any]]] = [table]
    page_tables: List[List[List[List[Any]]]] = [page, []]
    doc = build_document("/tmp/spaced_heji_t3.pdf", [page_t3, page_s3], page_tables, 1000)
    doc.report_kind = "final"
    issues = R33220_Narrative3_T3().apply(doc)
    assert issues == []


def test_quoted_section_reference_does_not_truncate_section1_and_supports_carryover_paren() -> None:
    pages = [
        (
            "一、收入支出决算总体情况说明\n"
            "上海市科学技术委员会2024 年度收入支出总计（含结转结余）1,060,373.72 万元。"
            "与2023 年度相比，收入支出总计增加192,515.39 万元，增长22.18%。\n"
            "五、一般公共预算财政拨款支出决算情况说明\n"
            "主要原因：同上述“一、收入支出决算总体情况说明”中列示的变动原因。"
        )
    ]
    doc = build_document("/tmp/quoted_s1.pdf", pages, [[]], 1000)
    merged, offsets = _txt_fund_merged_pages(doc)
    secs = _nar_repeat_sections(merged, doc, offsets)
    titles = [title for title, _, _ in secs]
    assert titles == ["一、收入支出决算总体情况说明", "五、一般公共预算财政拨款支出决算情况说明"]

    text, ranges = ntt.sections(
        doc,
        lambda t: "收入支出决算总体情况说明" in t
        and not any(w in t for w in ("财政拨款", "政府性基金", "国有资本")),
    )
    assert len(ranges) == 1
    _, begin, end = ranges[0]
    curr = list(nnc._CURRENT.finditer(text.compact[begin:end]))
    assert len(curr) == 1
    assert curr[0]["metric"] == "收入支出总计"
    assert curr[0]["current"] == "1,060,373.72"
    assert curr[0]["unit"] == "万元"

    # R33102._current_total_claim also matches （含结转结余）
    _, _, _, val, _ = R33102_TotalSheet_vs_Text._current_total_claim(
        doc, ["收入支出总计", "支出总计"]
    )
    assert val == Decimal("1060373.72")


def test_section6_heading_typo_does_not_shadow_section5_or_fail_section6() -> None:
    page1 = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "一般公共预算财政拨款支出13,248.32万元。\n"
        "六、一般公共预算财政拨款本支出决算情况说明\n"
        "一般公共预算财政拨款基本支出11,633.87万元。"
        "其中：人员经费10,450.25万元；公用经费1,183.62万元。"
    )
    t6_table = [
        ["经济分类科目名称", "基本支出决算数"],
        ["人员经费合计", "10,450.25"],
        ["公用经费合计", "1,183.62"],
    ]
    doc = build_document("/tmp/sec6_typo.pdf", [page1], [[t6_table]], 1000)
    doc.report_kind = "final"
    # V33-222 section matcher must NOT pick Section 6 typo over Section 5
    _, ranges5 = ntt.sections(doc, ntt.is_general_expenditure_title)
    assert len(ranges5) == 1
    assert ranges5[0][0].startswith("五、")

    doc.page_texts[0] = "一般公共预算财政拨款基本支出决算表\n单位：万元\n" + page1
    assert R33107_BasicExpense_Check().apply(doc) == []
    assert R33223_Narrative6_T6().apply(doc) == []


def test_budget_expenditure_and_final_expenditure_variants_in_v33251_and_completion_rate() -> None:
    # 79de1947: 年初支出预算; edb56827: 年初预算数总计; aa12b9f8: 决算支出
    page_v251 = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为12,518.13万元，支出决算为12,518.13万元。\n"
        "1、农林水支出（类）农业农村（款）农垦运行（项）。"
        "年初支出预算为12,518.13万元，支出决算为12,518.13万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc1 = build_document("/tmp/v251_budget_exp.pdf", [page_v251], [[]], 1000)
    doc1.report_kind = "final"
    assert R33251_FunctionalDetailCompleteness().apply(doc1) == []

    # 决算支出 & 年初预算数总计 regexes in native_final_disclosure + WP4E
    assert _DECISION.search("决算支出91.30万元") is not None
    assert _FUND_ITEM_DECISION.search("决算支出为91.30万元") is not None
    assert _wp4e_role_key("年初支出预算") == "年初预算"

    cover_wp4e = "某某单位\n2025年度单位决算\n"
    page_wp4e = (
        "第三部分 2025年度部门决算情况说明\n"
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "1、社会保障和就业支出（类）行政事业单位养老支出（款）机关事业单位基本养老保险缴费支出（项）。"
        "年初预算数总计2064.21万元；支出决算为2055.14万元，完成年初预算的99.56%。\n"
        "2、卫生健康支出（类）行政事业单位医疗（款）行政单位医疗（项）。"
        "年初预算106.24万元，决算支出91.30万元，完成年初预算的85.94%。"
    )
    doc2 = build_document("/tmp/wp4e_variants.pdf", [cover_wp4e, page_wp4e], [[], []], 1000)
    doc2.report_kind = "final"
    issues = R33TrendCompletionRate().apply(doc2)
    assert issues == []

    # Wrong pct proves both variants actually parsed numerator & denominator and recomputed
    bad_page_wp4e = page_wp4e.replace("85.94%", "70.00%")
    doc2_bad = build_document("/tmp/wp4e_variants_bad.pdf", [cover_wp4e, bad_page_wp4e], [[], []], 1000)
    doc2_bad.report_kind = "final"
    bad_issues = R33TrendCompletionRate().apply(doc2_bad)
    assert len(bad_issues) == 1 and "行政单位医疗" in bad_issues[0].message


def test_is_near_subject_name_candidate_synonym_and_one_char_sub() -> None:
    assert _is_near_subject_name_candidate("其他行政事业单位离退休支出", "其他行政事业单位养老支出")
    assert _is_near_subject_name_candidate("突发公共卫生事件应急处理", "突发公共卫生事件应急处置")
    assert not _is_near_subject_name_candidate("行政运行", "事业运行")


def test_klass_mismatch_reason_identifies_unique_table_class() -> None:
    leaf = SimpleNamespace(
        subject_code="2101101",
        subject_name="行政单位医疗",
        indicator="total_actual",
        extra_dims={"evidence_refs": [{"physical_page": 12}]},
    )
    kuan = SimpleNamespace(
        subject_code="21011",
        subject_name="行政事业单位医疗",
        indicator="total_actual",
        extra_dims={"evidence_refs": [{"physical_page": 12}]},
    )
    klass = SimpleNamespace(
        subject_code="210",
        subject_name="卫生健康支出",
        indicator="total_actual",
        extra_dims={"evidence_refs": [{"physical_page": 12}]},
    )
    paths = {"210": "210", "21011": "21011", "2101101": "2101101"}
    doc = SimpleNamespace(page_texts=["其他卫生健康支出（类）行政事业单位医疗（款）行政单位医疗（项）"])
    class _ItemMatch:
        def __init__(self) -> None:
            self._d = {
                "klass": "其他卫生健康支出",
                "kuan": "行政事业单位医疗",
                "xiang": "行政单位医疗",
            }

        def __getitem__(self, k: str) -> str:
            return self._d[k]

        def start(self) -> int:
            return 0

        def end(self) -> int:
            return 10

    msg = _klass_mismatch_reason(doc, [klass, kuan, leaf], _ItemMatch(), paths)
    assert msg is not None
    assert "编码210写“卫生健康支出”" in msg


def test_v33227_name_mismatch_roots_covers_abbreviated_repeat_clauses() -> None:
    obl = CoverageObligationItem.model_validate(
        {
            "obligation_id": "OBL-TXT-T5",
            "status": "insufficient_data",
            "reason_codes": ["name_mismatch_confirmed"],
            "detail": (
                "科目名称差异候选（款）：正文第25页写“政府办公厅及相关机构事务”（涉及项“行政运行”）；"
                "表格第16页编码20103写“政府办公厅（室）及相关机构事务”。名称不一致，尚未绑定金额；"
                "请核对科目名称后确认，不能仅凭金额相同认定一致。；"
                "科目名称差异候选（款）：正文第25页写“政府办公厅及相关机构事务”（涉及项“一般行政管理事务”）；"
                "科目名称差异候选（款）：正文第25页写“政府办公厅及相关机构事务”（涉及项“事业运行”）"
            ),
            "missing_operands": [
                {"source": "text", "page": 25, "row_key": "政府办公厅及相关机构事务"},
                {"source": "text", "page": 25, "row_key": "政府办公厅及相关机构事务"},
                {"source": "text", "page": 25, "row_key": "政府办公厅及相关机构事务"},
            ],
        }
    )
    result_row = {
        "rule_findings": [
            {
                "id": "V33-227-14",
                "source": "rule",
                "rule_id": "V33-227",
                "severity": "warn",
                "tags": ["V33-227"],
                "message": "说明5款级科目名称与T5不一致（201-03）",
                "location": {
                    "code": "20103",
                    "actual_name": "政府办公厅及相关机构事务",
                    "expected_name": "政府办公厅（室）及相关机构事务",
                    "page": 25,
                },
            }
        ]
    }
    assert _v33227_name_mismatch_roots(obl, result_row, {"V33-227-14"}) == ["V33-227-14"]


def test_three_public_unitless_zero_detail_only_matches_literal_zero() -> None:
    import re

    pat = re.compile(
        rf'(?:支出)?(?:决算|预算)?(?:数)?(?:为|是)?'
        rf'(?:(?P<amount>{ntt._NUMBER})(?P<unit>{ntt._UNIT})|(?P<zero>0+(?:\.0+)?)(?=[。；;，,、]|$))'
    )
    m_zero = pat.match("支出为0。")
    assert m_zero is not None and m_zero.group("zero") == "0"
    m_unit = pat.match("支出为19.76万元。")
    assert m_unit is not None and m_unit.group("amount") == "19.76" and m_unit.group("unit") == "万元"
    assert pat.match("支出为19.76。") is None

