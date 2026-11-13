"""文档预算耗尽是待调度，不等同于识别完成。"""

import hashlib

import fitz
import pytest

from src.schemas.parse_artifact import ParseArtifact
from src.services import ocr_tiled_pipeline as pipeline


@pytest.mark.asyncio
async def test_document_slice_marks_unscheduled_pages_pending_then_resume(monkeypatch, tmp_path):
    path = tmp_path / "three-pages.pdf"
    with fitz.open() as pdf:
        for index in range(3):
            page = pdf.new_page(width=100, height=80)
            page.insert_text((15, 30), str(index))
        pdf.save(path)
    now = [0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
    oriented, seen = [], []

    async def orientation(*args, **kwargs):
        oriented.append(kwargs["page"])
        return {"angle": 0, "status": "resolved"}

    async def tiles(image, **kwargs):
        seen.append(kwargs["page"])
        assert kwargs["soft_budget"] <= 600
        now[0] += 601 if len(seen) == 1 else 0
        return (
            [],
            [],
            {"status": "completed", "pending": [], "unresolved": [], "elapsed_seconds": 601},
        )

    monkeypatch.setattr(pipeline, "select_orientation", orientation)
    monkeypatch.setattr(pipeline, "recognize_tiles", tiles)
    artifact = ParseArtifact(hashlib.sha256(path.read_bytes()).hexdigest(), page_count=3)
    health = {"model_fingerprint": "a" * 64, "pipeline_fingerprint": "b" * 64}
    first = await pipeline.recognize_document_tiles(path, [1, 2, 3], artifact, health)
    assert seen == oriented == [1]
    assert first["status"] == "pending" and first["failed_pages"] == [2, 3]
    assert [p.get("pending_phase") for p in first["pages"]][1:] == ["document_schedule"] * 2
    second = await pipeline.recognize_document_tiles(path, [2, 3], artifact, health)
    assert seen == [1, 2, 3] and second["status"] == "review_required"
    assert not second["failed_pages"] and not second["usable_for_rules"]


@pytest.mark.asyncio
async def test_zero_remaining_document_budget_does_not_render_or_infer(monkeypatch):
    artifact = ParseArtifact("a" * 64)

    # 无需打开 PDF 的证据通过 mock 句柄确保页面获取不发生。
    class EmptyPdf:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __getitem__(self, index):
            raise AssertionError("must not render unscheduled page")

    monkeypatch.setattr(fitz, "open", lambda _: EmptyPdf())
    result = await pipeline.recognize_document_tiles(
        "not-opened.pdf",
        [1],
        artifact,
        {"model_fingerprint": "b" * 64, "pipeline_fingerprint": "c" * 64},
        document_soft_budget=0,
    )
    assert result["status"] == "pending" and result["failed_pages"] == [1]
