"""扫描页/低文本页检测测试（Task 2，B-01：仅检测不 OCR）。

断言意图：
1. 正常文本 PDF 覆盖率为 1，且不产出低文本页，保证不引入误报；
2. 空文本页被标记出具体 1-based 页码，并计入 scanned_page_count；
3. 纯表格页（正文字符很少但抽到表格单元格）不被误判为扫描页；
4. 边界：0 页 PDF 覆盖率按 0 处理（宁可拦成待复核，也不算成覆盖完整）、单页、异常结构；
5. 阈值可由 SCANNED_PAGE_MIN_CHARS 覆盖，非法值回落默认 50。
"""

import pytest

from api.main import _assess_page_extraction, _count_non_empty_table_cells, _scanned_page_min_chars

FULL_TEXT_PAGE = "一般公共预算财政拨款支出预算表" * 10


def test_full_text_document_has_full_coverage():
    result = _assess_page_extraction([FULL_TEXT_PAGE] * 3, [[], [], []])
    assert result["page_count"] == 3
    assert result["page_coverage"] == 1.0
    assert result["low_text_pages"] == []
    assert result["scanned_page_count"] == 0
    assert result["text_page_count"] == 3
    assert result["ocr_applied"] is False


def test_blank_pages_are_flagged_with_1_based_page_numbers():
    # 第 2、4 页无文本层（典型扫描件）
    result = _assess_page_extraction(
        [FULL_TEXT_PAGE, "", FULL_TEXT_PAGE, "   \n  "],
        [[], [], [], []],
    )
    assert result["low_text_pages"] == [2, 4]
    assert result["scanned_pages"] == [2, 4]
    assert result["scanned_page_count"] == 2
    assert result["page_coverage"] == 0.5
    assert result["text_page_count"] == 2


def test_fully_scanned_document_has_zero_coverage():
    result = _assess_page_extraction(["", "", ""], None)
    assert result["page_coverage"] == 0.0
    assert result["scanned_page_count"] == 3
    assert result["low_text_page_count"] == 3


def test_table_only_page_is_not_treated_as_scanned():
    # 正文字符数远低于阈值，但抽到了非空表格单元格 -> 说明有文本层，不算低文本页
    page_tables = [[[["项目", "金额"], ["三公经费合计", "35.20"]]]]
    result = _assess_page_extraction(["合计"], page_tables)
    assert result["low_text_pages"] == []
    assert result["scanned_page_count"] == 0
    assert result["page_coverage"] == 1.0


def test_empty_table_cells_do_not_rescue_a_blank_page():
    # 表格结构存在但全是空单元格，不能当作有文本层
    page_tables = [[[["", None], ["  ", ""]]]]
    result = _assess_page_extraction([""], page_tables)
    assert result["low_text_pages"] == [1]
    assert result["scanned_page_count"] == 1


def test_zero_page_document_reports_zero_coverage():
    result = _assess_page_extraction([], [])
    assert result["page_count"] == 0
    assert result["page_coverage"] == 0.0
    assert result["low_text_pages"] == []
    assert result["scanned_page_count"] == 0


def test_single_page_document():
    result = _assess_page_extraction([FULL_TEXT_PAGE], [[]])
    assert result["page_count"] == 1
    assert result["page_coverage"] == 1.0


def test_page_tables_shorter_than_page_texts_is_tolerated():
    # page_tables 长度不足时不能抛异常（跨页断裂/解析部分失败的现实情况）
    result = _assess_page_extraction([FULL_TEXT_PAGE, ""], [[]])
    assert result["low_text_pages"] == [2]


@pytest.mark.parametrize("bad_input", [None, "not-a-list", 123, {"page": 1}])
def test_non_list_page_texts_is_tolerated(bad_input):
    result = _assess_page_extraction(bad_input, None)
    assert result["page_count"] == 0
    assert result["page_coverage"] == 0.0


@pytest.mark.parametrize(
    "bad_tables",
    ["not-a-list", [None], [["not-a-row"]], [[[None]]], [[123]]],
)
def test_malformed_table_structures_count_as_zero_cells(bad_tables):
    assert _count_non_empty_table_cells(bad_tables) >= 0


def test_threshold_is_configurable(monkeypatch):
    text = "预算说明" * 5  # 20 个字符
    monkeypatch.setenv("SCANNED_PAGE_MIN_CHARS", "10")
    assert _assess_page_extraction([text], [[]])["low_text_pages"] == []
    monkeypatch.setenv("SCANNED_PAGE_MIN_CHARS", "100")
    assert _assess_page_extraction([text], [[]])["low_text_pages"] == [1]


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5", "  "])
def test_invalid_threshold_falls_back_to_default(monkeypatch, raw):
    monkeypatch.setenv("SCANNED_PAGE_MIN_CHARS", raw)
    assert _scanned_page_min_chars() == 50


def test_total_text_chars_ignores_whitespace():
    result = _assess_page_extraction(["a b\nc", ""], [[], []])
    assert result["total_text_chars"] == 3
    assert result["min_chars_threshold"] == _scanned_page_min_chars()
