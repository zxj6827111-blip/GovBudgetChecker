"""Round 7 final-account optimization tests and anti-false-green controls."""
from __future__ import annotations

from decimal import Decimal
import pytest

from src.engine.fact_context import FactContext
from src.engine.native_empty_operands import verified_blank_zero_by_code
from src.engine.native_table_arithmetic import _economic_table_view
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33222_Narrative5_T5,
    R33227_Narrative5_T5_NameConsistency,
    R33TrendCompletionRate,
    extract_money_unit,
)
from src.schemas.parse_artifact import (
    EvidenceRef,
    FiscalFact,
    LogicalTableInstance,
    ParseArtifact,
    ParsedCell,
    ParsedTable,
    SourceToken,
)
from src.services.fiscal_column_identity import canonical_column_mappings
from src.services.fiscal_table_rules import NINE_TABLE_RULES
from src.services.pdf_parser import PDFParser
from src.services.rule_source_binding import bind_rule_sources

FILE_HASH = "c" * 64
FIN_05 = "FIN_05_general_public_expenditure"
FIN_06 = "FIN_06_basic_expenditure"


def _build_t5_doc(table_rows, narrative_text, *, table_page=1, narr_page=2):
    """Build a native-grid certified Document with FIN_05 facts + ParseArtifact."""
    parser = PDFParser(None)
    table_lines = ["一般公共预算财政拨款支出决算表", "单位：万元", "功能分类科目编码 科目名称 合计 基本支出 项目支出"]
    for row in table_rows[2:]:
        table_lines.append(" ".join(str(x or "") for x in row))
    pages = ["\n".join(table_lines), narrative_text]

    cells = []
    tokens = []
    facts = []
    for r, row in enumerate(table_rows):
        y0, y1 = r * 20.0 + 40.0, r * 20.0 + 58.0
        for c, val in enumerate(row):
            raw = "" if val is None else str(val)
            x0, x1 = c * 90.0 + 10.0, (c + 1) * 90.0 + 10.0
            bbox = [x0, y0, x1, y1]
            cells.append(
                ParsedCell(
                    row_idx=r,
                    col_idx=c,
                    raw_text=raw,
                    normalized_text=raw,
                    numeric_value=parser._parse_numeric(raw),
                    page_number=table_page,
                    bbox=tuple(bbox),
                    is_header=r < 2,
                    confidence=0.99,
                    unit_hint="万元",
                    extraction_method="lines",
                    native_blank_verified=not raw,
                )
            )
            if raw:
                tokens.append(
                    SourceToken(
                        token_uid=f"tok:{r}:{c}",
                        raw_text=raw,
                        physical_page=table_page,
                        bbox=bbox,
                        source_method="native_text",
                        source_ref={
                            "source_file_hash": FILE_HASH,
                            "source_block_id": f"blk:{r}:{c}",
                        },
                    )
                )
        row_key = str(row[0] or "").strip()
        if r >= 2 and (row_key.isdigit() or row_key == "合计"):
            code = "total" if row_key == "合计" else row_key
            name = "合计" if row_key == "合计" else str(row[1] or "")
            for c_idx, ind in ((2, "total_actual"), (3, "basic_actual"), (4, "project_actual")):
                amt_raw = str(row[c_idx] or "").strip()
                if not amt_raw:
                    continue
                cell_sid = f"frag_fin05_{table_page}:r{r}c{c_idx}"
                code_sid = f"frag_fin05_{table_page}:r{r}c0"
                facts.append(
                    FiscalFact(
                        fact_uid=f"{FIN_05}:{ind}:{code}",
                        indicator=ind,
                        raw_value=amt_raw,
                        unit="万元",
                        normalized_value=Decimal(amt_raw),
                        org_name="测试单位",
                        org_scope="单位",
                        fiscal_year=2025,
                        period="本年",
                        fund_scope="general_public_budget",
                        budget_stage="决算",
                        subject_code=code,
                        subject_name=name,
                        table_code=FIN_05,
                        instance_uid="inst:fin05",
                        row_order=r,
                        extra_dims={
                            "evidence_refs": [
                                EvidenceRef(
                                    file_hash=FILE_HASH,
                                    physical_page=table_page,
                                    source_cell_id=cell_sid,
                                    quote=amt_raw,
                                    bbox=[c_idx * 90.0 + 10.0, y0, (c_idx + 1) * 90.0 + 10.0, y1],
                                    location_level="exact_cell",
                                ).to_dict()
                            ],
                            "row_identity_sources": [
                                {
                                    "file_hash": FILE_HASH,
                                    "physical_page": table_page,
                                    "source_cell_id": code_sid,
                                    "quote": row_key,
                                    "grid_raw_text": row_key,
                                }
                            ] if row_key.isdigit() else [],
                        },
                    )
                )

    frag = ParsedTable(
        FIN_05,
        "一般公共预算财政拨款支出决算表",
        table_page,
        0,
        (10.0, 40.0, 460.0, len(table_rows) * 20.0 + 40.0),
        2,
        0.99,
        "lines",
        "万元",
        table_rows,
        cells,
        f"frag_fin05_{table_page}",
    )
    lt = LogicalTableInstance(
        instance_uid="inst:fin05",
        table_code=FIN_05,
        title="一般公共预算财政拨款支出决算表",
        fragment_uids=[frag.fragment_uid],
        page_start=table_page,
        page_end=table_page,
    )
    artifact = ParseArtifact(
        source_file_hash=FILE_HASH,
        parse_run_uid="run_round7",
        source_tokens=tokens,
        table_fragments=[frag],
        logical_tables=[lt],
        physical_fragments=[frag],
    )

    doc = build_document("round7_test.pdf", pages, [table_rows, []], 1024)
    doc.units_per_page = [extract_money_unit(p) for p in pages]
    doc.dominant_year = 2025
    doc.report_kind = "final"
    doc.anchors = {"一般公共预算财政拨款支出决算表": [table_page]}
    doc.parse_artifact = artifact
    doc.fact_context = FactContext(
        version_token="v1",
        file_hash=FILE_HASH,
        parse_run_uid="run_round7",
        report_kind="final",
        fiscal_year=2025,
        org_name="测试单位",
        facts=facts,
    )
    return doc


