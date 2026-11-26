"""Round 5 final-account optimization tests and anti-false-green controls."""
from __future__ import annotations

from decimal import Decimal
import pytest

from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.common_rules import CMM007_ZeroBaseTrendRecompute, _cmm007_safe_decimal
from src.engine.fact_context import FactContext
from src.engine.native_table_arithmetic import derive_missing_detail_total_fact
from src.engine.native_table_text import _inside_full_quotation, check_three_public_details, context_rejection
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33113_PunctuationCheck,
    R33223_Narrative6_T6,
    R33246_DomesticReceptionDisclosure,
    R33CrossSanGongEcon,
    R33TrendCompletionRate,
    _extract_final_functional_narrative_mentions,
    _txt_fund_bind_statements,
    extract_money_unit,
)
from src.engine.three_public_disclosure_policy import _positive_reception_claim
from src.schemas.parse_artifact import (
    EvidenceRef,
    FiscalFact,
    LogicalTableInstance,
    ParseArtifact,
    ParsedCell,
    ParsedTable,
)
from src.services.fiscal_column_identity import canonical_column_mappings
from src.services.fiscal_table_rules import NINE_TABLE_RULES
from src.services.pdf_parser import PDFParser
from src.utils.issue_bbox import PDFBBoxLocator

FILE_HASH = "a" * 64


def _make_doc(pages, *, org="某区某部门", year=2025, facts=(), artifact=None):
    doc = build_document("fake.pdf", list(pages), [], 1024)
    doc.units_per_page = [extract_money_unit(p) for p in pages]
    doc.dominant_year = year
    doc.report_kind = "final"
    doc.parse_artifact = artifact
    doc.fact_context = FactContext(
        version_token="v1",
        file_hash=FILE_HASH,
        parse_run_uid="run1",
        report_kind="final",
        fiscal_year=year,
        org_name=org,
        facts=list(facts),
    )
    return doc


def _fin_fact(table_code, indicator, code, name, value, *, stage="决算", unit="万元", page=10, instance_uid=None):
    return FiscalFact(
        fact_uid=f"{table_code}:{indicator}:{code}:{stage}",
        indicator=indicator,
        raw_value=str(value),
        unit=unit,
        normalized_value=Decimal(str(value)),
        org_name="某区某部门",
        org_scope="单位",
        fiscal_year=2025,
        period="本年",
        fund_scope="fiscal_appropriation",
        budget_stage=stage,
        subject_code=code,
        subject_name=name,
        table_code=table_code,
        instance_uid=instance_uid or f"inst:{table_code}",
        extra_dims={
            "evidence_refs": [
                EvidenceRef(
                    file_hash=FILE_HASH,
                    physical_page=page,
                    source_cell_id=f"cell:{code}:{indicator}",
                    quote=str(value),
                    location_level="exact_cell",
                ).to_dict()
            ]
        },
    )


def _parsed_table(rows, *, table_code, title, page=1, header_rows=2):
    parser = PDFParser(None)
    cells = []
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            raw = "" if val is None else str(val)
            cells.append(
                ParsedCell(
                    r, c, raw, raw, parser._parse_numeric(raw), page,
                    (c * 80.0, r * 20.0 + 40.0, (c + 1) * 80.0, r * 20.0 + 60.0),
                    r < header_rows, 0.99, "万元", "lines", native_blank_verified=not raw,
                )
            )
    return ParsedTable(
        table_code, title, page, 0,
        (0.0, 40.0, len(rows[0]) * 80.0, len(rows) * 20.0 + 40.0),
        header_rows, 0.99, "lines", "万元", rows, cells, f"frag_{table_code}_{page}",
    )


def test_p0_derive_missing_total_skips_malformed_six_digit_code():
    facts = [
        _fin_fact("FIN_02_income", "total_actual", "201102", "非法六位编码", "10.00"),
        _fin_fact("FIN_02_income", "total_actual", "201", "一般公共服务支出", "10.00"),
    ]
    assert derive_missing_detail_total_fact(None, facts, "total_actual") is None


