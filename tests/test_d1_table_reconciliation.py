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
    # R6-02：角色按列位传入（col0/col3 为编码列）。
    roles = {0: "code", 3: "code"}
    identity, numbers = _row_identity_and_numbers(cells, roles)
    # R3-03：多车道行身份包含两条车道的完整类码，金额逐位保留。
    assert identity == "301|302"
    assert numbers == ["11860.23", "1844.57"]


# ---------------------------------------------------------------------------
# R3-03：拆码（301 01）、续行名称、纯金额行必须按完整科目身份对账
# ---------------------------------------------------------------------------

SPLIT_CODE_TEXT = (
    "一般公共预算财政拨款基本支出决算表\n"
    "301 01 基本工资 100.00 302 01 办公费 10.00\n"
    "301 02 津贴补贴 200.00 302 02 印刷费 20.00\n"
)


def _split_rows(second_amount: str = "200.00"):
    return [
        {"page": 1, "cells": ["301", "01", "基本工资", "100.00",
                              "302", "01", "办公费", "10.00"]},
        {"page": 1, "cells": ["301", "02", "津贴补贴", second_amount,
                              "302", "02", "印刷费", "20.00"]},
    ]


def test_split_codes_are_distinct_identities_and_pass():
    result = _reconcile_row_alignment(_split_rows(), [SPLIT_CODE_TEXT], [1])
    assert result["row_mismatches"] == []
    assert "301:01|302:01" in result["merged_row_identities"]
    assert "301:02|302:02" in result["merged_row_identities"]


def test_split_code_amount_swap_is_rejected():
    """拆码行本身不能变成同一身份；金额互换仍必须被发现。"""
    rows = _split_rows()
    rows[0]["cells"][3], rows[1]["cells"][3] = "200.00", "100.00"
    result = _reconcile_row_alignment(rows, [SPLIT_CODE_TEXT], [1])
    kinds = {item["kind"] for item in result["row_mismatches"]}
    assert "row_number_sequence_mismatch" in kinds


WRAPPED_NAME_TEXT = (
    "财政拨款收入支出决算总表\n"
    "二十二、灾害防治及应急管理支\n"
    "0.00 0.00 0.00 0.00\n"
    "出\n"
    "二十三、其他支出 1.00 1.00 0.00 0.00\n"
)


def test_wrapped_long_row_name_keeps_identity():
    rows = [
        {"page": 1, "cells": ["二十二、灾害防治及应急管理支出",
                              "0.00", "0.00", "0.00", "0.00"]},
        {"page": 1, "cells": ["二十三、其他支出", "1.00", "1.00", "0.00", "0.00"]},
    ]
    result = _reconcile_row_alignment(rows, [WRAPPED_NAME_TEXT], [1])
    assert result["row_mismatches"] == []


def test_wrapped_row_amount_corruption_is_rejected():
    rows = [
        {"page": 1, "cells": ["二十二、灾害防治及应急管理支出",
                              "0.00", "2.00", "0.00", "0.00"]},
        {"page": 1, "cells": ["二十三、其他支出", "1.00", "1.00", "0.00", "0.00"]},
    ]
    result = _reconcile_row_alignment(rows, [WRAPPED_NAME_TEXT], [1])
    kinds = {item["kind"] for item in result["row_mismatches"]}
    assert "row_number_sequence_mismatch" in kinds


def test_numeric_only_rows_do_not_swallow_codes():
    """三公表纯金额行身份为 #numeric；带编码行身份保持完整科目。

    R6-02 起两种版式分表核验：纯金额表（无编码列）与编码表各自学习
    列角色；混排合成表不是真实版式。
    """
    numeric_text = "31.47 19.86 30.67 19.56\n"
    numeric_rows = [{"page": 1, "cells": ["31.47", "19.86", "30.67", "19.56"]}]
    numeric_result = _reconcile_row_alignment(numeric_rows, [numeric_text], [1])
    assert numeric_result["row_mismatches"] == []
    assert numeric_result["merged_row_identities"] == ["#numeric"]

    coded_text = "301 08 67.84\n"
    coded_rows = [{"page": 1, "cells": ["301", "08", "", "67.84"]}]
    coded_result = _reconcile_row_alignment(coded_rows, [coded_text], [1])
    assert coded_result["row_mismatches"] == []
    assert coded_result["merged_row_identities"] == ["301:08"]


# ---------------------------------------------------------------------------
# R4-02：整数金额不得凭形态被当成编码，金额逐位核对不得漏
# ---------------------------------------------------------------------------

