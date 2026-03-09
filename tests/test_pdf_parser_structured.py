"""
Tests for structured PDF parser helpers.
"""

from src.services.pdf_parser import ParsedCell, ParsedTable
from unittest.mock import AsyncMock

import pytest

from src.services.pdf_parser import PDFParser


@pytest.fixture
def parser():
    return PDFParser(AsyncMock())


def test_parse_numeric_handles_formatted_values(parser):
    assert parser._parse_numeric("1,234.50") == 1234.5
    assert parser._parse_numeric("（25）") == -25.0
    assert parser._parse_numeric("(25)") == -25.0
    assert parser._parse_numeric("25万元") == 25.0
    assert parser._parse_numeric("—") is None
    assert parser._parse_numeric("") is None


def test_detect_header_row_count_prefers_text_rows(parser):
    rows = [
        ["收入支出决算总表", "", ""],
        ["项目", "本年收入", "本年支出"],
        ["一般公共预算财政拨款", "100", "80"],
        ["事业收入", "20", "0"],
    ]

    assert parser._detect_header_row_count(rows) == 2


def test_score_table_prefers_dense_numeric_tables(parser):
    rich_rows = [
        ["项目", "合计", "基本支出", "项目支出"],
        ["教育支出", "100", "60", "40"],
        ["文化旅游体育与传媒支出", "50", "20", "30"],
    ]
    poor_rows = [["备注"], ["无"]]

    assert parser._score_table(rich_rows) > parser._score_table(poor_rows)


def test_coalesce_tables_merges_consecutive_same_code(parser):
    first = ParsedTable(
        table_code="FIN_05_general_public_expenditure",
        title="一般公共预算支出功能分类预算表",
        page_number=20,
        table_index=0,
        bbox=(0, 0, 100, 100),
        header_row_count=3,
        confidence=0.9,
        extraction_method="lines",
        unit_hint="元",
        rows=[
            ["项目", "合计", "基本支出", "项目支出"],
            ["类", "款", "项", ""],
            ["201", "", "", "一般公共服务支出"],
            ["201", "05", "07", "专项普查活动"],
        ],
        cells=[
            ParsedCell(0, 0, "项目", "项目", None, 20, None, True, 0.9, "元", "lines"),
            ParsedCell(3, 0, "201", "201", 201.0, 20, None, False, 0.9, "元", "lines"),
        ],
    )
    second = ParsedTable(
        table_code="FIN_05_general_public_expenditure",
        title="项目 一般公共预算支出",
        page_number=21,
        table_index=0,
        bbox=(0, 0, 100, 100),
        header_row_count=3,
        confidence=0.88,
        extraction_method="lines",
        unit_hint="元",
        rows=[
            ["项目", "合计", "基本支出", "项目支出"],
            ["类", "款", "项", ""],
            ["204", "", "", "公共安全支出"],
        ],
        cells=[
            ParsedCell(2, 0, "204", "204", 204.0, 21, None, False, 0.88, "元", "lines"),
        ],
    )

    merged = parser._coalesce_tables([first, second])

    assert len(merged) == 1
    assert merged[0].table_code == "FIN_05_general_public_expenditure"
    assert len(merged[0].rows) == 5
    assert any(cell.row_idx == 4 and cell.raw_text == "204" for cell in merged[0].cells)


def test_coalesce_tables_attaches_unknown_continuation(parser):
    first = ParsedTable(
        table_code="FIN_06_basic_expenditure",
        title="一般公共预算基本支出部门预算经济分类预算表",
        page_number=27,
        table_index=0,
        bbox=(0, 0, 100, 100),
        header_row_count=3,
        confidence=0.95,
        extraction_method="lines",
        unit_hint="元",
        rows=[
            ["项目", "合计", "人员经费", "公用经费"],
            ["类", "款", "", ""],
            ["301", "", "工资福利支出", "90"],
        ],
        cells=[ParsedCell(2, 0, "301", "301", 301.0, 27, None, False, 0.95, "元", "lines")],
    )
    second = ParsedTable(
        table_code="UNKNOWN_P28_T0",
        title="302 05 水费 106,000 0 106,000",
        page_number=28,
        table_index=0,
        bbox=(0, 0, 100, 100),
        header_row_count=1,
        confidence=0.91,
        extraction_method="text",
        unit_hint="元",
        rows=[
            ["302", "05", "水费", "106000", "0", "106000"],
            ["302", "06", "电费", "325000", "0", "325000"],
        ],
        cells=[ParsedCell(0, 0, "302", "302", 302.0, 28, None, False, 0.91, "元", "text")],
    )

    merged = parser._coalesce_tables([first, second])

    assert len(merged) == 1
    assert merged[0].table_code == "FIN_06_basic_expenditure"
    assert len(merged[0].rows) == 5


