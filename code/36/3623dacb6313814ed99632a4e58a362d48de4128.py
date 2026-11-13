"""恢复机制反例；合成控制不计入真实材料配额。"""

import json
from decimal import Decimal
import pytest
from PIL import Image, ImageDraw
from src.services.input_geometry import Affine
from src.services.ocr_client import OcrBlock, OcrPageResult
from src.services.ocr_tiles import recognize_tiles, seam_regions


def printed_image():
    image = Image.new("RGB", (240, 100), "white")
    ImageDraw.Draw(image).text((100, 30), "123.45", fill="black")
    return image


async def run(image, path, **kwargs):
    return await recognize_tiles(
        image,
        file_hash="a" * 64,
        page=1,
        pixel_to_pdf=Affine(),
        client=None,
        model="b" * 64,
        pipeline="c" * 64,
        checkpoint_path=path,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_exhausted_two_rounds_remain_unresolved_and_do_not_reset(monkeypatch, tmp_path):
    calls = []

    async def infer(client, **kwargs):
        calls.append(kwargs["tile_context"]["attempt"])
        return OcrPageResult(1, blocks=[])

    monkeypatch.setattr("src.services.ocr_client.ocr_page", infer)
    path = tmp_path / "page.json"
    for _ in range(3):
        _, _, state = await run(printed_image(), path)
        assert state["status"] == "review_required" and state["unresolved"] and not state["pending"]
    assert calls == [0, 1, 2]
    rounds = next(iter(state["regions"].values()))["rounds"]
    assert set(rounds) == {"1", "2"}


@pytest.mark.asyncio
async def test_pause_inside_recheck_reuses_saved_first_round(monkeypatch, tmp_path):
    calls, now = [], [0]
    monkeypatch.setattr("src.services.ocr_tiles.time.monotonic", lambda: now[0])

    async def infer(client, **kwargs):
        attempt = kwargs["tile_context"]["attempt"]
        calls.append(attempt)
        if attempt == 1:
            now[0] = 2
        size = kwargs["tile_context"]["size_px"]
        return OcrPageResult(
            1, blocks=[OcrBlock("123.45", 0.999, [0, 0, *size], 1)] if attempt == 2 else []
        )

    monkeypatch.setattr("src.services.ocr_client.ocr_page", infer)
    path = tmp_path / "page.json"
    _, _, first = await run(printed_image(), path, soft_budget=1)
    assert first["status"] == "pending" and calls == [0, 1]
    _, returned, second = await run(printed_image(), path, soft_budget=1)
    assert second["status"] == "completed" and calls == [0, 1, 2]
    assert any(response.blocks for _, response in returned)
    _, returned, final = await run(printed_image(), path)
    assert calls == [0, 1, 2] and returned and final["status"] == "completed"


def test_high_score_partial_at_internal_edge_always_proposes_expanded_read():
    context = {"size_px": [960, 640], "crop_box_px": [1000, 300, 1960, 940]}
    response = OcrPageResult(8, blocks=[OcrBlock("0.0", 0.999, [909, 90, 960, 130])])
    boxes = seam_regions(context, response, (2480, 3508))
    assert len(boxes) == 1 and boxes[0][2] > 1960
    assert boxes[0][0] < 1909 and boxes[0][3] > 430
    # 页面本身的边缘不伪装成“原件之外仍有邻块”。
    assert not seam_regions(
        {"size_px": [960, 640], "crop_box_px": [0, 0, 960, 640]}, response, (960, 640)
    )


@pytest.mark.asyncio
async def test_corrupt_saved_tile_is_recomputed(monkeypatch, tmp_path):
    calls = []

    async def infer(client, **kwargs):
        calls.append(0)
        return OcrPageResult(1)

    monkeypatch.setattr("src.services.ocr_client.ocr_page", infer)
    image = Image.new("RGB", (120, 100), "white")
    path = tmp_path / "page.json"
    await run(image, path)
    state = json.loads(path.read_text())
    record = next(iter(state["completed"].values()))
    record["result"]["blocks"] = [
        {"text": "999", "score": 1, "bbox": [0, 0, 10, 10], "page_number": 1}
    ]
    path.write_text(json.dumps(state))
    await run(image, path)
    assert len(calls) == 2


def test_unparsed_source_is_preserved_without_generated_decimal():
    from tests.remediation.test_round16_operand_admission import native_contract

    context, _ = native_contract(raw="1,23.00")
    assert len(context.facts) == 2 and not context.definite_facts()
    assert all(f.raw_value == "1,23.00" and f.normalized_value is None for f in context.facts)
    assert all(f.extra_dims["value_kind"] == "invalid" for f in context.facts)


def test_summary_sides_bind_to_their_own_labels(tmp_path):
    from tests.remediation.test_round6_export_chain import create_pdf
    from src.services.fiscal_input_builder import FiscalInputBuilder

    path = tmp_path / "source.pdf"
    create_pdf(path)
    builder = FiscalInputBuilder()
    artifact = builder.read_native(path)
    context = builder.facts(artifact, org_name="测试局", fiscal_year=2026, report_kind="budget")
    totals = [f for f in context.facts if f.subject_code == "total"]
    assert {(f.indicator, f.subject_name) for f in totals} == {
        ("income_actual", "收入总计"),
        ("expenditure_actual", "支出总计"),
    }
    assert all(f.normalized_value == Decimal("100.00") for f in totals)
    assert {
        tuple(s["source_cell_id"] for s in f.extra_dims["evidence_refs"]) for f in totals
    }.__len__() == 2


@pytest.mark.parametrize("degrees", [90, 180, 270, 2.5, -3])
def test_rotation_metadata_inverts_actual_image_geometry(degrees):
    from src.services.ocr_preprocessing import rotate

    image = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(image).rectangle((40, 20, 60, 30), fill="black")
    result, inverse, ops = rotate(image, degrees)
    forward = inverse.inverse()
    import numpy as np

    ys, xs = np.where(np.asarray(result.convert("L")) < 80)
    expected = forward.box([40, 20, 61, 31])
    assert [min(xs), min(ys), max(xs) + 1, max(ys) + 1] == pytest.approx(expected, abs=2)
    for point in [(0, 0), (40, 20), (200, 100)]:
        assert inverse.point(*forward.point(*point)) == pytest.approx(point)
    assert ops[0]["geometry_inverse_verified"] and ops[0]["input_size"] == [200, 100]


def test_deskew_uses_layout_ink_and_leaves_empty_page_alone():
    from src.services.ocr_preprocessing import estimate_layout_angle

    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    for y in range(40, 350, 20):
        draw.rectangle((50, y, 550, y + 2), fill="black")
    tilted = image.rotate(3, expand=True, fillcolor="white")
    assert estimate_layout_angle(tilted) == pytest.approx(-3, abs=0.5)
    assert estimate_layout_angle(Image.new("RGB", (300, 200), "white")) == 0


def test_complete_v3_first_pass_can_enter_without_same_model_vote():
    from tests.remediation.test_round11_ocr import fixture
    from src.services.ocr_qualification import qualification

    artifact, candidate = fixture()
    for cell in candidate.cells:
        cell.pop("region_recheck", None)
    transform = artifact.coordinate_transforms[candidate.coordinate_transform_id]
    transform["region_requests"] = []
    result = qualification(
        candidate, artifact, organization="测试局", year=2025, report_kind="final"
    )
    assert result["status"] == "qualified", result
    candidate.provenance["schema_version"] = "gbc.ocr.cells.v2"
    assert (
        qualification(candidate, artifact, organization="测试局", year=2025, report_kind="final")[
            "status"
        ]
        == "rejected"
    )


def test_high_score_truncated_first_pass_is_not_complete():
    from src.services.ocr_qualification import complete_first_pass_cell

    cell = {"fragments": [{"text": "0.0", "score": 0.999, "bbox": [909, 90, 960, 130]}]}
    context = {
        "size_px": [960, 640],
        "crop_box_px": [1000, 300, 1960, 940],
        "parent_size_px": [2480, 3508],
    }
    assert not complete_first_pass_cell(cell, context)


def test_pdf_object_reordering_keeps_amounts_and_real_character_indexes(tmp_path):
    import hashlib
    import fitz
    import pdfplumber
    from src.services.fiscal_input_builder import FiscalInputBuilder

    signatures = []
    renders = []
    for reverse in [False, True]:
        path = tmp_path / f"{reverse}.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=650, height=400)
            for x in [40, 140, 300, 450, 600]:
                page.draw_line((x, 110), (x, 240))
            for y in [110, 155, 195, 240]:
                page.draw_line((40, y), (600, y))
            placements = []
            for x, y, text in [
                (40, 40, "测试局2024年度部门决算"),
                (40, 70, "收入决算表"),
                (40, 95, "单位：万元"),
            ]:
                placements.extend((x + i * 10, y, c) for i, c in enumerate(text))
            for row, values in enumerate(
                [
                    ["科目编码", "科目名称", "本年收入合计", "财政拨款收入"],
                    ["", "合计", "123.45", "123.45"],
                    ["201", "一般公共服务支出", "23.45", "23.45"],
                ]
            ):
                for col, text in enumerate(values):
                    placements.extend(
                        ([40, 140, 300, 450][col] + 5 + i * 10, 135 + 40 * row, c)
                        for i, c in enumerate(text)
                    )
            for x, y, char in reversed(placements) if reverse else placements:
                page.insert_text((x, y), char, fontname="china-s", fontsize=10)
            pdf.save(path)
            renders.append(hashlib.sha256(page.get_pixmap().samples).hexdigest())
        builder = FiscalInputBuilder()
        artifact = builder.read_native(path)
        context = builder.facts(artifact, org_name="测试局", fiscal_year=2024, report_kind="final")
        assert context and len(context.definite_facts()) == 4
        signatures.append(sorted((f.indicator, f.subject_name, f.raw_value) for f in context.facts))
        with pdfplumber.open(path) as pdf:
            for fact in context.facts:
                for ref in fact.extra_dims["evidence_refs"]:
                    sources = ref["source_provenance"]["native_text_sources"]
                    assert sources and "".join(s["text"] for s in sources) == fact.raw_value
                    for source in sources:
                        chars = pdf.pages[source["physical_page"] - 1].chars
                        assert (
                            "".join(
                                c["text"]
                                for c in chars[
                                    source["start_char_index"] : source["end_char_index"]
                                ]
                            )
                            == source["text"]
                        )
    assert renders[0] == renders[1] and signatures[0] == signatures[1]


def test_center_overlap_does_not_hide_missing_visible_digits():
    from src.services.ocr_tiles import ink_regions, uncovered_regions

    image = Image.new("RGB", (200, 80), "white")
    ImageDraw.Draw(image).text((30, 30), "123456789", fill="black")
    # 高分片段只覆盖中部；左右仍有原图墨迹。
    blocks = [OcrBlock("456", 0.999, [48, 29, 67, 43], 1)]
    assert uncovered_regions(ink_regions(image), blocks, image=image)
    full = [OcrBlock("123456789", 0.999, [28, 28, 95, 45], 1)]
    assert not uncovered_regions(ink_regions(image), full, image=image)


def test_unresolved_ink_blocks_only_affected_cell_not_all_other_amounts():
    from tests.remediation.test_round11_ocr import fixture
    from src.services.ocr_qualification import qualification

    artifact, candidate = fixture()
    for cell in candidate.cells:
        cell.pop("region_recheck", None)
    transform = artifact.coordinate_transforms[candidate.coordinate_transform_id]
    transform["page_recovery"] = {
        "status": "review_required",
        "unresolved_pdf_regions": [[210, 60, 230, 70]],
    }
    kwargs = {"organization": "测试局", "year": 2025, "report_kind": "final"}
    bad = qualification(candidate, artifact, cell_position=(1, 2), **kwargs)
    good = qualification(candidate, artifact, cell_position=(1, 3), **kwargs)
    assert bad["status"] == "rejected" and "cell_region_recovery_incomplete" in bad["reasons"]
    assert good["status"] == "qualified", good


def test_authoritative_empty_snapshot_blocks_grid_fallback_and_keeps_text_checks():
    from src.schemas.parse_artifact import ParseArtifact
    from src.services.fiscal_input_builder import FiscalInputBuilder
    from src.engine.fact_context import FactContext, resolve_operand, apply_fact_overlay

    context = FiscalInputBuilder().facts(
        ParseArtifact("a" * 64), org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    assert context is not None and not context.facts and context.input_status == "no_fiscal_facts"
    assert resolve_operand(context, "total_actual", table_code="FIN_02_income").status == "blocked"
    snapshot = context.to_dict()
    assert FactContext.from_dict(snapshot).to_dict() == snapshot
    assert apply_fact_overlay(context, []).input_status == context.input_status
    snapshot["input_status"] = "parsed"
    with pytest.raises(ValueError, match="SHA-256"):
        FactContext.from_dict(snapshot)