def _build_fin06_doc(rows, *, table_code=FIN_06, title="一般公共预算财政拨款基本支出决算表", page=1, header_rows=2):
    parser = PDFParser(None)
    cells = []
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            raw = "" if val is None else str(val)
            cells.append(
                ParsedCell(
                    row_idx=r,
                    col_idx=c,
                    raw_text=raw,
                    normalized_text=raw,
                    numeric_value=parser._parse_numeric(raw),
                    page_number=page,
                    bbox=(c * 80.0 + 10.0, r * 20.0 + 40.0, (c + 1) * 80.0 + 10.0, r * 20.0 + 60.0),
                    is_header=r < header_rows,
                    confidence=0.99,
                    unit_hint="万元",
                    extraction_method="lines",
                    native_blank_verified=not raw,
                )
            )
    frag = ParsedTable(
        table_code,
        title,
        page,
        0,
        (10.0, 40.0, len(rows[0]) * 80.0 + 10.0, len(rows) * 20.0 + 40.0),
        header_rows,
        0.99,
        "lines",
        "万元",
        rows,
        cells,
        f"frag_{table_code}_{page}",
    )
    mappings = canonical_column_mappings(frag, NINE_TABLE_RULES[table_code])
    lt = LogicalTableInstance(
        instance_uid=f"inst:{table_code}",
        table_code=table_code,
        title=title,
        fragment_uids=[frag.fragment_uid],
        page_start=page,
        page_end=page,
        column_mappings=mappings,
    )
    artifact = ParseArtifact(
        source_file_hash=FILE_HASH,
        parse_run_uid="run_round7_fin06",
        table_fragments=[frag],
        logical_tables=[lt],
        physical_fragments=[frag],
    )
    pages = [f"{title}\n单位：万元\n" + "\n".join(" ".join(str(x or "") for x in r) for r in rows)]
    doc = build_document("fin06_test.pdf", pages, [rows], 1024)
    doc.units_per_page = [extract_money_unit(pages[0])]
    doc.dominant_year = 2025
    doc.report_kind = "final"
    doc.parse_artifact = artifact
    doc.fact_context = FactContext(
        version_token="v1",
        file_hash=FILE_HASH,
        parse_run_uid="run_round7_fin06",
        report_kind="final",
        fiscal_year=2025,
        org_name="测试单位",
        facts=[],
    )
    return doc, frag


