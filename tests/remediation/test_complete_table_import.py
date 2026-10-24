"""表格读取和存储反例；与规则通过率分开验收。"""
from copy import deepcopy
import json
from types import SimpleNamespace
from decimal import Decimal

import pytest

from test_native_blank_cells import grid_document
from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.fact_table_projection import ProjectionUnavailable, fact_bound_table_rows
from src.schemas.parse_artifact import ParseArtifact, ParsedCell
from src.services.document_profile_resolver import detect_cover_facts
from src.services.fiscal_row_roles import native_bilateral_padding_positions
from src.services.parse_artifact_store import artifact_payload_hash, load_parse_artifact
from src.services.pdf_parser import PDFParser
from src.services.table_import_receipt import build_table_import_receipt


@pytest.mark.parametrize("cover,expected", [
    ("上海市普陀区城市运行管理中心\n(上海市普陀区城市网格化综合管理\n中心、上海市普陀区政务服务中心、\n上海市普陀区大数据中心)", "上海市普陀区城市运行管理中心"),
    ("上海市普陀区文化馆", "上海市普陀区文化馆"),
    ("上海市普陀区石泉路街道\n综合行政执法队", "上海市普陀区石泉路街道综合行政执法队"),
    ("上海市普陀区人民政府长\n寿路街道办事处（本级）", "上海市普陀区人民政府长寿路街道办事处"),
    ("上海市普陀区总工会", "上海市普陀区总工会"),
])
def test_source_cover_blocks(cover, expected):
    assert detect_cover_facts([cover + "\n2025年度部门决算"])["cover_org_name"] == expected


@pytest.mark.parametrize("cover", [
    "甲局\n乙局", "某单位公开情况说明", "甲中心\n（乙中心", "目录\n甲财政局\n乙实验学校",
])
def test_ambiguous_cover_does_not_invent_owner(cover):
    assert not detect_cover_facts([cover + "\n2025年度部门决算"])["cover_org_name"]


@pytest.mark.parametrize("name", ["上海市普陀区妇女联合会", "上海市普陀区重大项目推进办公室",
    "上海市普陀区发改委", "上海市普陀区新普陀小学东校", "上海市总工会沪西工人文化宫"])
def test_cover_keeps_legitimate_organization_names(name):
    assert detect_cover_facts([name + "\n2025年度决算"])["cover_org_name"] == name


@pytest.mark.parametrize("name,expected", [
    ("上海市普陀区人民代表大会\n常务委员会办公室", "上海市普陀区人民代表大会常务委员会办公室"),
    ("中共上海市普陀区委\n老干部局", "中共上海市普陀区委老干部局"),
    ("上海市普陀区档案局本级", "上海市普陀区档案局"),
])
def test_source_cover_namespace_and_explicit_caliber(name, expected):
    assert detect_cover_facts([name + "\n2025年度决算"])["cover_org_name"] == expected


def test_annual_cover_heading_can_continue_on_next_line():
    cover = "上海市普陀区建设和管理\n委员会 2025 年度部门\n决算"
    facts = detect_cover_facts([cover])
    assert facts["cover_org_name"] == "上海市普陀区建设和管理委员会"
    assert "2025" in facts["cover_title"] and "决算" in facts["cover_title"]


def test_two_complete_name_fragments_require_direct_subject_heading_confirmation():
    cover = "上海市儿童世界基金会\n普陀幼儿园\n2025 年度决算"
    assert not detect_cover_facts([cover])["cover_org_name"]
    contents = "第一部分 上海市儿童世界基金会普陀幼儿园概况"
    assert detect_cover_facts([cover, contents])["cover_org_name"] == "上海市儿童世界基金会普陀幼儿园"
    assert not detect_cover_facts([cover, "日常工作涉及上海市儿童世界基金会普陀幼儿园"])["cover_org_name"]


def test_blank_amounts_do_not_turn_explicit_business_rows_into_headers():
    parser = PDFParser(None)
    rows = [["功能分类科目编码", "科目名称", "本年收入合计", "财政拨款收入"],
            ["合计", None, "", ""], ["201", "一般公共服务支出", "", ""]]
    assert parser._detect_header_row_count(rows) == 1
    assert parser._detect_header_row_count([rows[0], rows[2]]) == 1


def test_native_sparse_single_row_and_empty_grids_are_retained():
    parser = PDFParser(None)
    for rows in [[["2101202", "城乡居民医疗补助", "999.55"]],
                 [["序号", "单位名称", "备注"], ["1", "甲局", ""], *[["", "", ""]] * 8],
                 [["", ""], ["", ""]]]:
        assert parser._is_meaningful_table(rows, native_grid=True)
    assert not parser._is_meaningful_table([[""]], native_grid=True)


