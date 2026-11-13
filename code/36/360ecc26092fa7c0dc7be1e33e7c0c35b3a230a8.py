"""真实片段协议的合成机制控制；不计入真实原件逐格验收。"""

from copy import deepcopy
import hashlib
from decimal import Decimal

from PIL import Image, ImageDraw

from src.schemas.parse_artifact import ParseArtifact
from src.services.input_geometry import Affine
from src.services.ocr_tiles import make_tile, png_bytes, digest
from src.services.ocr_client import OcrPageResult, OcrBlock
from src.services.ocr_tiled_pipeline import append_tile_results
from src.services.ocr_image_grid import physical_grid_tables
from src.services.fiscal_input_builder import FiscalInputBuilder


def fixture(
    *, extra_unit=None, amount="123.45", low_header=False, title="2025年部门收入决算表", page=1
):
    image = Image.new("RGB", (720, 310), "white")
    draw = ImageDraw.Draw(image)
    xs, ys = [20, 120, 280, 470, 700], [120, 170, 220, 270]
    for x in xs:
        draw.line((x, ys[0], x, ys[-1]), fill="black", width=2)
    for y in ys:
        draw.line((xs[0], y, xs[-1], y), fill="black", width=2)
    tokens = [
        (title, [220, 25, 490, 45]),
        ("单位：万元", [550, 75, 680, 95]),
        ("编制单位：测试局", [30, 75, 260, 95]),
    ]
    if extra_unit:
        tokens.append(("单位：" + extra_unit, [300, 75, 430, 95]))
    values = [
        ["科目编码", "科目名称", "本年收入合计", "财政拨款收入"],
        ["", "合计", amount, amount],
        ["201", "一般公共服务支出", "23.45", "23.45"],
    ]
    for row, items in enumerate(values):
        for col, text in enumerate(items):
            if text:
                tokens.append((text, [xs[col] + 10, ys[row] + 12, xs[col + 1] - 10, ys[row] + 30]))
    for _, box in tokens:
        draw.text((box[0] + 5, box[1] + 3), "x", fill="black")
    parent = hashlib.sha256(png_bytes(image)).hexdigest()
    request = make_tile(
        image,
        (0, 0, *image.size),
        parent_sha=parent,
        file_hash="a" * 64,
        page=page,
        pixel_to_pdf=Affine(),
        root_image_sha256=parent,
    )
    raw = {
        "overall_ocr_res": {
            "rec_texts": [t for t, b in tokens],
            "rec_scores": [
                0.97 if low_header and t == "财政拨款收入" else 0.995 for t, b in tokens
            ],
            "rec_polys": [
                [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]] for t, b in tokens
            ],
        }
    }
    source = {
        "schema_version": "gbc.ocr.cells.v3",
        "input_sha256": request.context["input_sha256"],
        "model_fingerprint": "b" * 64,
        "pipeline_fingerprint": "c" * 64,
        "tile_context": request.context,
        "raw_prediction": raw,
        "raw_prediction_sha256": digest(raw),
        "source_size_px": list(image.size),
        "prediction_coordinate_scale": [1.0, 1.0],
    }
    response = OcrPageResult(
        page,
        blocks=[
            OcrBlock(t, s, b, page)
            for (t, b), s in zip(tokens, raw["overall_ocr_res"]["rec_scores"], strict=True)
        ],
        provenance=source,
    )
    artifact = ParseArtifact("a" * 64, page_count=page, ocr_model_fingerprint="b" * 64)
    artifact.source_grids = physical_grid_tables(
        image,
        file_hash=artifact.source_file_hash,
        page=page,
        pixel_to_pdf=Affine(),
        parent_image_sha256=parent,
    )
    append_tile_results(
        artifact,
        [(request.context, response)],
        page_number=page,
        layout_sha="d" * 64,
        page_recovery={},
        page_render={},
    )
    return artifact