# ---------------------------------------------------------------------------
# P0: M == 0, N == 1 zero-value omitted item vs non-zero cross-class / M>=2
# ---------------------------------------------------------------------------


def test_p0_m0_n1_zero_omitted_item_passes_while_nonzero_cross_class_stays_blocked():
    t5_rows = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["", "", "决算数", "决算数", "决算数"],
        ["合计", "合计", "200.00", "200.00", "0.00"],
        ["208", "社会保障和就业支出", "200.00", "200.00", "0.00"],
        ["20805", "行政事业单位养老支出", "200.00", "200.00", "0.00"],
        ["2080502", "事业单位离退休", "200.00", "200.00", "0.00"],
    ]
    # Narrative omits nothing and includes a zero-actual item absent from T5 (M == 0, N == 1),
    # even when the kuan uses legacy alias "行政事业单位离退休" for T5 "行政事业单位养老支出" (ba53967e pattern)
    narr_zero_ok = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为200.00万元。其中：\n"
        "1、社会保障和就业支出（类）行政事业单位离退休（款）行政单位离退休（项）。"
        "年初预算为0.00万元，支出决算为0万元。\n"
        "2、社会保障和就业支出（类）行政事业单位养老支出（款）事业单位离退休（项）。"
        "年初预算为200.00万元，支出决算为200.00万元。"
    )
    doc_ok = _build_t5_doc(t5_rows, narr_zero_ok)
    R33222_Narrative5_T5().apply(doc_ok)
    # No unresolved insufficient_data on the omitted 0万元 item (reconciled to parent kuan)
    receipts = getattr(doc_ok, "fact_receipts", [])
    assert not any(r.get("status") == "insufficient_data" for r in receipts), receipts

    # Anti-false-green control (f901b866 pattern): non-zero cross-class error MUST stay blocked!
    narr_bad = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为200.00万元。其中：\n"
        "1、社会保障和就业支出（类）行政事业单位养老支出（款）其他城乡社区公共设施支出（项）。"
        "支出决算为2443万元。\n"
        "2、社会保障和就业支出（类）行政事业单位养老支出（款）事业单位离退休（项）。"
        "支出决算为200.00万元。"
    )
    doc_bad = _build_t5_doc(t5_rows, narr_bad)
    with pytest.raises(RuleDeferred) as exc_bad:
        R33222_Narrative5_T5().apply(doc_bad)
    assert any("其他城乡社区公共设施支出" in r for r in exc_bad.value.unresolved_reasons)


def test_p0_tier3_multiplicity_still_blocks_with_detail_message():
    t5_rows = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["", "", "决算数", "决算数", "决算数"],
        ["合计", "合计", "30.00", "30.00", "0.00"],
        ["208", "社会保障和就业支出", "30.00", "30.00", "0.00"],
        ["20805", "行政事业单位养老支出", "30.00", "30.00", "0.00"],
        ["2080501", "行政单位离退休", "10.00", "10.00", "0.00"],
        ["2080502", "事业单位离退休", "20.00", "20.00", "0.00"],
    ]
    narr_m2 = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为30.00万元。其中：\n"
        "1、社会保障和就业支出（类）行政事业单位养老支出（款）旧退休A（项）。支出决算为0万元。\n"
        "2、社会保障和就业支出（类）行政事业单位养老支出（款）旧退休B（项）。支出决算为0万元。"
    )
    doc = _build_t5_doc(t5_rows, narr_m2)
    with pytest.raises(RuleDeferred) as exc_t3:
        R33222_Narrative5_T5().apply(doc)
    assert any("多对多/一对多未匹配科目" in r for r in exc_t3.value.unresolved_reasons)


# ---------------------------------------------------------------------------
# P1a: Opening-sentence 10,000x unit-typo vs real contradiction in completion rate
# ---------------------------------------------------------------------------