def test_closed_grid_preserves_source_positions_over_text_strategy_score():
    doc, grid = ledger()
    text = deepcopy(grid)
    grid.table_code = text.table_code = "UNKNOWN_P1_T0"
    grid.confidence, text.confidence, text.extraction_method = .4, .99, "text"
    assert PDFParser(None)._merge_candidates([grid], [text]) == [grid]
    assert PDFParser(None)._merge_candidates([text], [grid]) == [grid]


def test_actual_merged_cell_spans_come_from_original_grid_boundaries():
    boxes = [[(0, 0, 20, 20), None, (20, 0, 30, 10)],
             [(0, 0, 20, 20), None, (20, 10, 30, 20)],
             [(0, 20, 10, 30), (10, 20, 20, 30), (20, 20, 30, 30)]]
    table = SimpleNamespace(rows=[SimpleNamespace(cells=row) for row in boxes])
    cells = PDFParser(None)._build_cells(1, [["合并", None, "金额"], [None, None, "10"], ["甲", "乙", "20"]],
                                       table, 1, "万元", "lines", .99)
    assert (cells[0].row_span, cells[0].col_span) == (2, 2)
    assert (cells[-1].row_span, cells[-1].col_span) == (1, 1)


def test_literal_amount_precision_survives_numeric_float_and_json_roundtrip():
    raw = "9,007,199,254,740,993.01"
    cell = ParsedCell(1, 1, raw, raw, float(raw.replace(",", "")), 1, (0, 0, 10, 10), False, .99, "万元", "lines")
    assert cell.normalized_value_wan == Decimal("9007199254740993.01")
    restored = ParsedCell.from_dict(json.loads(json.dumps(cell.to_dict())))
    assert restored.normalized_value_wan == cell.normalized_value_wan and restored.raw_text == raw


def ledger():
    rows = [["收入", None, "支出", None, None, None, None],
            ["项目", "决算数", "项目", "合计", "一般公共预算财政拨款", "政府性基金预算财政拨款", "国有资本经营预算财政拨款"],
            ["本年收入合计", "10.00", "本年支出合计", "10.00", "10.00", "0.00", "0.00"],
            ["年初财政拨款结转和结余", "0.00", "年末财政拨款结转和结余", "0.00", "0.00", "0.00", "0.00"],
            ["一、一般公共预算财政拨款", "0.00", "", "0.00", "0.00", "0.00", "0.00"],
            ["二、政府性基金预算财政拨款", "0.00", "", "0.00", "0.00", "0.00", "0.00"],
            ["三、国有资本经营预算财政拨款", "0.00", "", "0.00", "0.00", "0.00", "0.00"],
            ["总计", "10.00", "总计", "10.00", "10.00", "0.00", "0.00"]]
    doc = grid_document(rows, code="FIN_04_fiscal_grant_total")
    table = doc.parse_artifact.table_fragments[0]
    for cell in table.cells:
        if cell.row_idx == 0:
            if cell.col_idx == 0:
                cell.bbox = (0, 50, 140, 75)
            elif cell.col_idx == 2:
                cell.bbox = (140, 50, 490, 75)
            else:
                cell.bbox = None
    doc.fact_context = build_fact_context_from_artifact(
        doc.parse_artifact, org_name="甲局", fiscal_year=2024, report_kind="final", doc_type="dept_final")
    return doc, table


def test_padding_keeps_every_source_zero_without_right_side_facts():
    doc, table = ledger()
    positions = native_bilateral_padding_positions(table)
    assert positions == {(r, c) for r in range(4, 7) for c in range(3, 7)}
    before = table.to_dict()
    rows = fact_bound_table_rows(doc, "财政拨款收入支出决算总表")
    assert rows[4][3:] == ["0.00"] * 4
    assert table.to_dict() == before
    receipt = build_table_import_receipt(doc.parse_artifact, doc.fact_context.facts, org_name="甲局")
    assert receipt["padding_cell_count"] == 12 and receipt["unresolved_money_cell_count"] == 0
    assert receipt["original_pdf_full_coverage_verified"] is False
    assert not [f for f in doc.fact_context.facts if f.row_order in range(4, 7) and f.indicator != "income_actual"]


@pytest.mark.parametrize("problem", ["nonzero", "right_label", "uncertified_label", "ocr", "missing_total", "missing_zero"])
def test_unlabelled_amounts_are_not_silently_waived(problem):
    doc, table = ledger()
    cell = next(c for c in table.cells if (c.row_idx, c.col_idx) == (4, 3))
    label = next(c for c in table.cells if (c.row_idx, c.col_idx) == (4, 2))
    if problem == "nonzero":
        cell.raw_text, cell.numeric_value = "5.00", 5.0
    elif problem == "right_label":
        label.raw_text = "待核实业务"
    elif problem == "uncertified_label":
        label.native_blank_verified = False
    elif problem == "ocr":
        cell.extraction_method = "ocr"
    elif problem == "missing_total":
        table.rows[7][2] = "其他"
    else:
        table.cells.remove(cell)
    assert native_bilateral_padding_positions(table) == set()
    with pytest.raises(ProjectionUnavailable):
        fact_bound_table_rows(doc, "财政拨款收入支出决算总表")