def test_integer_amount_is_verified_not_eaten_as_code():
    """原件"合计 100"与合并"合计 200"必须 GAP；一致时 PASS。"""
    page = "支出决算表\n单位：万元\n项目 决算数\n合计 100"
    normal = _reconcile_row_alignment(
        [{"page": 1, "cells": ["合计", "100"]}], [page], [1])
    assert normal["row_mismatches"] == []
    assert normal["merged_row_identities"] == ["合计"]
    corrupted = _reconcile_row_alignment(
        [{"page": 1, "cells": ["合计", "200"]}], [page], [1])
    kinds = {item["kind"] for item in corrupted["row_mismatches"]}
    assert "row_number_sequence_mismatch" in kinds


def test_coded_row_integer_amount_is_verified():
    """带明确 201 科目的整数金额不得整行消失。"""
    page = "支出决算表\n201 一般公共服务支出 100"
    normal = _reconcile_row_alignment(
        [{"page": 1, "cells": ["201", "一般公共服务支出", "100"]}], [page], [1])
    assert normal["row_mismatches"] == []
    assert normal["merged_row_identities"] == ["201"]
    corrupted = _reconcile_row_alignment(
        [{"page": 1, "cells": ["201", "一般公共服务支出", "200"]}], [page], [1])
    assert corrupted["row_mismatches"]


def test_pure_integer_numeric_row_keeps_amounts():
    """无编码无标签的纯整数金额行按 #numeric 逐位核对。"""
    page = "31.47 19.86\n100 200\n"
    normal = _reconcile_row_alignment(
        [{"page": 1, "cells": ["31.47", "19.86"]},
         {"page": 1, "cells": ["100", "200"]}], [page], [1])
    assert normal["row_mismatches"] == []
    corrupted = _reconcile_row_alignment(
        [{"page": 1, "cells": ["31.47", "19.86"]},
         {"page": 1, "cells": ["100", "201"]}], [page], [1])
    assert corrupted["row_mismatches"]


def test_negative_and_zero_amounts_are_still_compared():
    """负数、零与两位小数金额同机制逐位核对，不因格式豁免。"""
    page = "合计 0.00\n201 教育支出 -5.00\n202 科技支出 100"
    normal = _reconcile_row_alignment(
        [{"page": 1, "cells": ["合计", "", "0.00"]},
         {"page": 1, "cells": ["201", "教育支出", "-5.00"]},
         {"page": 1, "cells": ["202", "科技支出", "100"]}], [page], [1])
    assert normal["row_mismatches"] == []
    corrupted = _reconcile_row_alignment(
        [{"page": 1, "cells": ["合计", "", "0.00"]},
         {"page": 1, "cells": ["201", "教育支出", "5.00"]},
         {"page": 1, "cells": ["202", "科技支出", "100"]}], [page], [1])
    assert corrupted["row_mismatches"]


# ---------------------------------------------------------------------------
# R5-02：无编码双栏表、同值编码/金额、年份形态金额按列角色与车道状态定角色
# ---------------------------------------------------------------------------

MULTILANE_TEXT = (
    "收入支出决算总表\n"
    "收入项目 金额 支出项目 金额\n"
    "本年收入A 100 本年支出A 100\n"
    "本年收入B 200 本年支出B 200\n"
    "收入总计 300 支出总计 300\n"
)


def _multilane_rows(expenditure=(100, 200)):
    return [
        {"page": 1, "cells": ["本年收入A", "100", "本年支出A", str(expenditure[0])]},
        {"page": 1, "cells": ["本年收入B", "200", "本年支出B", str(expenditure[1])]},
        {"page": 1, "cells": ["收入总计", "300", "支出总计", "300"]},
    ]


def test_no_code_multilane_keeps_all_amounts_verified():
    """无编码双栏表：6 个金额格全部进入逐位核对，不得学成编码后零比较。"""
    result = _reconcile_row_alignment(_multilane_rows(), [MULTILANE_TEXT], [1])
    assert result["row_mismatches"] == []
    assert set(result["merged_row_identities"]) == {
        "本年收入A|本年支出A", "本年收入B|本年支出B", "收入总计|支出总计"}


def test_no_code_multilane_swap_is_rejected():
    """互换两格支出金额（加总仍平衡）必须 GAP。"""
    result = _reconcile_row_alignment(
        _multilane_rows(expenditure=(200, 100)), [MULTILANE_TEXT], [1])
    kinds = {item["kind"] for item in result["row_mismatches"]}
    assert "row_number_sequence_mismatch" in kinds


