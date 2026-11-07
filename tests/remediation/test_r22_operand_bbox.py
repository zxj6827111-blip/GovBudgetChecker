"""R2.2 §5：缺数位置坐标（ParsedCell/blank_term 样板——取得到才写，取不到 null）。"""
from __future__ import annotations

from src.engine.diagnostic_fields import (
    _clean_bbox,
    table_missing_operand,
    text_missing_operand,
)


def test_table_operand_carries_bbox_when_available():
    operand = table_missing_operand(table_code="FIN_05_general_public_expenditure",
                                    role="total_actual/决算操作数", page=18,
                                    row_key="2130314", column="total_actual",
                                    bbox=(10.0, 20.0, 30.0, 40.0))
    assert operand["bbox"] == [10.0, 20.0, 30.0, 40.0]


def test_operand_bbox_is_null_when_unavailable():
    """坐标取不到写 null，不编造（对齐 ParsedCell/blank_term 样板纪律）。"""
    for bad in (None, [], (1, 2, 3), (1, 2, 3, 4, 5), ("a", "b", "c", "d")):
        operand = table_missing_operand(table_code="FIN_05", role="r", page=1, bbox=bad)
        assert operand["bbox"] is None, bad
    text_operand = text_missing_operand(section="决算情况说明", role="说明金额", page=32)
    assert text_operand["bbox"] is None


def test_clean_bbox_rejects_nan():
    assert _clean_bbox((float("nan"), 2.0, 3.0, 4.0)) is None