def test_p1a_completion_rate_allows_pure_wan_yuan_opening_typo_and_blocks_real_mismatch():
    # 9d59a62a P20 pattern: opening "47.72元" vs explicit "支出决算为47.72万元" (110.06%)
    text_typo = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "1、住房保障支出（类）住房改革支出（款）住房公积金（项）47.72元。"
        "主要用于：职工住房公积金。年初预算为43.36万元，支出决算为47.72万元，"
        "完成年初预算的110.06%。"
    )
    doc_ok = _build_t5_doc(
        [
            ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
            ["", "", "决算数", "决算数", "决算数"],
            ["合计", "合计", "47.72", "47.72", "0.00"],
        ],
        text_typo,
    )
    res_ok = R33TrendCompletionRate().apply(doc_ok)
    assert isinstance(res_ok, list) and res_ok == [], res_ok

    # Anti-false-green control: 石泉 S03 real contradiction (257.14万元 vs 245.53万元) MUST defer
    text_conflict = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "1、住房保障支出（类）住房改革支出（款）住房公积金（项）257.14万元。"
        "主要用于：职工住房公积金。年初预算为262.37万元，支出决算为245.53万元，"
        "完成年初预算的93.57%。"
    )
    doc_bad = _build_t5_doc(
        [
            ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
            ["", "", "决算数", "决算数", "决算数"],
            ["合计", "合计", "245.53", "245.53", "0.00"],
        ],
        text_conflict,
    )
    with pytest.raises(RuleDeferred) as exc_info:
        R33TrendCompletionRate().apply(doc_bad)
    assert "无法唯一确定" in str(exc_info.value)


# ---------------------------------------------------------------------------
# P1b: Multi-line wrapped class chain + klass_name_difference evidence + legacy alias + zero kuan
# ---------------------------------------------------------------------------


def test_p1b_multiline_class_chain_binds_with_verified_evidence_and_no_evidence_incomplete():
    # 717ec667 pattern: wrapped cells in T5 + 1-char class omission "社会保障就业支出"
    t5_rows = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["", "", "决算数", "决算数", "决算数"],
        ["合计", "合计", "100.00", "100.00", "0.00"],
        ["208", "社会保障和就\n业支出", "100.00", "100.00", "0.00"],
        ["20805", "行政事业单位\n养老支出", "100.00", "100.00", "0.00"],
        ["2080501", "行政单位离退\n休", "100.00", "100.00", "0.00"],
    ]
    narr = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为100.00万元。其中：\n"
        "1、社会保障就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。"
        "支出决算为100.00万元。"
    )
    doc = _build_t5_doc(t5_rows, narr)
    out = R33222_Narrative5_T5().apply(doc)
    assert isinstance(out, list)
    klass_issues = [i for i in out if (i.location or {}).get("narrative_class_name") == "社会保障就业支出"]
    assert len(klass_issues) == 1
    loc = klass_issues[0].location
    assert loc.get("file_hash") == FILE_HASH
    assert loc.get("table_refs"), "klass_name_difference must include verified table_refs"

    # Run literal source binding to verify it binds cleanly rather than leaving unresolved
    finding = {
        "id": "test-klass-1",
        "source": "rule",
        "rule": "V33-222",
        "rule_id": "V33-222",
        "severity": klass_issues[0].severity,
        "title": "科目名称差异提示",
        "message": klass_issues[0].message,
        "location": dict(loc),
        "evidence": [],
    }
    bind_res = bind_rule_sources(
        {"rule_findings": [finding]},
        page_texts=doc.page_texts,
        source_file_hash=FILE_HASH,
        artifact=doc.parse_artifact,
        fact_context=doc.fact_context,
    )
    assert bind_res["bound_rule_findings"] == 1
    assert finding.get("source_binding_status") != "unresolved"
    assert finding.get("evidence")


