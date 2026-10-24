"""上海 2022 决算版式的入库回归：封面不写文种、编码格内折行、取词跨格线、逐格待复核。

对应 2026-10-23 反馈：执法总队一份挂出 563 条「结构化识别待复核」、财政局一份 93 条，
根因都在解析/入库层，不是材料问题。
"""

from copy import deepcopy
from decimal import Decimal

from src.schemas.parse_artifact import (
    LogicalTableInstance,
    ParseArtifact,
    ParsePageState,
    ParsedCell,
    ParsedTable,
    ParsedTextBlock,
)
from src.services.fiscal_fact_materializer import FiscalFactMaterializer
from src.services.fiscal_source_scope import heading_scope, organization_scope
from src.services.structured_ingest_runner import _reading_review_items
from src.services.table_reading_assessment import READING_VERSION, assess_table_reading
from tests.remediation.test_native_pdf_core import context_for, expense_fragments, grid
from src.services.pdf_parser import PDFParser


# ---------------------------------------------------------------------------
# 主体范围：封面只写“2022 年度决算”
# ---------------------------------------------------------------------------


def _scope_artifact(cover, body):
    blocks = [ParsedTextBlock(block_uid="cover", page_number=1, text=cover)]
    blocks += [ParsedTextBlock(block_uid=f"body{index}", page_number=2, text=line)
               for index, line in enumerate(body)]
    return ParseArtifact(source_file_hash="a" * 64, page_count=2, text_blocks=blocks)


def test_cover_without_kind_falls_back_to_part_headings():
    department = _scope_artifact("上海市某执法总队\n2022 年度决算", [
        "第二部分 上海市某执法总队2022 年度部门决算表",
        "第三部分 上海市某执法总队2022 年度部门决算情况说明",
    ])
    unit = _scope_artifact("上海市某研究中心\n2022 年度决算", [
        "第二部分 上海市某研究中心2022 年度决算表",
        "第三部分 上海市某研究中心2022 年度决算情况说明",
    ])
    silent = _scope_artifact("上海市某研究中心\n2022 年度决算", ["第一部分 概况", "一、主要职能"])
    assert organization_scope(department) == "department"
    assert organization_scope(unit) == "unit"
    assert organization_scope(silent) is None


def test_single_leftover_department_heading_does_not_block_a_unit_material():
    # 真实样本（上海市环境学校 2022）：目录两处写“年度决算”，正文第三部分标题残留“部门”。
    body = ["第二部分 上海市某学校2022 年度决算表",
            "第三部分 上海市某学校2022 年度决算情况说明",
            "第二部分 上海市某学校2022 年度决算表",
            "八、政府性基金预算财政拨款收入支出决算表\n本表无数据。",
            "第三部分 上海市某学校2022 年度部门决算情况说明"]
    text = "\n".join(body)
    # 规则侧（CMM-018）仍按严格口径判不出，模板混写照常可报。
    assert heading_scope(text, "final") is None
    assert heading_scope(text, "final", majority=True) == "unit"
    assert organization_scope(_scope_artifact("上海市某学校\n2022 年度决算", body)) == "unit"


def test_cover_kind_still_wins_over_body_headings():
    artifact = _scope_artifact("上海市某局\n2022 年度单位决算", ["第二部分 上海市某局2022 年度部门决算表"])
    assert organization_scope(artifact) == "unit"


def test_heading_scope_is_shared_with_budget_wording():
    assert heading_scope("一、部门财务收支预算总表\n二、部门收入预算总表", "budget") == "department"
    assert heading_scope("一、单位财务收支预算总表", "budget") == "unit"
    assert heading_scope("二、部门决算单位构成", "final") == "department"
    assert heading_scope("部门财务收支预算总表 单位财务收支预算总表", "budget") is None


# ---------------------------------------------------------------------------
# 编码格内折行：“20700\n00”
# ---------------------------------------------------------------------------


def test_wrapped_seven_digit_code_is_still_a_code():
    result = FiscalFactMaterializer(None)._extract_classification(
        [{"raw_text": "20700\n00"}, {"raw_text": "文化旅游体育与传媒\n支出"}])
    assert result["classification_code"] == "2070000"
    assert result["classification_name"] == "文化旅游体育与传媒\n支出"


