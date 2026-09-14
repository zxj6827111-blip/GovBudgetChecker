"""PDF 表格抽取的几何元数据兼容性测试。"""

from __future__ import annotations

from typing import Any

from src.engine.structured_rules import build_parsed_tables
from src.services.pdf_page_extract import ExtractedTable, extract_tables_from_page


class _Crop:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _Table:
    def __init__(self, bbox: tuple[float, float, float, float]) -> None:
        self.bbox = bbox


class _Page:
    width = 800.0
    height = 1000.0

    def __init__(self) -> None:
        self._tables = [
            [["收入决算表", ""], ["项目", "合计"]],
            [["支出决算表", ""], ["项目", "决算数"]],
        ]
        self._objects = [
            _Table((50.0, 200.0, 750.0, 350.0)),
            _Table((50.0, 600.0, 750.0, 750.0)),
        ]

    def extract_tables(self, *, table_settings: Any = None) -> list[list[list[str]]]:
        return self._tables

    def find_tables(self, *, table_settings: Any = None) -> list[_Table]:
        return self._objects

    def crop(self, bbox: tuple[float, float, float, float]) -> _Crop:
        # crop 的 bottom 是各自 table bbox 的 top；据此模拟两个不同的
        # 标题区域，验证整页标题不会被错误复用给第二张表。
        return _Crop("收入决算表") if bbox[3] <= 200.0 else _Crop("支出决算表")


class _MismatchedPage(_Page):
    def find_tables(self, *, table_settings: Any = None) -> list[_Table]:
        return self._objects[:1]


class _WordsPage(_Page):
    def extract_words(self) -> list[dict[str, Any]]:
        return [
            {"text": "收入决算表", "x0": 200.0, "top": 150.0, "x1": 360.0, "bottom": 165.0},
            {"text": "支出决算表", "x0": 200.0, "top": 550.0, "x1": 360.0, "bottom": 565.0},
        ]

    def crop(self, bbox: tuple[float, float, float, float]) -> _Crop:
        raise AssertionError("词级 bbox 已足够绑定标题，不应退回固定 crop 窗口")


def test_extract_tables_preserves_list_contract_and_binds_anchor_to_bbox() -> None:
    tables = extract_tables_from_page(_Page())

    assert len(tables) == 2
    assert all(isinstance(table, ExtractedTable) for table in tables)
    # legacy 消费者仍可按二维 list 读取。
    assert tables[0][1][1] == "合计"
    assert tables[1][1][1] == "决算数"
    # structured 消费者得到每张表自己的 bbox/标题锚。
    assert tables[0].bbox == (50.0, 200.0, 750.0, 350.0)
    assert tables[1].bbox == (50.0, 600.0, 750.0, 750.0)
    assert tables[0].anchor_table_name == "收入决算表"
    assert tables[1].anchor_table_name == "支出决算表"


def test_extract_tables_uses_word_geometry_before_fixed_crop() -> None:
    tables = extract_tables_from_page(_WordsPage())

    assert [table.anchor_table_name for table in tables] == ["收入决算表", "支出决算表"]


def test_structured_merge_consumes_per_table_anchor_from_extractor() -> None:
    """真实抽取层的 list 包装元数据可解除混合页标题歧义。"""
    base = ExtractedTable(
        [["项目", "合计"], ["201", "100"]],
        bbox=(40.0, 100.0, 560.0, 260.0),
        anchor_table_name="支出决算表",
    )
    continuation = ExtractedTable(
        [["202", "20"]],
        bbox=(40.0, 40.0, 560.0, 180.0),
        # 续表必须由抽取层用 bbox 绑定到上一表；空锚不能作为正向证据。
        anchor_table_name="支出决算表",
    )
    new_table = ExtractedTable(
        [["项目", "决算数"], ["301", "10"]],
        bbox=(40.0, 420.0, 560.0, 620.0),
        anchor_table_name="收入决算表",
    )

    tables = build_parsed_tables(
        [[base], [continuation, new_table]],
        ["支出决算表\n", "支出决算表\n"],
    )

    assert sorted(table.page_span for table in tables.values()) == [(1, 2), (2, 2)]
    merged = next(table for table in tables.values() if table.page_span == (1, 2))
    assert merged.anchor_table_name == "支出决算表"


def test_extract_tables_drops_unproven_partial_bbox_alignment() -> None:
    tables = extract_tables_from_page(_MismatchedPage())

    assert len(tables) == 2
    assert all(table.bbox is None for table in tables)
    assert all(table.anchor_table_name == "" for table in tables)