def test_p1b_legacy_medical_alias_and_zero_omitted_kuan():
    # 1) ebd9e8fe pattern: "医疗卫生健康（类）医疗保障（款）事业单位医疗（项）" -> binds 210 / 2101102
    t5_ebd = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["", "", "决算数", "决算数", "决算数"],
        ["合计", "合计", "138.62", "138.62", "0.00"],
        ["204", "公共安全支出", "100.00", "100.00", "0.00"],
        ["20406", "司法", "100.00", "100.00", "0.00"],
        ["2040650", "事业运行", "100.00", "100.00", "0.00"],
        ["210", "卫生健康支出", "38.62", "38.62", "0.00"],
        ["21011", "行政事业单位医疗", "38.62", "38.62", "0.00"],
        ["2101102", "事业单位医疗", "38.62", "38.62", "0.00"],
    ]
    narr_ebd = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为138.62万元。其中：\n"
        "1、公共安全支出（类）司法（款）事业运行（项）。支出决算为100.00万元。\n"
        "2、医疗卫生健康（类）医疗保障（款）事业单位医疗（项）。支出决算为38.62万元。"
    )
    doc_ebd = _build_t5_doc(t5_ebd, narr_ebd)
    out_ebd = R33222_Narrative5_T5().apply(doc_ebd)
    assert isinstance(out_ebd, list), out_ebd
    v227_issues = R33227_Narrative5_T5_NameConsistency().apply(doc_ebd)
    assert any("210" in (i.location or {}).get("code", "") and "医疗卫生健康" in i.message for i in v227_issues)

    # 2) b48439d3 / faecb53b pattern: omitted zero-actual kuan ("其它卫生健康支出" 0万元) when 210 reconciles
    narr_zero_kuan = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为138.62万元。其中：\n"
        "1、公共安全支出（类）司法（款）事业运行（项）。支出决算为100.00万元。\n"
        "2、卫生健康支出（类）行政事业单位医疗（款）事业单位医疗（项）。支出决算为38.62万元。\n"
        "3、卫生健康支出（类）其它卫生健康支出（款）其它卫生健康支出（项）。支出决算为0万元。"
    )
    doc_zk = _build_t5_doc(t5_ebd, narr_zero_kuan)
    out_zk = R33222_Narrative5_T5().apply(doc_zk)
    assert isinstance(out_zk, list), out_zk

    # Anti-false-green control: if parent class 210 does NOT reconcile, zero kuan MUST stay blocked!
    t5_unbalanced = list(t5_ebd)
    t5_unbalanced[6] = ["210", "卫生健康支出", "50.00", "50.00", "0.00"]
    doc_unbal = _build_t5_doc(t5_unbalanced, narr_zero_kuan)
    with pytest.raises(RuleDeferred) as exc_unbal:
        R33222_Narrative5_T5().apply(doc_unbal)
    assert any("其它卫生健康支出" in r for r in exc_unbal.value.unresolved_reasons)


# ---------------------------------------------------------------------------
# P2a: Split-code FIN_06 under merged "经济分类科目编码" header
# ---------------------------------------------------------------------------


