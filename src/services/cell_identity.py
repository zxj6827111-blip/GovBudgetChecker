"""证据格稳定标识：解析、事实与回执共同消费的纯函数。

该模块只决定来源格 ID 的拼法，属于解析产物身份的一部分；回执、诊断和
规则准入逻辑不在本模块，改动它们不得改变 ``parse_run_uid``。
"""

from __future__ import annotations

from typing import Any


def source_cell_id(table: Any, cell: Any) -> str:
    return (f"{cell.origin_fragment_uid or table.fragment_uid}:"
            f"r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}"
            f"c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}")
