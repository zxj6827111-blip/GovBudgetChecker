"""D1b 列身份回归：合并父表头只落首子列时的父级身份恢复。

背景（2026-11-12 民政局批次诊断）：财政局拨款收入支出决算总表（FIN_04）的
父表头行画有竖线，"收入"在物理网格里只落首子列，"决算数"列的父段是空串，
收入侧 9 个金额格因此无列身份、5 类关系连带阻断。
"""

import json
from pathlib import Path

import pytest

from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.fiscal_column_identity import canonical_column_mappings, header_paths
from src.services.fiscal_table_rules import NINE_TABLE_RULES

FIXTURE = Path(__file__).parents[1] / "fixtures/column_identity/civil_bureau_fin04_fragment.json"


def rigid_grid(code, title, rows, header_rows, page=1, top=0, uid="t", width=10):
    """刚性网格：每个位置都有带 bbox 的格（父行画竖线的物理实况），
    合并父表头文本只写进首子列，其余子列是空占位格。"""
    cells = []
    for row_idx, row in enumerate(rows):
        for col, value in enumerate(row):
            text = value or ""
            numeric = None
            stripped = text.replace(",", "").replace(".", "")
            if stripped.isdigit():
                numeric = float(text.replace(",", ""))
            box = (col * width, top + row_idx * 10, (col + 1) * width, top + (row_idx + 1) * 10)
            cells.append(ParsedCell(row_idx, col, text, text, numeric, page, box,
                                    row_idx < header_rows, .99, "元", "lines"))
    bbox = (0, top, max(len(row) for row in rows) * width, top + len(rows) * 10)
    return ParsedTable(code, title, page, 0, bbox, header_rows, .99, "lines", "元", rows, cells, uid)


def measures(table):
    return {m["source_col_idx"]: m["canonical_measure"]
            for m in canonical_column_mappings(table, NINE_TABLE_RULES[table.table_code])}


def test_rigid_parent_row_recovers_income_ledger():
    # 民政局 FIN_04 版式：父表头行 [收入, 空, 支出, 空×4]，全部格带 bbox。
    rows = [["收入", "", "支出", "", "", "", ""],
            ["项目", "决算数", "项目", "合计", "一般公共预算财政拨款", "政府性基金预算财政拨款", "国有资本经营预算财政拨款"],
            ["一、一般公共预算", "116179.27", "一、一般公共服务支出", "120787.59", "116179.27", "4608.32", "0.00"]]
    table = rigid_grid("FIN_04_fiscal_grant_total", "财政拨款收入支出决算总表", rows, 2)
    paths = header_paths(table)
    assert paths[1] == ["收入", "决算数"], paths
    assert paths[3] == ["支出", "合计"]
    assert paths[4] == ["支出", "一般公共预算财政拨款"]
    assert measures(table) == {1: "income_actual", 3: "expenditure_actual",
                               4: "general_public_budget", 5: "government_fund_budget",
                               6: "state_capital_budget"}


def test_leaf_row_empty_cell_does_not_inherit_left_leaf():
    # 空叶格不得继承左邻叶子文本：叶子是该列自己的标签。
    rows = [["合计", "", "项目支出", "10"],
            ["一、一般公共预算", "100", "二、住房保障支出", "20"]]
    table = rigid_grid("FIN_01_income_expenditure_total", "收入支出决算总表", rows, 1)
    paths = header_paths(table)
    assert paths[0][0] == "合计" and paths[1][0] == ""


def test_no_left_text_and_preamble_untouched():
    # 左侧全空：不外推。表题行（preamble）保持空段。
    rows = [["财政拨款收入支出决算总表", "", "", ""],
            ["", "决算数", "合计", "基本支出"],
            ["一、收入", "100", "80", "20"]]
    table = rigid_grid("FIN_04_fiscal_grant_total", "财政拨款收入支出决算总表", rows, 3)
    paths = header_paths(table)
    assert paths[0][0] == ""          # preamble 行（单行横跨全表）
    assert paths[1][1] == "决算数"
    assert paths[0][1] == ""          # 行0 左侧是 preamble 文本，但同格自身为空且行0已是表题行
    assert paths[1][0] == ""          # 行1 col0 左侧无非空，不外推