def test_same_value_as_code_and_amount_split_by_lane_state():
    """301/302 是编码列身份；合计行金额 301 改 302 仍必须 GAP。"""
    page = ("支出决算表\n"
            "301 工资福利支出 100\n"
            "302 商品和服务支出 200\n"
            "合计 301\n")
    normal = _reconcile_row_alignment(
        [{"page": 1, "cells": ["301", "工资福利支出", "100"]},
         {"page": 1, "cells": ["302", "商品和服务支出", "200"]},
         {"page": 1, "cells": ["合计", "", "301"]}], [page], [1])
    assert normal["row_mismatches"] == []
    corrupted = _reconcile_row_alignment(
        [{"page": 1, "cells": ["301", "工资福利支出", "100"]},
         {"page": 1, "cells": ["302", "商品和服务支出", "200"]},
         {"page": 1, "cells": ["合计", "", "302"]}], [page], [1])
    assert corrupted["row_mismatches"]


def test_year_shaped_amounts_are_verified():
    """金额列的 2025→2026 必须被对账拒绝；标题“2025 年度…”不是金额。"""
    page = ("收入支出决算总表\n"
            "某局 2025 年度决算表\n"
            "合计 2025\n")
    normal = _reconcile_row_alignment(
        [{"page": 1, "cells": ["合计", "2025"]}], [page], [1])
    assert normal["row_mismatches"] == []
    corrupted = _reconcile_row_alignment(
        [{"page": 1, "cells": ["合计", "2026"]}], [page], [1])
    assert corrupted["row_mismatches"]


def test_midrow_lane_code_followed_by_amount_is_identity():
    """`30399 30240 税金及附加费用 20.81`：30240 是下一车道编码不是金额。"""
    page = "基本支出决算表\n30399 30240 税金及附加费用 20.81\n"
    result = _reconcile_row_alignment(
        [{"page": 1, "cells": ["30399", "其他对个人和家庭的补助", "",
                               "30240", "税金及附加费用", "20.81"]}],
        [page], [1])
    assert result["row_mismatches"] == []
    assert result["merged_row_identities"] == ["30399|30240"]


# ---------------------------------------------------------------------------
# R6-02：年份形态金额按列角色保留；行级 grounding 对回原件
# ---------------------------------------------------------------------------

YEAR_AMOUNT_TEXT = (
    "收入支出决算总表（金额角色控制）\n"
    "金额单位：万元\n"
    "收入项目 金额 支出项目 金额\n"
    "本年收入合计 3000 本年支出合计 2800\n"
    "使用非财政拨款结余 0 结余分配 0\n"
    "年初结转和结余 2025 年末结转和结余 2225\n"
    "总计 5025 总计 5025\n"
)


def _year_amount_rows(left_carry: str = "2025"):
    return [
        {"page": 1, "cells": ["本年收入合计", "3000", "本年支出合计", "2800"]},
        {"page": 1, "cells": ["使用非财政拨款结余", "0", "结余分配", "0"]},
        {"page": 1, "cells": ["年初结转和结余", left_carry,
                              "年末结转和结余", "2225"]},
        {"page": 1, "cells": ["总计", "5025", "总计", "5025"]},
    ]


def test_year_shaped_amount_in_amount_column_is_verified():
    """金额列 2025 与右侧“年末…”科目是两个车道；2025→2026 必须 GAP。"""
    normal = _reconcile_row_alignment(_year_amount_rows(), [YEAR_AMOUNT_TEXT], [1])
    assert normal["row_mismatches"] == []
    corrupted = _reconcile_row_alignment(_year_amount_rows("2026"),
                                         [YEAR_AMOUNT_TEXT], [1])
    kinds = {item["kind"] for item in corrupted["row_mismatches"]}
    assert kinds & {"row_number_sequence_mismatch", "row_grounding_mismatch"}


def test_value_elsewhere_on_page_does_not_satisfy_row():
    """R7-02：同页其他行出现同一数值不能证明本行正确（逐行身份约束）。"""
    page = ("合计 3000 合计 2800\n"
            "本年收入合计 3000 本年支出合计 2800\n")
    merged = [{"page": 1, "cells": ["本年收入合计", "3000", "本年支出合计", "9999"]}]
    result = _reconcile_row_alignment(merged, [page], [1])
    assert result["row_mismatches"], "同页存在 3000/2800 不能使本行的 9999 通过"
    kinds = {item["kind"] for item in result["row_mismatches"]}
    assert "row_missing_on_page" in kinds or "row_number_sequence_mismatch" in kinds


def test_original_row_missing_from_merged_is_reported():
    """R7-02 反向覆盖：原件有、合并视图没有的数据行必须报告。"""
    page = "合计 100 合计 200\n本年收入合计 100 本年支出合计 200\n"
    merged = [{"page": 1, "cells": ["合计", "100", "合计", "200"]}]
    result = _reconcile_row_alignment(merged, [page], [1])
    assert any(item["kind"] == "row_missing_in_merged"
               for item in result["row_mismatches"])


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
