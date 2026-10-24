"""扫描件：跨页同表接续、切块主体名、倾斜网线与 1 像素容差（2026-10-21 科创办第 8、9 页）。"""
from __future__ import annotations

from types import SimpleNamespace

from PIL import Image, ImageDraw

from src.schemas.parse_artifact import LogicalTableInstance, ParseArtifact
from src.services.ocr_client import OcrBlock
from src.services.ocr_fact_promotion import _join_ocr_continuations
from src.services.ocr_grid_reconstruction import stitched_identity
from src.services.ocr_tiles import uncovered_regions
from src.services.raster_grid_candidates import discover_grid_cells
from tests.remediation.test_budget_2022_table_semantics import grid

ORG = "上海推进科技创新中心建设办公室"
HEAD = [["功能分类科目编码", "功能分类科目名称", "合计", "财政拨款收入"]]


def ocr_fragment(page, rows, uid):
    table = grid("FIN_02_income", "OCR候选表:FIN_02_income", HEAD + rows, {}, 1, page=page, uid=uid)
    table.extraction_method = "ocr"
    return table


def promoted(*fragments):
    artifact = ParseArtifact(source_file_hash="a" * 64)
    artifact.table_fragments = list(fragments)
    artifact.logical_tables = [LogicalTableInstance(
        instance_uid=f.fragment_uid, table_code=f.table_code, title=f.title, fragment_uids=[f.fragment_uid],
        page_start=f.page_number, page_end=f.page_number, org_name=ORG, fiscal_year=2022,
        column_mappings=[{"source_col_idx": 2, "canonical_measure": "total_actual"}]) for f in fragments]
    return artifact


def test_scanned_table_split_across_pages_becomes_one_instance():
    first = ocr_fragment(8, [["206", "科学技术支出", "101,360,282", "101,360,282"]], "p8")
    second = ocr_fragment(9, [["221", "住房保障支出", "6,194,400", "6,194,400"],
                              ["合计", "", "111,615,328", "111,615,328"]], "p9")
    artifact = promoted(first, second)
    _join_ocr_continuations(artifact, [first, second])
    [instance] = artifact.logical_tables
    assert (instance.page_start, instance.page_end) == (8, 9)
    assert {"p8", "p9"} <= set(instance.fragment_uids)
    [table] = artifact.table_fragments
    # 续页重复的表头不进数据行；每格保留原页。
    assert [row[0] for row in table.rows] == ["功能分类科目编码", "206", "221", "合计"]
    assert {c.page_number for c in table.cells if c.raw_text == "6,194,400"} == {9}


def test_a_finished_table_is_not_continued_by_the_next_page():
    first = ocr_fragment(8, [["合计", "", "100", "100"]], "a")
    second = ocr_fragment(9, [["221", "住房保障支出", "50", "50"]], "b")
    artifact = promoted(first, second)
    _join_ocr_continuations(artifact, [first, second])
    assert len(artifact.logical_tables) == 2


def block(text, box, score):
    return SimpleNamespace(text=text, bbox=box, confidence=score, page_number=8)


def test_owner_line_cut_by_tiles_is_stitched_from_overlapping_reads():
    left = block("编制部门：上海推进科技创新中心建设办公", [121, 550, 134, 777], .996)
    right = block("设办公室", [120, 536, 134, 579], .949)
    assert stitched_identity([left, right], ORG) == [left, right]
    apart = block("设办公室", [120, 300, 134, 340], .99)
    assert stitched_identity([left, apart], ORG) == []


def test_slightly_skewed_thin_rules_are_still_grid_lines():
    # 200 DPI 扫描略斜：3px 细线在整宽上漂移 6px，上下跨度 9px 超过 7px 上限，按平均笔画仍是细线。
    image = Image.new("RGB", (600, 360), "white")
    draw = ImageDraw.Draw(image)
    for y in (50, 120, 190):
        draw.line((40, y, 560, y), fill="black", width=3)
    draw.line((40, 260, 560, 266), fill="black", width=3)
    for x in (40, 300, 560):
        draw.line((x, 50, x, 266), fill="black", width=3)
    found = discover_grid_cells(image, dpi=200)
    assert found["horizontal_axes"] == 4
    assert {cell["row_idx"] for cell in found["cells"]} == {0, 1, 2}


def test_one_pixel_tolerance_survives_fractional_box_edges():
    image = Image.new("RGB", (40, 20), "white")
    ImageDraw.Draw(image).rectangle((10, 5, 20, 14), fill="black")
    block = OcrBlock("收入", .99, [10, 5, 18.9999, 14])
    assert uncovered_regions([[0, 0, 40, 20]], [block], image=image) == []
