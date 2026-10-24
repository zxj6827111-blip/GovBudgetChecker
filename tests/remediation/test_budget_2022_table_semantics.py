"""上海 2022 预算版式的表种、续页与列语义回归（验收批次 pdf_acceptance_shanghai2022_20261021）。

决算版式调出的语义层在预算材料上成片失效：财政拨款收支总表无事实、三公表
车辆分项丢列、截断表题整表落成 UNKNOWN、按单位明细附加表被并入功能分类表
导致编码当金额入库。这里用最小网格固定每个根因的期望行为。
"""
import pytest

from src.engine.budget_rules import _extract_year_candidates, _strip_citation_years
from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.document_profile_resolver import resolve_document_profile
from src.services.fiscal_column_identity import canonical_column_mappings
from src.services.fiscal_table_rules import NINE_TABLE_RULES, detect_table_code
from src.services.pdf_parser import PDFParser


def grid(code, title, rows, spans, header_rows, page=1, top=0, uid="t", width=10):
    """按列宽 width 排版；spans[(row, col)] = (列跨度, 行跨度)。被合并覆盖的位置不出格。"""
    covered = set()
    for (row_idx, col), (col_span, row_span) in spans.items():
        covered |= {(r, c) for r in range(row_idx, row_idx + row_span)
                    for c in range(col, col + col_span)} - {(row_idx, col)}
    cells = []
    for row_idx, row in enumerate(rows):
        for col, value in enumerate(row):
            if (row_idx, col) in covered:
                continue
            col_span, row_span = spans.get((row_idx, col), (1, 1))
            text = value or ""
            numeric = float(text.replace(",", "")) if text.replace(",", "").replace(".", "").isdigit() else None
            box = (col * width, top + row_idx * 10, (col + col_span) * width, top + (row_idx + row_span) * 10)
            cells.append(ParsedCell(row_idx, col, text, text, numeric, page, box,
                                    row_idx < header_rows, .99, "元", "lines"))
    bbox = (0, top, max(len(row) for row in rows) * width, top + len(rows) * 10)
    return ParsedTable(code, title, page, 0, bbox, header_rows, .99, "lines", "元", rows, cells, uid)


def measures(table):
    return {m["source_col_idx"]: m["canonical_measure"]
            for m in canonical_column_mappings(table, NINE_TABLE_RULES[table.table_code])}


def test_budget_fiscal_grant_total_maps_both_ledgers():
    rows = [["财政拨款收入", None, "财政拨款支出", None, None, None, None],
            ["项目", "预算数", "项目", "合计", "一般公共预算", "政府性基金预算", "国有资本经营预算"],
            ["一、一般公共预算资金", "100", "一、教育支出", "100", "100", "", ""]]
    table = grid("FIN_04_fiscal_grant_total", "2022年部门财政拨款收支预算总表", rows,
                 {(0, 0): (2, 1), (0, 2): (5, 1)}, 2)
    assert measures(table) == {1: "income_actual", 3: "expenditure_actual", 4: "general_public_budget",
                               5: "government_fund_budget", 6: "state_capital_budget"}
    labels = {m["source_col_idx"]: m["source_label_cols"]
              for m in canonical_column_mappings(table, NINE_TABLE_RULES[table.table_code])}
    assert labels[1] == [0] and labels[3] == [2]


def test_budget_fiscal_grant_total_without_expenditure_parent_header():
    # 过宽总表把「财政拨款支出」父表头挤到下一页，本页只剩叶子「合计」。
    rows = [["财政拨款收入", None, None, None, None, None],
            ["项目", "预算数", "项目", "合计", "一般公共预算", "政府性基金预算"],
            ["一、一般公共预算资金", "100", "一、教育支出", "100", "100", ""]]
    table = grid("FIN_04_fiscal_grant_total", "2022年部门财政拨款收支预算总表", rows, {(0, 0): (2, 1)}, 2)
    assert measures(table)[3] == "expenditure_actual"


