"""原图物理网格机制验证；合成控制不算真实扫描件验收。"""

from PIL import Image, ImageDraw
from src.services.ocr_image_grid import detect_image_grids, physical_grid_tables
from src.services.input_geometry import Affine
from src.schemas.parse_artifact import CellPresence, ParseArtifact


def image_grid():
    image = Image.new("RGB", (400, 220), "white")
    d = ImageDraw.Draw(image)
    for y in [20, 70, 120, 170]:
        d.line((20, y, 320, y), fill="black", width=2)
    for x in [20, 220, 320]:
        d.line((x, 20, x, 170), fill="black", width=2)
    d.line((120, 70, 120, 170), fill="black", width=2)
    d.text((230, 85), "123.45", fill="black")
    return image


def test_original_ink_grid_has_merged_header_and_distinguishes_unknown_from_white():
    image = image_grid()
    grids = detect_image_grids(image)
    assert len(grids) == 1
    grid = grids[0]
    assert grid["row_count"] == grid["column_count"] == 3
    assert len(grid["cells"]) == 8
    merged = next(c for c in grid["cells"] if (c["row_idx"], c["col_idx"]) == (0, 0))
    assert merged["col_span"] == 2 and merged["row_span"] == 1
    assert not grid["incomplete_regions"]
    amount = next(c for c in grid["cells"] if (c["row_idx"], c["col_idx"]) == (1, 2))
    assert not amount["image_blank_verified"] and amount["interior_ink_pixels"] > 0
    assert all(c["image_blank_verified"] for c in grid["cells"] if c is not amount)


def test_physical_grids_preserve_source_identity_without_creating_amounts_or_character_boxes():
    image = image_grid()
    matrix = Affine(a=0, d=0.0, b=0.24, c=-0.24, e=60, f=10)
    tables = physical_grid_tables(image, file_hash="a" * 64, page=2, pixel_to_pdf=matrix)
    table = tables[0]
    assert table.rows[0][1] is None
    assert all(
        c.numeric_value is None and c.normalized_value_wan is None and not c.raw_text
        for c in table.cells
    )
    assert {c.presence for c in table.cells} == {CellPresence.BLANK, CellPresence.UNRECOGNIZED}
    assert all(not c.native_text_sources for c in table.cells)
    for cell in table.cells:
        source = cell.ocr_provenance["physical_grid"]
        assert source["file_hash"] == "a" * 64 and source["page"] == 2
        for pixel, pdf in zip(
            [
                (source["cell"]["bbox_px"][0], source["cell"]["bbox_px"][1]),
                (source["cell"]["bbox_px"][2], source["cell"]["bbox_px"][1]),
                (source["cell"]["bbox_px"][2], source["cell"]["bbox_px"][3]),
                (source["cell"]["bbox_px"][0], source["cell"]["bbox_px"][3]),
            ],
            source["polygon_pdf"],
            strict=True,
        ):
            assert matrix.point(*pixel) == tuple(pdf)
    artifact = ParseArtifact("a" * 64, source_grids=tables)
    assert ParseArtifact.from_dict(artifact.to_dict()).to_dict() == artifact.to_dict()
    assert (
        tables[0].fragment_uid
        == physical_grid_tables(image, file_hash="a" * 64, page=2, pixel_to_pdf=matrix)[
            0
        ].fragment_uid
    )


def test_text_or_horizontal_rules_alone_do_not_become_a_fiscal_grid():
    image = Image.new("RGB", (400, 220), "white")
    d = ImageDraw.Draw(image)
    for y in [30, 80, 130]:
        d.line((20, y, 350, y), fill="black", width=2)
    d.text((30, 50), "123.45", fill="black")
    assert not detect_image_grids(image)


def test_long_dash_inside_a_cell_is_not_erased_as_a_grid_line_or_zero():
    image = image_grid()
    ImageDraw.Draw(image).line((40, 90, 100, 90), fill="black", width=2)
    grids = detect_image_grids(image)
    cell = next(c for c in grids[0]["cells"] if (c["row_idx"], c["col_idx"]) == (1, 0))
    assert not cell["image_blank_verified"] and cell["interior_ink_pixels"] > 0


def test_measured_thick_boundaries_do_not_make_a_white_cell_unreadable_or_erase_its_dash():
    image = Image.new("RGB", (500, 300), "white")
    draw = ImageDraw.Draw(image)
    for x in [30, 250, 470]:
        draw.line((x, 30, x, 270), fill="black", width=12)
    for y in [30, 150, 270]:
        draw.line((30, y, 470, y), fill="black", width=12)
    draw.line((80, 205, 150, 205), fill="black", width=3)
    before = image.tobytes()
    grid = detect_image_grids(image)[0]
    blank = next(c for c in grid["cells"] if (c["row_idx"], c["col_idx"]) == (0, 0))
    dash = next(c for c in grid["cells"] if (c["row_idx"], c["col_idx"]) == (1, 0))
    assert blank["observed_ink_runs_px"] and blank["measured_boundary_ink_runs_px"]
    assert blank["image_blank_verified"] and not blank["content_ink_runs_px"]
    assert dash["content_ink_runs_px"] and not dash["image_blank_verified"]
    assert image.tobytes() == before


def test_decimal_point_and_minus_inside_closed_grid_remain_content_ink():
    image = Image.new("RGB", (500, 300), "white")
    draw = ImageDraw.Draw(image)
    for x in [30, 250, 470]:
        draw.line((x, 30, x, 270), fill="black", width=12)
    for y in [30, 150, 270]:
        draw.line((30, y, 470, y), fill="black", width=12)
    draw.rectangle((100, 90, 102, 92), fill="black")
    draw.line((330, 90, 350, 90), fill="black", width=2)
    cells = detect_image_grids(image)[0]["cells"]
    assert not next(c for c in cells if (c["row_idx"], c["col_idx"]) == (0, 0))[
        "image_blank_verified"
    ]
    assert not next(c for c in cells if (c["row_idx"], c["col_idx"]) == (0, 1))[
        "image_blank_verified"
    ]
