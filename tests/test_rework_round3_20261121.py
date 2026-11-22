"""第三轮返工（2026-11-21）单元测试：R4 异常读数恢复 + 加总行凭证/未决一致 + R7 收口。

覆盖：
- R4-1：``_manual_claim_for_unparsed_item``——显式读数人工校正驱动原解析失败
  的单项完成核对；位置落条、缺单位不猜、同版本冲突不猜。
- R4-2：加总回执的科目行凭证序列化与"科目身份未决"的排除/文案。
- R7：合计收口行（编码列/名称列）阻断无表头续表；真续表不受影响。
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.checklist_final_rules import (
    _calc_item_evidence,
    _completeness_summary,
    _operand_evidence,
)
from src.engine.native_final_disclosure import (
    _manual_claim_for_unparsed_item,
    _manual_correction_for_item,
)
from src.engine.native_narrative_checks import _Text
from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.pdf_parser import PDFParser


def _correction(**overrides):
    base = {
        "correction_id": "cor-test",
        "field": "raw_value",
        "value": "5465.41",
        "page": 1,
        "pos": 0,
        "span": "支出决算为54,65.41",
        "actor": "tester",
        "analysis_basis_token": "job:b1",
        "reason": "R4 复核",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# R4-1：解析失败条目的显式读数恢复
# ---------------------------------------------------------------------------

MALFORMED_PAGE_26 = (
    "（三）一般公共预算财政拨款支出决算具体情况\n"
    "1、商业服务业等支出（类）商业流通事务（款）事业运行（项）。"
    "主要用于：事业单位运行经费。年初预算为5,764.81 万元，支出决算为54,65.41，"
    "决算数小于预算数的主要原因：部分支出未执行。\n"
)


def _item_for(text: _Text, needle: str):
    start = text.compact.find(needle)
    assert start >= 0
    return SimpleNamespace(start=lambda: start, end=lambda: start + len(needle))


class _Claim:
    """与正则 match 同接口的声明视图（同上轮测试）。"""

    def __init__(self, amount, unit, start, end, inherited=True):
        self._data = {"amount": amount, "unit": unit}
        self._start, self._end = start, end
        self.inherited_unit = inherited

    def __getitem__(self, key):
        return self._data[key]

    def start(self):
        return self._start

    def end(self):
        return self._end


def _recovered(text: _Text, corrections, item=None):
    item = item or _item_for(text, "商业服务业等支出")
    doc = SimpleNamespace(text_corrections=corrections)
    return _manual_claim_for_unparsed_item(doc, text, item, len(text.compact))


def test_r4_recovered_claim_uses_explicit_reading_and_unit():
    """pos 落条：读数+单位都由复核人显式给出 → 恢复出有效读数与原句区间。"""
    text = _Text.from_pages([MALFORMED_PAGE_26])
    item_start = text.compact.find("商业服务业等支出")
    # 页内 pos：原句 raw 起点 - 页 raw 起点
    raw_sentence = re.search(r"支出决算为54,65.41", text.raw)
    raw_pos = raw_sentence.start() - text.pages[0][0]
    claim, manual = _recovered(text, [
        _correction(value="5465.41", pos=raw_pos),
        _correction(field="unit", value="万元", pos=raw_pos, correction_id="cor-test"),
    ])
    assert manual["amount"] == "5465.41"
    assert manual["unit"] == "万元"
    assert manual["unit_field_applied"] is True
    assert manual["original_amount"] == "54,65.41"
    assert manual["original_unit_written"] is False
    assert manual["recovered_claim"] is True
    assert claim.inherited_unit is True
    # 声明区间必须落在条目范围内，供 text.refs 取原句证据。
    assert item_start <= claim.start() < claim.end() <= len(text.compact)
    refs = text.refs(claim.start(), claim.end(), "原文决算金额声明")
    assert refs and "54,65.41" in refs[0]["span"].replace("\n", "")


def test_r4_recovery_requires_explicit_unit():
    """原句没有可继承的完整声明：只改读数不确认单位 → 保持缺数判定。"""
    text = _Text.from_pages([MALFORMED_PAGE_26])
    raw_sentence = re.search(r"支出决算为54,65.41", text.raw)
    raw_pos = raw_sentence.start() - text.pages[0][0]
    assert _recovered(text, [_correction(value="5465.41", pos=raw_pos)]) is None


def test_r4_recovery_requires_position_inside_item():
    """pos 落在其他条目范围 → 不扩散到后一条目。"""
    second_page = (
        MALFORMED_PAGE_26
        + "2、节能环保支出（类）污染减排（款）减排专项支出（项）。"
        "年初预算为0 万元，支出决算为477.81 万元，决算数小于预算数。\n"
    )
    text = _Text.from_pages([second_page])
    raw_sentence = re.search(r"支出决算为54,65.41", text.raw)
    raw_pos = raw_sentence.start() - text.pages[0][0]
    second_item = _item_for(text, "2、节能环保支出")
    doc = SimpleNamespace(text_corrections=[_correction(value="5465.41", pos=raw_pos),
                                            _correction(field="unit", value="万元", pos=raw_pos)])
    assert _manual_claim_for_unparsed_item(doc, text, second_item, len(text.compact)) is None
    # 本条目内仍恢复成功。
    first_item = _item_for(text, "1、商业服务业等支出")
    assert _manual_claim_for_unparsed_item(doc, text, first_item, len(text.compact)) is not None


def test_r4_recovery_same_version_conflict_stays_unresolved():
    text = _Text.from_pages([MALFORMED_PAGE_26])
    raw_sentence = re.search(r"支出决算为54,65.41", text.raw)
    raw_pos = raw_sentence.start() - text.pages[0][0]
    assert _recovered(text, [
        _correction(value="5465.41", pos=raw_pos, analysis_basis_token="job:b1"),
        _correction(value="5455.41", pos=raw_pos, analysis_basis_token="job:b1"),
        _correction(field="unit", value="万元", pos=raw_pos),
    ]) is None


def test_r4_recovery_still_consume_by_manual_correction_when_claim_exists():
    """可解析条目走原有 _manual_correction_for_item：恢复函数不改变其语义。"""
    text = _Text.from_pages([MALFORMED_PAGE_26.replace("54,65.41", "5,465.41")])
    item = _item_for(text, "商业服务业等支出")
    claim_start = text.compact.find("5,465.41")
    claim = _Claim("5,465.41", "万元", claim_start, claim_start + 8, inherited=True)
    doc = SimpleNamespace(text_corrections=[
        _correction(value="5465.41", pos=None, span="支出决算为5,465.41"),
        _correction(field="unit", value="万元", pos=None, span="支出决算为5,465.41"),
    ])
    manual = _manual_correction_for_item(doc, text, item, len(text.compact), claim)
    assert manual is not None and manual["amount"] == "5465.41"


# ---------------------------------------------------------------------------
# R4-2：加总侧科目行凭证与"科目身份未决"
# ---------------------------------------------------------------------------

class _FakeFact:
    """真实 FiscalFact（仅填序列化所需字段）。"""

    def __new__(cls):
        from src.schemas.parse_artifact import FiscalFact

        return FiscalFact(
            fact_uid="fact-1",
            indicator="total_actual",
            raw_value="118.11",
            unit="万元",
            normalized_value=Decimal("118.11"),
            org_name="甲单位",
            fiscal_year=2023,
            fund_scope="一般公共预算",
            budget_stage="决算",
            subject_code="2011350",
            subject_name="事业运行",
            table_code="FIN_05_general_public_expenditure",
            extra_dims={"evidence_refs": [{
                "physical_page": 16,
                "source_cell_id": "cell-1",
                "quote": "2011350 事业运行 118.11",
                "bbox": [1.0, 2.0, 3.0, 4.0],
                "file_hash": "a" * 64,
            }]},
        )


def _locate_of(page_start):
    def locate(pos):
        page_text = " " * page_start + "支出决算为5,465.41，决算"
        return 1, page_text, page_start, page_start + len("支出决算为5,465.41，决算")
    return locate


def test_r4_sum_entry_carries_subject_row_basis():
    record = {
        "pos": 0, "raw": "5465.41", "kind": "final", "unit": "万元",
        "unit_accounting": "万元", "unit_source": "manual_correction",
        "value_wan": Decimal("5465.41"),
        "subject_row_basis": _FakeFact(),
    }
    evidence = _calc_item_evidence(record, _locate_of(10))
    basis = evidence["subject_row_basis"]
    assert isinstance(basis, dict)
    assert basis["subject_code"] == "2011350"
    assert basis["subject_name"] == "事业运行"
    assert basis["page"] == 16
    assert basis["quote"] == "2011350 事业运行 118.11"
    assert basis["bbox"] == [1.0, 2.0, 3.0, 4.0]
    operand = _operand_evidence(record)
    assert isinstance(operand["subject_row_basis"], dict)


def test_r4_subject_conflict_entry_is_pending_with_reason():
    record = {
        "pos": 0, "raw": "5465.41", "kind": "final", "unit": "万元",
        "unit_accounting": "万元", "unit_source": "manual_correction",
        "value_wan": Decimal("5465.41"),
        "not_included_reason": "subject_identity_unresolved",
        "subject_identity_unresolved": {
            "subject_code": "2011350", "subject_name": "住房公积金",
            "table_subject_name": "事业运行", "reason": "subject_name_conflict",
        },
    }
    evidence = _calc_item_evidence(record, _locate_of(10))
    assert evidence["not_included_reason"] == "subject_identity_unresolved"
    assert evidence["subject_identity_unresolved"]["table_subject_name"] == "事业运行"
    summary = _completeness_summary([record], "支出决算")
    assert "1处复核人指定科目无法在表内唯一确认" in summary


# ---------------------------------------------------------------------------
# R7：合计收口行识别扩展到名称列；收口后无表头续页不再并入
# ---------------------------------------------------------------------------

def _table(code, page, rows, header, widths=(50, 140, 60, 60, 60), title=""):
    cells = []
    x_edges = [0]
    for width in widths:
        x_edges.append(x_edges[-1] + width)
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            x0, x1 = x_edges[col_index], x_edges[min(col_index + 1, len(widths))]
            cells.append(ParsedCell(
                row_index, col_index, str(value), str(value), None, page,
                (x0, 100 + row_index * 20, x1, 120 + row_index * 20),
                row_index < header, 0.95, "万元", "lines",
            ))
    return ParsedTable(
        table_code=code, title=title, page_number=page, table_index=0,
        bbox=(0, 100, sum(widths), 100 + 20 * len(rows)),
        header_row_count=header, confidence=0.9, extraction_method="lines",
        unit_hint="万元", rows=rows, cells=cells,
        fragment_uid=f"p{page}-{code}",
    )


def test_r7_total_in_name_column_blocks_headerless_merge():
    """名称列“合计”（编码列空白）同样视为收口行：后页设备清单不再并入。"""
    parser = PDFParser(None)
    previous = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["2160250", "事业运行", "100", "80", "20"],
         ["", "合计", "100", "80", "20"]],
        1, title="一般公共预算财政拨款支出决算表",
    )
    fragment = _table(
        "UNKNOWN_P2_T0", 2,
        [["办公桌", "台", "3", "1", "2"], ["电脑", "台", "4", "2", "2"]],
        0,
    )
    assert parser._is_continuation_table(previous, fragment) is False
    assert parser._row_is_total_close(["", "合计", "100", "80", "20"]) is True
    assert parser._row_is_total_close(["2160250", "事业运行", "100", "80", "20"]) is False


def test_r7_device_codes_after_total_row_stay_separate():
    """前页合计收口 + 后页 301/302 设备编号（首行形似科目行）→ 不并入。"""
    parser = PDFParser(None)
    previous = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["2160250", "事业运行", "100", "80", "20"],
         ["合计", "", "100", "80", "20"]],
        1, title="一般公共预算财政拨款支出决算表",
    )
    fragment = _table(
        "UNKNOWN_P2_T0", 2,
        [["301", "办公桌", "3", "1", "2"], ["302", "电脑", "4", "2", "2"]],
        0,
    )
    assert parser._is_continuation_table(previous, fragment) is False


def test_r7_row_sequence_evidence_rejects_name_column_total():
    parser = PDFParser(None)
    previous = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["20502", "普通教育", "100", "80", "20"],
         ["", "合计", "100", "80", "20"]],
        1, title="一般公共预算财政拨款支出决算表",
    )
    fragment = _table(
        "UNKNOWN_P2_T0", 2,
        [["2050201", "学前教育", "60", "50", "10"]],
        0,
    )
    # 名称列收口：行序列证据与续表判定都必须拒绝。
    assert parser._continuation_row_sequence_evidence(previous, fragment) is False
    assert parser._is_continuation_table(previous, fragment) is False


def _parsed_pdf_table(pdf: Path):
    parser = PDFParser(None)
    return parser.build_parse_artifact(str(pdf))


@pytest.mark.parametrize("name", [
    "r7-total_in_name_column.pdf",
    "r7-nonfiscal_inventory_number_after_total.pdf",
    "r7-required_negative.pdf",
    "r7-office_same_geometry_after_total.pdf",
    "r7-office_count_in_second_column.pdf",
])
def test_r7_real_pdf_negative_cases_keep_device_fragment_separate(name):
    """六份真实 PDF 中的五个负例：设备片段独立保留，不并入 FIN_05。"""
    pdf = Path(__file__).resolve().parents[1] / "outputs" / "zcode_rework3_20261121" / "env" / "input" / name
    if not pdf.exists():  # 单测环境可能没有样本目录
        pytest.skip(f"样本不存在：{name}")
    artifact = _parsed_pdf_table(pdf)
    fin_rows = [row for table in artifact.table_fragments
                if table.table_code == "FIN_05_general_public_expenditure"
                for row in table.rows]
    joined = re.sub(r"\s+", "", "".join(str(cell) for row in fin_rows for cell in row))
    assert "办公桌" not in joined and "电脑" not in joined
    unknown = [table for table in artifact.table_fragments
               if table.table_code.startswith("UNKNOWN_")]
    assert unknown, "设备片段应独立保留"
    unknown_text = "".join(str(cell) for table in unknown for row in table.rows for cell in row)
    assert "办公桌" in unknown_text or "电脑" in unknown_text


def test_r7_real_pdf_genuine_continuation_still_merges():
    pdf = (Path(__file__).resolve().parents[1] / "outputs" / "zcode_rework3_20261121"
           / "env" / "input" / "r7-genuine.pdf")
    if not pdf.exists():
        pytest.skip("样本不存在：r7-genuine.pdf")
    artifact = _parsed_pdf_table(pdf)
    fin_tables = [table for table in artifact.table_fragments
                  if table.table_code == "FIN_05_general_public_expenditure"]
    assert len(fin_tables) == 1
    joined = [[str(cell) for cell in row] for row in fin_tables[0].rows]
    assert ["2050201", "学前教育", "60", "50", "10"] in joined
    assert ["2050202", "小学教育", "40", "30", "10"] in joined


# ---------------------------------------------------------------------------
# 回归补充（GPT-6.2 对抗检查）：R7 / R2 / R4 / P24
# ---------------------------------------------------------------------------

def test_r7_top_closed_subtotal_and_open_nonfiscal_device_fragments_stay_separate():
    """R7 对抗场景：顶部已平衡合计、本页小计、无合计行后接同几何设备清单均不得并入。"""
    parser = PDFParser(None)
    top_closed = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["", "合计", "100", "80", "20"],
         ["2160250", "事业运行", "100", "80", "20"]],
        1, title="一般公共预算财政拨款支出决算表",
    )
    assert parser._is_continuation_table(
        top_closed, _table("UNKNOWN_P2_T0", 2, [["办公桌", "台", "3", "1", "2"]], 0)
    ) is False
    assert parser._is_continuation_table(
        top_closed, _table("UNKNOWN_P2_T0", 2, [["301", "办公桌", "3", "1", "2"]], 0)
    ) is False

    subtotal_closed = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["2160250", "事业运行", "100", "80", "20"],
         ["", "本页小计", "100", "80", "20"]],
        1, title="一般公共预算财政拨款支出决算表",
    )
    assert parser._row_is_total_close(["", "本页小计", "100", "80", "20"]) is True
    assert parser._is_continuation_table(
        subtotal_closed, _table("UNKNOWN_P2_T0", 2, [["办公桌", "台", "3", "1", "2"]], 0)
    ) is False

    open_fin05 = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["20502", "普通教育", "100", "80", "20"]],
        1, title="一般公共预算财政拨款支出决算表",
    )
    assert parser._is_continuation_table(
        open_fin05, _table("UNKNOWN_P2_T0", 2, [["办公桌", "300", "3", "1", "2"]], 0)
    ) is False
    assert parser._is_continuation_table(
        open_fin05, _table("UNKNOWN_P2_T0", 2, [["办公桌", "台", "3", "1", "2"]], 0)
    ) is False
    assert parser._is_continuation_table(
        open_fin05, _table("UNKNOWN_P2_T0", 2, [["301", "办公桌", "3", "1", "2"]], 0)
    ) is False
    assert parser._is_continuation_table(
        open_fin05, _table("UNKNOWN_P2_T0", 2, [["2050201", "学前教育", "60", "50", "10"]], 0)
    ) is True


def _build_disclosure_doc(text_pages, facts, *, table_rows=None, text_corrections=()):
    from src.engine.fact_context import FactContext
    from src.engine.pipeline import build_document
    from src.schemas.parse_artifact import ParseArtifact

    file_hash = "b" * 64
    rows = table_rows or [["208", "社会保障和就业支出", "5,465.41"],
                          ["20805", "行政事业单位养老支出", "5,465.41"],
                          ["2080501", "归口管理的行政单位离退休", "5,465.41"]]
    cells = [
        ParsedCell(r_idx, c_idx, str(val), str(val), None, 2,
                   (10.0 * c_idx, 10.0 * r_idx, 10.0 * c_idx + 9.0, 10.0 * r_idx + 9.0),
                   False, 1.0, "万元", "lines")
        for r_idx, row in enumerate(rows)
        for c_idx, val in enumerate(row)
    ]
    table = ParsedTable(
        table_code="FIN_05_general_public_expenditure",
        title="一般公共预算财政拨款支出决算表",
        page_number=2, table_index=0, bbox=(0.0, 0.0, 100.0, 100.0),
        header_row_count=0, confidence=1.0, extraction_method="lines",
        unit_hint="万元", rows=rows, cells=cells, fragment_uid="fin05-test",
    )
    doc = build_document("rework3.pdf", text_pages, [[], []], 0)
    doc.report_kind = "final"
    doc.fact_receipts = []
    doc.text_corrections = list(text_corrections)
    doc.parse_artifact = ParseArtifact(
        source_file_hash=file_hash, parse_run_uid="run-test", physical_fragments=[table]
    )
    doc.fact_context = FactContext(
        facts, file_hash, "run-test", org_name="甲单位", fiscal_year=2023, report_kind="final"
    )
    return doc, file_hash


def _test_fact(code, name, raw, file_hash, *, row_idx=2):
    from src.schemas.parse_artifact import EvidenceRef, FiscalFact

    return FiscalFact(
        fact_uid=f"fact:{code}:total_actual",
        indicator="total_actual",
        raw_value=raw,
        unit="万元",
        normalized_value=Decimal(raw.replace(",", "")),
        org_name="甲单位",
        fiscal_year=2023,
        period="本年",
        fund_scope="general_public_budget",
        budget_stage="决算",
        subject_code=code,
        subject_name=name,
        table_code="FIN_05_general_public_expenditure",
        instance_uid="fin05-test",
        extra_dims={
            "evidence_refs": [
                EvidenceRef(
                    file_hash=file_hash,
                    physical_page=2,
                    source_cell_id=f"fin05-test:r{row_idx}c2",
                    quote=raw,
                    bbox=(20.0, 10.0 * row_idx, 29.0, 10.0 * row_idx + 9.0),
                    location_level="exact_cell",
                ).to_dict()
            ]
        },
    )


def test_r2_direct_unit_inherited_receipt_and_cmm011_note_separate_original_and_accounting_unit():
    """R2 正常上传直接核对：回执披露表内科目单位依据，CMM-011 附注不再写成“正文…万元”。"""
    from src.engine.native_final_disclosure import check_function_disclosures
    from src.engine.native_issue_dedup import _annotate_unit_recoveries
    from src.engine.rules_v33 import R33222_Narrative5_T5

    narrative = (
        "甲单位\n2023年度部门决算\n"
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出决算为5,465.41万元。其中：\n"
        "1、社会保障和就业支出（类）行政事业单位养老支出（款）归口管理的行政单位离退休（项）。"
        "年初预算为5,465.41万元，支出决算为5,455.41。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明\n"
    )
    table_page = (
        "一般公共预算财政拨款支出决算表\n单位：万元\n"
        "208 社会保障和就业支出 5,465.41\n"
        "20805 行政事业单位养老支出 5,465.41\n"
        "2080501 归口管理的行政单位离退休 5,465.41\n"
    )
    file_hash = "b" * 64
    facts = [
        _test_fact("total", "合计", "5,465.41", file_hash, row_idx=0),
        _test_fact("208", "社会保障和就业支出", "5,465.41", file_hash, row_idx=0),
        _test_fact("20805", "行政事业单位养老支出", "5,465.41", file_hash, row_idx=1),
        _test_fact("2080501", "归口管理的行政单位离退休", "5,465.41", file_hash, row_idx=2),
    ]
    doc, _ = _build_disclosure_doc([narrative, table_page], facts)
    issues = check_function_disclosures(R33222_Narrative5_T5(), doc)
    fail_receipts = [
        r for r in doc.fact_receipts
        if r.get("rule_id") == "V33-222"
        and str(r.get("sub_check_id") or "").startswith("general:text:item:")
        and r.get("status") == "fail"
    ]
    assert len(fail_receipts) == 1
    sources = fail_receipts[0]["disclosure_sources"]
    basis_entries = [s for s in sources if s.get("role") == "同科目原表单位依据"]
    assert len(basis_entries) == 1
    entry = basis_entries[0]
    assert entry["original_unit_written"] is False
    assert entry["original_unit"] is None
    assert entry["accounting_unit"] == "万元"
    assert entry["accounting_unit_source"] == "原表同科目单位依据恢复"
    assert entry["subject_basis"]["subject_code"] == "2080501"
    assert issues and "原文未写单位" in issues[0].message

    # CMM-011 附加核对结论同样不得把缺单位原数写成“正文5,455.41万元”
    cmm_issues = [{
        "rule_id": "CMM-011",
        "message": "第1页金额“5,455.41”缺少单位“万元”",
        "location": {"page": 1},
    }]
    v33_251_receipt = [{
        "rule_id": "V33-251",
        "status": "fail",
        "calculation_evidence": {
            "included_count": 1,
            "subtotal_wan": "5455.41",
            "difference_wan": "-10.00",
            "included": [{
                "page": 1,
                "raw": "5,455.41",
                "span": "支出决算为5,455.41。",
                "unit_original": None,
                "value_wan": "5455.41",
                "basis": {
                    "role": "same_subject_table_basis",
                    "page": 2,
                    "table_code": "FIN_05_general_public_expenditure",
                    "subject_code": "2080501",
                    "subject_name": "归口管理的行政单位离退休",
                    "quote": "5,465.41",
                    "normalized_value": "5465.41",
                    "unit": "万元",
                },
            }],
        },
    }]
    _annotate_unit_recoveries(cmm_issues, v33_251_receipt)
    note = cmm_issues[0]["location"]["verified_by"]["note"]
    assert "正文5,455.41万元" not in note
    assert "正文披露5,455.41（未写单位，按万元核算）少10.00万元" in note


@pytest.mark.parametrize("extra_corrections", [
    [
        {
            "correction_id": "cor-code",
            "target_id": "tgt-1",
            "field": "subject_code",
            "value": "2011350",
            "page": 1,
            "pos": 0,
            "span": "支出决算为5,465.41万元",
            "actor": "tester",
            "analysis_basis_token": "job:b1",
            "reason": "指定编码",
        },
        {
            "correction_id": "cor-name",
            "target_id": "tgt-1",
            "field": "subject_name",
            "value": "住房公积金",
            "page": 1,
            "pos": 0,
            "span": "支出决算为5,465.41万元",
            "actor": "tester",
            "analysis_basis_token": "job:b1",
            "reason": "矛盾名称",
        },
    ],
    [
        {
            "correction_id": "cor-name-only",
            "target_id": "tgt-1",
            "field": "subject_name",
            "value": "住房公积金",
            "page": 1,
            "pos": 0,
            "span": "支出决算为5,465.41万元",
            "actor": "tester",
            "analysis_basis_token": "job:b1",
            "reason": "仅传矛盾名称",
        },
    ],
])
def test_r4_subject_identity_conflict_keeps_sources_and_avoids_rule_no_reason(extra_corrections):
    """R4 科目指定矛盾（编码+名称冲突 或 仅名称冲突）：保留原文证据且不退回 rule_no_reason。"""
    from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
    from src.engine.native_final_disclosure import check_function_disclosures
    from src.engine.rule_outcome import RuleOutcomeSignal
    from src.engine.rules_v33 import R33222_Narrative5_T5

    narrative = (
        "甲单位\n2023年度部门决算\n"
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出决算为5,465.41万元。其中：\n"
        "1、一般公共服务支出（类）商贸事务（款）事业运行（项）。"
        "年初预算为5,465.41万元，支出决算为5,465.41万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明\n"
    )
    table_page = (
        "一般公共预算财政拨款支出决算表\n单位：万元\n"
        "201 一般公共服务支出 5,465.41\n"
        "20113 商贸事务 5,465.41\n"
        "2011350 事业运行 5,465.41\n"
    )
    file_hash = "b" * 64
    facts = [
        _test_fact("total", "合计", "5,465.41", file_hash, row_idx=0),
        _test_fact("201", "一般公共服务支出", "5,465.41", file_hash, row_idx=0),
        _test_fact("20113", "商贸事务", "5,465.41", file_hash, row_idx=1),
        _test_fact("2011350", "事业运行", "5,465.41", file_hash, row_idx=2),
    ]
    sentence = "支出决算为5,465.41万元"
    pos = narrative.rfind(sentence)
    corrections = [{**c, "pos": pos} for c in extra_corrections]
    table_rows = [
        ["201", "一般公共服务支出", "5,465.41"],
        ["20113", "商贸事务", "5,465.41"],
        ["2011350", "事业运行", "5,465.41"],
    ]

    doc, _ = _build_disclosure_doc(
        [narrative, table_page], facts, table_rows=table_rows, text_corrections=corrections
    )
    with pytest.raises(RuleOutcomeSignal):
        check_function_disclosures(R33222_Narrative5_T5(), doc)
    item_receipts = [
        r for r in doc.fact_receipts
        if r.get("rule_id") == "V33-222"
        and str(r.get("sub_check_id") or "").startswith("general:text:item:")
    ]
    assert len(item_receipts) == 1
    r = item_receipts[0]
    assert r["status"] == "insufficient_data"
    assert "code_name_conflict" in (r.get("reason_codes") or [])
    assert "rule_no_reason" not in (r.get("reason_codes") or [])
    assert r.get("disclosure_sources"), "冲突待核回执必须保留 disclosure_sources"
    assert any("5,465.41" in str(s.get("span") or "") for s in r["disclosure_sources"])

    # V33-251 分项加总同样将这笔标为 subject_identity_unresolved
    doc_sum, _ = _build_disclosure_doc(
        [narrative, table_page], facts, table_rows=table_rows, text_corrections=corrections
    )
    try:
        R33251_FunctionalDetailCompleteness().apply(doc_sum)
    except RuleOutcomeSignal:
        pass
    v251 = [r for r in doc_sum.fact_receipts if r.get("sub_check_id") == "V33-251:final:item_sum"]
    assert len(v251) == 1
    not_included = (v251[0].get("calculation_evidence") or {}).get("not_included") or []
    assert any(row.get("not_included_reason") == "subject_identity_unresolved" for row in not_included)


def test_p24_unfinished_group_binds_parent_class_issue_without_cross_subject_jump():
    """第 24 页同页两个未闭环项：文化产业发展专项支出绑定其 207 类问题，不跨跳到同页 2080501。"""
    from src.services.unfinished_check_groups import build_unfinished_check_groups

    checks = [
        {
            "id": "V33-222:general:text:item:1",
            "title": "一般公共预算支出决算说明与T5核对",
            "subject_name": "文化产业发展专项支出",
            "detail": "说明第24页「文化产业发展专项支出」找不到对应编码：类级名称不一致（表格“文化旅游体育与传媒支出”，说明“文化体育与传媒支出”）",
            "missing_operands": [{"page": 24, "row_key": "文化产业发展专项支出"}],
        },
        {
            "id": "V33-222:general:text:item:2",
            "title": "一般公共预算支出决算说明与T5核对",
            "subject_name": "归口管理的行政单位离退休",
            "detail": "说明第24页「归口管理的行政单位离退休」未能在T5确认",
            "missing_operands": [{"page": 24, "row_key": "归口管理的行政单位离退休"}],
        },
    ]
    findings = [
        {
            "id": "issue-207",
            "rule_id": "V33-227",
            "severity": "warn",
            "message": "说明5类级科目名称与T5不一致（207）：表格“文化旅游体育与传媒支出”，说明“文化体育与传媒支出”",
            "location": {
                "page": 24,
                "pages": [24],
                "code_level": "类",
                "actual_name": "文化体育与传媒支出",
                "table_refs": [
                    {"page": 24, "span": "5、文化体育与传媒支出（类）其他文化体育与传媒支出（款）\n文化产业发展专项支出（项）"}
                ],
            },
        },
        {
            "id": "issue-20799",
            "rule_id": "V33-227",
            "severity": "warn",
            "message": "说明5款级科目名称与T5不一致（207-99）：表格“其他文化旅游体育与传媒支出”，说明“其他文化体育与传媒支出”（应以表格“其他文化旅游体育与传媒支出”为准）",
            "location": {
                "page": 24,
                "pages": [24],
                "code_level": "款",
                "actual_name": "其他文化体育与传媒支出",
                "table_refs": [
                    {"page": 24, "span": "5、文化体育与传媒支出（类）其他文化体育与传媒支出（款）\n文化产业发展专项支出（项）"}
                ],
            },
        },
        {
            "id": "issue-201-suffix",
            "rule_id": "V33-227",
            "severity": "low",
            "message": "说明5类级科目名称与T5不一致（201）：表格“一般公共服务支出”，说明“一般公共服务”（名称多/少“支出/决算”等字；涉及说明第3、4项）",
            "location": {
                "page": 24,
                "pages": [24],
                "code_level": "类",
                "actual_name": "一般公共服务",
                "table_refs": [
                    {"page": 24, "span": "5、文化体育与传媒支出（类）其他文化体育与传媒支出（款）\n文化产业发展专项支出（项）"}
                ],
            },
        },
        {
            "id": "issue-2080501",
            "rule_id": "V33-227",
            "severity": "warn",
            "message": "说明5科目名称与T5不一致：归口管理的行政单位离退休",
            "location": {
                "page": 24,
                "pages": [24],
                "actual_name": "归口管理的行政单位离退休",
                "table_refs": [{"page": 24, "span": "归口管理的行政单位离退休（项）"}],
            },
        },
    ]
    groups = build_unfinished_check_groups(checks, {"FIN_05_general_public_expenditure": "一般公共预算财政拨款支出决算表"}, findings=findings)
    by_subject = {g["subject"]: g for g in groups}
    assert by_subject["文化产业发展专项支出"]["related_issue_ids"] == ["issue-207", "issue-20799"]
    assert by_subject["文化产业发展专项支出"]["affected_check_count"] == 1
    assert by_subject["归口管理的行政单位离退休"]["related_issue_ids"] == ["issue-2080501"]


def test_r7_open_fin05_rejects_inrange_inventory_with_or_without_unit_and_keeps_wrapped_code():
    """R7 合并复核补充：未收口明细表不得并入 217/218 实物清单（带或不带（台）），折行编码仍可并表。"""
    parser = PDFParser(None)
    open_fin05 = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["216025", "0事业运行", "100", "80", "20"]],
        1, title="一般公共预算财政拨款支出决算表",
    )
    # 1. 带量词后缀：217 办公桌（台）、218 电脑（台）
    with_unit = _table(
        "UNKNOWN_P2_T0", 2,
        [["217", "办公桌（台）", "3", "1", "2"], ["218", "电脑（台）", "4", "2", "2"]],
        0,
    )
    assert parser._is_continuation_table(open_fin05, with_unit) is False

    # 2. 对抗变体（去掉（台））：前表含 7 位明细行 2160250，后片两条连续非零 3 位类码无任何款项子行
    no_unit = _table(
        "UNKNOWN_P2_T0", 2,
        [["217", "办公桌", "3", "1", "2"], ["218", "电脑", "4", "2", "2"]],
        0,
    )
    assert parser._is_continuation_table(open_fin05, no_unit) is False

    # 2b. 对抗变体（无单位单行）：前表含 7 位明细行 2160250，后片仅有「217 办公桌 3 1 2」
    one_row_no_unit = _table(
        "UNKNOWN_P2_T0", 2,
        [["217", "办公桌", "3", "1", "2"]],
        0,
    )
    assert parser._is_continuation_table(open_fin05, one_row_no_unit) is False

    # 3. 折行 7 位科目编码 21000\n00（合法续表）不得误杀
    wrapped_valid = _table(
        "UNKNOWN_P2_T0", 2,
        [["21000\n00", "卫生健康", "50", "30", "20"]],
        0,
    )
    assert parser._is_continuation_table(open_fin05, wrapped_valid) is True

    # 4. 独立复核真实样本 audit-extra-open-inrange.pdf 与 audit-inrange-one-row.pdf（如存在）：设备片段必须与 FIN_05 分离
    root_out = Path(__file__).resolve().parents[1] / "outputs"
    for sample in (
        root_out / "independent_round3_final_43x14hoi" / "r7-inputs" / "audit-extra-open-inrange.pdf",
        root_out / "independent_signoff_43y07bdz" / "dual" / "audit-inrange-one-row.pdf",
        root_out / "independent_r7final_gre9gudu" / "dual" / "audit-single-after-class-only.pdf",
    ):
        if sample.exists():
            artifact = _parsed_pdf_table(sample)
            fin_rows = [row for table in artifact.table_fragments
                        if table.table_code == "FIN_05_general_public_expenditure"
                        for row in table.rows]
            joined = re.sub(r"\s+", "", "".join(str(cell) for row in fin_rows for cell in row))
            assert "办公桌" not in joined and "电脑" not in joined, f"{sample.name} 泄漏进 FIN_05"
            unknown = [table for table in artifact.table_fragments
                       if table.table_code.startswith("UNKNOWN_")]
            assert unknown, f"{sample.name} 的设备片段应独立保留"


def test_r7_class_only_prefix_rejects_nonfiscal_but_three_page_class_before_detail_merges():
    """R7 终审补充：类级前表拒绝 217 办公桌；类行在第 2 页、子项在第 3 页的三页续表合并并保全部事实。"""
    parser = PDFParser(None)

    # 1. 前表只有 3 位类码 216，后页无单位单行「217 办公桌」名码不符 → 拒绝并入
    class_only = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["216", "商业服务业等支出", "100", "80", "20"]],
        1, title="一般公共预算财政拨款支出决算表",
    )
    single_desk = _table(
        "UNKNOWN_P2_T0", 2,
        [["217", "办公桌", "3", "1", "2"]],
        0,
    )
    assert parser._is_continuation_table(class_only, single_desk) is False
    split_merged = parser._coalesce_tables([class_only, single_desk])
    assert len(split_merged) == 2

    # 2. 第 1 页明细合计 150 + 216025|0事业运行 100；第 2 页仅有 221 住房保障支出 50；第 3 页 221020|1住房公积金 50
    p1 = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["", "合计", "150", "110", "40"],
         ["216025", "0事业运行", "100", "80", "20"]],
        1, title="一般公共预算财政拨款支出决算表",
    )
    p2 = _table(
        "UNKNOWN_P2_T0", 2,
        [["221", "住房保障支出", "50", "30", "20"]],
        0, title="续表",
    )
    p3 = _table(
        "UNKNOWN_P3_T0", 3,
        [["221020", "1住房公积金", "50", "30", "20"]],
        0, title="续表",
    )
    merged_3p = parser._coalesce_tables([p1, p2, p3])
    assert len(merged_3p) == 1
    assert any(r[0] == "221" and r[1] == "住房保障支出" for r in merged_3p[0].rows)
    assert any(r[0] == "221020" and r[1] == "1住房公积金" for r in merged_3p[0].rows)

    # 3. 顶部合计非过早闭合：第 1 页同时含 216 类（100）和 2160250 项（100），第 2 页 221 类（50）后 top_sum=150==stated，
    #    因表内已有明细行而 leaf_sum=100<150，不得提前封表，第 3 页 2210201（50）仍须合并。
    p1_with_class = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["", "合计", "150", "110", "40"],
         ["216", "商业服务业等支出", "100", "80", "20"],
         ["2160250", "事业运行", "100", "80", "20"]],
        1, title="一般公共预算财政拨款支出决算表",
    )
    p3_clean = _table(
        "UNKNOWN_P3_T0", 3,
        [["2210201", "住房公积金", "50", "30", "20"]],
        0, title="续表",
    )
    merged_top_check = parser._coalesce_tables([p1_with_class, p2, p3_clean])
    assert len(merged_top_check) == 1
    assert any(r[0] == "2210201" for r in merged_top_check[0].rows)

    # 4. 真实复现 PDF（如存在）：audit-true-class-page-before-detail.pdf 合并 3 页并生成 221 与 2210201 共 6 条事实
    three_page_pdf = (Path(__file__).resolve().parents[1] / "outputs" / "independent_r7final_gre9gudu"
                      / "dual" / "audit-true-class-page-before-detail.pdf")
    if three_page_pdf.exists():
        from src.engine.fact_context import build_fact_context_from_artifact

        artifact = _parsed_pdf_table(three_page_pdf)
        assert [t.table_code for t in artifact.table_fragments] == ["FIN_05_general_public_expenditure"]
        ctx = build_fact_context_from_artifact(artifact, report_kind="final")
        facts_221 = [f for f in ctx.facts if f.subject_code in {"221", "2210201", "221020"}]
        assert len(facts_221) == 6
        assert any(f.subject_code == "221" and f.indicator == "total_actual" and str(f.raw_value) == "50" for f in facts_221)
        assert any(f.subject_code in {"2210201", "221020"} and f.indicator == "total_actual" and str(f.raw_value) == "50" for f in facts_221)


def test_r7_split_code_headers_on_total_row_and_class_220_continuation_merge():
    """R7 真实材料回归：类/款/项落在第4行（合计行）与 220 自然资源海洋气象等支出续页均须正常并表。"""
    parser = PDFParser(None)

    # 1. 普陀区财政局 2024 决算模板：header_row_count=3，第4行（row 3）才是「类｜款｜项｜合计」
    fin02 = _table(
        "FIN_02_income", 8,
        [
            ["项目", "", "", "", "本年收入合计", "财政拨款收入"],
            ["支出功能分类", "", "", "科目名称", "", ""],
            ["科目编码", "", "", "", "", ""],
            ["类", "款", "项", "合计", "2,946.25", "2,940.05"],
            ["201", "", "", "一般公共服务支出", "2,112.86", "2,106.66"],
        ],
        3,
        widths=(40, 40, 40, 140, 70, 70),
        title="收入决算表",
    )
    fin02_p9 = _table(
        "UNKNOWN_P9_T0", 9,
        [
            ["210", "11", "01", "行政单位医疗", "60.99", "60.99"],
            ["221", "", "", "住房保障支出", "445.69", "445.69"],
        ],
        0,
        widths=(40, 40, 40, 140, 70, 70),
    )
    merged_putuo = parser._coalesce_tables([fin02, fin02_p9])
    assert len(merged_putuo) == 1
    assert ["210", "11", "01", "行政单位医疗", "60.99", "60.99"] in merged_putuo[0].rows

    # 2. 220 自然资源海洋气象等支出在续页首出现不得误杀
    fin05_213 = _table(
        "FIN_05_general_public_expenditure", 1,
        [
            ["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
            ["2130399", "其他水利支出", "190,553.95", "0.00", "190,553.95"],
        ],
        1,
        title="一般公共预算财政拨款支出决算表",
    )
    fin05_220 = _table(
        "UNKNOWN_P2_T0", 2,
        [
            ["220", "自然资源海洋气象等支出", "47,846.16", "3,123.68", "44,722.49"],
            ["22001", "自然资源事务", "47,846.16", "3,123.68", "44,722.49"],
        ],
        0,
    )
    merged_220 = parser._coalesce_tables([fin05_213, fin05_220])
    assert len(merged_220) == 1
    assert ["220", "自然资源海洋气象等支出", "47,846.16", "3,123.68", "44,722.49"] in merged_220[0].rows

    # 3. 仓库自带《上海市普陀区财政局2024年度部门决算.pdf》：p8-9、p10-11 合并且生成 193 条事实
    putuo_pdf = (Path(__file__).resolve().parents[1] / "samples" / "good"
                 / "上海市普陀区财政局2024年度部门决算.pdf")
    if putuo_pdf.exists():
        from src.engine.fact_context import build_fact_context_from_artifact

        artifact = _parsed_pdf_table(putuo_pdf)
        by_code = {t.table_code: t for t in artifact.table_fragments}
        assert sorted({c.page_number for c in by_code["FIN_02_income"].cells}) == [8, 9]
        assert sorted({c.page_number for c in by_code["FIN_03_expenditure"].cells}) == [10, 11]
        ctx = build_fact_context_from_artifact(artifact, report_kind="final")
        assert len(ctx.facts) == 193
        assert any(
            f.table_code == "FIN_02_income" and f.subject_code == "2101101"
            and f.indicator == "total_actual" and str(f.raw_value) == "60.99"
            for f in ctx.facts
        )
        assert any(
            f.table_code == "FIN_03_expenditure" and f.subject_code == "2210201"
            and f.indicator == "total_actual" and str(f.raw_value) == "187.19"
            for f in ctx.facts
        )


@pytest.mark.parametrize(
    "code_headers", [("科目编码", "", ""), ("类", "款", "项")],
    ids=["single-code-header", "split-code-header"],
)
@pytest.mark.parametrize("asset_code", ["216", "217"], ids=["same-class-prefix", "next-class-prefix"])
@pytest.mark.parametrize("aligned", [True, False], ids=["aligned-columns", "sequence-fallback"])
def test_r7_headerless_split_asset_codes_stay_in_a_separate_fragment(code_headers, asset_code, aligned, monkeypatch):
    parser = PDFParser(None)
    widths = (40, 40, 40, 140, 70, 70, 70)
    first = _table(
        "FIN_05_general_public_expenditure", 1,
        [[*code_headers, "科目名称", "合计", "基本支出", "项目支出"],
         ["", "", "", "合计", "150", "110", "40"],
         ["216", "", "", "商业服务业等支出", "100", "80", "20"]],
        1, widths=widths, title="一般公共预算财政拨款支出决算表",
    )
    equipment = _table(
        "UNKNOWN_P2_T0", 2,
        [[asset_code, "01", "01", "办公桌", "3", "1", "2"]],
        0, widths=widths,
    )
    if not aligned:
        monkeypatch.setattr(parser, "_native_continuation_columns_match", lambda *_: False)
    fragments = parser._coalesce_tables([first, equipment])
    assert [table.table_code for table in fragments] == [first.table_code, equipment.table_code]


@pytest.mark.parametrize(
    ("preceding_row", "item_row"),
    [
        (["216", "", "", "商业服务业等支出", "100", "80", "20"],
         ["221", "02", "01", "住房公积金", "50", "30", "20"]),
        (["208", "05", "", "行政事业单位养老支出", "100", "80", "20"],
         ["208", "05", "99", "其他行政事业单位养老支出", "50", "30", "20"]),
    ],
    ids=["class-name-evidence", "preceding-parent-row"],
)
def test_r7_genuine_headerless_split_item_keeps_its_fiscal_row(preceding_row, item_row):
    parser = PDFParser(None)
    widths = (40, 40, 40, 140, 70, 70, 70)
    first = _table(
        "FIN_05_general_public_expenditure", 1,
        [["类", "款", "项", "科目名称", "合计", "基本支出", "项目支出"],
         ["", "", "", "合计", "150", "110", "40"],
         preceding_row],
        1, widths=widths, title="一般公共预算财政拨款支出决算表",
    )
    continuation = _table("UNKNOWN_P2_T0", 2, [item_row], 0, widths=widths)
    merged = parser._coalesce_tables([first, continuation])
    assert len(merged) == 1
    assert item_row in merged[0].rows