def test_three_public_budget_leaf_columns_keep_vehicle_details():
    rows = [["2022年“三公”经费预算数", None, None, None, None, None, "2022年机关运行\n经费预算数"],
            ["合计", "因公出国(境)费", "公务接待费", "公务用车购置及运行费", None, None, None],
            [None, None, None, "小计", "购置费", "运行费", None],
            ["998.57", "524.96", "238.01", "235.60", "73.00", "162.60", "1,170.31"]]
    spans = {(0, 0): (6, 1), (0, 6): (1, 3), (1, 0): (1, 2), (1, 1): (1, 2), (1, 2): (1, 2), (1, 3): (3, 1)}
    table = grid("FIN_07_three_public", "部门“三公”经费和机关运行经费预算表", rows, spans, 3)
    assert measures(table) == {0: "total_actual", 1: "overseas", 2: "reception",
                               3: "vehicle_purchase_operation", 4: "vehicle_purchase",
                               5: "vehicle_operation", 6: "org_operation_expense"}


@pytest.mark.parametrize("title, headers, expected", [
    # 6 列版总表只剩「政府性基金预算」列，表头独占标记不能压过精确表题。
    ("2022年部门财政拨款收支预算总表",
     ["财政拨款收入", "项目", "预算数", "项目", "合计", "一般公共预算", "政府性基金预算"], "FIN_04_fiscal_grant_total"),
    # 原件把表题印截断，表头足以确认经济分类基本支出表。
    ("2022年部门一般公共预算基本支出",
     ["项目", "一般公共预算基本支出", "部门预算经济分类科目编码", "经济分类科目名称", "合计", "人员经费", "公用经费"],
     "FIN_06_basic_expenditure"),
    # 按预算单位逐行列示的附加表不是九表。
    ("2022年部门财政拨款支出预算明细表",
     ["序号", "预算单位", "财政拨款支出", "合计", "基本支出", "人员经费", "公用经费", "项目支出"], None),
    ("2022年部门支出预算分单位明细表",
     ["序号", "预算单位", "合计", "基本支出", "项目支出"], None),
])
def test_budget_table_identity(title, headers, expected):
    code, _score = detect_table_code(title, headers, " ".join(headers))
    assert code == expected


def test_corner_form_number_is_not_the_table_title():
    parser = PDFParser(None)
    assert parser._score_title_line("部门预算06表", 0) < parser._score_title_line("2022年部门财政拨款支出预算明细表", 1)


def test_titled_table_with_distinct_header_is_not_a_continuation():
    parser = PDFParser(None)
    function_rows = [["项目", None, None, None, "一般公共预算支出", None, None],
                     ["功能分类科目编码", None, None, "功能分类科目名称", "合计", "基本支出", "项目支出"],
                     ["类", "款", "项", None, None, None, None],
                     ["208", "", "", "社会保障和就业支出", "100", "80", "20"]]
    previous = grid("FIN_05_general_public_expenditure", "2022年部门一般公共预算支出功能分类预算表",
                    function_rows, {(0, 0): (4, 1), (0, 4): (3, 1), (1, 0): (3, 1)}, 3, page=13, uid="p13")
    unit_rows = [["序号", "预算单位", "财政拨款支出", None, None, None],
                 [None, None, "合计", "基本支出", None, "项目支出"],
                 [None, None, None, "人员经费", "公用经费", None],
                 ["1", "上海市医疗保障局", "100", "60", "20", "20"]]
    aux = grid("UNKNOWN_P14_T0", "2022年部门财政拨款支出预算明细表", unit_rows,
               {(0, 2): (4, 1), (1, 3): (2, 1)}, 3, page=14, uid="p14")
    assert parser._is_continuation_table(previous, aux) is False
    repeated = grid("UNKNOWN_P14_T0", " ".join(str(v) for v in function_rows[0] if str(v).strip()),
                    [*function_rows[:3], ["210", "", "", "卫生健康支出", "50", "50", ""]],
                    {(0, 0): (4, 1), (0, 4): (3, 1), (1, 0): (3, 1)}, 3, page=14, uid="p14b")
    assert parser._starts_new_table(previous, repeated) is False


