"""结构存在性视图独立于财政数字资格；OCR 可证明标题线索，不能证明缺失。"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ocr_service.protocol import valid_score


def build_structure_view(
    page_texts: Sequence[str], artifact: Any = None,
    page_assessment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    texts = [str(text or "") for text in page_texts]
    scanned = set((page_assessment or {}).get("scanned_pages") or [])
    unknown = {index + 1 for index, text in enumerate(texts) if not text.strip()} | scanned
    hints: dict[int, list[str]] = {}
    for block in getattr(artifact, "text_blocks", []) or []:
        page = block.page_number
        score = valid_score(block.confidence)
        if (block.extraction_method != "ocr" or isinstance(page, bool)
            or not isinstance(page, int) or not 1 <= page <= len(texts)
            or score is None or score < .90):
            continue
        hints.setdefault(page, []).append(block.text)
    for page, blocks in hints.items():
        texts[page - 1] += "\n" + "\n".join(blocks)
    return {
        "page_texts": texts,
        "page_count": len(texts),
        "unknown_pages": sorted(unknown),
        "ocr_hint_pages": sorted(hints),
        "absence_check_ready": bool(texts) and not unknown,
        "basis": "native_structure_with_review_only_ocr_hints",
        "reason": "扫描页或无文本页未完整核验，OCR标题线索不证明其他内容缺失" if unknown else None,
    }


def structure_view_for_document(doc: Any) -> dict[str, Any]:
    view = getattr(doc, "structure_view", None)
    if isinstance(view, dict):
        return view
    return build_structure_view(getattr(doc, "page_texts", []) or [])
