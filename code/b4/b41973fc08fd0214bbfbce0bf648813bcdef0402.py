"""跨图块原页位置对齐；合成协议材料不作为真实准确性验收。"""

from copy import deepcopy
from PIL import Image, ImageDraw
from src.services.ocr_grid_fragments import (
    group_projections,
    compose_literal,
    align_grid_fragments,
    collect_bound_fragments,
)
from src.services.ocr_image_grid import physical_grid_tables
from src.services.input_geometry import Affine


def fragment(text, box, status="qualified_fragment", score=0.995):
    return {
        "fragment_id": text + str(box),
        "text": text,
        "grid_bbox": box,
        "score": score,
        "status": status,
        "granularity": "fragment",
        "render_bbox": box,
        "source": {"input_sha256": "a" * 64},
    }


def test_clipped_high_score_projection_does_not_override_complete_word():
    partial = fragment("0.0", [10, 10, 30, 20], "requires_regional_read", 0.9999)
    complete = fragment("0.00", [10, 10, 35, 20])
    groups = group_projections([partial, complete])
    assert len(groups) == 1 and groups[0]["selected"]["text"] == "0.00"
    assert groups[0]["status"] == "resolved" and len(groups[0]["alternates"]) == 2


def test_complete_legal_conflict_is_not_decided_by_score_or_same_model_vote():
    values = [
        fragment("0.00", [10, 10, 35, 20], score=0.999),
        fragment("8.00", [10, 10, 35, 20], score=0.9999),
    ]
    groups = group_projections(values)
    assert groups[0]["status"] == "requires_regional_read"
    assert {f["text"] for f in groups[0]["alternates"]} == {"0.00", "8.00"}


def test_identical_amounts_in_distinct_positions_stay_distinct_and_do_not_concatenate():
    groups = group_projections(
        [fragment("100", [10, 10, 30, 20]), fragment("100", [50, 10, 70, 20])]
    )
    assert len(groups) == 2 and compose_literal(groups) == "100 100"


def grid_fixture(ink=True):
    from tests.remediation.test_round11_ocr import fixture

    artifact, candidate = fixture()
    image = Image.new("RGB", (400, 100), "white")
    d = ImageDraw.Draw(image)
    for x in [0, 100, 200, 300, 399]:
        d.line((x, 0, x, 99), fill="black", width=2)
    for y in [0, 50, 99]:
        d.line((0, y, 399, y), fill="black", width=2)
    if ink:
        d.text((220, 65), "100", fill="black")
        d.text((320, 65), "100", fill="black")
    parent = candidate.provenance["tile_context"]["parent_image_sha256"]
    grid = physical_grid_tables(
        image,
        file_hash=artifact.source_file_hash,
        page=1,
        pixel_to_pdf=Affine(),
        parent_image_sha256=parent,
    )[0]
    return artifact, grid


def test_source_bound_raw_fragments_join_one_grid_without_value_matching():
    artifact, grid = grid_fixture()
    original = deepcopy(grid.to_dict())
    aligned = align_grid_fragments(artifact, grid)
    amounts = [c for c in aligned["cells"] if c["row_idx"] == 1 and c["col_idx"] in [2, 3]]
    assert [c["raw_text"] for c in amounts] == ["100", "100"]
    assert len({c["physical_source_id"] for c in amounts}) == 2
    assert all(c["status"] == "resolved" for c in amounts)
    assert grid.to_dict() == original
    assert all(
        f["granularity"] == "fragment"
        for c in amounts
        for g in c["fragment_groups"]
        for f in g["alternates"]
    )
    # 父图或原始响应不匹配时，保留未知格，不能复用不相干的图块。
    grid.cells[0].ocr_provenance["physical_grid"]["parent_image_sha256"] = "f" * 64
    assert not collect_bound_fragments(artifact, grid)


def test_ocr_word_in_original_white_cell_remains_conflicting_not_money():
    artifact, grid = grid_fixture(ink=False)
    aligned = align_grid_fragments(artifact, grid)
    amounts = [c for c in aligned["cells"] if c["row_idx"] == 1 and c["col_idx"] in [2, 3]]
    assert all(c["raw_text"] == "100" and c["status"] == "requires_regional_read" for c in amounts)
    assert all(cell.normalized_value_wan is None for cell in grid.cells)