def test_wrapped_code_continuation_still_aligns_to_split_code_header():
    first, second = expense_fragments()
    second.rows[0][0] = "21000\n00"
    second.cells[0].raw_text = second.cells[0].normalized_text = "21000\n00"
    merged = PDFParser(None)._coalesce_tables([first, second])
    assert len(merged) == 1
    assert merged[0].rows[-1] == ["21000\n00", None, None, "卫生健康", "50", "30", "10"]
    doc = context_for(merged, [first, second])
    fact = next(f for f in doc.fact_context.facts
                if f.subject_code == "2100000" and f.indicator == "total_actual")
    assert fact.normalized_value == Decimal("50")
    assert fact.recognition_status == "unverified"


def test_code_led_continuation_title_is_not_read_as_another_year():
    first, second = expense_fragments()
    first.title = "2022年度支出决算表"
    second.title = "2060301 机构运行 9,957.68 9,863.64"  # 无表题续页以首行文字充当标题
    assert not PDFParser(None)._fragment_titles_conflict(first, second)
    second.title = "2021年度支出决算表"
    assert PDFParser(None)._fragment_titles_conflict(first, second)


def test_part_heading_does_not_replace_the_table_title_below_it():
    parser = PDFParser(None)
    heading = parser._score_title_line("第二部分 上海市某委员会2022 年度部门决算表", 0)
    title = parser._score_title_line("2022 年度收入支出决算总表", 1)
    assert title > heading