def test_p0_cmm007_safe_decimal_and_malformed_yoy_defers():
    assert _cmm007_safe_decimal("12..34") is None
    assert _cmm007_safe_decimal("1,234.56") == Decimal("1234.56")
    doc = _make_doc([
        "一、收入支出决算总体情况说明\n本年度收入支出总计 100.00 万元。"
        "与 2024 年度相比，收入支出总计增加 10..00 万元，增长 11.11%。"
    ])
    with pytest.raises(RuleDeferred):
        CMM007_ZeroBaseTrendRecompute().apply(doc)


def test_p1_v33227_double_dash_and_bare_class_only():
    table_entries = {
        "201": {"level": "类", "name": "一般公共服务支出"},
        "20101": {"level": "款", "name": "人大事务"},
        "2010101": {"level": "项", "name": "行政运行"},
    }
    doc_dash = _make_doc([
        "1、一般公共服务支出——人大事务——行政运行。主要用于基本运转。"
    ])
    mentions_dash = _extract_final_functional_narrative_mentions(doc_dash, table_entries)
    assert "2010101" in mentions_dash

    doc_bare = _make_doc([
        "1、一般公共服务支出。主要用于保障机构正常运转和开展业务。"
    ])
    mentions_bare = _extract_final_functional_narrative_mentions(doc_bare, table_entries)
    assert "201" in mentions_bare

    doc_wrong = _make_doc([
        "1、一般公共错写支出。主要用于保障机构正常运转。"
    ])
    assert _extract_final_functional_narrative_mentions(doc_wrong, table_entries) == {}


def test_p2_v33223_cross_page_footer_and_explicit_zero():
    doc_cross = _make_doc(
        [
            "六、一般公共预算财政拨款基本支出决算情况说明\n"
            "2025年度一般公共预算财政拨款基本支出 150.00 万元。其中：人\n23\n"
            "员经费 100.00 万元，公用经费 50.00 万元。"
        ],
        facts=[
            _fin_fact("FIN_06_basic_expenditure", "total_actual", "total_personnel", "人员经费合计", "100.00"),
            _fin_fact("FIN_06_basic_expenditure", "total_actual", "total_public", "公用经费合计", "50.00"),
        ],
    )
    doc_cross.page_tables = [[
        [
            ["经济分类科目编码", "科目名称", "决算数"],
            ["人员经费合计", "人员经费合计", "100.00"],
            ["公用经费合计", "公用经费合计", "50.00"],
        ]
    ]]
    doc_cross.anchors = {"一般公共预算财政拨款基本支出决算表": [1]}
    assert R33223_Narrative6_T6().apply(doc_cross) == []

    doc_zero_ok = _make_doc(
        [
            "六、一般公共预算财政拨款基本支出决算情况说明\n"
            "2025年度一般公共预算财政拨款基本支出0.00万元。"
        ],
        facts=[
            _fin_fact("FIN_06_basic_expenditure", "total_actual", "total_personnel", "人员经费合计", "0.00"),
            _fin_fact("FIN_06_basic_expenditure", "total_actual", "total_public", "公用经费合计", "0.00"),
        ],
    )
    assert R33223_Narrative6_T6().apply(doc_zero_ok) == []

    doc_zero_nonzero_table = _make_doc(
        [
            "六、一般公共预算财政拨款基本支出决算情况说明\n"
            "2025年度一般公共预算财政拨款基本支出0.00万元。"
        ],
        facts=[
            _fin_fact("FIN_06_basic_expenditure", "total_actual", "total_personnel", "人员经费合计", "10.00"),
        ],
    )
    with pytest.raises(RuleDeferred):
        R33223_Narrative6_T6().apply(doc_zero_nonzero_table)


