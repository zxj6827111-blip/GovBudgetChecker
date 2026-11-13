"""方向选择的合成机制控制，不计作真实倾斜/扫描材料验收。"""

import pytest
from PIL import Image, ImageDraw

from src.services.ocr_client import OcrPageResult, OcrBlock
from src.services.input_geometry import Affine, raster_dpi
from src.services.ocr_orientation import select_orientation, choose_angle
from ocr_service.protocol import validate_tile_context


def image_fixture():
    image = Image.new("RGB", (900, 700), "white")
    draw = ImageDraw.Draw(image)
    draw.text((200, 100), "public budget", fill="black")
    return image, image.resize((450, 350))


def options(path):
    return dict(
        file_hash="a" * 64,
        page=1,
        pixel_to_pdf=Affine(a=0.24, d=0.24),
        model="b" * 64,
        pipeline="c" * 64,
        client=None,
        checkpoint_path=path,
    )


@pytest.mark.asyncio
async def test_real_crop_geometry_and_direction_decision_are_reused_on_resume(
    monkeypatch, tmp_path
):
    calls = []

    async def infer(client, **kwargs):
        context = kwargs["tile_context"]
        validate_tile_context(context, kwargs["image_png"], context["size_px"])
        rotations = [
            op["degrees_counterclockwise"]
            for op in context["preprocessing"]
            if op["operation"] == "rotate"
        ]
        angle = rotations[-1] if rotations else 0
        calls.append(angle)
        assert context["preprocessing"][0]["operation"] == "source_crop"
        assert raster_dpi(context) == pytest.approx(300)
        text = "测试单位一般公共预算收入支出表" if angle == 90 else "123"
        return OcrPageResult(1, blocks=[OcrBlock(text, 0.995, [10, 10, 200, 40], 1)])

    monkeypatch.setattr("src.services.ocr_orientation.ocr_page", infer)
    image, layout = image_fixture()
    path = tmp_path / "orientation.json"
    first = await select_orientation(image, layout, **options(path))
    assert first["angle"] == 90 and first["status"] == "resolved" and calls == [0, 90, 180, 270]
    second = await select_orientation(image, layout, **options(path))
    assert second["angle"] == 90 and calls == [0, 90, 180, 270]
    assert second["evidence_role"] == "same_model_direction_selection_not_independent_accuracy_vote"


@pytest.mark.asyncio
async def test_orientation_budget_keeps_pending_probe_and_does_not_restart_completed_work(
    monkeypatch, tmp_path
):
    now, calls = [0], []
    monkeypatch.setattr("src.services.ocr_orientation.time.monotonic", lambda: now[0])

    async def infer(client, **kwargs):
        calls.append(kwargs["tile_context"]["tile_id"])
        if len(calls) == 1:
            now[0] += 2
        return OcrPageResult(
            1, blocks=[OcrBlock("测试单位预算公开情况说明", 0.995, [10, 10, 200, 40], 1)]
        )

    monkeypatch.setattr("src.services.ocr_orientation.ocr_page", infer)
    image, layout = image_fixture()
    path = tmp_path / "orientation.json"
    first = await select_orientation(image, layout, **options(path), soft_budget=1)
    assert first["status"] == "pending" and len(calls) == 1
    second = await select_orientation(image, layout, **options(path), soft_budget=1)
    assert second["status"] == "ambiguous" and len(calls) == 4
    before = path.read_bytes()
    with pytest.raises(ValueError, match="preserve journal"):
        await select_orientation(image, layout, **{**options(path), "model": "f" * 64})
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_pure_white_page_has_no_model_calls(monkeypatch, tmp_path):
    async def fail(*args, **kwargs):
        raise AssertionError("no text to orient")

    monkeypatch.setattr("src.services.ocr_orientation.ocr_page", fail)
    result = await select_orientation(
        Image.new("RGB", (200, 150), "white"),
        Image.new("RGB", (100, 75), "white"),
        **options(tmp_path / "white.json"),
    )
    assert result["status"] == "resolved" and result["angle"] == 0 and not result["completed"]


def test_ambiguous_direction_is_not_certified_by_same_model_counts():
    assert choose_angle({0: 20, 90: 0, 180: 20, 270: 0}) == (0, "ambiguous")
    assert choose_angle({0: 0, 90: 3, 180: 0, 270: 0}) == (0, "ambiguous")


@pytest.mark.asyncio
async def test_health_and_inference_endpoint_must_match_before_opening_material(monkeypatch):
    from src.services.ocr_tiled_pipeline import recognize_document_tiles

    monkeypatch.setenv("OCR_SERVICE_URL", "http://127.0.0.1:9010")
    with pytest.raises(ValueError, match="health endpoint differs"):
        await recognize_document_tiles(
            "must-not-be-opened.pdf", [1], None, {"service_url": "http://127.0.0.1:19017"}
        )


@pytest.mark.asyncio
async def test_second_regional_read_keeps_confirmed_direction_instead_of_flipping_six_and_nine(
    monkeypatch, tmp_path
):
    import fitz
    import hashlib
    from src.schemas.parse_artifact import ParseArtifact
    from src.services import ocr_tiled_pipeline

    source = tmp_path / "source.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=300, height=200)
        page.insert_text((20, 40), "6 9 16.69", fontsize=12)
        pdf.save(source)

    async def orientation(*args, **kwargs):
        return {"angle": 0, "status": "resolved"}

    async def tiles(image, **kwargs):
        for attempt, dpi in [(1, 450), (2, 600)]:
            crop, box, affine, operations = kwargs["high_resolution"]([10, 10, 300, 200], attempt)
            assert affine.a == pytest.approx(72 / dpi) and affine.d == pytest.approx(72 / dpi)
            assert not any(op.get("degrees_counterclockwise") == 180 for op in operations)
        return (
            [],
            [],
            {"status": "completed", "pending": [], "unresolved": [], "elapsed_seconds": 0},
        )

    monkeypatch.setattr(ocr_tiled_pipeline, "select_orientation", orientation)
    monkeypatch.setattr(ocr_tiled_pipeline, "recognize_tiles", tiles)
    artifact = ParseArtifact(hashlib.sha256(source.read_bytes()).hexdigest(), page_count=1)
    await ocr_tiled_pipeline.recognize_document_tiles(
        source, [1], artifact, {"model_fingerprint": "a" * 64, "pipeline_fingerprint": "b" * 64}
    )