def test_source_grid_rebuilds_facts_without_overwriting_physical_cells_or_deduping_same_amount():
    artifact = fixture()
    physical = deepcopy([g.to_dict() for g in artifact.source_grids])
    builder = FiscalInputBuilder()
    context = builder.facts(artifact, org_name="测试局", fiscal_year=2025, report_kind="final")
    assert len(context.facts) == len(context.definite_facts()) == 4
    assert {f.subject_name for f in context.facts} == {"合计", "一般公共服务支出"}
    totals = [f for f in context.facts if f.subject_name == "合计"]
    assert {f.indicator for f in totals} == {"total_actual", "fiscal_allocation"}
    assert all(f.normalized_value == Decimal("123.45") for f in totals)
    assert len({f.physical_source_key() for f in totals}) == 2
    assert all(f.extra_dims["row_identity_sources"] for f in context.facts)
    assert all(
        f.budget_stage == "决算" and f.org_name == "测试局" and f.fiscal_year == 2025
        for f in context.facts
    )
    assert [g.to_dict() for g in artifact.source_grids] == physical
    serialized = artifact.to_dict()
    restored = ParseArtifact.from_dict(serialized)
    again = builder.facts(restored, org_name="测试局", fiscal_year=2025, report_kind="final")
    assert restored.to_dict() == serialized and again.to_dict() == context.to_dict()


def test_conflicting_explicit_units_or_profile_never_become_operands():
    builder = FiscalInputBuilder()
    for artifact, year in [(fixture(extra_unit="元"), 2025)]:
        context = builder.facts(artifact, org_name="测试局", fiscal_year=year, report_kind="final")
        assert len(context.facts) == 4 and not context.definite_facts()
        assert all(f.fiscal_year == 2025 for f in context.facts)
        assert all(f.extra_dims["report_year"] == 2025 for f in context.facts)


def test_low_score_one_header_blocks_only_its_lane_and_illegal_amount_is_retained():
    context = FiscalInputBuilder().facts(
        fixture(low_header=True), org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    assert len(context.facts) == 4 and len(context.definite_facts()) == 2
    assert {f.indicator for f in context.definite_facts()} == {"total_actual"}
    bad = FiscalInputBuilder().facts(
        fixture(amount="1,23.45"), org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    assert len(bad.facts) == 4 and len(bad.definite_facts()) == 2
    assert all(
        f.raw_value == "1,23.45" and f.normalized_value is None
        for f in bad.facts
        if f.subject_name == "合计"
    )


def test_altered_saved_raw_response_cannot_supply_a_grid_cell():
    artifact = fixture()
    transform = next(iter(artifact.coordinate_transforms.values()))
    transform["ocr_response"]["raw_prediction"]["overall_ocr_res"]["rec_texts"][-1] = "999.99"
    context = FiscalInputBuilder().facts(
        artifact, org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    assert not context.facts


def test_column_mapping_overlay_survives_rebuild_on_logical_copy_only():
    artifact = fixture()
    builder = FiscalInputBuilder()
    profile = dict(org_name="测试局", fiscal_year=2025, report_kind="final")
    builder.reconstruct(artifact, **profile)
    original = deepcopy(artifact.to_dict())
    uid = artifact.logical_tables[0].instance_uid
    result = builder.finish(
        artifact,
        overlay_records=[
            {
                "record_id": "manual-map-1",
                "changes": [
                    {
                        "field": "column_mapping",
                        "target_id": uid,
                        "target_type": "table_instance",
                        "value": {
                            "mappings": [
                                {"source_col_idx": 3, "canonical_measure": "total_actual"},
                                {"source_col_idx": 2, "canonical_measure": "fiscal_allocation"},
                            ]
                        },
                        "evidence_ids": ["human-source-review"],
                    }
                ],
            }
        ],
        **profile,
    )
    assert result.overlay_report["column_mapping_overlay"]["applied"] == 1

    def columns(context):
        return {
            f.indicator: f.extra_dims["evidence_refs"][0]["source_cell_id"].split("c")[-1]
            for f in context.facts
        }

    assert columns(result.raw_context) == {"total_actual": "2", "fiscal_allocation": "3"}
    assert columns(result.effective_context) == {"total_actual": "3", "fiscal_allocation": "2"}
    assert artifact.to_dict() == original


def test_unmatched_high_score_title_and_letter_amount_are_reread_without_correct_first_pass_requirement():
    from src.services.ocr_grid_fragments import grid_recovery_regions

    artifact = fixture(title="2025年部门收人决算表")
    requests = grid_recovery_regions(artifact, artifact.source_grids)
    assert any("source_table_title_not_semantically_resolved" in r["reasons"] for r in requests)
    artifact = fixture(amount="O.OO")
    requests = grid_recovery_regions(artifact, artifact.source_grids)
    assert (
        sum("typed_amount_lane_contains_non_amount_literal" in r["reasons"] for r in requests) == 2
    )