def test_p3_v33251_merged_items_warn_and_sum():
    doc_exceed = _make_doc([
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为 800.00 万元，支出决算为 850.00 万元，完成年初预算的 106.25%。其中：\n"
        "1、文化支出（类）体育（款）群众体育（项）。主要用于群众体育。年初预算为 700.00 万元，支出决算为 700.00 万元。\n"
        "2、社会保障和就业支出（类）行政事业单位养老支出（款）事业单位离退休（项）机关事业单位基本养老保险缴费支出（项）机关事业单位职业年金缴费支出（项）。"
        "主要用于养老缴费。年初预算为 100.00 万元，支出决算为 162.00 万元。"
    ])
    issues = R33251_FunctionalDetailCompleteness().apply(doc_exceed)
    checklist_items = {(i.location or {}).get("checklist_item") for i in issues}
    assert "A-09" in checklist_items
    assert "C-12" in checklist_items

    doc_group_budget = _make_doc([
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为 200.00 万元，支出决算为 200.00 万元，完成年初预算的 100.00%。其中：\n"
        "1、社会保障和就业支出（类）养老支出（款）基本养老（项）、职业年金（项）。"
        "主要用于缴费。其中：基本养老 120.00 万元，职业年金 80.00 万元。"
        "年初预算为 200.00 万元；支出决算为 200.00 万元。"
    ])
    issues_b = R33251_FunctionalDetailCompleteness().apply(doc_group_budget)
    assert any((i.location or {}).get("checklist_item") == "A-09" and i.severity == "warn" for i in issues_b)
    assert any(
        r.get("status") == "pass" and "V33-251:budget:item_sum" in str(r)
        for r in doc_group_budget.fact_receipts
    )


def test_p4_quotation_and_section_context_boundaries():
    text = "（模板示例：“某单位引用。……\n一、收入支出决算总体情况说明\n本部门收入支出总计100.00万元。"
    sec_start = text.index("一、")
    assert _inside_full_quotation(
        text[sec_start:], text.index("100.00") - sec_start, text.index("万元") - sec_start
    ) is False

    doc = _make_doc(["第三部分 2024 年度决算情况说明\n一、收入支出决算总体情况说明\n本部门收入支出总计100.00万元。"])
    compact = "".join(doc.page_texts[0].split())
    sec_b = compact.index("一、")
    amt_s = compact.index("100.00")
    amt_e = amt_s + len("100.00万元")
    assert context_rejection(doc, compact, amt_s, amt_e, section_start=sec_b) is None
    same_sec = compact + "2024年度预算调整10.00万元。"
    assert context_rejection(doc, same_sec, len(same_sec) - 8, len(same_sec) - 1, section_start=sec_b) is not None


def test_p5_three_public_overseas_not_occurred_and_negative_controls():
    t7_rows = [
        ["合计", "因公出国（境）费", "公务用车购置及运行维护费", "小计", "公务用车购置费", "公务用车运行维护费", "公务接待费"],
        ["决算数", "决算数", "决算数", "决算数", "决算数", "决算数", "决算数"],
        ["10.00", "0.00", "10.00", "10.00", "0.00", "10.00", "0.00"],
    ]
    pt = _parsed_table(t7_rows, table_code="FIN_07_three_public", title="财政拨款“三公”经费支出决算表", page=1)
    lt = LogicalTableInstance(
        instance_uid="inst:t7",
        table_code="FIN_07_three_public",
        title="财政拨款“三公”经费支出决算表",
        fragment_uids=["frag_FIN_07_three_public_1"],
        page_start=1,
        page_end=1,
        org_name="某区某部门",
        fiscal_year=2025,
        row_count=len(t7_rows),
        confidence=1.0,
        column_mappings=canonical_column_mappings(pt, NINE_TABLE_RULES["FIN_07_three_public"]),
    )
    artifact = ParseArtifact(
        source_file_hash=FILE_HASH,
        parse_run_uid="run1",
        physical_fragments=[pt],
        table_fragments=[pt],
        logical_tables=[lt],
    )
    t7_facts = [
        _fin_fact("FIN_07_three_public", "total_actual", "total", "合计", "10.00", page=1, instance_uid="inst:t7"),
        _fin_fact("FIN_07_three_public", "overseas", "total", "因公出国（境）费", "0.00", page=1, instance_uid="inst:t7"),
        _fin_fact("FIN_07_three_public", "vehicle_purchase_operation", "total", "公务用车购置及运行维护费", "10.00", page=1, instance_uid="inst:t7"),
        _fin_fact("FIN_07_three_public", "vehicle_purchase", "total", "公务用车购置费", "0.00", page=1, instance_uid="inst:t7"),
        _fin_fact("FIN_07_three_public", "vehicle_operation", "total", "公务用车运行维护费", "10.00", page=1, instance_uid="inst:t7"),
        _fin_fact("FIN_07_three_public", "reception", "total", "公务接待费", "0.00", page=1, instance_uid="inst:t7"),
    ]
    rule_stub = R33TrendCompletionRate()
    doc_ok = _make_doc(
        [
            "七、一般公共预算财政拨款“三公”经费支出决算情况说明\n"
            "（一）“三公”经费财政拨款支出决算为 10.00 万元，因本年度因公出国（境）活动、学术交流接待等活动未发生。\n"
            "（二）具体情况说明\n"
            "1、公务用车购置及运行维护费支出决算 10.00 万元，其中：公务用车购置费 0.00 万元，运行维护费 10.00 万元。\n"
            "2、公务接待费 0.00 万元。"
        ],
        facts=t7_facts,
        artifact=artifact,
    )
    assert check_three_public_details(rule_stub, doc_ok, require_vehicle_details=False) == []

    # Negative control: YoY / outbreak excuse without "未发生" must STILL raise RuleDeferred
    doc_omit = _make_doc(
        [
            "七、一般公共预算财政拨款“三公”经费支出决算情况说明\n"
            "（一）“三公”经费财政拨款支出决算为 10.00 万元，受疫情影响，因公出国（境）、公务用车减少，公务出国活动无法正常开展。\n"
            "（二）具体情况说明\n"
            "1、公务用车购置及运行维护费支出决算 10.00 万元。\n"
            "2、公务接待费 0.00 万元。"
        ],
        facts=t7_facts,
        artifact=artifact,
    )
    with pytest.raises(RuleDeferred):
        check_three_public_details(rule_stub, doc_omit, require_vehicle_details=False)


