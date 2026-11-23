"""2026-11-22 解耦核对改造回归测试（模块 1~6 正控 + 负控）。

核心原则：
1. 缺陷照报：科目名称错误、编码位数异常/漏码、单位重复/缺失、表格非法数字格式、
   文字漏字等原件缺陷全部保留为正式问题，绝不吞掉。
2. 金额/后续核对解耦完成：只要存在唯一、可追溯的原件/结构锚点，不再因上游文字或单行
   格式瑕疵阻断后续表文/跨表/加总核对。
3. 负控守住 fail-closed：多候选歧义、双未解项、加总不闭合、显式“未提供”、父级类不闭合、
   同小节金额冲突、真实漏解析科目等场景一律保持 insufficient_data，不猜测放行。
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.check_obligations import build_obligation_ledger
from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.checklist_text_rules import CMM010_AmountFormat, CMM011_AmountUnitWan
from src.engine.common_rules import CMM007_ZeroBaseTrendRecompute
from src.engine.fact_context import FactContext, build_fact_context_from_artifact
from src.engine.native_final_disclosure import (
    _ITEM,
    _hierarchy_fact,
    _native_grid_name_rows,
)
from src.engine.native_hierarchy_checks import _unrepresented_source_codes
from src.engine.native_narrative_checks import _Text
from src.engine.native_table_arithmetic import check_basic_economics
from src.engine.native_table_text import check_three_public_totals
from src.engine.pipeline import build_document, build_issues_payload
from src.engine.rule_outcome import RuleDeferred, RuleOutcomeSignal
from src.engine.rules_v33 import (
    R33103_Income_vs_Text,
    R33108_ThreePublic_vs_Text,
    R33222_Narrative5_T5,
    R33226_Narrative2_T2,
    R33227_Narrative5_T5_NameConsistency,
    R33CrossSanGongEcon,
    R33TrendCompletionRate,
    _txt_fund_match_item,
)
from src.schemas.material_detail import CoverageObligationItem
from src.schemas.parse_artifact import FiscalFact
from src.schemas.source_reconstruction import SourceToken
from src.services.fiscal_subject_names import subject_variant_key
from src.services.obligation_finding_coverage import cascade_root_issue_ids
from src.services.review_lifecycle_service import blocking_coverage_obligations
from tests.remediation.test_native_parent_name_order import document as t5_document
from tests.remediation.test_native_pdf_core import context_for, grid
from tests.remediation.test_r22_a2_identity_chain import ORG, YEAR, _fact, _item_match


# ---------------------------------------------------------------------------
# 模块 1：OBL-TXT-T5 / V33-227 / V33-222 解耦核对
# ---------------------------------------------------------------------------


def test_m1_vertically_merged_last_code_cell_and_descender_certifies_grid():
    """末行编码格下并入“合计”空编码格 + 折行字形底边下探 <=2.5pt 均应认证通过；
    若合并覆盖到另一编码行则必须拒收。"""
    t5_rows = [
        ["项目", "", "", "", "一般公共预算财政拨款支出决算数", "", ""],
        ["类", "款", "项", "科目名称", "合计", "基本支出", "项目支出"],
        ["208", "", "", "社会保障和就业支出", "100.00", "100.00", "0.00"],
        ["20805", "", "", "行政事业单位养老支出", "100.00", "100.00", "0.00"],
        ["2080599", "", "", "其他行政事业单位养老支\n出", "100.00", "100.00", "0.00"],
        ["", "", "", "合计", "100.00", "100.00", "0.00"],
    ]
    t5 = grid(
        t5_rows,
        [0, 30, 60, 90, 210, 250, 290, 330],
        uid="t5-merge",
        page=1,
        code="FIN_05_general_public_expenditure",
        headers=2,
        confidence=0.99,
        unit="万元",
    )
    t5.title = "一般公共预算财政拨款支出决算表"
    # 模拟真实 PDF：数据行类/款/项三列合并为 [0, 90]；末数据行(r=4)编码格向下合并到合计行(r=5)
    total_y1 = max(c.bbox[3] for c in t5.cells if c.row_idx == 5 and c.bbox)
    tokens = []
    for cell in t5.cells:
        if cell.row_idx in (2, 3, 4) and cell.col_idx == 0:
            y1 = total_y1 if cell.row_idx == 4 else cell.bbox[3]
            cell.bbox = (0.0, cell.bbox[1], 90.0, y1)
        if cell.row_idx in (2, 3, 4) and cell.col_idx == 0 and cell.raw_text:
            tokens.append(
                SourceToken(
                    token_uid=f"tok-{cell.row_idx}-0",
                    raw_text=cell.raw_text,
                    physical_page=1,
                    bbox=[4.0, cell.bbox[1] + 2.0, 50.0, cell.bbox[1] + 10.0],
                    source_method="native_text",
                    source_ref={"source_file_hash": "a" * 64, "source_block_id": f"b-{cell.row_idx}"},
                )
            )
        elif cell.row_idx in (2, 3) and cell.col_idx == 3 and cell.raw_text:
            tokens.append(
                SourceToken(
                    token_uid=f"tok-{cell.row_idx}-3",
                    raw_text=cell.raw_text,
                    physical_page=1,
                    bbox=[cell.bbox[0] + 1.0, cell.bbox[1] + 1.0, cell.bbox[2] - 1.0, cell.bbox[3] - 1.0],
                    source_method="native_text",
                    source_ref={"source_file_hash": "a" * 64, "source_block_id": f"b-{cell.row_idx}"},
                )
            )
        elif cell.row_idx == 4 and cell.col_idx == 3:
            # 模拟市委金融办：末字“出”中心在格内，但底边下探 cell.bbox[3] + 1.6pt
            y0, y1 = cell.bbox[1], cell.bbox[3]
            tokens.append(
                SourceToken(
                    token_uid="tok-4-3a",
                    raw_text="其他行政事业单位养老支",
                    physical_page=1,
                    bbox=[cell.bbox[0] + 1.0, y0 + 0.5, cell.bbox[2] - 1.0, y0 + 8.0],
                    source_method="native_text",
                    source_ref={"source_file_hash": "a" * 64, "source_block_id": "b-4"},
                )
            )
            tokens.append(
                SourceToken(
                    token_uid="tok-4-3b",
                    raw_text="出",
                    physical_page=1,
                    bbox=[cell.bbox[0] + 1.0, y1 - 6.0, cell.bbox[0] + 11.0, y1 + 1.6],
                    source_method="native_text",
                    source_ref={"source_file_hash": "a" * 64, "source_block_id": "b-4"},
                )
            )
    # 清除合计行左列占位格
    t5.cells = [c for c in t5.cells if not (c.row_idx == 5 and c.col_idx in (0, 1, 2))]
    base = context_for([t5], [t5])
    base.parse_artifact.source_tokens = tokens
    page_text = "一般公共预算财政拨款支出决算表\n208 社会保障和就业支出 100.00\n20805 行政事业单位养老支出 100.00\n2080599 其他行政事业单位养老支出 100.00\n合计 100.00"
    doc = build_document("/tmp/t5-merge.pdf", [page_text], [[t5.rows]], 100)
    doc.parse_artifact, doc.fact_context = base.parse_artifact, base.fact_context

    rows, _refs = _native_grid_name_rows(doc, [1])
    assert rows.get("2080599") == "其他行政事业单位养老支出"

    # 负控：如果下探行不是“合计”而是另一科目名称，必须拒收
    bad_doc = deepcopy(doc)
    for c in bad_doc.parse_artifact.table_fragments[0].cells:
        if c.row_idx == 5 and c.col_idx == 3:
            c.raw_text = "购房补贴"
    bad_rows, _ = _native_grid_name_rows(bad_doc, [1])
    assert bad_rows == {}


def test_m1_legacy_aliases_and_cross_kuan_variant_bind():
    """历史别名（中专教育/其他医疗卫生与计划生育）唯一绑定并保留逐字报错。"""
    assert subject_variant_key("中专教育") == subject_variant_key("中等职业教育")
    assert subject_variant_key("其他医疗卫生与计划生育支出") == subject_variant_key("其他卫生健康支出")

    entries = [
        ("210", "卫生健康支出"),
        ("21011", "行政事业单位医疗"),
        ("2101101", "行政单位医疗"),
        ("21099", "其他卫生健康支出"),
        ("2109999", "其他卫生健康支出"),
    ]
    doc, facts = t5_document(entries=entries)
    aliases = []
    # 说明把款写成了“行政事业单位医疗”，项写成了旧名“其他医疗卫生与计划生育支出”
    fact, err = _hierarchy_fact(
        doc,
        facts,
        _item_match("卫生健康支出", "行政事业单位医疗", "其他医疗卫生与计划生育支出"),
        aliases=aliases,
    )
    assert err is None and fact is not None and fact.subject_code == "2109999"


def test_m1_fallback_requires_original_candidate_census_and_row_identity():
    rows = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["208", "社会保障和就业支出", "100.00", "100.00", "0.00"],
        ["20805", "行政事业单位养老支出", "100.00", "100.00", "0.00"],
        ["2080501", "行政单位离退休", "100.00", "100.00", "0.00"],
    ]
    table = grid(rows, [0, 30, 200, 250, 290, 330], uid="printed-old-name", page=1,
                 code="FIN_05_general_public_expenditure", confidence=.99, unit="万元")
    for cell in table.cells:
        if cell.col_idx == 0 and cell.row_idx > 0:
            cell.native_text_sources = [{"text": cell.raw_text, "bbox": list(cell.bbox), "physical_page": 1}]
    doc = context_for([table], [table])
    doc.page_texts = ["\n".join(" ".join(row) for row in rows), "一般公共预算财政拨款基本支出决算表"]
    facts = doc.fact_context.facts
    item = _item_match("社会保障和就业支出", "行政事业单位离退休", "行政单位离退休")
    fact, error = _hierarchy_fact(doc, facts, item)
    assert error is None and fact.subject_code == "2080501"

    # 另一条原文同名款未进入解析事实，唯一性不成立。
    doc.page_texts[1] = "续表\n20806 行政事业单位养老支出 50.00\n2080601 行政单位离退休 50.00"
    assert _hierarchy_fact(doc, facts, item)[0] is None


def test_m1_tier2_near_name_tier3_bijection_and_negative_two_unresolved():
    """Tier 2 唯一近名 + Tier 3 同父级 1 对 1 剩余项双射均解耦绑定；
    负控：同款下存在 2 个未解项时严禁猜配，必须保持 insufficient_data。"""
    entries = [
        ("210", "卫生健康支出"),
        ("21004", "公共卫生"),
        ("2100401", "疾病预防控制机构"),
        ("2100407", "其他专业公共卫生机构"),
        ("2100410", "突发公共卫生事件应急处置"),
    ]
    base_doc, facts = t5_document(entries=entries)
    t5_rows = [
        ["功能分类科目编码", "科目名称", "本年支出", "基本支出", "项目支出"],
        *[[code, name, "100.00", "100.00", "0.00"] for code, name in entries],
        ["合计", "", "300.00", "300.00", "0.00"],
    ]
    narrative = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "（一）一般公共预算财政拨款支出决算总体情况\n"
        "一般公共预算财政拨款支出决算为300.00万元。\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "1、卫生健康支出（类）公共卫生（款）疾病预防控制机构（项）。支出决算为100.00万元。\n"
        "2、卫生健康支出（类）公共卫生（款）其他公共卫生服务（项）。支出决算为100.00万元。\n"
        "3、卫生健康支出（类）公共卫生（款）突发公共卫生事件应急处理（项）。支出决算为100.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    pages = ["一般公共预算财政拨款支出决算表\n" + base_doc.page_texts[0], narrative]
    doc = build_document("/tmp/t5-t23.pdf", pages, [[t5_rows], []], 1000)
    doc.parse_artifact = base_doc.parse_artifact
    doc.fact_context = FactContext(deepcopy(facts), "a" * 64, "run", org_name=ORG, fiscal_year=YEAR)
    total_fact = _fact("total", "合计", "300.00")
    total_fact.extra_dims["evidence_refs"][0]["physical_page"] = 1
    doc.fact_context.facts.append(total_fact)
    # 修正汇总行金额，使总额与类/款合计自洽
    for f in doc.fact_context.facts:
        if f.subject_code in ("210", "21004"):
            f.normalized_value = Decimal("300.00")
            f.raw_value = "300.00"
    doc.fact_context.version_token = doc.fact_context.compute_version_token()

    # 1) V33-222 全部自动核对通过
    v222_issues = R33222_Narrative5_T5().apply(doc)
    assert not v222_issues
    assert not any(r.get("status") == "insufficient_data" for r in doc.fact_receipts)

    # 2) V33-227 照常报出 2100407 和 2100410 两处名称差异
    name_doc = build_document("/tmp/t5-t23.pdf", pages, [[t5_rows], []], 1000)
    v227_issues = R33227_Narrative5_T5_NameConsistency().apply(name_doc)
    reported_codes = {i.location.get("code") for i in v227_issues}
    assert {"2100407", "2100410"} <= reported_codes

    # 3) 负控：若同款下出现 2 个互不近似的未解项，Tier 3 必须拒绝绑定
    ambig_narrative = narrative.replace("突发公共卫生事件应急处理", "完全不相关的项目甲").replace(
        "其他公共卫生服务", "完全不相关的项目乙"
    )
    ambig_doc = build_document("/tmp/t5-ambig.pdf", [pages[0], ambig_narrative], [[t5_rows], []], 1000)
    ambig_doc.parse_artifact = doc.parse_artifact
    ambig_doc.fact_context = doc.fact_context
    text_obj = _Text.from_pages(ambig_doc.page_texts)
    all_items = list(_ITEM.finditer(text_obj.compact))
    assert len(all_items) == 3
    fact_b, err_b = _hierarchy_fact(ambig_doc, ambig_doc.fact_context.facts, all_items[1], siblings=all_items)
    assert fact_b is None and err_b


def test_m1_tier4_shifted_hierarchy_binds_and_reports_v33227():
    """一级层级错位（类位写款名、款位写项名）凭原生款→项父子链绑定金额并由 V33-227 报错。"""
    entries = [
        ("208", "社会保障和就业支出"),
        ("20808", "抚恤"),
        ("2080801", "死亡抚恤"),
    ]
    base_doc, facts = t5_document(entries=entries)
    t5_rows = [
        ["功能分类科目编码", "科目名称", "本年支出", "基本支出", "项目支出"],
        *[[code, name, "100.00", "100.00", "0.00"] for code, name in entries],
        ["合计", "", "100.00", "100.00", "0.00"],
    ]
    narrative = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "（一）一般公共预算财政拨款支出决算总体情况\n"
        "一般公共预算财政拨款支出决算为100.00万元。\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "1、抚恤（类）死亡抚恤（款）离退休死亡抚恤（项）。支出决算为100.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    pages = ["一般公共预算财政拨款支出决算表\n" + base_doc.page_texts[0], narrative]
    doc = build_document("/tmp/t5-shift.pdf", pages, [[t5_rows], []], 1000)
    doc.parse_artifact = base_doc.parse_artifact
    doc.fact_context = FactContext(deepcopy(facts), "a" * 64, "run", org_name=ORG, fiscal_year=YEAR)
    total_fact = _fact("total", "合计", "100.00")
    total_fact.extra_dims["evidence_refs"][0]["physical_page"] = 1
    doc.fact_context.facts.append(total_fact)

    assert R33222_Narrative5_T5().apply(doc) == []
    name_doc = build_document("/tmp/t5-shift.pdf", pages, [[t5_rows], []], 1000)
    v227 = R33227_Narrative5_T5_NameConsistency().apply(name_doc)
    assert {"208", "20808", "2080801"} <= {i.location.get("code") for i in v227}


# ---------------------------------------------------------------------------
# 模块 2：OBL-TXT-FINAL-DETAIL / V33-251 解耦核对
# ---------------------------------------------------------------------------


def test_m2_budget_missing_unit_same_clause_wan_closes_and_warns():
    """年初预算漏单位但同句支出决算为万元且计入后严格闭合 → 报 C-14 缺陷并通过预算加总；
    不闭合时仍保持 insufficient_data。"""
    text_close = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为150.11万元，支出决算为368.40万元。其中：\n"
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算为100.00万元，支出决算为100.00万元。\n"
        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算为50.11，支出决算为268.40万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = build_document("/tmp/v251-unit-close.pdf", [text_close], [[]], 1000)
    doc.report_kind = "final"
    issues = R33251_FunctionalDetailCompleteness().apply(doc)
    assert any("年初预算金额缺少单位" in i.message and "50.11" in i.message for i in issues)
    budget_rc = next(r for r in doc.fact_receipts if r.get("sub_check_id") == "V33-251:budget:item_sum")
    assert budget_rc["status"] == "pass"

    # 负控：不闭合（总额改成 160.00）时不能自动放行
    doc_bad = build_document("/tmp/v251-unit-open.pdf", [text_close.replace("150.11", "160.00")], [[]], 1000)
    doc_bad.report_kind = "final"
    with pytest.raises(RuleOutcomeSignal) as sig:
        R33251_FunctionalDetailCompleteness().apply(doc_bad)
    assert sig.value.status == "insufficient_data"


def test_m2_budget_midyear_zero_omission_closes_and_keeps_negative_guards():
    """预算侧年中追加项省略“年初预算为0万元”、其余项精确闭合 → pass + low 提示；
    显式未提供留待核对，独立确认的非零漏列报正式问题。"""
    text_midyear = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为100.00万元，支出决算为140.00万元。其中：\n"
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算为100.00万元，支出决算为100.00万元。\n"
        "2、其他支出（类）其他支出（款）其他支出（项）。主要用于年中追加专项。支出决算为40.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = build_document("/tmp/v251-midyear.pdf", [text_midyear], [[]], 1000)
    doc.report_kind = "final"
    issues = R33251_FunctionalDetailCompleteness().apply(doc)
    assert any(i.severity == "low" and "年初预算为 0" in i.message for i in issues)
    budget_rc = next(r for r in doc.fact_receipts if r.get("sub_check_id") == "V33-251:budget:item_sum")
    assert budget_rc["status"] == "pass"

    # 负控 1：显式写了“年初预算未提供” → 必须保持 insufficient_data
    doc_explicit = build_document(
        "/tmp/v251-midyear-explicit.pdf",
        [text_midyear.replace("主要用于年中追加专项。", "年初预算未提供。")],
        [[]],
        1000,
    )
    doc_explicit.report_kind = "final"
    with pytest.raises(RuleOutcomeSignal) as sig1:
        R33251_FunctionalDetailCompleteness().apply(doc_explicit)
    assert sig1.value.status == "insufficient_data"

    # 决算侧闭合且预算侧短缺，预算欠披露是可确定的材料问题。
    doc_mismatch = build_document(
        "/tmp/v251-midyear-mismatch.pdf",
        [text_midyear.replace("年初预算为100.00万元，支出决算为140.00", "年初预算为120.00万元，支出决算为140.00")],
        [[]],
        1000,
    )
    doc_mismatch.report_kind = "final"
    issues_gap = R33251_FunctionalDetailCompleteness().apply(doc_mismatch)
    assert any(i.location.get("unaccounted_budget_gap_wan") == "20.00" for i in issues_gap)
    assert next(r for r in doc_mismatch.fact_receipts if r["sub_check_id"] == "V33-251:budget:item_sum")["status"] == "fail"


def test_m2_duplicated_unit_recovers_only_with_table_unit_confirmation():
    """“万万元”经同科目原表证实万元后解耦完成加总并保留 warn 缺陷；无原表依据时保持未完成。"""
    t5_rows = [
        ["项目", "", "", "", "一般公共预算财政拨款支出决算数", "", ""],
        ["类", "款", "项", "科目名称", "合计", "基本支出", "项目支出"],
        ["合计", "", "", "", "100.00", "100.00", "0.00"],
        ["205", "", "", "教育支出", "100.00", "100.00", "0.00"],
        ["20506", "", "", "广播电视教育", "100.00", "100.00", "0.00"],
        ["2050699", "", "", "其他广播电视教育支出", "100.00", "100.00", "0.00"],
    ]
    t5 = grid(
        t5_rows,
        [0, 30, 60, 90, 210, 250, 290, 330],
        uid="t5-dup",
        page=1,
        code="FIN_05_general_public_expenditure",
        headers=2,
        confidence=0.99,
        unit="万元",
    )
    t5.title = "一般公共预算财政拨款支出决算表"
    base = context_for([t5], [t5])
    narrative = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为90.00万元，支出决算为100.00万元。其中：\n"
        "1、教育支出（类）广播电视教育（款）其他广播电视教育支出（项）。年初预算为90.00万元，支出决算为100.00万万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = build_document("/tmp/v251-dup.pdf", ["一般公共预算财政拨款支出决算表\n单位：万元", narrative], [[t5.rows], []], 1000)
    doc.report_kind = "final"
    doc.parse_artifact = base.parse_artifact
    doc.fact_context = base.fact_context

    issues = R33251_FunctionalDetailCompleteness().apply(doc)
    assert any("万万元" in i.message and i.severity == "warn" for i in issues)
    final_rc = next(r for r in doc.fact_receipts if r.get("sub_check_id") == "V33-251:final:item_sum")
    assert final_rc["status"] == "pass"

    def run_with(source):
        candidate = build_document(doc.path, [doc.page_texts[0],
            "五、一般公共预算财政拨款支出决算情况说明\n"
            "一般公共预算财政拨款支出决算为100.00万元。\n" + source], [[t5.rows], []], 1000)
        candidate.report_kind = "final"
        candidate.parse_artifact, candidate.fact_context = doc.parse_artifact, doc.fact_context
        return candidate

    verified = run_with(narrative)
    assert any("单位重复" in issue.message for issue in R33222_Narrative5_T5().apply(verified))
    item_receipt = next(r for r in verified.fact_receipts if r["sub_check_id"].startswith("general:text:item:"))
    assert item_receipt["status"] == "pass"
    assert any(ref.get("original_unit") == "万万元" for ref in item_receipt["disclosure_sources"])

    mismatch = run_with(narrative.replace("100.00万万元", "110.00万万元"))
    mismatch_issues = R33222_Narrative5_T5().apply(mismatch)
    assert any("表文金额不一致" in issue.message and "110.00万万元" in issue.message for issue in mismatch_issues)

    ambiguous = run_with(narrative.replace("100.00万万元。", "100.00万万元，支出决算为90.00万元。"))
    with pytest.raises(RuleDeferred):
        R33222_Narrative5_T5().apply(ambiguous)


def test_m2_unit_after_wei_is_preserved_and_budget_percentage_is_not_an_amount():
    text = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为100.00万元，支出决算为100.00万元。其中：\n"
        "1、教育支出（类）普通教育（款）小学教育（项）。"
        "年初预算100.00 为万元，支出决算为100.00万元，完成年初预算数100%。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    for amount, expected in (("100.00", "pass"), ("110.00", "fail")):
        doc = build_document("/tmp/amount-unit-order.pdf", [text.replace("100.00 为万元", f"{amount} 为万元")], [[]], 1000)
        doc.report_kind = "final"
        issues = R33251_FunctionalDetailCompleteness().apply(doc)
        assert any("金额语序有误" in issue.message for issue in issues)
        budget = next(r for r in doc.fact_receipts if r["sub_check_id"] == "V33-251:budget:item_sum")
        assert budget["status"] == expected


# ---------------------------------------------------------------------------
# 模块 3：OBL-CROSS-SAN-GONG-ECON 解耦核对
# ---------------------------------------------------------------------------


def _build_t6_t7_doc(t6_rows, t7_rows):
    p1 = "一般公共预算财政拨款基本支出决算表\n2025年度\n单位：万元\n" + "\n".join(" ".join(r) for r in t6_rows)
    p2 = "财政拨款“三公”经费支出决算表\n2025年度\n单位：万元\n" + "\n".join(" ".join(r) for r in t7_rows)
    return build_document("/tmp/sg-cross.pdf", [p1, p2], [[t6_rows], [t7_rows]], 1000)


def test_m3_spaced_2025_econ_codes_and_reconciled_omitted_row():
    """含空格的 2025 经济编码（'302 11'/'302 16'/'302 29'）与父级 310 闭合的未列行均应 pass；
    负控：若父级 310 未闭合（疑似漏行），未列 31013 必须保持 insufficient_data。"""
    t6_rows = [
        ["经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "科目名称", "决算数"],
        ["301", "工资福利支出", "100.00", "302", "商品和服务支出", "60.00"],
        ["301 01", "基本工资", "100.00", "302 09", "差旅费", "10.00"],
        ["", "", "", "302 11", "因公出国（境）费用", "20.00"],
        ["", "", "", "302 16", "公务接待费", "5.00"],
        ["", "", "", "302 29", "公务用车运行维护费", "25.00"],
        ["", "", "", "310", "资本性支出", "10.00"],
        ["", "", "", "310 99", "其他资本性支出", "10.00"],
    ]
    t7_rows = [
        ["财政拨款“三公”经费", "", "", "", "", "", "", "", "", "", "", ""],
        ["合计", "", "因公出国（境）费", "", "公务用车购置及运行维护费", "", "", "", "", "", "公务接待费", ""],
        ["", "", "", "", "小计", "", "公务用车购置费", "", "公务用车运行维护费", "", "", ""],
        ["预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数"],
        ["100.00", "90.00", "20.00", "20.00", "70.00", "65.00", "40.00", "40.00", "30.00", "25.00", "10.00", "5.00"],
    ]
    doc = _build_t6_t7_doc(t6_rows, t7_rows)
    assert R33CrossSanGongEcon().apply(doc) == []

    # 负控：把 310 类合计改成 50.00（大于已列 31099 的 10.00 → 不能证明未列 31013 为 0）
    t6_unclosed = deepcopy(t6_rows)
    t6_unclosed[6][5] = "50.00"
    doc_unclosed = _build_t6_t7_doc(t6_unclosed, t7_rows)
    with pytest.raises(RuleDeferred) as deferred:
        R33CrossSanGongEcon().apply(doc_unclosed)
    assert "公务用车购置费" in deferred.value.detail


# ---------------------------------------------------------------------------
# 模块 4：OBL-TREND-COMPLETION-RATE & OBL-TREND-ZERO-BASE
# ---------------------------------------------------------------------------


def test_m4_halfwidth_parens_isolate_completion_rate_entries():
    """半角 (类)(款)(项) 条目正确切分，不再合并成“未定位条目”触发 parse_ambiguity。"""
    text = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2024年度\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "1、一般公共服务支出(类)发展与改革事务(款)行政运行(项)。年初预算为100.00万元，支出决算为100.00万元，完成年初预算的100.00%。\n"
        "2、一般公共服务支出(类)发展与改革事务(款)一般行政管理事务(项)。年初预算为200.00万元，支出决算为180.00万元，完成年初预算的90.00%。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = build_document("/tmp/wp4e-half.pdf", [text], [[]], 1000)
    assert R33TrendCompletionRate().apply(doc) == []


def test_m4_cmm007_subsection_disambiguates_cross_subsection_conflict():
    """跨小节（一）与（二）之间的单位笔误不阻塞（一）小节同比复算；同小节冲突仍报 parse_ambiguity。"""
    page = (
        "2024年度部门决算情况说明\n"
        "七、财政拨款“三公”经费支出决算情况说明\n"
        "（一）“三公”经费财政拨款支出决算总体情况说明。\n"
        "因公出国（境）费支出决算为266.18万元。\n"
        "2024年度因公出国（境）费支出决算比2023年度增加145.71万元，增长120.95%。\n"
        "（二）“三公”经费财政拨款支出决算具体情况说明。\n"
        "1、因公出国（境）费支出266.18元。\n"
    )
    doc = build_document("/tmp/cmm007-sub.pdf", [page], [[]], 1000)
    assert CMM007_ZeroBaseTrendRecompute().apply(doc) == []


# ---------------------------------------------------------------------------
# 模块 5：折行粘连伪影与 FIN_06 单行编码异常解耦
# ---------------------------------------------------------------------------


def test_m5_unrepresented_source_codes_ignores_wrap_glued_adjacent_codes():
    """相邻已入表编码格折行粘连产生的“2208050”不再误判漏解析；真实漏解析码仍被检出。"""
    cells = [
        SimpleNamespace(row_idx=0, col_idx=0, raw_text="2080502", page_number=8, bbox=(72.0, 248.0, 114.8, 266.0)),
        SimpleNamespace(row_idx=1, col_idx=0, raw_text="2080505", page_number=8, bbox=(72.0, 266.0, 114.8, 284.0)),
    ]
    frag = SimpleNamespace(
        fragment_uid="f-p8",
        page_number=8,
        table_code="FIN_03_expenditure",
        title="支出决算表",
        extraction_method="lines",
        rows=[["2080502", "事业单位离退休", "26.35"], ["2080505", "基本养老", "128.16"]],
        cells=cells,
        header_row_count=0,
        confidence=0.99,
    )
    glued_block = SimpleNamespace(
        block_id="blk-glued",
        page_number=8,
        text="2208050",
        bbox=(76.0, 266.7, 107.5, 279.7),
    )
    art = SimpleNamespace(
        parse_run_uid="run-1",
        source_file_hash="a" * 64,
        parser_name="test",
        page_count=8,
        text_blocks=[glued_block],
        table_fragments=[frag],
        physical_fragments=[frag],
    )
    assert _unrepresented_source_codes(art, frag, {"2080502", "2080505"}, 2) == []

    # 负控：未入表位置的真实科目编码必须检出
    missing_block = SimpleNamespace(
        block_id="blk-miss",
        page_number=8,
        text="2080599",
        bbox=(76.0, 310.0, 107.5, 322.0),
    )
    art.text_blocks.append(missing_block)
    missing = _unrepresented_source_codes(art, frag, {"2080502", "2080505"}, 2)
    assert any("2080599" in m for m in missing)


@pytest.mark.parametrize("ambiguous_quote", [False, True])
def test_m5_econ_spilled_total_and_code_anomalies_decouple_and_warn(ambiguous_quote):
    """FIN_06 溢字合计（出\\n公用经费合计）、6位笔误码（302041 水费）与漏码（其他资本性支出）
    均解耦释放 low_confidence、完成 V33-243 核对并正式报出编码缺陷。"""
    t6_rows = [
        ["经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "科目名称", "决算数"],
        ["301", "工资福利支出", "100.00", "302", "商品和服务支出", "53.81"],
        ["30101", "基本工资", "100.00", "30201", "办公费", "50.00"],
        ["", "", "", "302041", "水费", "3.81"],
        ["", "", "", "310", "资本性支出", "20.00"],
        ["", "", "", "", "其他资本性支出", "20.00"],
        ["人员经费合计", "", "100.00", "出\n公用经费合计", "", "73.81"],
    ]
    t6 = grid(
        t6_rows,
        [0, 50, 150, 220, 270, 370, 440],
        uid="t6-anom",
        page=1,
        code="FIN_06_basic_expenditure",
        headers=1,
        confidence=0.99,
        unit="万元",
    )
    t6.title = "一般公共预算财政拨款基本支出决算表"
    next(cell for cell in t6.cells if (cell.row_idx, cell.col_idx) == (5, 3)).native_blank_verified = True
    base = context_for([t6], [t6])
    fc = build_fact_context_from_artifact(base.parse_artifact, org_name=ORG, fiscal_year=YEAR, report_kind="final")
    assert not any(f.recognition_status == "low_confidence" for f in fc.facts)

    page_text = "一般公共预算财政拨款基本支出决算表\n单位：万元\n302041 水费 3.81\n其他资本性支出 20.00"
    if ambiguous_quote:
        page_text += "\n302041 水费 3.81\n其他资本性支出 20.00"
    doc = build_document("/tmp/t6-anom.pdf", [page_text], [[t6.rows]], 100)
    doc.parse_artifact = base.parse_artifact
    doc.fact_context = fc

    class _Dummy243:
        code = "V33-243"
        def _issue(self, msg, loc, severity="warn", **kw):
            return SimpleNamespace(rule=self.code, message=msg, location=loc, severity=severity)

    issues = check_basic_economics(_Dummy243(), doc)
    length_issue, = [issue for issue in issues if issue.location.get("predicate") == "econ_code_length_anomaly"]
    missing_issue, = [issue for issue in issues if issue.location.get("predicate") == "econ_missing_subject_code"]
    assert "302041" in length_issue.message and length_issue.severity == "warn"
    assert "其他资本性支出" in missing_issue.message and "31099" in missing_issue.message
    if ambiguous_quote:
        assert "table_refs" not in length_issue.location and "table_refs" not in missing_issue.location
    else:
        assert length_issue.location["table_refs"][0]["span"] == "302041 水费 3.81"
        assert missing_issue.location["table_refs"][0]["span"] == "其他资本性支出 20.00"
        assert missing_issue.location["blank_code_cells"][0]["source_cell_id"] == "t6-anom:r5c3"


# ---------------------------------------------------------------------------
# 模块 6：T2 漏字、T8 近名、表内双小数点 CMM-010 与连带归并
# ---------------------------------------------------------------------------


def test_m6_income_typo_zhengbokuan_verifies_and_reports_low():
    """说明二漏字“政拨款收入”：报 low 漏字缺陷，同时完成表文金额与占比核对。"""
    t2_rows = [
        ["项目", "科目名称", "本年收入合计", "财政拨款收入", "其他收入"],
        ["合计", "", "200.00", "150.00", "50.00"],
    ]
    p1 = "收入决算表\n2024年度\n单位：万元\n合计 200.00 150.00 50.00"
    p2 = (
        "二、收入决算情况说明\n"
        "本年收入合计200.00万元，其中：政拨款收入150.00万元，占75.00%；其他收入50.00万元，占25.00%。\n"
        "三、支出决算情况说明"
    )
    doc = build_document("/tmp/t2-typo.pdf", [p1, p2], [[t2_rows], []], 1000)
    issues = R33103_Income_vs_Text().apply(doc)
    assert any("政拨款收入" in i.message and i.severity == "low" for i in issues)
    assert not any(r.get("status") == "insufficient_data" for r in doc.fact_receipts)


def test_m6_income_trailing_dot_does_not_hide_difference_or_guess_digits():
    rows = [
        ["功能分类科目编码", "科目名称", "本年收入合计", "财政拨款收入"],
        ["合计", "", "200.00", "200.00"],
    ]
    t2 = grid(rows, [0, 30, 170, 210, 260], uid="income-trailing-dot", page=1,
              code="FIN_02_income", confidence=.99, unit="万元")
    base = context_for([t2], [t2])
    for raw, expected in (("200.00.", "pass"), ("205.00.", "fail"), ("20.0.01", "insufficient_data")):
        texts = ["收入决算表\n2025年度\n单位：万元", f"二、收入决算情况说明\n本年收入合计{raw}万元，"
                 "其中：财政拨款收入200.00万元。\n三、支出决算情况说明"]
        doc = build_document("/tmp/t2-dot.pdf", texts, [[rows], []], 1000)
        doc.parse_artifact, doc.fact_context = base.parse_artifact, base.fact_context
        for rule in (R33103_Income_vs_Text(), R33226_Narrative2_T2()):
            if expected == "insufficient_data":
                with pytest.raises(RuleDeferred):
                    rule.apply(doc)
            else:
                issues = rule.apply(doc)
                relation = next(r for r in doc.fact_receipts if r["rule_id"] == rule.code and
                                ("income_total" in r["sub_check_id"] or "total_actual" in r["sub_check_id"]))
                assert relation["status"] == expected
                if rule.code == "V33-103":
                    assert any("多写了一个句点" in issue.message for issue in issues)


def test_m6_fund_detail_near_xiang_binds_and_reports_warn():
    """政府性基金同款下唯一多字项名“和其他交通运输支出”→绑定 2149899 并标出项名差异。"""
    hierarchy = {
        "214": "交通运输支出",
        "21498": "超长期特别国债安排的支出",
        "2149801": "公路水路运输",
        "2149899": "其他交通运输支出",
    }
    items = [
        {"code": "2149801", "name": "公路水路运输", "amount": Decimal("10.00"), "page": 1},
        {"code": "2149899", "name": "其他交通运输支出", "amount": Decimal("98359.00"), "page": 1},
    ]
    found, err = _txt_fund_match_item(
        items, hierarchy, "交通运输支出", "超长期特别国债安排的支出", "和其他交通运输支出"
    )
    assert err == "" and found is not None and found["code"] == "2149899"
    assert found["identity_basis"]["kind"] == "klass_kuan_near_xiang_unique"


def test_m6_table_invalid_number_cmm010_and_cascade_roots():
    """表格格内“2.359.46”由 CMM-010 报出并承接同表同页 cell_unbound 阻断；
    三公经济编码错配 finding 承接 OBL-CROSS-SAN-GONG-ECON。"""
    cells = [
        SimpleNamespace(row_idx=0, col_idx=0, raw_text="220", page_number=9, is_header=False),
        SimpleNamespace(row_idx=0, col_idx=1, raw_text="自然资源海洋气象等支出", page_number=9, is_header=False),
        SimpleNamespace(row_idx=0, col_idx=3, raw_text="2.359.46", page_number=9, is_header=False),
    ]
    frag = SimpleNamespace(
        fragment_uid="a191b44bee5bb1e8",
        page_number=9,
        table_code="FIN_03_expenditure",
        title="支出决算表",
        extraction_method="lines",
        rows=[["220", "自然资源海洋气象等支出", "3,790.92", "2.359.46"]],
        cells=cells,
        header_row_count=0,
        confidence=0.99,
    )
    art = SimpleNamespace(
        parse_run_uid="run-cmm10",
        source_file_hash="b" * 64,
        parser_name="test",
        page_count=9,
        text_blocks=[],
        table_fragments=[frag],
        physical_fragments=[frag],
    )
    pages = [""] * 8 + ["220 自然资源海洋气象等支出 3,790.92 2.359.46"]
    doc = build_document("/tmp/num-fmt.pdf", pages, [[] for _ in pages], 1000)
    doc.parse_artifact = art
    issues = CMM010_AmountFormat().apply(doc)
    assert len(issues) == 1
    assert issues[0].location.get("defect_type") == "invalid_table_number_format"
    assert "2.359.46" in issues[0].message

    finding = {
        "id": "cmm10-1",
        "rule_id": "CMM-010",
        "severity": "warn",
        "message": issues[0].message,
        "location": issues[0].location,
    }
    ob_t3 = SimpleNamespace(
        obligation_id="OBL-INTERNAL-T3-TOTAL",
        status="insufficient_data",
        reason_codes=["cell_unbound"],
        detail="原件金额格式非法：第9页FIN_03_expenditure 有金额格原文无法解析为数值（FIN_03_expenditure:a191b44bee5bb1e8:r0c3）",
        blocked_by=[],
        missing_operands=[{
            "page": 9, "table_code": "FIN_03_expenditure", "source_cell_id": "a191b44bee5bb1e8:r0c3",
        }],
    )
    assert cascade_root_issue_ids(ob_t3, {"rule_findings": [finding]}) == ["cmm10-1"]

    # 三公经济编码错配连带归并（及负控：未覆盖全部冲突科目时不得归并）
    sg_finding = {
        "id": "sg-1",
        "rule_id": "V33-CROSS-SAN-GONG-ECON",
        "severity": "warn",
        "message": "基本支出经济分类表科目编码与名称不一致：“31007 公务用车购置”",
        "location": {"defect_type": "economic_code_name_mismatch", "subject": "公务用车购置费", "page": 17},
    }
    ob_sg = SimpleNamespace(
        obligation_id="OBL-CROSS-SAN-GONG-ECON",
        status="insufficient_data",
        reason_codes=["row_level_unresolved"],
        detail="基本支出表「公务用车购置费」身份不一致：31007 公务用车购置",
        blocked_by=[],
        missing_operands=[],
    )
    assert cascade_root_issue_ids(ob_sg, {"rule_findings": [sg_finding]}) == ["sg-1"]

    ob_sg_mixed = SimpleNamespace(
        obligation_id="OBL-CROSS-SAN-GONG-ECON",
        status="insufficient_data",
        reason_codes=["row_level_unresolved"],
        detail="基本支出表「公务用车购置费」身份不一致：31007 公务用车购置；基本支出表「公务接待费」身份不一致：30299 公务接待费",
        blocked_by=[],
        missing_operands=[],
    )
    assert cascade_root_issue_ids(ob_sg_mixed, {"rule_findings": [sg_finding]}) == []


# ---------------------------------------------------------------------------
# 独立复验负控与补缺回归（2026-11-22 审计 4 项闭环）
# ---------------------------------------------------------------------------


def test_recheck_p1_invalid_number_cascade_rejects_cross_cell_table_and_independent_gap():
    """P1 #1：非法金额格连带归并严格按 cell_id/table_code/row_code/independent_pages 校验，
    绝不允许跨格、跨表或吞掉独立的 column_identity_unconfirmed/跨页缺口。"""
    root = {
        "id": "known-invalid",
        "rule_id": "CMM-010",
        "severity": "warn",
        "message": "原件金额格式非法",
        "location": {
            "defect_type": "invalid_table_number_format",
            "table_code": "FIN_03_expenditure",
            "page": 9,
            "source_cell_id": "abcdef0123456789:r2c3",
            "row_code": "220",
            "column": "basic_actual",
        },
    }
    probes = [
        (
            "支出决算表的项目支出第9页未绑定（123456789abcdef0:r7c4）",
            ["cell_unbound"],
            [{"source": "table", "page": 9, "table_code": "FIN_03_expenditure", "cell_id": "123456789abcdef0:r7c4"}],
        ),
        (
            "第9页支出决算表另一统计口径未核实",
            ["operand_missing"],
            [{"source": "table", "page": 9, "table_code": "FIN_05_general_public_expenditure"}],
        ),
        (
            "原件金额格式非法：第9页支出决算表 abcdef0123456789:r2c3；第10页列身份未确认",
            ["cell_unbound", "column_identity_unconfirmed"],
            [
                {"source": "table", "page": 9, "table_code": "FIN_03_expenditure"},
                {"source": "table", "page": 10, "table_code": "FIN_05_general_public_expenditure"},
            ],
        ),
    ]
    for detail, codes, operands in probes:
        item = CoverageObligationItem(
            obligation_id="OBL-INTERNAL-T3-TOTAL",
            group_id="grp-internal",
            group_title="表内勾稽",
            title="支出决算表校验",
            status="insufficient_data",
            blocks_gate=True,
            reason="insufficient_data",
            reason_label="数据不足",
            detail=detail,
            reason_codes=codes,
            blocked_by=[],
            missing_operands=operands,
        )
        result = {"rule_findings": [root]}
        assert cascade_root_issue_ids(item, result) == []
        assert len(blocking_coverage_obligations([item], result, visible_issue_ids=["known-invalid"])) == 1

    # 缺少物理格 ID 时，表、页、行、列均须可核对。
    detail_ok = CoverageObligationItem(
        obligation_id="OBL-INTERNAL-DETAIL-ROW",
        group_id="grp-internal",
        group_title="表内勾稽",
        title="明细行横向闭合",
        status="insufficient_data",
        blocks_gate=True,
        reason="insufficient_data",
        reason_label="数据不足",
        detail="支出决算表科目220/basic_actual原始单元格或当前事实缺失；结构：缺数位置 第9页 table:FIN_03_expenditure",
        reason_codes=["operand_missing"],
        blocked_by=[],
        missing_operands=[{
            "source": "table", "page": 9, "table_code": "FIN_03_expenditure",
            "row_key": "220", "column": "basic_actual",
        }],
    )
    assert cascade_root_issue_ids(detail_ok, {"rule_findings": [root]}) == ["known-invalid"]

    detail_extra_row = deepcopy(detail_ok)
    detail_extra_row.missing_operands.append(
        {"source": "table", "page": 9, "table_code": "FIN_03_expenditure", "row_key": "22099"}
    )
    assert cascade_root_issue_ids(detail_extra_row, {"rule_findings": [root]}) == []

    root_no_row_code = deepcopy(root)
    root_no_row_code["location"].pop("row_code", None)
    assert cascade_root_issue_ids(detail_ok, {"rule_findings": [root_no_row_code]}) == []
    no_column = deepcopy(detail_ok)
    no_column.missing_operands[0].pop("column")
    assert cascade_root_issue_ids(no_column, {"rule_findings": [root]}) == []
    independent_project = deepcopy(detail_ok)
    independent_project.detail += "；原件金额格 abcdef0123456789:r2c3"
    independent_project.missing_operands.append({
        "source": "table", "page": 9, "table_code": "FIN_03_expenditure", "column": "project_actual",
    })
    review = {"review_items": [{
        "id": "reading:number-format:t3", "type": "invalid_number_format",
        "table_code": "FIN_03_expenditure", "page_number": 9,
        "cells": [{"source_cell_id": "abcdef0123456789:r2c3", "page": 9}],
    }]}
    assert cascade_root_issue_ids(independent_project, {"rule_findings": [root]}, structured_ingest=review) == []
    exact_cell = deepcopy(detail_ok)
    exact_cell.missing_operands[0]["source_cell_id"] = "abcdef0123456789:r2c3"
    assert cascade_root_issue_ids(exact_cell, {}, structured_ingest=review) == ["reading:number-format:t3"]


def test_recheck_p1_cross_san_gong_reverse_mismatch_and_2025_codes():
    """P1 #2：T6 出现 31013 其他资本性支出 80.00 时严禁判未列=0通过；
    改名公务用车购置或仅前4字以内窄列折行溢出且精确码 31013 时照常抓超额 40 万元；
    2025 表缺差旅费行时仍能凭专属编码对识别新版编码。"""
    t7 = [
        ["财政拨款“三公”经费", "", "", "", "", "", "", "", "", "", "", ""],
        ["合计", "", "因公出国（境）费", "", "公务用车购置及运行维护费", "", "", "", "", "", "公务接待费", ""],
        ["", "", "", "", "小计", "", "公务用车购置费", "", "公务用车运行维护费", "", "", ""],
        ["预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数"],
        ["100.00", "90.00", "20.00", "20.00", "70.00", "65.00", "40.00", "40.00", "30.00", "25.00", "10.00", "5.00"],
    ]
    base_t6 = [
        ["经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "科目名称", "决算数"],
        ["301", "工资福利支出", "100.00", "302", "商品和服务支出", "60.00"],
        ["301 01", "基本工资", "100.00", "302 09", "差旅费", "10.00"],
        ["", "", "", "302 11", "因公出国（境）费用", "20.00"],
        ["", "", "", "302 16", "公务接待费", "5.00"],
        ["", "", "", "302 29", "公务用车运行维护费", "25.00"],
        ["", "", "", "310", "资本性支出", "80.00"],
        ["", "", "", "310 13", "其他资本性支出", "80.00"],
    ]
    doc_rev = _build_t6_t7_doc(base_t6, t7)
    with pytest.raises(RuleDeferred) as deferred:
        R33CrossSanGongEcon().apply(doc_rev)
    assert any(
        i.location.get("defect_type") == "economic_code_name_mismatch"
        and "31013" in i.message
        and "其他资本性支出" in i.message
        for i in (deferred.value.partial_issues or [])
    )
    vp_rc = next(
        r for r in doc_rev.fact_receipts
        if str(r.get("sub_check_id") or "").endswith(":vehicle_purchase")
    )
    assert vp_rc["status"] == "insufficient_data"

    # 同行改为标准名「公务用车购置」或窄列 3 字折行溢出「置更新\n公务用车购置」→ 抓出超额 40 万元
    for valid_name in ("公务用车购置", "置更新\n公务用车购置"):
        t6_ok = deepcopy(base_t6)
        t6_ok[-1][4] = valid_name
        doc_ok = _build_t6_t7_doc(t6_ok, t7)
        issues = R33CrossSanGongEcon().apply(doc_ok)
        assert len(issues) == 1 and "80.00 万元" in issues[0].message and "40.00 万元" in issues[0].message

    t6_wrong_code = deepcopy(base_t6)
    t6_wrong_code[-1][3:5] = ["310 07", "公务用车购置"]
    doc_wrong_code = _build_t6_t7_doc(t6_wrong_code, t7)
    issues = R33CrossSanGongEcon().apply(doc_wrong_code)
    assert any(issue.location.get("defect_type") == "economic_code_name_mismatch" for issue in issues)
    assert any("80.00 万元" in issue.message and "40.00 万元" in issue.message for issue in issues)

    t6_conflicting = [*t6_wrong_code, ["", "", "", "310 13", "其他交通工具购置", "30.00"]]
    with pytest.raises(RuleDeferred):
        R33CrossSanGongEcon().apply(_build_t6_t7_doc(t6_conflicting, t7))

    # 负控：前缀 > 4 字（两个科目拼在同一格）不能放行，必须继续报冲突
    t6_bad_wrap = deepcopy(base_t6)
    t6_bad_wrap[-1][4] = "其他资本性支出\n公务用车购置"
    doc_bad_wrap = _build_t6_t7_doc(t6_bad_wrap, t7)
    with pytest.raises(RuleDeferred):
        R33CrossSanGongEcon().apply(doc_bad_wrap)

    # 无差旅费行的 2025 表（有 302 12 维修（护）费 + 302 29 公务用车运行维护费）应准确识别为 2025 编码且零误报
    t6_no_travel = [row for row in deepcopy(base_t6) if row[4] != "差旅费"]
    t6_no_travel.insert(2, ["", "", "", "302 12", "维修（护）费", "10.00"])
    t6_no_travel[7] = ["", "", "", "310 13", "公务用车购置", "40.00"]
    t6_no_travel[6][5] = "40.00"
    assert R33CrossSanGongEcon().apply(_build_t6_t7_doc(t6_no_travel, t7)) == []


def test_recheck_p2_generic_three_public_budget_discrepancy_and_cascade():
    """P2 #1：T7 表头仅写「预算数」而三公说明写「年初预算」且相差 >0.01（无预算调整说明，
    允许「外汇汇率调整」等非预算调整字样）→ 产出正式 warn 并承接纯阶段歧义导致的完成率阻断。"""
    p_table = "财政拨款“三公”经费支出决算表\n单位：万元\n预算数 1,384.72 决算数 1,158.99"
    p_text = (
        "七、财政拨款“三公”经费支出决算情况说明\n"
        "（一）“三公”经费财政拨款支出决算总体情况说明。\n"
        "“三公”经费财政拨款支出年初预算为 1,435.72 万元，支出决算为 1,158.99 万元，完成预算的 80.72%。\n"
        "主要原因是受外汇汇率调整因素影响。"
    )
    doc = build_document("/tmp/jtw-t7.pdf", [p_table, p_text], [[], []], 1000)
    facts = [
        FiscalFact(
            fact_uid="f-total-budget",
            indicator="total_actual",
            raw_value="1,384.72",
            unit="万元",
            normalized_value=Decimal("1384.72"),
            org_name=ORG,
            fiscal_year=2025,
            period="本年",
            budget_stage="年初预算",
            subject_code="total",
            subject_name="合计",
            table_code="FIN_07_three_public",
            instance_uid="t7-inst",
            extra_dims={
                "source_header": "合计/预算数",
                "evidence_refs": [{"physical_page": 1, "quote": "1,384.72"}],
            },
        ),
        FiscalFact(
            fact_uid="f-total-final",
            indicator="total_actual",
            raw_value="1,158.99",
            unit="万元",
            normalized_value=Decimal("1158.99"),
            org_name=ORG,
            fiscal_year=2025,
            period="本年",
            budget_stage="决算",
            subject_code="total",
            subject_name="合计",
            table_code="FIN_07_three_public",
            instance_uid="t7-inst",
            extra_dims={
                "source_header": "合计/决算数",
                "evidence_refs": [{"physical_page": 1, "quote": "1,158.99"}],
            },
        ),
    ]
    doc.fact_context = FactContext(facts, "a" * 64, "run", org_name=ORG, fiscal_year=2025)
    doc.parse_artifact = SimpleNamespace(source_file_hash="a" * 64, parse_run_uid="run")

    issues = check_three_public_totals(R33108_ThreePublic_vs_Text(), doc)
    assert len(issues) == 1
    assert issues[0].severity == "warn"
    assert issues[0].location.get("defect_type") == "three_public_budget_phase_discrepancy"
    assert issues[0].location.get("difference_wan") == "51.00"
    assert issues[0].location.get("pages") == [1, 2]

    finding = {
        "id": "t7-phase-1",
        "rule_id": "V33-108",
        "severity": "warn",
        "message": issues[0].message,
        "location": issues[0].location,
    }
    ob_rate = CoverageObligationItem(
        obligation_id="OBL-TREND-COMPLETION-RATE",
        group_id="grp-rate",
        group_title="完成率复算",
        title="预算完成率",
        status="insufficient_data",
        blocks_gate=True,
        reason="insufficient_data",
        reason_label="数据不足",
        detail="三公经费合计声明「完成预算的80.72%」：表头仅披露“预算数”，未取得与原文一致的显式预算阶段证据；结构：缺数位置 第2页 text:预算完成率说明",
        reason_codes=["operand_missing"],
        blocked_by=[{"dimension": "budget_stage", "ref": "FIN_07_three_public:t7-inst:年初预算", "reason_code": "scope_unconfirmed"}],
        missing_operands=[{"source": "text", "page": 2, "role": "年初预算分母"}],
    )
    assert cascade_root_issue_ids(ob_rate, {"rule_findings": [finding]}) == ["t7-phase-1"]

    for other_ref in ("FIN_07:other-instance:年初预算", "FIN_07:t7-inst:全年预算"):
        ob_other_basis = deepcopy(ob_rate)
        ob_other_basis.blocked_by[0]["ref"] = other_ref
        assert cascade_root_issue_ids(ob_other_basis, {"rule_findings": [finding]}) == []

    # 负控 1：混入其他页非阶段问题 → 绝不误归并
    ob_mixed = deepcopy(ob_rate)
    ob_mixed.detail = ob_rate.detail.split("；结构：", 1)[0] + "；第8页行政运行未找到预算分母"
    assert cascade_root_issue_ids(ob_mixed, {"rule_findings": [finding]}) == []

    # 负控 2：正文含显式“年中预算调整”说明 → 仍保持 insufficient_data，不误报差异
    doc_explained = build_document(
        "/tmp/jtw-t7-adj.pdf",
        [p_table, p_text + "\n说明：年中预算调整调减了相关费用。"],
        [[], []],
        1000,
    )
    doc_explained.fact_context = doc.fact_context
    doc_explained.parse_artifact = doc.parse_artifact
    with pytest.raises(RuleDeferred) as sig:
        check_three_public_totals(R33108_ThreePublic_vs_Text(), doc_explained)
    assert "预算数" in sig.value.detail


def test_recheck_p2_completion_rate_unit_typo_and_v33251_omitted_budget_gap():
    """P2 #2：发改委年初预算 7677.81 元 / 8071.11 万元 / 105.12% 不再衍生 1,051,225.54% 假错（真算术错仍报）；
    V33-251 预算缺项且已披露之和与总额存在差额时发出 C-11 正式 warn 并承接未完成义务。"""
    rate_text = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2024年度\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "1、一般公共服务支出(类)发展与改革事务(款)行政运行(项)。"
        "年初预算为7677.81元，支出决算为8071.11万元，完成年初预算的105.12%。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc_unit = build_document("/tmp/fgw-rate.pdf", [rate_text], [[]], 1000)
    assert R33TrendCompletionRate().apply(doc_unit) == []

    doc_bad_math = build_document("/tmp/fgw-rate-bad.pdf", [rate_text.replace("105.12%", "120.00%")], [[]], 1000)
    issues_bad = R33TrendCompletionRate().apply(doc_bad_math)
    assert len(issues_bad) == 1 and "完成率复算不一致" in issues_bad[0].message

    v251_text = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为120.00万元，支出决算为140.00万元。其中：\n"
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算为100.00万元，支出决算为100.00万元。\n"
        "2、其他支出（类）其他支出（款）其他支出（项）。支出决算为40.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc_gap = build_document("/tmp/v251-gap.pdf", [v251_text], [[]], 1000)
    doc_gap.report_kind = "final"
    issues_gap = R33251_FunctionalDetailCompleteness().apply(doc_gap)
    warn_issues = [
        i for i in issues_gap
        if i.severity == "warn" and i.location.get("checklist_item") == "C-11"
    ]
    assert len(warn_issues) == 1
    assert warn_issues[0].location.get("unaccounted_budget_gap_wan") == "20.00"
    assert "相差20.00万元" in warn_issues[0].message
    assert next(r for r in doc_gap.fact_receipts if r["sub_check_id"] == "V33-251:budget:item_sum")["status"] == "fail"

    finding = {
        "id": "v251-gap-1",
        "rule_id": "V33-251",
        "severity": "warn",
        "message": warn_issues[0].message,
        "location": warn_issues[0].location,
    }
    ob_v251 = CoverageObligationItem(
        obligation_id="OBL-TXT-FINAL-DETAIL",
        group_id="grp-txt",
        group_title="说明明细",
        title="支出决算具体情况加总",
        status="insufficient_data",
        blocks_gate=True,
        reason="insufficient_data",
        reason_label="数据不足",
        detail="年初预算有1处金额未纳入合计：1项未披露年初预算（定位：第1页「2、其他支出（类）其他支出（款）」）",
        reason_codes=["scope_unconfirmed", "item_amount_not_included", "sum_scope_not_comparable"],
        blocked_by=[{"dimension": "fund_scope", "ref": "V33-251:budget:年初预算:分项范围", "reason_code": "scope_unconfirmed"}],
        missing_operands=[],
    )
    assert cascade_root_issue_ids(ob_v251, {"rule_findings": [finding]}) == ["v251-gap-1"]


# ---------------------------------------------------------------------------
# 二轮独立复验负控与闭环回归（2026-11-22 3 P1 + 1 P2）
# ---------------------------------------------------------------------------


def test_recheck2_p1_invalid_table_number_column_scoped_and_cmm010_column():
    """二轮 P1 #1：同 row_code 不同列（basic_actual vs project_actual）绝不误归并；
    CMM010_AmountFormat 从同列 fact_context 事实或中文表头自动回填 column 身份。"""
    invalid_root = {
        "id": "invalid-basic",
        "rule_id": "CMM-010",
        "severity": "warn",
        "message": "基本支出原件金额格式非法",
        "location": {
            "defect_type": "invalid_table_number_format",
            "table_code": "FIN_03_expenditure",
            "page": 9,
            "source_cell_id": "abcdef0123456789:r2c3",
            "row_code": "220",
            "column": "basic_actual",
        },
    }
    cases = [
        ("basic_actual", False, ["invalid-basic"], 0),
        ("project_actual", False, [], 1),
        ("project_actual", True, [], 1),
    ]
    for measure, with_cell, expected_roots, expected_blocks in cases:
        op = {"source": "table", "page": 9, "table_code": "FIN_03_expenditure", "row_key": "220", "column": measure}
        if with_cell:
            op["source_cell_id"] = "abcdef0123456789:r2c4"
        item = CoverageObligationItem(
            obligation_id="OBL-INTERNAL-DETAIL-ROW",
            group_id="grp-internal",
            group_title="表内勾稽",
            title="明细行横向闭合",
            status="insufficient_data",
            blocks_gate=True,
            reason="insufficient_data",
            reason_label="数据不足",
            detail=f"支出决算表科目220/{measure}原始单元格或当前事实缺失；结构：缺数位置 第9页 table:FIN_03_expenditure",
            reason_codes=["operand_missing"],
            blocked_by=[],
            missing_operands=[op],
        )
        res = {"rule_findings": [invalid_root]}
        assert cascade_root_issue_ids(item, res, visible_issue_ids=["invalid-basic"]) == expected_roots
        assert len(blocking_coverage_obligations([item], res, visible_issue_ids=["invalid-basic"])) == expected_blocks

    from api.main import _evaluate_quality_gate

    root_cell = invalid_root["location"]["source_cell_id"]
    reading = {"reading_import": {"reading_quality": {"unbound_money_cells": [
        {"source_cell_id": root_cell, "page": 9, "reason": "fact_unbound"},
    ]}}}
    for cell, expected_codes in (
        (root_cell, {"table_reading_incomplete"}),
        ("abcdef0123456789:r2c4", {"table_reading_incomplete", "rules_insufficient_data"}),
    ):
        finding = {"rule_id": "V33-120", "status": "insufficient_data", "diagnostics": [{
            "reason_codes": ["cell_unbound"],
            "blocked_by": [{"dimension": "cell", "ref": f"第9页FIN_03_expenditure:{cell}"}],
            "missing_operands": [{"source": "table", "page": 9, "row_key": cell, "column": "金额"}],
        }]}
        gate = _evaluate_quality_gate(
            {"page_coverage": 1.0, "scanned_page_count": 0}, "final", 2025, False, False, 1,
            structured_ingest=reading, result_row={"rule_findings": [invalid_root]},
            rule_execution_summary={"executed": 1, "insufficient_data": 1, "unresolved_rules": [finding]},
        )
        assert gate["status"] == "review_required"
        assert {reason["code"] for reason in gate["review_reasons"]} == expected_codes

    # 验证 CMM010_AmountFormat 优先从同列 fact_context 事实解析 column
    cells = [
        SimpleNamespace(row_idx=0, col_idx=0, raw_text="220", page_number=9, is_header=False),
        SimpleNamespace(row_idx=0, col_idx=1, raw_text="自然资源海洋气象等支出", page_number=9, is_header=False),
        SimpleNamespace(row_idx=0, col_idx=3, raw_text="2.359.46", page_number=9, is_header=False),
    ]
    frag = SimpleNamespace(
        fragment_uid="a191b44bee5bb1e8",
        page_number=9,
        table_code="FIN_03_expenditure",
        title="支出决算表",
        extraction_method="lines",
        rows=[["220", "自然资源海洋气象等支出", "3,790.92", "2.359.46"]],
        cells=cells,
        header_row_count=0,
        confidence=0.99,
    )
    art = SimpleNamespace(
        parse_run_uid="run-cmm10-col",
        source_file_hash="b" * 64,
        parser_name="test",
        page_count=9,
        text_blocks=[],
        table_fragments=[frag],
        physical_fragments=[frag],
    )
    sibling_fact = FiscalFact(
        fact_uid="f-sib-c3",
        indicator="basic_actual",
        raw_value="100.00",
        unit="万元",
        normalized_value=Decimal("100.00"),
        org_name=ORG,
        fiscal_year=2025,
        period="本年",
        budget_stage="决算",
        subject_code="22001",
        subject_name="自然资源事务",
        table_code="FIN_03_expenditure",
        instance_uid="inst-t3",
        extra_dims={"evidence_refs": [{"physical_page": 9, "source_cell_id": "a191b44bee5bb1e8:r1c3"}]},
    )
    pages = [""] * 8 + ["220 自然资源海洋气象等支出 3,790.92 2.359.46"]
    doc = build_document("/tmp/num-col-fc.pdf", pages, [[] for _ in pages], 1000)
    doc.parse_artifact = art
    doc.fact_context = FactContext([sibling_fact], "b" * 64, "run-cmm10-col", org_name=ORG, fiscal_year=2025)
    issues_fc = CMM010_AmountFormat().apply(doc)
    assert len(issues_fc) == 1 and issues_fc[0].location.get("column") == "basic_actual"

    # 无 fact_context 时回退读取列头中文标签（基本支出 -> basic_actual）
    hdr_cells = [
        SimpleNamespace(row_idx=0, col_idx=0, raw_text="科目编码", page_number=9, is_header=True),
        SimpleNamespace(row_idx=0, col_idx=1, raw_text="科目名称", page_number=9, is_header=True),
        SimpleNamespace(row_idx=0, col_idx=2, raw_text="合计", page_number=9, is_header=True),
        SimpleNamespace(row_idx=0, col_idx=3, raw_text="基本支出", page_number=9, is_header=True),
        SimpleNamespace(row_idx=1, col_idx=0, raw_text="220", page_number=9, is_header=False),
        SimpleNamespace(row_idx=1, col_idx=1, raw_text="自然资源海洋气象等支出", page_number=9, is_header=False),
        SimpleNamespace(row_idx=1, col_idx=3, raw_text="2.359.46", page_number=9, is_header=False),
    ]
    frag_hdr = SimpleNamespace(
        fragment_uid="h191b44bee5bb1e8",
        page_number=9,
        table_code="FIN_03_expenditure",
        title="支出决算表",
        extraction_method="lines",
        rows=[["科目编码", "科目名称", "合计", "基本支出"], ["220", "自然资源海洋气象等支出", "3,790.92", "2.359.46"]],
        cells=hdr_cells,
        header_row_count=1,
        confidence=0.99,
    )
    doc_hdr = build_document("/tmp/num-col-hdr.pdf", pages, [[] for _ in pages], 1000)
    doc_hdr.parse_artifact = SimpleNamespace(
        parse_run_uid="run-hdr", source_file_hash="b" * 64, parser_name="test", page_count=9,
        text_blocks=[], table_fragments=[frag_hdr], physical_fragments=[frag_hdr],
    )
    issues_hdr = CMM010_AmountFormat().apply(doc_hdr)
    assert len(issues_hdr) == 1 and issues_hdr[0].location.get("column") == "basic_actual"


def test_recheck2_p1_three_public_phase_rejects_independent_operands():
    """二轮 P1 #2a：three_public_budget_phase_discrepancy 仅允许纯三公预算阶段歧义归并；
    同页额外决算分子缺失或跨页非三公科目缺失必须保持阻塞。"""
    phase_root = {
        "id": "phase-total",
        "rule_id": "V33-108",
        "severity": "warn",
        "message": "三公经费合计年初预算表文差异",
        "location": {
            "defect_type": "three_public_budget_phase_discrepancy",
            "pages": [26, 39],
            "table_code": "FIN_07_three_public",
            "subject": "三公经费合计",
            "stage": "年初预算",
        },
    }
    base_op = {"source": "text", "page": 39, "row_key": "三公经费合计", "role": "年初预算分母", "column": "年初预算"}
    combined_real_op = {
        "source": "text", "page": 39, "row_key": "“三公”经费财政拨款支出",
        "role": "预算分母/本年决算分子", "column": "完成率声明",
    }
    indep_final_op = {
        "source": "text", "page": 39, "row_key": "公务接待费",
        "role": "决算分子", "column": "决算金额", "reason_codes": ["operand_missing"],
    }
    indep_other_page_op = {
        "source": "text", "page": 45, "row_key": "行政运行",
        "role": "年初预算分母", "column": "完成率", "reason_codes": ["operand_missing"],
    }
    indep_other_table = {
        "source": "table", "page": 26, "table_code": "FIN_06_basic_expenditure",
        "row_key": "公务接待费", "role": "年初预算分母", "column": "年初预算",
    }
    indep_actual = {
        "source": "table", "page": 26, "table_code": "FIN_07_three_public",
        "row_key": "公务接待费", "role": "amount", "column": "total_actual", "stage": "决算",
    }

    for ops, expected_roots, expected_blocks in (
        ([base_op], ["phase-total"], 0),
        ([combined_real_op], [], 1),
        ([base_op, indep_final_op], [], 1),
        ([base_op, indep_other_page_op], [], 1),
        ([base_op, indep_other_table], [], 1),
        ([base_op, indep_actual], [], 1),
    ):
        item = CoverageObligationItem(
            obligation_id="OBL-TREND-COMPLETION-RATE",
            group_id="grp-rate",
            group_title="完成率复算",
            title="预算完成率",
            status="insufficient_data",
            blocks_gate=True,
            reason="insufficient_data",
            reason_label="数据不足",
            detail="三公经费合计声明「完成预算的80.72%」：表头仅披露“预算数”，未取得与原文一致的显式预算阶段证据；结构：缺数位置 第39页 text:预算完成率说明",
            reason_codes=["operand_missing", "scope_unconfirmed"],
            blocked_by=[],
            missing_operands=ops,
        )
        res = {"rule_findings": [phase_root]}
        assert cascade_root_issue_ids(item, res, visible_issue_ids=["phase-total"]) == expected_roots
        assert len(blocking_coverage_obligations([item], res, visible_issue_ids=["phase-total"])) == expected_blocks


def test_recheck2_p1_v33251_and_unit_cascade_require_all_blocked_kinds():
    """二轮 P1 #2b：V33-251 复合未完成义务若同时阻断年初预算与支出决算两侧，
    单侧 C-11 或单侧 C-14 万万元绝不得一并解除另一侧未完成门禁；纯单侧仍照常归并。"""
    preamble = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为120.00万元，支出决算为140.00万元。其中：\n"
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算为100.00万元，支出决算为100.00万元。\n"
        "2、其他支出（类）其他支出（款）其他支出（项）。"
    )
    footer = "\n六、一般公共预算财政拨款基本支出决算情况说明"
    cases = [
        ("支出决算为40.00万元。", [], 0),
        ("支出决算为40.00万万元。", [], 1),
        ("支出决算为40.00。", [], 1),
        ("支出决算金额见附件。", [], 1),
    ]
    for omission, expected_roots, expected_blocks in cases:
        doc = build_document("/tmp/probe-v251-v2.pdf", [preamble + omission + footer], [[]], 1000)
        doc.report_kind = "final"
        result = build_issues_payload(doc, report_kind="final", rules=[R33251_FunctionalDetailCompleteness()])
        obs = build_obligation_ledger(
            None,
            report_kind="final",
            rule_execution_summary=result["rule_execution_summary"],
            fact_receipts=result.get("fact_receipts") or [],
        )["instances"]
        ob = CoverageObligationItem.model_validate(
            next(r for r in obs if r["obligation_id"] == "OBL-TXT-FINAL-DETAIL")
        )
        findings = [f for f in result["issues"]["all"] if f["severity"] != "info"]
        visible = [f["id"] for f in findings]
        assert cascade_root_issue_ids(ob, result, visible_issue_ids=visible) == expected_roots
        assert len(blocking_coverage_obligations([ob], result, visible_issue_ids=visible)) == expected_blocks

    # 纯单侧万万元仍正常归并（0ed5318c 对照）
    unit_doc = build_document(
        "/tmp/probe-v251-unit-only.pdf",
        [preamble.replace("年初预算为120.00万元", "年初预算为140.00万元")
         + "年初预算为40.00万元，支出决算为40.00万万元。" + footer],
        [[]],
        1000,
    )
    unit_doc.report_kind = "final"
    unit_res = build_issues_payload(unit_doc, report_kind="final", rules=[R33251_FunctionalDetailCompleteness()])
    unit_obs = build_obligation_ledger(
        None,
        report_kind="final",
        rule_execution_summary=unit_res["rule_execution_summary"],
        fact_receipts=unit_res.get("fact_receipts") or [],
    )["instances"]
    unit_ob = CoverageObligationItem.model_validate(
        next(r for r in unit_obs if r["obligation_id"] == "OBL-TXT-FINAL-DETAIL")
    )
    unit_vis = [f["id"] for f in unit_res["issues"]["all"] if f["severity"] != "info"]
    assert len(cascade_root_issue_ids(unit_ob, unit_res, visible_issue_ids=unit_vis)) == 1
    assert blocking_coverage_obligations([unit_ob], unit_res, visible_issue_ids=unit_vis) == []


def test_recheck2_p1_completion_rate_identity_hit_before_unit_realign():
    """二轮 P1 #3：同时出现年初预算（漏万写为元）与全年预算（万元）且完成率与全年预算吻合时，
    必须先触发「完成率分母身份错误」，绝不被单侧单位容错静默放行；无备用分母时仍抑制万倍派生错。"""
    header = (
        "甲局2024年度部门决算\n"
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "1、一般公共服务支出（类）发展与改革事务（款）行政运行（项）。"
    )
    footer = "\n六、一般公共预算财政拨款基本支出决算情况说明"

    doc_err = build_document(
        "/tmp/rate-id.pdf",
        [header + "年初预算为1000.00元，全年预算为1000.00万元，支出决算为1.00万元，完成年初预算的0.10%。" + footer],
        [[]],
        1000,
    )
    doc_err.report_kind = "final"
    issues_err = R33TrendCompletionRate().apply(doc_err)
    assert len(issues_err) == 1 and "完成率分母身份错误" in issues_err[0].message
    assert any("漏写“万”字" in i.message for i in CMM011_AmountUnitWan().apply(doc_err))

    doc_ok = build_document(
        "/tmp/rate-annual-ok.pdf",
        [header + "年初预算为1000.00元，全年预算为1000.00万元，支出决算为1.00万元，完成全年预算的0.10%。" + footer],
        [[]],
        1000,
    )
    doc_ok.report_kind = "final"
    assert R33TrendCompletionRate().apply(doc_ok) == []

    doc_bad_math = build_document(
        "/tmp/rate-bad-math.pdf",
        [header + "年初预算为1000.00元，全年预算为1000.00万元，支出决算为1.00万元，完成年初预算的1.00%。" + footer],
        [[]],
        1000,
    )
    doc_bad_math.report_kind = "final"
    issues_math = R33TrendCompletionRate().apply(doc_bad_math)
    assert len(issues_math) == 1 and "完成率复算不一致" in issues_math[0].message


def test_recheck2_p2_t6_econ_parent_closure_and_v33251_merged_clauses():
    """二轮 P2：T6 重复编码（310 99 + 310 99）、重名（其他资本性支出×2）、非规范编码（310 98 / 310 999）
    均拒绝借 310 类合计闭合放行未列项；V33-251 多（项）合并披露条目不得误报未披露非零年初预算 warn。"""
    t7 = [
        ["财政拨款“三公”经费", "", "", "", "", "", "", "", "", "", "", ""],
        ["合计", "", "因公出国（境）费", "", "公务用车购置及运行维护费", "", "", "", "", "", "公务接待费", ""],
        ["", "", "", "", "小计", "", "公务用车购置费", "", "公务用车运行维护费", "", "", ""],
        ["预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数"],
        ["100.00", "90.00", "20.00", "20.00", "70.00", "65.00", "40.00", "40.00", "30.00", "25.00", "10.00", "5.00"],
    ]
    for c1, n1, c2, n2 in (
        ("310 99", "其他资本性支出", "310 99", "其他资本性支出"),
        ("310 98", "其他资本性支出", "310 99", "其他资本性支出"),
        ("310 98", "自造资本性科目", "310 99", "其他资本性支出"),
        ("310 999", "自造六位科目", "310 99", "其他资本性支出"),
    ):
        t6 = [
            ["经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "科目名称", "决算数"],
            ["301", "工资福利支出", "100.00", "302", "商品和服务支出", "60.00"],
            ["301 01", "基本工资", "100.00", "302 09", "差旅费", "10.00"],
            ["", "", "", "302 11", "因公出国（境）费用", "20.00"],
            ["", "", "", "302 16", "公务接待费", "5.00"],
            ["", "", "", "302 29", "公务用车运行维护费", "25.00"],
            ["", "", "", "310", "资本性支出", "80.00"],
            ["", "", "", c1, n1, "40.00"],
            ["", "", "", c2, n2, "40.00"],
        ]
        doc = _build_t6_t7_doc(t6, t7)
        with pytest.raises(RuleDeferred):
            R33CrossSanGongEcon().apply(doc)
        vp = next(
            r for r in doc.fact_receipts
            if str(r.get("sub_check_id") or "").endswith(":vehicle_purchase")
        )
        assert vp["status"] == "insufficient_data"

    # 上海商业会计学校形态：第 2 条把 3 个（项）并入同一合并年初预算 3064.88 万元
    school_text = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为13995.10万元，支出决算为13859.21万元。其中：\n"
        "1、教育支出（类）职业教育（款）中等职业教育（项）。年初预算为9577.58万元，支出决算为9577.58万元。\n"
        "2、社会保障和就业支出（类）行政事业单位离退休（款）事业单位离退休（项）、"
        "机关事业单位基本养老保险（项）和机关事业单位职业年金缴费（项）。"
        "主要用于离休人员生活补贴等。年初预算为3064.88万元，支出决算为2937.65万元。\n"
        "3、卫生健康支出（类）行政事业单位医疗（款）事业单位医疗（项）。年初预算为795.63万元，支出决算为795.63万元。\n"
        "4、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算为556.94万元，支出决算为548.35万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    school_doc = build_document("/tmp/school-merged.pdf", [school_text], [[]], 1000)
    school_doc.report_kind = "final"
    with pytest.raises(RuleOutcomeSignal) as sig:
        R33251_FunctionalDetailCompleteness().apply(school_doc)
    assert not any(
        i.severity == "warn"
        and i.location.get("checklist_item") == "C-11"
        and "含非零年初预算" in i.message
        for i in (sig.value.partial_issues or [])
    )