def test_coalesce_tables_attaches_unknown_continuation_with_repeated_headers(parser):
    first = ParsedTable(
        table_code="FIN_05_general_public_expenditure",
        title="2025年长征镇一般公共预算支出执行情况表",
        page_number=2,
        table_index=0,
        bbox=(0, 0, 100, 100),
        header_row_count=1,
        confidence=0.88,
        extraction_method="lines",
        unit_hint="万元",
        rows=[
            ["项目", "年初预算数", "调整后预算数", "执行数", "执行数为调整后预算数的%"],
            ["一般公共服务支出", "16749", "15156", "15156", "100.00%"],
        ],
        cells=[ParsedCell(1, 0, "一般公共服务支出", "一般公共服务支出", None, 2, None, False, 0.88, "万元", "lines")],
    )
    second = ParsedTable(
        table_code="UNKNOWN_P3_T0",
        title="项目 年初预算数 调整后预算数 执行数 执行数为调整后预算数的%",
        page_number=3,
        table_index=0,
        bbox=(0, 0, 100, 100),
        header_row_count=2,
        confidence=0.86,
        extraction_method="lines",
        unit_hint="万元",
        rows=[
            ["项目", "年初预算数", "调整后预算数", "执行数", "执行数为调整后预算数的%"],
            ["", "", "", "", ""],
            ["教育支出", "800", "760", "760", "100.00%"],
        ],
        cells=[ParsedCell(2, 0, "教育支出", "教育支出", None, 3, None, False, 0.86, "万元", "lines")],
    )

    merged = parser._coalesce_tables([first, second])

    assert len(merged) == 1
    assert merged[0].table_code == "FIN_05_general_public_expenditure"
    assert any(cell.row_idx >= 2 and cell.raw_text == "教育支出" for cell in merged[0].cells)


def test_coalesce_tables_allows_chained_continuations_after_merge(parser):
    first = ParsedTable(
        table_code="FIN_05_general_public_expenditure",
        title="第一页",
        page_number=2,
        table_index=0,
        bbox=(0, 0, 100, 100),
        header_row_count=1,
        confidence=0.9,
        extraction_method="lines",
        unit_hint="万元",
        rows=[
            ["项目", "预算数", "执行数"],
            ["一般公共服务支出", "100", "100"],
        ],
        cells=[ParsedCell(1, 0, "一般公共服务支出", "一般公共服务支出", None, 2, None, False, 0.9, "万元", "lines")],
    )
    second = ParsedTable(
        table_code="FIN_05_general_public_expenditure",
        title="第二页",
        page_number=3,
        table_index=0,
        bbox=(0, 0, 100, 100),
        header_row_count=1,
        confidence=0.9,
        extraction_method="lines",
        unit_hint="万元",
        rows=[
            ["项目", "预算数", "执行数"],
            ["教育支出", "80", "80"],
        ],
        cells=[ParsedCell(1, 0, "教育支出", "教育支出", None, 3, None, False, 0.9, "万元", "lines")],
    )
    third = ParsedTable(
        table_code="UNKNOWN_P4_T0",
        title="项目 预算数 执行数",
        page_number=4,
        table_index=0,
        bbox=(0, 0, 100, 100),
        header_row_count=2,
        confidence=0.88,
        extraction_method="lines",
        unit_hint="万元",
        rows=[
            ["项目", "预算数", "执行数"],
            ["", "", ""],
            ["文化体育与传媒支出", "30", "30"],
        ],
        cells=[ParsedCell(2, 0, "文化体育与传媒支出", "文化体育与传媒支出", None, 4, None, False, 0.88, "万元", "lines")],
    )

    merged = parser._coalesce_tables([first, second, third])

    assert len(merged) == 1
    assert any(cell.page_number == 4 and cell.raw_text == "文化体育与传媒支出" for cell in merged[0].cells)


class _FakePage:
    def __init__(self, text: str, width: float = 595, height: float = 842):
        self._text = text
        self.width = width
        self.height = height

    def extract_text(self):
        return self._text


def test_build_page_text_placeholder_detects_empty_basic_expenditure_table(parser):
    page = _FakePage(
        "\n".join(
            [
                "2026年预算单位一般公共预算基本支出部门预算经济分类预算表",
                "编制单位：上海市普陀区国防动员办公室 单位：元",
                "项目 一般公共预算基本支出",
                "经济分类科目编码",
                "部门经济分类科目名称 合计 人员经费 公用经费",
                "类 款",
                "合计",
            ]
        )
    )

    placeholder = parser._build_page_text_placeholder(page, 14, [])

    assert placeholder is not None
    assert placeholder.table_code == "FIN_06_basic_expenditure"
    assert placeholder.extraction_method == "page_text_fallback"
    assert placeholder.page_number == 14


def test_score_title_line_prefers_real_title(parser):
    assert parser._score_title_line("2025年部门财政拨款收支预算总表", 0) > parser._score_title_line(
        "编制部门：上海市普陀区人民政府万里街道办事处 单位：元", 1
    )