def test_misdrawn_code_rule_on_continuation_folds_back_into_label_columns():
    first = grid([["科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
                  ["合计", "", "150", "100", "50"],
                  ["208", "社会保障", "100", "70", "30"]],
                 [0, 30, 60, 80, 100, 120], uid="first", page=1,
                 code="FIN_03_expenditure", confidence=.99, unit="万元")
    # 续页：第 2 行的编码格右线画在 32 而不是 30，整页多出一列标签。
    second = grid([["210", "卫生健康", None, "50", "30", "10"],
                   ["21001", None, "行政运行", "20", "10", "10"]],
                  [0, 30, 32, 60, 80, 100, 120], uid="second", page=2)
    second.cells = [c for c in second.cells if c.raw_text]
    for cell in second.cells:
        if (cell.row_idx, cell.col_idx) == (0, 1):
            cell.bbox = (30, 0, 60, 10)
        elif (cell.row_idx, cell.col_idx) == (1, 0):
            cell.bbox = (0, 10, 32, 20)
    merged = PDFParser(None)._coalesce_tables([first, second])
    assert len(merged) == 1
    assert merged[0].rows[-2:] == [["210", "卫生健康", "50", "30", "10"], ["21001", "行政运行", "20", "10", "10"]]
    assert second.rows[1][2] == "行政运行"  # 原物理片段不改
    doc = context_for(merged, [first, second])
    fact = next(f for f in doc.fact_context.facts if f.subject_code == "21001" and f.indicator == "total_actual")
    assert fact.normalized_value == Decimal("20") and fact.recognition_status == "unverified"
    assert fact.extra_dims["column_identity_status"] == "bound"


def test_split_label_fold_refuses_when_money_lanes_move():
    first = grid([["科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
                  ["208", "社会保障", "100", "70", "30"]],
                 [0, 30, 60, 80, 100, 120], uid="first", page=1,
                 code="FIN_03_expenditure", confidence=.99, unit="万元")
    second = grid([["210", "卫生健康", None, "50", "30", "10"]],
                  [0, 30, 32, 62, 80, 100, 120], uid="second", page=2)
    folded = PDFParser(None)._collapse_native_split_label_columns(first, second)
    assert folded is second


# ---------------------------------------------------------------------------
# 取词跨格线：“18,140.77” + “302” → “18,140.77302”
# ---------------------------------------------------------------------------

CODE = "FIN_06_basic_expenditure"


def _basic_artifact(extra_tokens):
    rows = [["经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "科目名称", "决算数"],
            ["301", "工资福利支出", "18,140.77", "302", "商品和服务支出", "2,675.11"]]
    xs = [80, 145, 228, 298, 340, 464, 542]
    amount_cols = {2, 5}
    cells = [ParsedCell(row_idx=r, col_idx=c, raw_text=text, normalized_text=text,
                        numeric_value=float(text.replace(",", "")) if r and c in amount_cols else None,
                        page_number=1, bbox=(xs[c], 200 + r * 20, xs[c + 1], 220 + r * 20),
                        is_header=not r, confidence=.99, unit_hint="万元", extraction_method="lines",
                        normalized_value_wan=Decimal(text.replace(",", "")) if r and c in amount_cols else None)
             for r, row in enumerate(rows) for c, text in enumerate(row)]
    table = ParsedTable(table_code=CODE, title="一般公共预算财政拨款基本支出决算表", page_number=1,
                        table_index=0, bbox=(80, 200, 542, 240), header_row_count=1, confidence=.99,
                        extraction_method="lines", unit_hint="万元", rows=rows, cells=cells,
                        fragment_uid="basic")
    probe = dict(page=1, width=595, height=842, blank_verified=False, table_hints=[CODE], image_regions=[],
                 amounts=[{"raw_text": "2,675.11", "bbox": [500, 223, 541, 236]}, *extra_tokens])
    return ParseArtifact(
        source_file_hash="a" * 64, page_count=1,
        page_states=[ParsePageState(page_number=1, has_text_layer=True, text_chars=500)],
        table_fragments=[table], physical_fragments=[deepcopy(table)],
        logical_tables=[LogicalTableInstance(instance_uid="basic", table_code=CODE, fragment_uids=["basic"],
                                             title=table.title, page_start=1, page_end=1, row_count=2)],
        reading_assessment=dict(version=READING_VERSION, pages=[probe]))


def test_word_merged_across_cell_border_is_not_a_reading_gap():
    merged = {"raw_text": "18,140.77302", "bbox": [249.8, 223, 313.9, 236]}
    assert assess_table_reading(_basic_artifact([merged]))["gaps"] == []


def test_word_that_cells_cannot_spell_is_still_a_gap():
    stray = {"raw_text": "18,140.77999", "bbox": [249.8, 223, 313.9, 236]}
    gaps = assess_table_reading(_basic_artifact([stray]))["gaps"]
    assert [g["reason"] for g in gaps] == ["native_amount_not_extracted"]


# ---------------------------------------------------------------------------
# 待复核项按表 / 按页汇总
# ---------------------------------------------------------------------------


def _cell(uid, page=7, fragment="t1", table="FIN_02_income"):
    return {"source_cell_id": uid, "page": page, "bbox": [1, 2, 3, 4], "raw_text": "1.00",
            "reason": "rule_admission_blocked", "fragment_uid": fragment, "table_code": table}


def test_review_items_fold_cells_into_their_table_and_gaps_into_their_page():
    quality = {
        "gaps": [{"id": "reading:p15:a", "page": 15, "reason": "native_amount_not_extracted", "bbox": [0, 0, 5, 5]},
                 {"id": "reading:p15:b", "page": 15, "reason": "native_amount_not_extracted", "bbox": [5, 5, 9, 9]},
                 {"id": "reading:p16:c", "page": 16, "reason": "native_amount_not_extracted", "bbox": [0, 0, 1, 1]}],
        "mapping_incomplete_tables": [{"fragment_uid": "t1", "table_code": "FIN_02_income", "title": "收入决算表",
                                       "page": 7, "unresolved_money_cell_count": 3,
                                       "reasons": {"rule_admission_blocked": 3}, "arithmetic_conflicts": []}],
        "unbound_money_cells": [_cell("t1:r1c4"), _cell("t1:r1c5"), _cell("t1:r9c4", page=8),
                                {**_cell("orphan:r0c1", page=9, fragment=None), "fragment_uid": None}],
    }
    items = _reading_review_items(quality)
    assert [item["type"] for item in items] == [
        "table_reading_gap", "table_reading_gap", "table_mapping_incomplete", "source_cell_unbound"]
    page15, page16, table, orphan = items
    assert page15["id"].startswith("reading:p15:gaps:") and len(page15["regions"]) == 2
    assert page15["bbox"] == [0, 0, 9, 9] and page15["page_number"] == 15
    assert page16["id"] == "reading:p16:c"  # 只有一处时沿用原 id
    assert table["id"] == "reading:mapping:t1" and len(table["cells"]) == 3
    assert table["page_numbers"] == [7, 8]
    assert orphan["id"] == "reading:unbound:orphan:r0c1"


def test_scanned_page_gaps_become_one_document_item():
    gaps = [{"id": f"reading:p{page}:img", "page": page, "reason": "image_region_not_structured",
             "bbox": [0, 0, 595, 842]} for page in (1, 2, 3, 5)]
    items = _reading_review_items({"gaps": gaps, "mapping_incomplete_tables": [], "unbound_money_cells": []})
    assert len(items) == 1
    assert items[0]["page_numbers"] == [1, 2, 3, 5] and len(items[0]["regions"]) == 4
    assert "第 1–3、5 页" in items[0]["message"]


def test_padded_kuan_code_in_a_mixed_table_is_read_as_its_kuan():
    from src.services.fiscal_code_hierarchy import hierarchy_paths, leaf_codes

    codes = ["210", "21011", "2101101", "2101102", "2109900", "2109999"]
    assert hierarchy_paths(codes)["2109900"] == "21099"
    assert leaf_codes(codes) == {"2101101", "2101102", "2109999"}
    # 没有同款下的明细作证，或已有同名 5 位行时，保持原编码。
    assert hierarchy_paths(["210", "21011", "2109900"])["2109900"] == "2109900"
    assert hierarchy_paths(["201", "20106", "2010600", "2010601"])["2010600"] == "2010600"


def _column_artifact(tokens):
    rows = [["经济分类科目编码", "科目名称", "决算数"],
            ["301", "工资福利支出", "253.16"],
            ["30101", "基本工资", "19.96"]]
    xs = [80, 145, 467, 505]
    cells = [ParsedCell(row_idx=r, col_idx=c, raw_text=text, normalized_text=text,
                        numeric_value=float(text) if r and c == 2 else None, page_number=1,
                        bbox=(xs[c], 151 + r * 16, xs[c + 1], 167 + r * 16), is_header=not r, confidence=.99,
                        unit_hint="万元", extraction_method="lines",
                        normalized_value_wan=Decimal(text) if r and c == 2 else None)
             for r, row in enumerate(rows) for c, text in enumerate(row)]
    table = ParsedTable(table_code=CODE, title="一般公共预算财政拨款基本支出决算表", page_number=1,
                        table_index=0, bbox=(80, 151, 505, 199), header_row_count=1, confidence=.99,
                        extraction_method="lines", unit_hint="万元", rows=rows, cells=cells, fragment_uid="basic")
    probe = dict(page=1, width=595, height=842, blank_verified=False, table_hints=[CODE], image_regions=[],
                 amounts=tokens)
    return ParseArtifact(
        source_file_hash="a" * 64, page_count=1,
        page_states=[ParsePageState(page_number=1, has_text_layer=True, text_chars=500)],
        table_fragments=[table], physical_fragments=[deepcopy(table)],
        logical_tables=[LogicalTableInstance(instance_uid="basic", table_code=CODE, fragment_uids=["basic"],
                                             title=table.title, page_start=1, page_end=1, row_count=3)],
        reading_assessment=dict(version=READING_VERSION, pages=[probe]))


def test_last_glyph_of_the_row_above_glued_to_an_amount_is_not_a_gap():
    # 上海共青森林公园 2023：本格 "19.96"，上一格 "253.16" 的末位 "6" 被粘成 "19.966"。
    glued = {"raw_text": "19.966", "bbox": [475.2, 182.5, 501.4, 196.0]}
    assert assess_table_reading(_column_artifact([glued]))["gaps"] == []
    stray = {"raw_text": "19.967", "bbox": [475.2, 182.5, 501.4, 196.0]}
    assert len(assess_table_reading(_column_artifact([stray]))["gaps"]) == 1