def test_p2a_verified_blank_zero_by_code_accepts_merged_econ_code_header():
    rows = [
        ["人员经费", "", "", "", "公用经费", "", "", ""],
        ["经济分类科目编码", "经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "经济分类科目编码", "科目名称", "决算数"],
        ["301", "01", "基本工资", "10.00", "310", "13", "公务用车购置", ""],
    ]
    doc, _frag = _build_fin06_doc(rows, header_rows=2)
    obs = verified_blank_zero_by_code(doc, FIN_06, 1, "31013")
    assert obs is not None
    assert obs["subject_code"] == "31013"
    assert obs["subject_name"] == "公务用车购置"


# ---------------------------------------------------------------------------
# P2b: Tolerate verified-blank empty-code row vs reject non-empty / garbled code
# ---------------------------------------------------------------------------


def test_p2b_economic_view_skips_verified_blank_empty_code_row_and_blocks_real_defects():
    # 1493a7ef pattern: right lane has empty code cell, label "培训费", verified-blank amount
    rows_ok = [
        ["人员经费", "", "", "公用经费", "", ""],
        ["科目编码", "科目名称", "决算数", "科目编码", "科目名称", "决算数"],
        ["", "人员经费合计", "10.00", "", "公用经费合计", "5.00"],
        ["301", "工资福利支出", "10.00", "302", "商品和服务支出", "5.00"],
        ["30101", "基本工资", "10.00", "30201", "办公费", "5.00"],
        ["", "", "", "", "培训费", ""],
    ]
    doc_ok, frag_ok = _build_fin06_doc(rows_ok, header_rows=2)
    facts_ok = [
        FiscalFact(
            fact_uid=f"f:{row_i}:{col_i}:{code}",
            indicator="total_actual",
            raw_value=val,
            unit="万元",
            normalized_value=Decimal(val),
            org_name="测试单位",
            org_scope="单位",
            fiscal_year=2025,
            period="本年",
            fund_scope="general_public_budget",
            budget_stage="决算",
            subject_code=code,
            subject_name=name,
            table_code=FIN_06,
            instance_uid="inst:fin06",
            row_order=row_i,
            extra_dims={
                "evidence_refs": [
                    EvidenceRef(
                        file_hash=FILE_HASH,
                        physical_page=1,
                        source_cell_id=f"{frag_ok.fragment_uid}:r{row_i}c{col_i}",
                        quote=val,
                        location_level="exact_cell",
                    ).to_dict()
                ]
            },
        )
        for row_i, col_i, code, name, val in [
            (2, 2, "total", "人员经费合计", "10.00"),
            (3, 2, "301", "工资福利支出", "10.00"),
            (4, 2, "30101", "基本工资", "10.00"),
            (2, 5, "total", "公用经费合计", "5.00"),
            (3, 5, "302", "商品和服务支出", "5.00"),
            (4, 5, "30201", "办公费", "5.00"),
        ]
    ]
    doc_ok.fact_context = FactContext(
        version_token="v1",
        file_hash=FILE_HASH,
        parse_run_uid="run_round7_fin06",
        report_kind="final",
        fiscal_year=2025,
        org_name="测试单位",
        facts=facts_ok,
    )
    view_ok, err_ok = _economic_table_view(doc_ok, facts_ok)
    assert view_ok is not None and err_ok is None
    assert any(
        o.get("kind") == "blank_code_omitted_row" and o.get("subject_name") == "培训费"
        for o in getattr(doc_ok, "_economic_zero_layout_observations", [])
    )

    # Anti-false-green 1 (5ff953fd pattern): non-empty amount 0.60 with 3-digit code -> must fail closed!
    rows_bad_amt = [
        ["科目编码", "科目名称", "决算数"],
        ["", "", "决算数"],
        ["", "公用经费合计", "5.60"],
        ["30201", "办公费", "5.00"],
        ["310", "其他资本性支出", "0.60"],
    ]
    doc_bad_amt, _frag_bad_amt = _build_fin06_doc(rows_bad_amt, header_rows=2)
    doc_bad_amt.fact_context.facts = []
    view_bad, err_bad = _economic_table_view(doc_bad_amt, [])
    assert view_bad is None and err_bad

    # Specifically for empty code with NON-BLANK amount ("0.60"), test omitted code + non-blank amount:
    rows_omitted_nonzero = [
        ["科目编码", "科目名称", "决算数"],
        ["", "", "决算数"],
        ["", "公用经费合计", "5.60"],
        ["30201", "办公费", "5.00"],
        ["", "培训费", "0.60"],
    ]
    doc_nz, _ = _build_fin06_doc(rows_omitted_nonzero, header_rows=2)
    view_nz, err_nz = _economic_table_view(doc_nz, [])
    assert view_nz is None and "科目编码无法唯一识别" in str(err_nz)

    # Anti-false-green 2 (f1ef0085 pattern): garbled code "03101 02" + blank amount -> must NOT skip!
    rows_garbled = [
        ["科目编码", "科目名称", "决算数"],
        ["", "", "决算数"],
        ["", "公用经费合计", "5.00"],
        ["03101 02", "乱码科目", ""],
    ]
    doc_g, _ = _build_fin06_doc(rows_garbled, header_rows=2)
    view_g, err_g = _economic_table_view(doc_g, [])
    assert view_g is None and "科目编码无法唯一识别" in str(err_g)