def blank_continuation():
    doc, table = ledger()
    previous = deepcopy(table)
    previous.rows = previous.rows[:2] + [["", "", "十五、商业服务业等支出", "", "", "", ""]]
    previous.cells = [deepcopy(c) for c in table.cells if c.row_idx < 3]
    for c in previous.cells:
        c.raw_text = str(previous.rows[c.row_idx][c.col_idx] or "")
        c.numeric_value = None
        c.native_blank_verified = not c.raw_text
    current = deepcopy(previous)
    current.table_code, current.title, current.page_number = "UNKNOWN_P2_T0", "十六、金融支出", 2
    current.rows = [["", "", "十六、金融支出", "", "", "", ""]]
    current.cells = [deepcopy(c) for c in previous.cells if c.row_idx == 2]
    current.header_row_count, current.fragment_uid = 1, "continuation"
    for c in current.cells:
        c.row_idx, c.page_number = 0, 2
        c.raw_text = str(current.rows[0][c.col_idx] or "")
    return previous, current


def test_native_blank_continuation_preserves_empty_money_and_identity():
    previous, current = blank_continuation()
    parser = PDFParser(None)
    assert parser._is_continuation_table(previous, current)
    merged = parser._append_table(previous, current)
    assert len(merged.rows) == 4 and merged.rows[-1][2] == "十六、金融支出"
    assert all(c.numeric_value is None for c in merged.cells)
    assert all(not c.is_header for c in merged.cells if c.page_number == 2)


@pytest.mark.parametrize("problem", ["sequence", "geometry", "blank_proof", "unit", "other_title", "page_gap"])
def test_blank_continuation_requires_literal_structure(problem):
    previous, current = blank_continuation()
    if problem == "sequence":
        current.rows[0][2] = "一、一般公共服务支出"
    elif problem == "geometry":
        current.cells[-1].bbox = (0, 0, 100, 25)
    elif problem == "blank_proof":
        current.cells[-1].native_blank_verified = False
    elif problem == "unit":
        current.unit_hint = "元"
    elif problem == "other_title":
        current.title = "收入决算表"
    else:
        current.page_number = 3
    assert not PDFParser(None)._is_continuation_table(previous, current)


@pytest.mark.asyncio
@pytest.mark.parametrize("codec", ["default", "dict"])
async def test_cache_preserves_exact_physical_and_logical_matrices(codec):
    doc, table = ledger()
    cell = table.cells[0]
    cell.grid_raw_text, cell.native_text_sources = "首段", [{"text": "收入", "bbox": [0, 50, 140, 75]}]
    cell.row_span, cell.col_span = 2, 2
    table.cells[15].is_header = True  # 单个表头格不能扩大整表 header_row_count。
    doc.parse_artifact.source_grids = [deepcopy(table)]
    doc.parse_artifact.source_grids[0].fragment_uid = "raw-source"
    doc.parse_artifact.source_grids[0].header_row_count = 1
    payload = doc.parse_artifact.to_dict()
    assert ParseArtifact.from_dict(payload).to_dict() == payload
    run = dict(payload, artifact_snapshot=payload if codec == "dict" else json.dumps(payload),
               artifact_snapshot_hash=artifact_payload_hash(payload))
    stored = [dict(c.to_dict(), instance_uid="instance") for c in table.cells]
    class Connection:
        async def fetchrow(self, *_args):
            return run
        async def fetch(self, *_args):
            return stored
    restored = await load_parse_artifact(Connection(), 1)
    assert restored.to_dict() == payload
    assert restored.table_fragments[0].rows[0][1] is None
    assert restored.table_fragments[0].header_row_count == 2
    assert restored.source_grids[0].fragment_uid == "raw-source" and restored.source_grids[0].header_row_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["hash", "identity", "cell_loss", "cell_text", "cell_role"])
async def test_corrupt_cache_cannot_report_success(problem):
    doc, table = ledger()
    payload = doc.parse_artifact.to_dict()
    run = dict(payload, artifact_snapshot=payload, artifact_snapshot_hash=artifact_payload_hash(payload))
    stored = [dict(c.to_dict(), instance_uid="instance") for c in table.cells]
    if problem == "hash":
        run["artifact_snapshot_hash"] = "0" * 64
    elif problem == "identity":
        run["source_file_hash"] = "other"
    elif problem == "cell_loss":
        stored.pop()
    elif problem == "cell_text":
        stored[-1]["raw_text"] = "99"
    else:
        stored[-1]["is_header"] = True
    class Connection:
        async def fetchrow(self, *_args):
            return run
        async def fetch(self, *_args):
            return stored
    with pytest.raises(ValueError):
        await load_parse_artifact(Connection(), 1)