def test_p5_txt_fund_leading_code_and_completion_rate_warn():
    body = (
        "政府性基金预算财政拨款收入 100.00 万元，支出 100.00 万元，支出具体情况如下：\n"
        "1、2069803 科学技术支出（类）超长期特别国债安排的支出（款）技术研究与开发（项）。"
        "主要用于研发。年初预算为 0 万元，支出决算为 100.00 万元。"
    )
    bindings, blocked = _txt_fund_bind_statements(body, 2025)
    assert not blocked
    assert len(bindings) == 1
    assert bindings[0]["klass"] == "科学技术支出"

    body_foreign_year = (
        "支出具体情况如下：\n"
        "1、2024年度住房保障支出（类）住房改革支出（款）住房公积金（项）。"
        "支出决算为 10.00 万元。"
    )
    _, blocked_fy = _txt_fund_bind_statements(body_foreign_year, 2025)
    assert blocked_fy

    doc_rate = _make_doc([
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为 584 万元，支出决算为 482.31 万元，完成全年预算的 82.59%。其中：\n"
        "1、公共安全支出（类）监狱（款）事业运行费 205.14 万元。主要用于运行。年初预算 205.14 万元，支出决算 205.14 万元，完成预算的 100.00%。"
    ])
    rate_issues = R33TrendCompletionRate().apply(doc_rate)
    assert len(rate_issues) == 1
    assert rate_issues[0].severity == "warn"
    assert "年初预算" in rate_issues[0].message


def test_p6_reception_yoy_cross_page_v33246_v33113_unit_and_nul():
    assert _positive_reception_claim("公务接待费支出决算减少0.50万元，说明2025年度无公务接待费支出。") is False
    assert _positive_reception_claim("公务接待费支出决算减少至0.50万元。") is True

    doc_v33113 = _make_doc(["正文内容正常。。后续说明"])
    issues_113 = R33113_PunctuationCheck().apply(doc_v33113)
    assert len(issues_113) == 1
    refs_113 = (issues_113[0].location or {}).get("table_refs") or []
    assert refs_113 and refs_113[0]["page"] == 1 and "。。" in refs_113[0]["span"]

    doc_v33246 = _make_doc([
        "某机构2025年度决算封面",
        "七、一般公共预算财政拨款“三公”经费支出决算情况说明\n"
        "1、公务接待费支出 1.00 万元。其中：仅接",
        "待外宾 0 批次、0 人次。",
    ])
    issues_246 = R33246_DomesticReceptionDisclosure().apply(doc_v33246)
    assert len(issues_246) == 1
    assert (issues_246[0].location or {}).get("page") == 2
    assert {r["page"] for r in issues_246[0].location["table_refs"]} == {2, 3}

    assert PDFBBoxLocator._native_text("456.83\x00万元") == "456.83万元"
    assert extract_money_unit("三公经费表\n单位 万元\n合计") == "万元"
    assert extract_money_unit("本单位万元以上开支") is None


