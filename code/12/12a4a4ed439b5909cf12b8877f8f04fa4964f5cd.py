"""字段判据、纯白测量及断点预算控制；合成机制验证。"""

from pathlib import Path
import json
import pytest
from PIL import Image, ImageDraw

from src.services.ocr_client import OcrPageResult, OcrBlock
from src.services.ocr_tiles import recognize_tiles, merge_recovery_regions
from src.services.input_geometry import Affine
from tests.test_fiscal_input_v2_recovery import printed_image


def options(path):
    return dict(
        file_hash="a" * 64,
        page=1,
        pixel_to_pdf=Affine(),
        client=None,
        model="b" * 64,
        pipeline="c" * 64,
        checkpoint_path=path,
    )


@pytest.mark.asyncio
async def test_semantic_gap_is_recovered_despite_first_pass_covering_all_ink_and_rounds_resume(
    monkeypatch, tmp_path
):
    calls, now = [], [0]
    monkeypatch.setattr("src.services.ocr_tiles.time.monotonic", lambda: now[0])

    async def infer(client, **kwargs):
        context = kwargs["tile_context"]
        calls.append(context["attempt"])
        if context["attempt"] == 1:
            now[0] += 2
        literal = "123.45" if context["attempt"] == 2 else "12%"
        return OcrPageResult(1, blocks=[OcrBlock(literal, 0.999, [0, 0, *context["size_px"]], 1)])

    monkeypatch.setattr("src.services.ocr_client.ocr_page", infer)

    def discover(first, retries):
        last = (retries or first)[-1][1]
        return (
            []
            if last.blocks[0].text == "123.45"
            else [
                {
                    "region_key": "physical-cell-1",
                    "box": [80, 20, 160, 60],
                    "reasons": ["ratio_in_amount_lane"],
                }
            ]
        )

    path = tmp_path / "page.json"
    _, _, first = await recognize_tiles(
        printed_image(), **options(path), soft_budget=1, region_discovery=discover
    )
    assert first["status"] == "pending" and calls == [0, 1]
    _, rechecks, second = await recognize_tiles(
        printed_image(), **options(path), soft_budget=1, region_discovery=discover
    )
    assert second["status"] == "completed" and calls == [0, 1, 2]
    assert second["semantic_requests"]["physical-cell-1"]["reasons"] == ["ratio_in_amount_lane"]
    assert {c["root_image_sha256"] for c, r in rechecks} == {second["identity"]["parent"]}
    await recognize_tiles(printed_image(), **options(path), region_discovery=discover)
    assert calls == [0, 1, 2]


@pytest.mark.asyncio
async def test_white_original_is_measured_without_faking_ocr_response(monkeypatch, tmp_path):
    async def fail(*args, **kwargs):
        raise AssertionError("pure white input must not invoke the model")

    monkeypatch.setattr("src.services.ocr_client.ocr_page", fail)
    path = tmp_path / "white.json"
    for _ in range(2):
        results, retries, state = await recognize_tiles(
            Image.new("RGB", (100, 80), "white"), **options(path)
        )
        assert not results and not retries and not state["completed"]
        assert state["status"] == "completed" and len(state["observed_empty"]) == 1
        assert (
            next(iter(state["observed_empty"].values()))["criterion"]
            == "all_original_rgb_pixels_equal_255"
        )


@pytest.mark.asyncio
async def test_different_identity_keeps_original_journal_and_does_not_reset_rounds(tmp_path):
    path = tmp_path / "white.json"
    await recognize_tiles(Image.new("RGB", (100, 80), "white"), **options(path))
    before = Path(path).read_bytes()
    changed = {**options(path), "model": "d" * 64}
    with pytest.raises(ValueError, match="explicitly migrate"):
        await recognize_tiles(Image.new("RGB", (100, 80), "white"), **changed)
    assert Path(path).read_bytes() == before


def test_adjacent_physical_cells_do_not_merge_just_because_of_crop_padding():
    assert len(merge_recovery_regions([[0, 0, 104, 60], [96, 0, 204, 60]])) == 2
    assert merge_recovery_regions([[0, 0, 104, 60], [3, 4, 100, 56]]) == [[0, 0, 104, 60]]


@pytest.mark.asyncio
async def test_model_pin_error_stops_further_requests_and_keeps_failure_journal(
    monkeypatch, tmp_path
):
    calls = []

    async def infer(client, **kwargs):
        calls.append(kwargs)
        return OcrPageResult(1, error="ValueError: OCR response model fingerprint mismatch")

    monkeypatch.setattr("src.services.ocr_client.ocr_page", infer)
    path = tmp_path / "failed.json"
    with pytest.raises(ValueError, match="failure journal preserved"):
        await recognize_tiles(printed_image(), **options(path))
    state = json.loads(path.read_text())
    assert len(calls) == 1 and len(state["failures"]) == 1 and state["status"] == "pending"
    assert not state["completed"]


@pytest.mark.asyncio
async def test_newly_exposed_semantic_region_appends_to_same_journal_without_resetting_rounds(
    monkeypatch, tmp_path
):
    image = Image.new("RGB", (500, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.text((50, 30), "123", fill="black")
    draw.text((350, 30), "456", fill="black")
    calls = []

    async def infer(client, **kwargs):
        context = kwargs["tile_context"]
        calls.append(context)
        text = (
            "标题"
            if context["attempt"] == 0
            else "金额"
            if context["crop_box_px"][0] < 200
            else "456.00"
        )
        return OcrPageResult(1, blocks=[OcrBlock(text, 0.999, [0, 0, *context["size_px"]], 1)])

    monkeypatch.setattr("src.services.ocr_client.ocr_page", infer)

    def discover(first, retries):
        texts = {b.text for c, r in retries for b in r.blocks}
        if "456.00" in texts:
            return []
        if "金额" in texts:
            return [
                {
                    "region_key": "amount",
                    "box": [330, 20, 430, 70],
                    "reasons": ["newly_exposed_amount_lane"],
                }
            ]
        return [{"region_key": "title", "box": [30, 20, 130, 70], "reasons": ["unmatched_title"]}]

    path = tmp_path / "dynamic.json"
    _, _, first = await recognize_tiles(image, **options(path), region_discovery=discover)
    assert first["status"] == "pending" and first["new_semantic_regions"] == 1
    assert len(calls) == 2 and set(first["semantic_requests"]) == {"title", "amount"}
    _, _, second = await recognize_tiles(image, **options(path), region_discovery=discover)
    assert second["status"] == "completed" and len(calls) == 3
    assert all(set(r["rounds"]) == {"1"} for r in second["regions"].values())
    _, rechecks, third = await recognize_tiles(image, **options(path), region_discovery=discover)
    assert third["status"] == "completed" and len(calls) == 3 and len(rechecks) == 2
