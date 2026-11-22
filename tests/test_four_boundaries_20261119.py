"""四类问题边界代表案例：科目、数字格式、跨页识别。"""

from __future__ import annotations

from types import SimpleNamespace

from src.engine.fiscal_lexeme import amount_in_wan, lex_numeric
from src.engine.native_final_disclosure import unit_basis_by_subject_code
from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.cell_number_text import invalid_number_text
from src.services.pdf_parser import PDFParser


def _table(code, page, rows, header, widths, title=""):
    cells = []
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            x0 = sum(widths[:col_index])
            x1 = x0 + widths[col_index]
            cells.append(ParsedCell(
                row_index, col_index, str(value), str(value), None, page,
                (x0, 100 + row_index * 20, x1, 120 + row_index * 20),
                row_index < header, 0.95, "万元", "lines",
            ))
    return ParsedTable(
        table_code=code,
        title=title,
        page_number=page,
        table_index=0,
        bbox=(0, 100, sum(widths), 100 + 20 * len(rows)),
        header_row_count=header,
        confidence=0.9,
        extraction_method="lines",
        unit_hint="万元",
        rows=rows,
        cells=cells,
        fragment_uid=f"p{page}-{code}",
    )


def test_cross_page_continuation_requires_confirmable_header_or_row_relation():
    parser = PDFParser(None)
    previous = _table(
        "FIN_05_general_public_expenditure",
        1,
        [["编码", "名称", "合计"], ["205", "教育支出", "100.00"], ["合计", "", "100.00"]],
        2,
        [50, 150, 100, 100],
        "一般公共预算财政拨款支出决算表",
    )
    # R7（2026-11-21 第三轮复审）：前页已以“合计”收口 → 本表已经结束；
    # 后页无表头片段即使首行形似科目行（301 办公桌 与 205 教育支出同形）
    # 也是另一张表——收口后不能再有无表头续页。
    aligned = _table("UNKNOWN_P2_T0", 2, [["205", "教育支出", "100.00"]], 0, [50, 150, 100, 100])
    assert parser._is_continuation_table(previous, aligned) is False
    # 可继续：前页未以合计收口，续页无表头但同宽、同一金额车道、
    # 首行是前页款级行所辖的项级科目（编码前缀连续且递增）。
    open_ended = _table(
        "FIN_05_general_public_expenditure",
        1,
        [["编码", "名称", "合计"], ["205", "教育支出", "100.00"]],
        2,
        [50, 150, 100, 100],
        "一般公共预算财政拨款支出决算表",
    )
    aligned_child = _table(
        "UNKNOWN_P2_T0", 2, [["20501", "学前教育阶段教育支出", "80.00"]], 0, [50, 150, 100, 100]
    )
    assert parser._is_continuation_table(open_ended, aligned_child) is True
    # 必须待核：前页已以“合计”收口，续页又重复完整表头 → 是另一张表，
    # 不能拿前页列头替它确认列身份；未收口前页接重复表头续页是合法续页。
    repeated_header = _table(
        "UNKNOWN_P2_T0", 2, [["编码", "名称", "合计"], ["205", "教育支出", "100.00"]],
        2, [50, 150, 100, 100],
    )
    assert parser._is_continuation_table(open_ended, repeated_header) is True
    assert parser._is_continuation_table(previous, repeated_header) is False


def test_numeric_dense_fragment_without_identity_is_not_merged():
    """R7：相邻页、列数相近、数值格很多只能算候选，不能单凭密度合并。"""
    parser = PDFParser(None)
    previous = _table(
        "FIN_05_general_public_expenditure",
        1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["2160250", "事业运行", "100", "80", "20"],
         ["合计", "", "100", "80", "20"]],
        1,
        [50, 140, 60, 60, 60],
        "一般公共预算财政拨款支出决算表",
    )
    fragment = _table(
        "UNKNOWN_P2_T0",
        2,
        [["办公桌", "台", "3", "1", "2"], ["电脑", "台", "4", "2", "2"]],
        0,
        [140, 40, 40, 30, 120],
    )
    assert parser._native_continuation_columns_match(previous, fragment) is False
    assert parser._is_continuation_table(previous, fragment) is False
    merged = parser._coalesce_tables([previous, fragment])
    assert [table.table_code for table in merged] == [
        "FIN_05_general_public_expenditure", "UNKNOWN_P2_T0",
    ]
    assert ["办公桌", "台", "3", "1", "2"] not in merged[0].rows


def test_true_continuation_is_confirmed_by_continuous_subject_rows():
    """R7：版式变化但科目编码连续时，续页仍归入原表。"""
    parser = PDFParser(None)
    previous = _table(
        "FIN_05_general_public_expenditure",
        1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["20502", "普通教育", "100", "80", "20"]],
        1,
        [50, 150, 60, 60, 60],
        "一般公共预算财政拨款支出决算表",
    )
    continuation = _table(
        "UNKNOWN_P2_T0",
        2,
        [["2050201", "学前教育", "60", "50", "10"],
         ["2050202", "小学教育", "40", "30", "10"]],
        0,
        [40, 140, 70, 70, 70],
    )
    assert parser._native_continuation_columns_match(previous, continuation) is False
    assert parser._is_continuation_table(previous, continuation) is True
    merged = parser._coalesce_tables([previous, continuation])
    assert len(merged) == 1
    assert merged[0].table_code == "FIN_05_general_public_expenditure"
    assert ["2050201", "学前教育", "60", "50", "10"] in merged[0].rows


def test_misplaced_separator_has_two_readings_and_stays_pending():
    raw = "79,85.49"
    lexeme = lex_numeric(raw)
    assert lexeme.kind == "invalid"
    assert lexeme.value is None
    assert invalid_number_text(raw) is True
    # 两种可解释读数都不被系统采用：金额恒为未知。
    assert amount_in_wan(raw, "万元") is None
    assert amount_in_wan(raw.replace(",", ""), "万元") is not None  # 7985.49
    assert amount_in_wan(raw.replace(",", "."), "万元") is None  # 79.8549 不是合法数字词


def test_unit_basis_by_subject_code_refuses_duplicate_rows():
    facts = [
        SimpleNamespace(indicator="total_actual", subject_code="2160250", budget_stage="决算",
                        unit="万元", extra_dims={}),
        SimpleNamespace(indicator="total_actual", subject_code="2160250", budget_stage="决算",
                        unit="元", extra_dims={}),
    ]
    assert unit_basis_by_subject_code(SimpleNamespace(), facts, "2160250") is None
    assert unit_basis_by_subject_code(SimpleNamespace(), facts, "216025") is None