def test_p6_cross_sangong_econ_filters_phantom_bleed_code():
    t6_rows = [
        ["经济分类科目编码", "科目名称", "决算数"],
        ["310", "资本性支出", "0.00"],
        ["0371013", "公务用车购置", "0.00"],
    ]
    t7_rows = [
        ["合计", "因公出国（境）费", "公务用车购置及运行维护费", "小计", "公务用车购置费", "公务用车运行维护费", "公务接待费"],
        ["决算数", "决算数", "决算数", "决算数", "决算数", "决算数", "决算数"],
        ["0.00", "0.00", "0.00", "0.00", "0.00", "0.00", "0.00"],
    ]
    p6_bleed = _parsed_table(t6_rows, table_code="FIN_06_basic_expenditure", title="一般公共预算财政拨款基本支出决算表", page=1, header_rows=1)
    p7 = _parsed_table(t7_rows, table_code="FIN_07_three_public", title="财政拨款“三公”经费支出决算表", page=2, header_rows=2)
    lt6 = LogicalTableInstance(
        instance_uid="inst:t6",
        table_code="FIN_06_basic_expenditure",
        title="一般公共预算财政拨款基本支出决算表",
        fragment_uids=["frag_FIN_06_basic_expenditure_1"],
        page_start=1,
        page_end=1,
        org_name="某区某部门",
        fiscal_year=2025,
        row_count=len(t6_rows),
        confidence=1.0,
        column_mappings=canonical_column_mappings(p6_bleed, NINE_TABLE_RULES["FIN_06_basic_expenditure"]),
    )
    lt7 = LogicalTableInstance(
        instance_uid="inst:t7",
        table_code="FIN_07_three_public",
        title="财政拨款“三公”经费支出决算表",
        fragment_uids=["frag_FIN_07_three_public_2"],
        page_start=2,
        page_end=2,
        org_name="某区某部门",
        fiscal_year=2025,
        row_count=len(t7_rows),
        confidence=1.0,
        column_mappings=canonical_column_mappings(p7, NINE_TABLE_RULES["FIN_07_three_public"]),
    )
    artifact = ParseArtifact(
        source_file_hash=FILE_HASH,
        parse_run_uid="run1",
        physical_fragments=[p6_bleed, p7],
        table_fragments=[p6_bleed, p7],
        logical_tables=[lt6, lt7],
    )
    # Page 1 text layer actually has 31013, NOT 0371013 -> phantom code mismatch suppressed
    doc = _make_doc(
        [
            "一般公共预算财政拨款基本支出决算表\n单位：万元\n2025年度\n310 资本性支出 0.00\n31013 公务用车购置 0.00",
            "财政拨款“三公”经费支出决算表\n单位：万元\n2025年度\n0.00",
        ],
        facts=[
            _fin_fact("FIN_07_three_public", "total_actual", "total", "合计", "0.00", page=2, instance_uid="inst:t7"),
            _fin_fact("FIN_07_three_public", "overseas", "total", "因公出国（境）费", "0.00", page=2, instance_uid="inst:t7"),
            _fin_fact("FIN_07_three_public", "vehicle_purchase_operation", "total", "公务用车购置及运行维护费", "0.00", page=2, instance_uid="inst:t7"),
            _fin_fact("FIN_07_three_public", "vehicle_purchase", "total", "公务用车购置费", "0.00", page=2, instance_uid="inst:t7"),
            _fin_fact("FIN_07_three_public", "vehicle_operation", "total", "公务用车运行维护费", "0.00", page=2, instance_uid="inst:t7"),
            _fin_fact("FIN_07_three_public", "reception", "total", "公务接待费", "0.00", page=2, instance_uid="inst:t7"),
        ],
        artifact=artifact,
    )
    doc.page_tables = [[t6_rows], [t7_rows]]
    doc.anchors = {"FIN_06_basic_expenditure": [1], "FIN_07_three_public": [2]}
    try:
        issues = R33CrossSanGongEcon().apply(doc)
    except RuleDeferred as exc:
        issues = list(exc.partial_issues or [])
    assert not any((i.location or {}).get("code") == "0371013" for i in issues)
