"""OCR 调度：原生分辨率渲染、整页方向探针、首轮文字模式与默认块尺寸。"""
import io

import pytest
from PIL import Image, ImageDraw

from src.services.ocr_client import OcrBlock, OcrPageResult


def scan_pdf(path, image_size=(1653, 2338)):
    import pymupdf as fitz

    image = Image.new("RGB", image_size, "white")
    draw = ImageDraw.Draw(image)
    for y in range(200, 2000, 60):
        draw.rectangle([200, y, 1400, y + 20], fill="black")
    data = io.BytesIO()
    image.save(data, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=data.getvalue())
    vector = doc.new_page(width=595, height=842)
    vector.insert_text((72, 72), "矢量页")
    doc.save(path)
    doc.close()


def test_scans_render_at_their_embedded_resolution(tmp_path):
    import pymupdf as fitz
    from src.services.ocr_tiled_pipeline import native_render_dpi

    path = tmp_path / "scan.pdf"
    scan_pdf(path)
    with fitz.open(path) as doc:
        assert native_render_dpi(doc[0]) == 200  # 200 DPI 扫描不再放大到 300 DPI
        assert native_render_dpi(doc[1]) == 300  # 无整页图像的矢量页沿用 300 DPI
    tiny = tmp_path / "tiny.pdf"
    scan_pdf(tiny, image_size=(400, 566))
    with fitz.open(tiny) as doc:
        assert native_render_dpi(doc[0]) == 150  # 过低分辨率不低于识别下限


def fake_ocr(answers):
    """按请求图像的方向返回预设识别结果：竖排文字框、正确方向高分、倒置低分。"""
    calls = []

    async def ocr_page(client, *, image_png, page_number, timeout_seconds, region_only, tile_context):
        assert region_only and tile_context["inference_mode"] == "text"
        operations = tile_context["preprocessing"]
        angle = operations[0]["degrees_counterclockwise"] if operations else 0
        calls.append(angle)
        tall, score = answers[angle]
        box = [10, 10, 30, 200] if tall else [10, 10, 200, 30]
        return OcrPageResult(page_number, blocks=[OcrBlock("编制部门上海", score, box)] * 4)

    return ocr_page, calls


@pytest.mark.parametrize("answers, hint, expected, probes", [
    # 侧放页：0° 文字框竖长，270° 高分直接采用（提示方向命中，不再试 90°）。
    ({0: (True, .6), 270: (False, .99), 90: (False, .3)}, 270, 270, [0, 270]),
    # 侧放页：提示方向低分，比较另一侧后选 90°。
    ({0: (True, .6), 270: (False, .4), 90: (False, .97)}, 0, 90, [0, 270, 90]),
    # 正放页：文字框横向且高分，只探一次。
    ({0: (False, .99)}, 0, 0, [0]),
    # 倒置页：文字框横向但低分，180° 明显更好。
    ({0: (False, .5), 180: (False, .98)}, 0, 180, [0, 180]),
    # 侧放页按 180° 读：识别器自行转正竖长框也能高分，但文字行仍竖排，不成立。
    ({0: (True, .64), 180: (True, .96), 270: (False, .99)}, 0, 270, [0, 270]),
])
@pytest.mark.asyncio
async def test_orthogonal_orientation_is_decided_by_recognition(tmp_path, monkeypatch, answers, hint,
                                                                  expected, probes):
    import pymupdf as fitz
    from src.services import ocr_client
    from src.services.ocr_tiled_pipeline import detect_orthogonal_rotation

    path = tmp_path / "scan.pdf"
    scan_pdf(path)
    fake, calls = fake_ocr(answers)
    monkeypatch.setattr(ocr_client, "ocr_page", fake)
    with fitz.open(path) as doc:
        chosen, evidence = await detect_orthogonal_rotation(None, doc[0], file_hash="a" * 64,
                                                            page_number=1, hint=hint)
    assert chosen == expected and calls == probes
    assert evidence["used_as_text_evidence"] is False
    assert [p["degrees_counterclockwise"] for p in evidence["probes"]] == probes


def test_first_pass_defaults_to_text_mode_and_square_tiles(monkeypatch):
    from src.services.ocr_tiles import first_pass_mode, tile_profile

    monkeypatch.delenv("OCR_FIRST_PASS_MODE", raising=False)
    monkeypatch.delenv("OCR_TILE_WIDTH", raising=False)
    monkeypatch.delenv("OCR_TILE_HEIGHT", raising=False)
    assert first_pass_mode() == "text" and tile_profile() == (512, 512)
    monkeypatch.setenv("OCR_FIRST_PASS_MODE", "pages")
    with pytest.raises(ValueError):
        first_pass_mode()


def test_skewed_broken_scan_line_is_removed_but_glyphs_stay():
    import numpy as np
    from src.services.ocr_tiles import _visible_text_ink

    image = Image.new("RGB", (1000, 200), "white")
    draw = ImageDraw.Draw(image)
    # 扫描网线：3 像素粗、带断口，并在中途下移一行（斜扫换行）。
    for x in range(50, 950):
        if x % 97 in (0, 1):
            continue
        y = 100 if x < 500 else 101
        draw.line([(x, y), (x, y + 2)], fill="black")
    draw.rectangle([100, 30, 128, 58], fill="black")  # 一个字大小的墨迹块
    ink = _visible_text_ink(image, independent_lines=True)
    assert not ink[95:110].any()
    assert ink[30:59, 100:129].all()


@pytest.mark.asyncio
async def test_scheduled_recheck_only_reads_untrusted_text_boxes(monkeypatch, tmp_path):
    from src.services.input_geometry import Affine
    from src.services.ocr_tiles import recognize_tiles

    image = Image.new("RGB", (240, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 40, 140, 60], fill="black")    # 读出但低分
    draw.rectangle([40, 150, 140, 170], fill="black")  # 首轮没有任何文字框
    calls = []

    async def infer(client, **kwargs):
        context = kwargs["tile_context"]
        calls.append((context["attempt"], context["crop_box_px"]))
        if context["attempt"] == 0:
            return OcrPageResult(1, blocks=[OcrBlock("一般公共服务", 0.9, [40, 40, 141, 61])])
        width, height = context["size_px"]
        return OcrPageResult(1, blocks=[OcrBlock("一般公共服务", 0.99, [1, 1, width - 1, height - 1])])

    monkeypatch.setattr("src.services.ocr_client.ocr_page", infer)
    _, rechecks, state = await recognize_tiles(
        image, file_hash="a" * 64, page=1, pixel_to_pdf=Affine(), client=None,
        model="b" * 64, pipeline="c" * 64, checkpoint_path=tmp_path / "page.json", dpi=200)
    assert [attempt for attempt, _ in calls] == [0, 1]
    assert calls[1][1][1] < 60 and calls[1][1][3] < 150  # 只重读低分框附近
    assert [region["status"] for region in state["regions"].values()] == ["recovered"]
    assert len(state["unread_regions"]) == 1 and state["unread_regions"][0][1] >= 140
    assert state["status"] == "review_required"
