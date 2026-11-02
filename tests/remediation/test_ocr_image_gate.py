from types import SimpleNamespace
from unittest.mock import AsyncMock

import pymupdf
import pytest

from src.schemas.parse_artifact import ParsePageState
from src.services.ocr_pipeline import image_page_gate, maybe_ocr_scanned_pages


def make_pdf(tmp_path, *, image: bool):
    document = pymupdf.open()
    page = document.new_page()
    if image:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
        pixmap.set_rect(pixmap.irect, (255, 0, 0))
        page.insert_image(pymupdf.Rect(50, 50, 200, 200), pixmap=pixmap)
    path = tmp_path / ("image.pdf" if image else "blank.pdf")
    document.save(path)
    document.close()
    return path


def test_image_gate_skips_blank_no_text_page(tmp_path):
    blank = make_pdf(tmp_path, image=False)
    pages, blanks, error = image_page_gate(str(blank), [1], no_text_pages={1})
    assert pages == [] and blanks == [1] and error is None


def test_image_gate_keeps_no_text_image_page(tmp_path):
    image = make_pdf(tmp_path, image=True)
    pages, blanks, error = image_page_gate(str(image), [1], no_text_pages={1})
    assert pages == [1] and blanks == [] and error is None


def test_image_gate_never_drops_text_pages(tmp_path):
    blank = make_pdf(tmp_path, image=False)
    pages, blanks, error = image_page_gate(str(blank), [1], no_text_pages=set())
    assert pages == [1] and blanks == [] and error is None


@pytest.mark.asyncio
async def test_blank_scan_page_is_skipped_without_ocr(monkeypatch, tmp_path):
    from src.services import ocr_pipeline

    blank = make_pdf(tmp_path, image=False)
    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.setenv("OCR_IMAGE_GATE", "true")
    monkeypatch.setenv("OCR_MODEL_FINGERPRINT", "a" * 64)
    monkeypatch.setattr(
        ocr_pipeline,
        "check_ocr_health",
        AsyncMock(return_value={
            "ok": True,
            "model_fingerprint": "a" * 64,
            "pipeline_fingerprint": "b" * 64,
        }),
    )
    artifact = SimpleNamespace(
        page_states=[ParsePageState(page_number=1, has_text_layer=False, text_chars=0, is_scanned=True)],
        page_count=1,
        reading_assessment={},
    )
    assessment = {"scanned_pages": [1]}
    summary = await maybe_ocr_scanned_pages(str(blank), assessment, artifact)
    assert summary["status"] == "no_image_pages"
    assert summary["requested_pages"] == [1]
    assert summary["image_pages"] == []
    assert summary["blank_pages"] == [1]
    assert summary["usable_for_rules"] is False
    assert assessment["blank_pages"] == [1]