def test_cross_page_total_footer_aligns_to_amount_columns():
    parser = PDFParser(None)
    rows = [["项目", None, None, "一般公共预算基本支出", None, None],
            ["部门预算经济分类科目编码", None, "经济分类科目名称", "合计", "人员经费", "公用经费"],
            ["类", "款", None, None, None, None],
            ["301", "", "工资福利支出", "100", "100", ""]]
    previous = grid("FIN_06_basic_expenditure", "2022年部门一般公共预算基本支出部门预算经济分类预算表",
                    rows, {(0, 0): (3, 1), (0, 3): (3, 1), (1, 0): (2, 1)}, 3, page=18, uid="p18")
    footer = grid("UNKNOWN_P19_T0", "合计 140 100 40", [["合计", "140", "100", "40"]], {}, 0, page=19, uid="p19")
    # 合计横跨三格维度列，金额格落在前表 合计/人员经费/公用经费 的物理列上。
    footer.cells[0] = ParsedCell(0, 0, "合计", "合计", None, 19, (0, 0, 30, 10), False, .99, "元", "lines")
    for index, cell in enumerate(footer.cells[1:], start=1):
        footer.cells[index] = ParsedCell(0, index, cell.raw_text, cell.raw_text, cell.numeric_value, 19,
                                         ((index + 2) * 10, 0, (index + 3) * 10, 10), False, .99, "元", "lines")
    aligned = parser._align_native_total_footer(previous, footer)
    assert aligned.rows == [["合计", None, None, "140", "100", "40"]]
    assert {(c.col_idx, c.origin_col_idx) for c in aligned.cells} == {(0, 0), (3, 1), (4, 2), (5, 3)}


def test_contents_page_numbering_is_not_a_decimal_amount():
    lines = ["目 录", "五、部门预算表", "1. 2022年部门财务收支预算总表",
             "10.2022年部门一般公共预算基本支出部门预算经济分类预算表"]
    assert PDFParser(None)._is_contents_or_section_page(lines) is True


def test_text_projection_without_amounts_is_a_shadow_of_the_grid():
    from dataclasses import replace

    rows = [["2022年“三公”经费预算数", None, None, None, None, None, "2022年机关运行\n经费预算数"],
            ["合计", "因公出国(境)费", "公务接待费", "公务用车购置及运行费", None, None, None],
            [None, None, None, "小计", "购置费", "运行费", None],
            ["998.57", "524.96", "238.01", "235.60", "73.00", "162.60", "1,170.31"]]
    spans = {(0, 0): (6, 1), (0, 6): (1, 3), (1, 0): (1, 2), (1, 1): (1, 2), (1, 2): (1, 2), (1, 3): (3, 1)}
    native = grid("FIN_07_three_public", "部门“三公”经费和机关运行经费预算表", rows, spans, 3)
    # 文字策略只排出单位、表头残片，阶段词让它带上映射列，但没有任何金额。
    fragments = grid("FIN_07_three_public", "部门“三公”经费和机关运行经费预算表",
                     [["人力资源和社会保障", "局", "", "", "单位:万元"], ["", "2022年“三公”经费预算数", "", "", ""]],
                     {}, 2, uid="text")
    text = replace(fragments, extraction_method="text",
                   cells=[replace(cell, extraction_method="text") for cell in fragments.cells])
    assert PDFParser(None)._text_grid_shadow(text, native) is True


def test_table_page_organization_label_backs_up_template_cover():
    pages = ["附件1：\n部门预算公开模板", "目 录",
             "2022年部门收入预算总表\n编制部门：上海虹桥国际中央商务区管理委员会 单位：元",
             "2022年部门支出预算总表\n编制部门：上海虹桥国际中央商务区管理委员会 单位：元"]
    profile = resolve_document_profile(filename="x.pdf", page_texts=pages)
    assert profile.organization_name.value == "上海虹桥国际中央商务区管理委员会"
    assert profile.organization_name.source == "table_title"
    mixed = resolve_document_profile(filename="x.pdf", page_texts=[
        "附件1", "编制部门：甲局 单位：元", "编制部门：乙局 单位：元"])
    assert mixed.organization_name.value is None


@pytest.mark.parametrize("line, years", [
    ("共上海市委上海市人民政府关于我市全面实施预算绩效管理的实施意见》（沪委发 (2019] 12 号）", []),
    ("根据《2019年预算管理办法》编制2021年预算", [2021]),
    ("2019年预算执行情况", [2019]),
])
def test_policy_citation_years_are_not_report_years(line, years):
    assert _extract_year_candidates(_strip_citation_years(line)) == years