def test_recorded_span_does_not_leak_into_missing_parent_zone():
    # 过宽总表把「财政拨款支出」挤到下页：父格「财政拨款收入」的 bbox 跨度
    # 只覆盖前两列，支出侧空列不得越界借用收入父表头。
    rows = [["财政拨款收入", None, None, None, None, None],
            ["项目", "预算数", "项目", "合计", "一般公共预算", "政府性基金预算"],
            ["一、一般公共预算资金", "100", "一、教育支出", "100", "100", ""]]
    cells = []
    for row_idx, row in enumerate(rows):
        for col, value in enumerate(row):
            span = 2 if row_idx == 0 and col == 0 else 1
            box = (col * 10, row_idx * 10, (col + span) * 10, (row_idx + 1) * 10) if value is not None else None
            cells.append(ParsedCell(row_idx, col, value or "", value or "",
                                    float(value) if value and str(value).replace(".", "").isdigit() else None,
                                    1, box, row_idx < 2, .99, "万元", "lines", 1, span))
    table = ParsedTable("FIN_04_fiscal_grant_total", "2022年部门财政拨款收支预算总表",
                        1, 0, (0, 0, 60, 30), 2, .99, "lines", "万元", rows, cells, "wide")
    paths = header_paths(table)
    assert paths[1][0] == "财政拨款收入"   # 跨度覆盖内（spanning 恢复）
    assert paths[3][0] == ""              # 支出侧父表头缺失，保持空段
    assert measures(table)[3] == "expenditure_actual"  # 走「仅叶合计」兜底


def test_three_public_reception_group_keeps_out_vehicle_parent():
    # 三公矩阵：「公务接待费」分组下的空行不得继承左邻「运行维护费」。
    rows = [["财政拨款“三公”经费", None, None, None, None, None, None, None, None, None, None, None],
            ["合计", None, "因公出国\n（境）费", None, "公务用车购置及运行维护费", None, None, None, None, None, "公务接待费", None],
            [None, None, None, None, "小计", None, "公务用车\n购置费", None, "公务用车\n运行维护费", None, None, None],
            ["预算数", "决算数"] * 6]
    cells = []
    spans = {(0, 0): 12, (1, 0): 2, (1, 2): 2, (1, 4): 6, (1, 10): 2,
             (2, 4): 2, (2, 6): 2, (2, 8): 2}
    for row_idx, row in enumerate(rows):
        for col, value in enumerate(row):
            span = spans.get((row_idx, col), 1)
            box = (col * 10, row_idx * 10, (col + span) * 10, (row_idx + 1) * 10) if value is not None else None
            cells.append(ParsedCell(row_idx, col, value or "", value or "", None, 1, box,
                                    row_idx < 4, .99, "万元", "lines", 1, span))
    table = ParsedTable("FIN_07_three_public", "财政拨款“三公”经费支出决算表",
                        1, 0, (0, 0, 120, 50), 4, .99, "lines", "万元", rows, cells, "matrix2")
    paths = header_paths(table)
    assert paths[9][2] == "公务用车\n运行维护费".replace("\n", "") or "运行维护费" in paths[9][2]
    assert paths[11][2] == "", paths[11]   # 接待费分组：不继承运行维护费
    mapping = measures(table)
    assert mapping.get(11) == "reception", mapping


def test_real_frozen_fragment_maps_income_column():
    """民政批次冻结快照中的真实 FIN_04 片段：9 个收入格恢复列身份。"""
    from src.schemas.parse_artifact import ParsedTable

    data = json.loads(FIXTURE.read_text())
    assert data["source_pdf_sha256"].startswith("989b5ccc4e4293a9")
    table = ParsedTable.from_dict(data["fragment"])
    paths = header_paths(table)
    assert paths[1] == ["收入", "决算数"]
    mapping = measures(table)
    assert mapping.get(1) == "income_actual"
    assert mapping.get(3) == "expenditure_actual"
