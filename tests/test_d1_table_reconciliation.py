"""IR-04：D1 逐格对账必须拒绝错行/错列/错页（Codex 注入反例回归）。

旧验收器比较数字多重集合，把"合计行本年收入"与"201 类本年收入"互换
仍 9/9 PASS。这里用合成 page_texts + 合并行直接回归行身份对齐：
- 正常表 → 无 row_mismatches；
- 互换两行金额 → 必须报 row_number_sequence_mismatch；
- 行页归属错误 → 必须报 row_missing_on_page；
- 右栏科目编码不得被当成金额。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from d1_table_reconciliation import (  # noqa: E402
    _reconcile_row_alignment,
    _row_identity_and_numbers,
)


PAGE_TEXT = (
    "收入决算表\n"
    "项目 本年收入合计\n"
    "合计 100.00 100.00\n"
    "201 一般公共服务支出 60.00 60.00\n"
    "20101 行政运行 40.00 40.00\n"
)


def _rows(swap: bool = False, page: int = 7):
    total = ["合计", "100.00", "100.00"]
    item = ["201", "一般公共服务支出", "60.00", "60.00"]
    if swap:
        total[1], total[2] = item[2], item[3]
        item[2], item[3] = "100.00", "100.00"
    return [
        {"page": page, "cells": total},
        {"page": page, "cells": item},
        {"page": page, "cells": ["20101", "行政运行", "40.00", "40.00"]},
    ]


def _page_texts() -> list:
    return [""] * 6 + [PAGE_TEXT]


def test_normal_alignment_passes():
    result = _reconcile_row_alignment(_rows(), _page_texts(), [7])
    assert result["row_mismatches"] == []


def test_swapped_amounts_are_rejected():
    result = _reconcile_row_alignment(_rows(swap=True), _page_texts(), [7])
    kinds = {item["kind"] for item in result["row_mismatches"]}
    assert "row_number_sequence_mismatch" in kinds
    identities = {item["identity"] for item in result["row_mismatches"]}
    assert {"合计", "201"} <= identities


def test_wrong_page_attribution_is_rejected():
    result = _reconcile_row_alignment(_rows(page=8), _page_texts(), [7, 8])
    kinds = {item["kind"] for item in result["row_mismatches"]}
    assert "row_missing_on_page" in kinds


def test_right_lane_subject_code_is_not_an_amount():
    cells = ["301", "工资福利支出", "11,860.23", "302", "商品和服务支出", "1,844.57"]
    known = {"301", "302"}
    identity, numbers = _row_identity_and_numbers(cells, known)
    assert identity == "301"
    assert numbers == ["11860.23", "1844.57"]


# ---------------------------------------------------------------------------
# R2-02：验收对象是实际逻辑合并视图；逻辑/物理任一侧损坏都必须拒绝
# ---------------------------------------------------------------------------

def _fragment(uid, page, rows, children=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        fragment_uid=uid, page_number=page, rows=rows,
        source_fragment_uids=list(children or []),
    )


def _fake_doc(logical_rows, physical_rows):
    from types import SimpleNamespace

    physical = _fragment("p1", 7, physical_rows)
    logical = _fragment("L", 7, logical_rows, ["p1"])
    instance = SimpleNamespace(table_code="FIN_02_income", fragment_uids=["L"])
    return SimpleNamespace(parse_artifact=SimpleNamespace(
        table_fragments=[logical], physical_fragments=[physical])), [instance]


def test_logical_view_matches_its_source_fragments():
    from d1_table_reconciliation import _logical_view_with_provenance

    rows = [["合计", "100.00", "100.00"], ["201", "一般公共服务支出", "60.00", "60.00"]]
    doc, instances = _fake_doc(rows, [list(row) for row in rows])
    view, errors = _logical_view_with_provenance(doc, instances)
    assert errors == []
    assert [row["page"] for row in view] == [7, 7]
    assert view[0]["cells"][:2] == ["合计", "100.00"]


def test_damaged_logical_merged_view_is_rejected():
    from d1_table_reconciliation import _logical_view_with_provenance

    physical = [["合计", "100.00", "100.00"], ["201", "一般公共服务支出", "60.00", "60.00"]]
    logical = [["合计", "60.00", "60.00"], ["201", "一般公共服务支出", "100.00", "100.00"]]
    doc, instances = _fake_doc(logical, physical)
    _view, errors = _logical_view_with_provenance(doc, instances)
    assert any(item["kind"] == "merged_view_source_mismatch" for item in errors)


def test_damaged_physical_source_fragment_is_rejected():
    from d1_table_reconciliation import _logical_view_with_provenance

    logical = [["合计", "100.00", "100.00"], ["201", "一般公共服务支出", "60.00", "60.00"]]
    physical = [["合计", "60.00", "60.00"], ["201", "一般公共服务支出", "100.00", "100.00"]]
    doc, instances = _fake_doc(logical, physical)
    _view, errors = _logical_view_with_provenance(doc, instances)
    assert any(item["kind"] == "merged_view_source_mismatch" for item in errors)
