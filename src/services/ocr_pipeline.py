"""OCR 管线挂钩（ZC-02 / SYS-10）：低文本页按需补识别，结果只追加、不改写。

纪律：
- 默认关闭（``OCR_ENABLED``），显式启用且存在低文本页才调用；
- 单并发、逐页渲染释放内存；单页 120 秒预算由客户端控制；
- 识别分数原样保留（``KEY_NUMBER_MIN_SCORE=0.98`` 仅为确定性判定的
  门槛常量，本模块不做业务校准）；
- OCR 输出先进入候选解析对象，不直接进入确定性规则；须通过后续校验才能
  晋升为规则事实。原生文本层与 OCR 冲突时只记录，不静默覆盖。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from src.services.ocr_client import KEY_NUMBER_MIN_SCORE, check_ocr_health, ocr_enabled, ocr_pages
from src.schemas.parse_artifact import OcrTableCandidate, ParsedTextBlock

logger = logging.getLogger(__name__)

#: 渲染 DPI（任务包 OCR 约定：默认 300）
DEFAULT_DPI = 300


def _render_pages_png(pdf_path: str, page_numbers: List[int], dpi: int = DEFAULT_DPI):
    """把指定物理页渲染为 PNG 字节流，并保存逐页坐标变换元数据。"""
    try:
        import fitz
    except Exception:
        logger.warning("fitz unavailable; OCR skipped")
        return None
    images: List[Optional[bytes]] = []
    transforms: Dict[int, Dict[str, Any]] = {}
    try:
        with fitz.open(str(pdf_path)) as document:
            zoom = dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            for page_number in page_numbers:
                if page_number < 1 or page_number > document.page_count:
                    images.append(None)
                    continue
                page = document.load_page(page_number - 1)
                pix = page.get_pixmap(matrix=matrix)
                images.append(pix.tobytes("png"))
                cropbox = page.cropbox
                page_rect = page.rect
                transforms[page_number] = {
                    "dpi": dpi,
                    "scale": zoom,
                    "render_origin_px": [int(pix.x), int(pix.y)],
                    "render_size_px": [int(pix.width), int(pix.height)],
                    "rotation": int(page.rotation or 0),
                    "cropbox": [float(cropbox.x0), float(cropbox.y0), float(cropbox.x1), float(cropbox.y1)],
                    "page_rect": [float(page_rect.x0), float(page_rect.y0), float(page_rect.x1), float(page_rect.y1)],
                    # 只对常规、未旋转且从页面原点开始的页面声明bbox可反变换。
                    "bbox_transform_verified": bool(
                        int(page.rotation or 0) == 0
                        and abs(float(cropbox.x0)) < 0.01
                        and abs(float(cropbox.y0)) < 0.01
                    ),
                }
                pix = None  # 逐页释放图像内存
    except Exception:
        logger.exception("page rendering for OCR failed")
        return None
    return images, transforms


def _pdf_bbox_from_render_bbox(bbox: Any, transform: Dict[str, Any]) -> Optional[List[float]]:
    """仅在已验证的简单坐标变换下，把 OCR 像素框换为 PDF 点坐标。"""
    if not transform.get("bbox_transform_verified") or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        scale = float(transform["scale"])
        origin_x, origin_y = (float(value) for value in transform["render_origin_px"])
        crop_x, crop_y = (float(transform["cropbox"][0]), float(transform["cropbox"][1]))
        x0, y0, x1, y1 = (float(value) for value in bbox)
    except (KeyError, TypeError, ValueError):
        return None
    if scale <= 0 or x1 <= x0 or y1 <= y0:
        return None
    return [
        crop_x + (x0 - origin_x) / scale,
        crop_y + (y0 - origin_y) / scale,
        crop_x + (x1 - origin_x) / scale,
        crop_y + (y1 - origin_y) / scale,
    ]


async def maybe_ocr_scanned_pages(
    pdf_path: str,
    page_assessment: Dict[str, Any],
    artifact: Any = None,
) -> Optional[Dict[str, Any]]:
    """对低文本页执行本地 OCR。未启用或无需识别时返回 None。

    ``artifact``（统一解析对象，可选）存在时把 OCR 页登记进 page_states
    （note="ocr_applied"），原生文本层不被覆盖；候选结果不直接进入规则输入。
    """
    if not ocr_enabled():
        return None
    candidates = list(page_assessment.get("low_text_pages") or page_assessment.get("scanned_pages") or [])
    if not candidates:
        return None

    expected_model_fingerprint = os.getenv("OCR_MODEL_FINGERPRINT", "").strip().lower()
    if len(expected_model_fingerprint) != 64 or any(
        char not in "0123456789abcdef" for char in expected_model_fingerprint
    ):
        return {
            "enabled": True,
            "requested_pages": candidates,
            "pages": [],
            "failed_pages": candidates,
            "status": "model_fingerprint_missing",
            "usable_for_rules": False,
        }
    service_health = await check_ocr_health()
    if not service_health.get("ok") or service_health.get("model_fingerprint") != expected_model_fingerprint:
        return {
            "enabled": True,
            "requested_pages": candidates,
            "pages": [],
            "failed_pages": candidates,
            "status": "model_fingerprint_mismatch" if service_health.get("ok") else "service_unavailable",
            "service_error": service_health.get("error"),
            "usable_for_rules": False,
        }
    if artifact is None:
        return {
            "enabled": True,
            "requested_pages": candidates,
            "pages": [],
            "failed_pages": candidates,
            "status": "unified_artifact_unavailable",
            "usable_for_rules": False,
        }
    artifact.ocr_model_fingerprint = expected_model_fingerprint
    artifact.parser_fingerprint = f"{artifact.parser_fingerprint}+ocr-candidate-v1"

    rendered = _render_pages_png(pdf_path, candidates)
    if rendered is None:
        return {
            "enabled": True,
            "requested_pages": candidates,
            "pages": [],
            "failed_pages": candidates,
            "status": "render_failed",
            "usable_for_rules": False,
        }
    images, transforms = rendered
    if not images:
        return {
            "enabled": True,
            "requested_pages": candidates,
            "pages": [],
            "failed_pages": candidates,
            "status": "render_failed",
            "usable_for_rules": False,
        }

    from src.services.ocr_client import ocr_base_url

    valid_pairs = [
        (img, page)
        for img, page in zip(images, candidates, strict=True)
        if img is not None
    ]
    results = await ocr_pages(
        [img for img, _ in valid_pairs],
        [page for _, page in valid_pairs],
    )

    page_summaries: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    table_count = 0
    for result in results:
        entry: Dict[str, Any] = {
            "page": result.page_number,
            "ok": result.ok,
            "block_count": len(result.blocks),
            "table_count": len(result.tables),
            "elapsed_ms": result.elapsed_ms,
        }
        page_state = next(
            (
                state
                for state in getattr(artifact, "page_states", []) or []
                if state.page_number == result.page_number
            ),
            None,
        )
        if not result.ok:
            entry["error"] = result.error
            if page_state is not None:
                page_state.ocr_status = "failed"
                page_state.ocr_error = result.error
        else:
            key_number_blocks = [b for b in result.blocks if b.score < KEY_NUMBER_MIN_SCORE]
            entry["below_key_score_blocks"] = len(key_number_blocks)
            ocr_text = result.page_text()
            entry["text_chars"] = len(ocr_text)
            table_count += len(result.tables)
            # 原生与 OCR 冲突保留：扫描页通常无文本层，但可能有残缺文本层
            transform = transforms.get(result.page_number, {})
            if artifact is not None:
                for state in getattr(artifact, "page_states", []) or []:
                    if state.page_number == result.page_number and state.text_chars:
                        conflicts.append(
                            {
                                "page": result.page_number,
                                "native_chars": state.text_chars,
                                "ocr_chars": len(ocr_text),
                            }
                        )
                        break
                for state in getattr(artifact, "page_states", []) or []:
                    if state.page_number == result.page_number:
                        state.note = "ocr_applied"
                        state.ocr_status = "candidate_only"
                        state.ocr_text_chars = len(ocr_text)
                        state.ocr_table_count = len(result.tables)
                        break
                for block_index, block in enumerate(result.blocks):
                    page_bbox = _pdf_bbox_from_render_bbox(block.bbox, transform)
                    artifact.text_blocks.append(
                        ParsedTextBlock(
                            block_uid=f"ocr-p{result.page_number}-b{block_index}",
                            page_number=result.page_number,
                            text=block.text,
                            bbox=tuple(page_bbox) if page_bbox is not None else None,
                            confidence=block.score,
                            extraction_method="ocr",
                        )
                    )
                if isinstance(getattr(artifact, "coordinate_transforms", None), dict):
                    artifact.coordinate_transforms[f"ocr_render_page_{result.page_number}"] = transform

                # OCR 候选表进入统一解析对象，但在口径绑定、逐格置信度及
                # 区域复识别均完成前，不加入规则输入、不生成确定性财政事实。
                for table_index, table in enumerate(result.tables):
                    rows = table.get("rows")
                    if not isinstance(rows, list) or not rows:
                        continue
                    table_rows = [[str(cell or "") for cell in row] for row in rows if isinstance(row, list)]
                    if not table_rows:
                        continue
                    render_bbox = table.get("bbox")
                    page_bbox = _pdf_bbox_from_render_bbox(render_bbox, transform)
                    artifact.ocr_table_candidates.append(
                        OcrTableCandidate(
                            candidate_uid=f"ocr-p{result.page_number}-t{table_index}",
                            page_number=result.page_number,
                            rows=table_rows,
                            render_bbox=(
                                tuple(float(value) for value in render_bbox)
                                if isinstance(render_bbox, (list, tuple)) and len(render_bbox) == 4
                                else None
                            ),
                            page_bbox=tuple(page_bbox) if page_bbox is not None else None,
                            coordinate_transform_id=f"ocr_render_page_{result.page_number}",
                        )
                    )
        page_summaries.append(entry)

    failed = [p["page"] for p in page_summaries if not p["ok"]]
    return {
        "enabled": True,
        "requested_pages": candidates,
        "pages": page_summaries,
        "failed_pages": failed,
        "conflicts": conflicts,
        "table_candidates": table_count,
        "usable_for_rules": False,
        "status": "review_required" if not failed else "partial",
        "service_url": ocr_base_url(),
    }
