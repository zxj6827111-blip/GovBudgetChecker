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
    *,
    extra_unit=None,
    amount="123.45",
    low_header=False,
    title="2025年部门收入决算表",
    page=1,
    low_context=None,
    body_code="201",
    empty_code_mark=None,
    income_header="本年收入合计",
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
        ["科目编码", "科目名称", income_header, "财政拨款收入"],
        ["", "合计", amount, amount],
        [body_code, "一般公共服务支出", "23.45", "23.45"],
    ]
    for row, items in enumerate(values):
        for col, text in enumerate(items):
            if text:
                tokens.append((text, [xs[col] + 10, ys[row] + 12, xs[col + 1] - 10, ys[row] + 30]))
    for _, box in tokens:
        draw.text((box[0] + 5, box[1] + 3), "x", fill="black")
    if empty_code_mark is not None:
        draw.point(empty_code_mark, fill=(230, 230, 230))
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
                0.97 if (low_header and t == "财政拨款收入") or t == low_context else 0.995
                for t, b in tokens
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


def test_explicit_total_does_not_invent_code_from_weak_measured_edge_fringe():
    artifact = fixture(empty_code_mark=(70, 174))
    physical = deepcopy([g.to_dict() for g in artifact.source_grids])
    context = FiscalInputBuilder().facts(artifact)
    totals = [f for f in context.definite_facts() if f.subject_name == "合计"]
    assert len(totals) == 2 and all(f.normalized_value == Decimal("123.45") for f in totals)
    code = next(c for c in artifact.source_grids[0].cells if c.row_idx == 1 and c.col_idx == 0)
    assert not code.raw_text and code.ocr_provenance["physical_grid"]["cell"]["observed_soft_ink_runs_px"]
    assert [g.to_dict() for g in artifact.source_grids] == physical


def test_unread_weak_ink_inside_code_cell_is_not_treated_as_edge_or_blank():
    artifact = fixture(empty_code_mark=(70, 195))
    context = FiscalInputBuilder().facts(artifact)
    assert not [f for f in context.definite_facts() if f.subject_name == "合计"]
    assert len(context.definite_facts()) == 2


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


def test_correct_organization_declaration_is_not_mistaken_for_invalid_monetary_unit():
    from src.services.ocr_grid_fragments import grid_recovery_regions

    artifact = fixture()
    requests = grid_recovery_regions(artifact, artifact.source_grids)
    assert not any(r["region_key"].endswith(":unit_declaration") for r in requests)


def test_low_or_clipped_critical_context_is_reread_even_when_literal_is_legal():
    from src.services.ocr_grid_fragments import grid_recovery_regions

    for literal, role in [
        ("2025年部门收入决算表", "table_title"),
        ("编制单位：测试局", "organization_declaration"),
        ("单位：万元", "unit_declaration"),
    ]:
        artifact = fixture(low_context=literal)
        requests = grid_recovery_regions(artifact, artifact.source_grids)
        assert any(r["region_key"].endswith(":" + role) for r in requests), literal


def test_pending_broad_region_keeps_local_complete_cells_and_preserves_pending_source_observation():
    artifact = fixture()
    for transform in artifact.coordinate_transforms.values():
        transform["page_recovery"] = {
            "status": "pending",
            "unresolved_pdf_regions": [[0, 0, 720, 310]],
            "budget_is_soft": True,
        }
    context = FiscalInputBuilder().facts(artifact)
    assert len(context.definite_facts()) == 4
    assert all(
        transform["page_recovery"]["status"] == "pending"
        for transform in artifact.coordinate_transforms.values()
    )
    groups = context.facts[0].extra_dims["ocr_provenance"]["fragment_groups"]
    assert groups[0]["selected"]["source_region_pending"] is True


def test_index_header_is_not_appended_to_the_amount_indicator_path():
    from src.services.ocr_grid_reconstruction import header_paths, semantic_mappings

    rows = [
        ["科目名称", "本年收入合计", "财政拨款收入"],
        ["栏次", "1", "2"],
        ["合计", "123.45", "123.45"],
    ]
    cells = [
        {
            "row_idx": row,
            "col_idx": col,
            "col_span": 1,
            "raw_text": text,
            "physical_source_id": f"r{row}c{col}",
        }
        for row, values in enumerate(rows)
        for col, text in enumerate(values)
    ]
    depth, paths = header_paths(cells, 3, 3)
    assert depth == 2
    assert [c["raw_text"] for c in paths[1]] == ["本年收入合计"]
    mappings = semantic_mappings("FIN_02_income", paths)
    assert {(m["source_col_idx"], m["canonical_measure"]) for m in mappings} == {
        (1, "total_actual"),
        (2, "fiscal_allocation"),
    }
    assert cells[4]["raw_text"] == "1"


def test_project_expenditure_measure_is_not_mistaken_for_the_project_name_lane():
    from src.services.ocr_grid_reconstruction import semantic_mappings

    headers = ["科目名称", "本年支出合计", "基本支出", "项目支出"]
    paths = {
        col: [{"raw_text": text, "physical_source_id": f"header-{col}"}]
        for col, text in enumerate(headers)
    }
    mappings = semantic_mappings("FIN_03_expenditure", paths)
    project = next(m for m in mappings if m["canonical_measure"] == "project_actual")
    assert project["source_col_idx"] == 3 and project["source_label_cols"] == [0]



def test_conflicting_amount_year_requests_header_read_even_when_amounts_are_correct():
    from src.services.ocr_grid_fragments import grid_recovery_regions

    artifact = fixture(income_header="2023年与2024年本年收入合计")
    source = deepcopy([g.to_dict() for g in artifact.source_grids])
    requests = grid_recovery_regions(artifact, artifact.source_grids)
    header = [request for request in requests
              if "temporal_header_requires_regional_read" in request["reasons"]]
    assert len(header) == 1 and header[0]["region_key"].endswith(":r0c2")
    assert "multiple_explicit_amount_years" in header[0]["reasons"]
    context = FiscalInputBuilder().facts(artifact)
    assert len(context.facts) == 4
    invalid = [f for f in context.facts if f.indicator == "total_actual"]
    assert len(invalid) == 2 and {f.raw_value for f in invalid} == {"123.45", "23.45"}
    assert all(f.fiscal_year is None and f.recognition_status == "low_confidence" for f in invalid)
    assert [g.to_dict() for g in artifact.source_grids] == source
