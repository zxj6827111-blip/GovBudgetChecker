"""structured_rules 跨页合并守卫测试（GPT5.6 P0-3 整改）。

背景：merge_compatible 此前在列宽不一致时只记 parse_error 就直接
extend 合并（错位合而不重映射），且 structured_ready 以"迁移列表非空"
判定、对 9/52 的覆盖率误报可切换。本文件锁定合并守卫的三个契约：
1. 有共同语义列 + 列宽漂移 → 显式尾部对齐重映射后合并；
2. 表头/语义列不兼容 → 拒绝合并，续表独立；
3. 重映射/拒绝均留 parse_errors 供质量门与评测消费。
"""

from src.engine.structured_rules import (
    ParsedRow,
    ParsedTable,
    _remap_continuation_rows,
    merge_compatible,
)


def _table(named_columns, rows_cells, width_hint=None):
    table = ParsedTable(table_code="T1", title="测试表", named_columns=dict(named_columns))
    for cells in rows_cells:
        table.rows.append(ParsedRow(cells=[_cell(c) for c in cells]))
    return table


def _cell(value):
    from src.engine.structured_rules import ParsedCell

    return ParsedCell(text=value)

def test_remap_continuation_rows_aligns_tail_columns():
    """宽 4 的续页行重映射到宽 6：尾部金额列按行宽差对齐，前置列独立。"""
    row = ParsedRow(cells=[_cell("301"), _cell("工资福利"), _cell("40.00"), _cell("1.00")])
    remapped = _remap_continuation_rows([row], 6)
    assert len(remapped[0].cells) == 6
    # 尾部对齐：宽 4 的 j 映射到 6 的 j+2，末列必须是原行最后一列
    assert remapped[0].cells[5].text == "1.00"
    assert remapped[0].cells[4].text == "40.00"
    # 前置填充列不应携带金额
    assert remapped[0].cells[0].text in (None, "")


def test_merge_same_width_appends_without_remap():
    base = _table({"合计": 2}, [["合计", "10.00", "x"]])
    cont = _table({"合计": 2}, [["明细", "1.00", "y"]])
    assert merge_compatible(base, cont) is True
    assert len(base.rows) == 2
    assert base.parse_errors == []


def test_merge_width_drift_with_shared_columns_remaps_then_merges():
    """列宽漂移 + 有共同语义列：重映射后合并，并留 continuation_width_remapped。"""
    base = _table({"合计": 3, "基本支出": 4}, [["类", "款", "合计", "基本支出"]])
    cont = _table({"合计": 2, "基本支出": 3}, [["款", "合计", "基本支出"]])
    assert merge_compatible(base, cont) is True
    assert any("continuation_width_remapped" in e for e in base.parse_errors)
    # 合并后的行宽统一到基准宽度
    assert len(base.rows[1].cells) == len(base.rows[0].cells)


def test_merge_rejects_incompatible_headers():
    """表头语义列不相交：拒绝合并，留 continuation_header_incompatible。"""
    base = _table({"合计": 2}, [["合计", "10.00"]])
    cont = _table({"预算数": 1}, [["预算数", "5.00"]])
    assert merge_compatible(base, cont) is False
    assert any("continuation_header_incompatible" in e for e in base.parse_errors)
    assert len(base.rows) == 1


def test_merge_rejects_width_drift_without_shared_columns():
    """列宽漂移且无共同语义列（宽差无法可靠对齐）：拒绝合并。

    场景：base 与 continuation 的 named_columns 为空（表头未识别出
    语义列），列宽族不重叠 → width 分支因无共享列拒绝，续表独立。
    """
    base = _table({}, [["类", "合计", "10.00"]])  # 宽 3
    cont = _table({}, ["合计", "1.00"])  # 宽 2
    cont.rows = [ParsedRow(cells=[_cell("合计"), _cell("1.00")])]
    assert merge_compatible(base, cont) is False
    assert any(
        "continuation_width_incompatible_no_shared_columns" in e
        for e in base.parse_errors
    )
    assert len(base.rows) == 1


def test_empty_continuation_merges_trivially():
    base = _table({"合计": 1}, [["合计", "1.00"]])
    cont = ParsedTable(table_code="T1-cont", named_columns={"合计": 0})
    assert merge_compatible(base, cont) is True
    assert base.parse_errors == []
