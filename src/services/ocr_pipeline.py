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
import asyncio
import time
import os
import hashlib
import io
import json
import math
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.services.ocr_client import KEY_NUMBER_MIN_SCORE, check_ocr_health, ocr_enabled, ocr_pages
from src.schemas.parse_artifact import OcrTableCandidate, ParsedTextBlock

logger = logging.getLogger(__name__)

#: 渲染 DPI（任务包 OCR 约定：默认 300）
DEFAULT_DPI = 300


def _table_recheck_eligible(table: Dict[str, Any], *, per_cell: bool = False) -> bool:
    from ocr_service.protocol import valid_score

    if table.get("mapping_status") != "mapped" or not table.get("cells"):
        return False
    if not per_cell:
        from ocr_service.protocol import valid_score
        for cell in table["cells"]:
            if not str(cell.get("raw_text") or "").strip():
                continue
            fragments = cell.get("fragments") or []
            if not fragments or any(valid_score(fragment.get("score")) is None or valid_score(fragment.get("score")) < KEY_NUMBER_MIN_SCORE for fragment in fragments):
                return False
    return True


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
                from src.services.input_geometry import (rendered_pixels_to_native,
                    page_viewport_to_native, GEOMETRY_VERSION)
                pixel_to_native = rendered_pixels_to_native(page, pix, dpi)
                native_visible_rect = page_viewport_to_native(page).box(list(page_rect))
                transforms[page_number] = {
                    "physical_page": page_number,
                    "dpi": dpi,
                    "scale": zoom,
                    "render_origin_px": [int(pix.x), int(pix.y)],
                    "render_size_px": [int(pix.width), int(pix.height)],
                    "rotation": int(page.rotation or 0),
                    "cropbox": [
                        float(cropbox.x0),
                        float(cropbox.y0),
                        float(cropbox.x1),
                        float(cropbox.y1),
                    ],
                    "page_rect": native_visible_rect,
                    "viewport_rect": list(page_rect),
                    "mediabox": list(page.mediabox),
                    "geometry_version": GEOMETRY_VERSION,
                    "source_sha256": hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest(),
                    "render_sha256": hashlib.sha256(images[-1]).hexdigest(),
                    "transform_version": "pdf-render-v4",
                    "coordinate_space": "native_rotated_mediabox_points",
                    "pixel_to_pdf_matrix": pixel_to_native.values(),
                    # PyMuPDF 的逆矩阵会产生 -0.0；JSONB 会把它存成 0.0，
                    # 从而使完整快照哈希失配。仅规范化几何零，不改 OCR 原始预测。
                    "rotation_matrix": [float(value) if value else 0.0 for value in page.rotation_matrix],
                    "derotation_matrix": [float(value) if value else 0.0 for value in page.derotation_matrix],
                    # 几何输入已检查；裁剪原点、页旋转和 UserUnit 均在矩阵中。
                    "bbox_transform_verified": bool(
                        int(page.rotation or 0) in {0, 90, 180, 270}
                        and pix.width > 0
                        and pix.height > 0
                    ),
                }
                pix = None  # 逐页释放图像内存
    except Exception:
        logger.exception("page rendering for OCR failed")
        return None
    return images, transforms


def _pdf_bbox_from_render_bbox(bbox: Any, transform: Dict[str, Any]) -> Optional[List[float]]:
    """仅在已验证的简单坐标变换下，把 OCR 像素框换为 PDF 点坐标。"""
    if (
        not transform.get("bbox_transform_verified")
        or not isinstance(bbox, (list, tuple))
        or len(bbox) != 4
    ):
        return None
    if transform.get('transform_version') == 'pdf-tile-v3':
        from src.services.input_geometry import Affine
        try:
            width, height = transform['render_size_px']
            if bbox[0] < 0 or bbox[1] < 0 or bbox[2] > width or bbox[3] > height:
                return None
            return Affine(*transform['pixel_to_pdf_matrix']).box(bbox)
        except (ValueError, TypeError, KeyError):
            return None
    if transform.get("transform_version") in {"pdf-render-v3", "pdf-render-v4"}:
        try:
            values = [float(value) for value in bbox]
            matrix = [float(value) for value in transform["pixel_to_pdf_matrix"]]
            width, height = transform["render_size_px"]
            rect = transform["page_rect"]
            if len(matrix) != 6 or not all(math.isfinite(v) for v in values + matrix + list(rect)):
                return None
            x0, y0, x1, y1 = values
            if x1 <= x0 or y1 <= y0 or x0 < 0 or y0 < 0 or x1 > width or y1 > height:
                return None
            a, b, c, d, e, f = matrix
            if (
                b
                or c
                or a <= 0
                or d <= 0
                or transform.get("coordinate_space") != (
                    "native_rotated_mediabox_points" if transform['transform_version']=='pdf-render-v4'
                    else "rotated_cropbox_points")
            ):
                return None
            result = [x0 * a + e, y0 * d + f, x1 * a + e, y1 * d + f]
            # Raster size rounds outwards by less than one pixel.
            tolerance = max(a, d) + 1e-6
            if any(
                v < rect[i] - tolerance if i < 2 else v > rect[i] + tolerance
                for i, v in enumerate(result)
            ):
                return None
            return [
                max(rect[0], result[0]),
                max(rect[1], result[1]),
                min(rect[2], result[2]),
                min(rect[3], result[3]),
            ]
        except (KeyError, TypeError, ValueError):
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
    if not all(
        math.isfinite(value)
        for value in (scale, x0, y0, x1, y1, origin_x, origin_y, crop_x, crop_y)
    ):
        return None
    width, height = transform.get("render_size_px", [float("inf"), float("inf")])
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
        return None
    return [
        crop_x + (x0 - origin_x) / scale,
        crop_y + (y0 - origin_y) / scale,
        crop_x + (x1 - origin_x) / scale,
        crop_y + (y1 - origin_y) / scale,
    ]


def _check_pipeline_identity(results, service_health):
    for result in results:
        if result.provenance and result.provenance.get("pipeline_fingerprint") != service_health.get("pipeline_fingerprint"):
            result.error = "pipeline_fingerprint_changed_during_request"
            result.blocks = []
            result.tables = []


async def _recheck_regions(results, images_by_page, transforms):
    # Regional re-recognition uses a new crop and a real second HTTP request.
    # Legacy responses remain candidates; they cannot supply v2 qualification.
    import httpx
    from PIL import Image
    from src.services.ocr_client import ocr_page, DEFAULT_REGION_TIMEOUT_SECONDS
    from src.services.ocr_client import DEFAULT_PAGE_TIMEOUT_SECONDS

    async with httpx.AsyncClient() as client:
        for result in results:
            if not result.ok or not result.provenance:
                continue
            region_started = time.monotonic()
            with Image.open(io.BytesIO(images_by_page[result.page_number])) as rendered_image:
                for table in result.tables:
                    eligible = _table_recheck_eligible(table, per_cell=True)
                    for cell in table.get("cells", []):
                        if not any(char.isdigit() for char in str(cell.get("raw_text", ""))):
                            continue
                        from src.services.fiscal_row_roles import is_index_header
                        from ocr_service.protocol import valid_score
                        row = (table.get("rows") or [])[cell["row_idx"]]
                        if is_index_header(row):
                            continue
                        fragments = cell.get("fragments") or []
                        cell_eligible = bool(fragments) and all(
                            valid_score(fragment.get("score")) is not None
                            and valid_score(fragment.get("score")) >= KEY_NUMBER_MIN_SCORE
                            for fragment in fragments
                        )
                        if not eligible or not cell_eligible:
                            cell["region_recheck"] = {
                                "status": "not_performed",
                                "error": "table_first_pass_not_qualified_for_recheck",
                            }
                            continue
                        remaining = (
                            DEFAULT_PAGE_TIMEOUT_SECONDS
                            - result.elapsed_ms / 1000
                            - (time.monotonic() - region_started)
                        )
                        if remaining <= 0:
                            cell["region_recheck"] = {
                                "status": "failed",
                                "error": "page_region_budget_exhausted",
                            }
                            continue
                        box = cell.get("render_bbox")
                        if _pdf_bbox_from_render_bbox(box, transforms[result.page_number]) is None:
                            continue
                        crop = rendered_image.crop(tuple(box))
                        buffer = io.BytesIO()
                        crop.save(buffer, format="PNG")
                        checked = await ocr_page(
                            client,
                            image_png=buffer.getvalue(),
                            page_number=result.page_number,
                            timeout_seconds=min(DEFAULT_REGION_TIMEOUT_SECONDS, remaining),
                            region_only=True,
                        )
                        cell["region_recheck"] = dict(
                            status="completed" if checked.ok else "failed",
                            text=checked.page_text(),
                            scores=[block.score for block in checked.blocks],
                            input_sha256=hashlib.sha256(buffer.getvalue()).hexdigest(),
                            provenance=checked.provenance,
                            error=checked.error,
                        )
                        # Keep the actual request journal outside candidate cell
                        # views so altered recheck metadata cannot change its source.
                        transforms[result.page_number].setdefault("region_requests", []).append(
                            {
                                "row_idx": cell["row_idx"],
                                "col_idx": cell["col_idx"],
                                "row_span": cell["row_span"],
                                "col_span": cell["col_span"],
                                "render_bbox": list(box),
                                "input_sha256": cell["region_recheck"]["input_sha256"],
                                "raw_prediction_sha256": checked.provenance.get(
                                    "raw_prediction_sha256"
                                ),
                            }
                        )


async def _recognize_incrementally(pdf_path, candidates, artifact, service_health, *, progress_callback, checkpoint_path):
    """Keep one raster in memory and checkpoint completed pages for retries."""
    from src.services.ocr_client import OcrPageResult, _normalise_blocks, DEFAULT_PAGE_TIMEOUT_SECONDS

    started = time.monotonic()
    identity = {"source_sha256": artifact.source_file_hash, "model": artifact.ocr_model_fingerprint,
                "pipeline": service_health.get("pipeline_fingerprint"), "config": artifact.config_fingerprint,
                "dpi": DEFAULT_DPI, "schema": "gbc.ocr.page-checkpoint.v1"}
    checkpoint = {"identity": identity, "pages": {}}
    if checkpoint_path is not None and checkpoint_path.is_file():
        try:
            stored = json.loads(checkpoint_path.read_text())
            if isinstance(stored, dict) and stored.get("identity") == identity and isinstance(stored.get("pages"), dict):
                checkpoint = stored
        except (OSError, TypeError, ValueError):
            pass
    results, transforms, reused = [], {}, []
    requests = 0

    async def progress(page, phase):
        if progress_callback is not None:
            await progress_callback({"phase": phase, "physical_page": page, "requested_pages": candidates,
                                     "completed_pages": [r.page_number for r in results], "completed_count": len(results),
                                     "total_pages": len(candidates), "reused_pages": list(reused),
                                     "elapsed_seconds": round(time.monotonic() - started, 2),
                                     "page_budget_seconds": DEFAULT_PAGE_TIMEOUT_SECONDS,
                                     "stage_budget_seconds": len(candidates) * DEFAULT_PAGE_TIMEOUT_SECONDS + 5})

    for page in candidates:
        await progress(page, "rendering")
        rendered = _render_pages_png(pdf_path, [page])
        if rendered is None or not rendered[0] or rendered[0][0] is None:
            results.append(OcrPageResult(page_number=page, error="render_failed"))
            await progress(page, "page_failed")
            continue
        image = rendered[0][0]
        transform = rendered[1][page]
        transform["page_request"] = {"source_sha256": artifact.source_file_hash, "physical_page": page,
                                     "input_sha256": hashlib.sha256(image).hexdigest(), "render_sha256": transform["render_sha256"]}
        transforms[page] = transform
        saved = checkpoint["pages"].get(str(page))
        result = None
        if isinstance(saved, dict):
            raw = saved.get("result") if isinstance(saved.get("result"), dict) else {}
            prior = saved.get("transform") if isinstance(saved.get("transform"), dict) else {}
            provenance = raw.get("provenance") if isinstance(raw.get("provenance"), dict) else {}
            try:
                raw_sha = hashlib.sha256(json.dumps(provenance.get("raw_prediction"), ensure_ascii=False, sort_keys=True,
                                                   separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                if (raw.get("page_number") == page and not raw.get("error")
                    and prior.get("render_sha256") == transform["render_sha256"]
                    and prior.get("physical_page") == page
                    and prior.get("source_sha256") == artifact.source_file_hash
                    and provenance.get("input_sha256") == transform["render_sha256"]
                    and provenance.get("model_fingerprint") == identity["model"]
                    and provenance.get("pipeline_fingerprint") == identity["pipeline"]
                    and provenance.get("raw_prediction_sha256") == raw_sha
                    and all(prior.get(key) == value for key, value in transform.items() if key != "page_request")):
                    from copy import deepcopy
                    from src.services.ocr_qualification import bound_response

                    reconstructed = bound_response(provenance)

                    def raw_table_view(table):
                        return {"rows": table.get("rows"), "cells": [
                            {key: cell.get(key) for key in (
                                "row_idx", "col_idx", "row_span", "col_span", "raw_text",
                                "render_bbox", "fragments", "score",
                            )} for cell in table.get("cells", [])]}

                    cached_tables = raw.get("tables") or []
                    if (reconstructed is not None and isinstance(cached_tables, list)
                        and [raw_table_view(table) for table in cached_tables]
                        == [raw_table_view(table) for table in reconstructed["tables"]]):
                        # Coordinates always come from this render. The checkpoint
                        # only supplies the bound prediction and completed rechecks.
                        restored_tables = deepcopy(reconstructed["tables"])
                        for table, cached_table in zip(restored_tables, cached_tables, strict=True):
                            for cell, cached_cell in zip(table.get("cells", []), cached_table.get("cells", []), strict=True):
                                if "region_recheck" in cached_cell:
                                    cell["region_recheck"] = deepcopy(cached_cell["region_recheck"])
                        result = OcrPageResult(page_number=page,
                            blocks=_normalise_blocks(reconstructed["blocks"], page),
                            tables=restored_tables, elapsed_ms=raw.get("elapsed_ms") or 0,
                            provenance=provenance)
                        transform["page_request"].update({key: provenance.get(key) for key in (
                            "raw_prediction_sha256", "model_fingerprint", "pipeline_fingerprint", "schema_version")})
                        transform["region_requests"] = deepcopy(prior.get("region_requests") or [])
                        reused.append(page)
            except (TypeError, ValueError, AttributeError):
                pass
        if result is None:
            await progress(page, "recognizing")
            page_started = time.monotonic()
            try:
                page_results = await asyncio.wait_for(ocr_pages([image], [page]), timeout=DEFAULT_PAGE_TIMEOUT_SECONDS)
                result = page_results[0]
            except asyncio.TimeoutError:
                result = OcrPageResult(page_number=page, error="page_budget_exhausted")
            requests += 1
            _check_pipeline_identity([result], service_health)
            transform["page_request"].update({key: result.provenance.get(key) for key in (
                "raw_prediction_sha256", "model_fingerprint", "pipeline_fingerprint", "schema_version")})
            await progress(page, "region_rechecking")
            remaining = DEFAULT_PAGE_TIMEOUT_SECONDS - (time.monotonic() - page_started)
            if remaining > 0:
                try:
                    await asyncio.wait_for(_recheck_regions([result], {page: image}, transforms), timeout=remaining)
                except asyncio.TimeoutError:
                    for table in result.tables:
                        for cell in table.get("cells", []):
                            cell.setdefault("region_recheck", {"status": "failed", "error": "page_region_budget_exhausted"})
            incomplete_region = any(cell.get("region_recheck", {}).get("status") == "failed" for table in result.tables for cell in table.get("cells", []))
            if result.ok and result.provenance and not incomplete_region and checkpoint_path is not None:
                checkpoint["pages"][str(page)] = {"result": result.to_dict(), "transform": transforms[page]}
                temporary = checkpoint_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, allow_nan=False))
                temporary.replace(checkpoint_path)
        results.append(result)
        image = None
        await progress(page, "page_completed" if result.ok else "page_failed")
    return results, transforms, {"mode": "incremental", "reused_pages": reused, "page_requests": requests,
                                 "elapsed_seconds": round(time.monotonic() - started, 2),
                                 "page_budget_seconds": DEFAULT_PAGE_TIMEOUT_SECONDS,
                                 "stage_budget_seconds": len(candidates) * DEFAULT_PAGE_TIMEOUT_SECONDS + 5}


async def maybe_ocr_scanned_pages(
    pdf_path: str,
    page_assessment: Dict[str, Any],
    artifact: Any = None,
    *,
    progress_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    checkpoint_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """对低文本页执行本地 OCR。未启用或无需识别时返回 None。

    ``artifact``（统一解析对象，可选）存在时把 OCR 页登记进 page_states
    （note="ocr_applied"），原生文本层不被覆盖；候选结果不直接进入规则输入。
    """
    from src.services.table_reading_assessment import recovery_pages
    candidates = recovery_pages(page_assessment, artifact)
    if not candidates:
        return None
    if not ocr_enabled():
        if page_assessment.get("table_recovery_pages"):
            return {"enabled": False, "status": "disabled", "requested_pages": candidates,
                    "failed_pages": candidates, "usable_for_rules": False}
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
    if (
        not service_health.get("ok")
        or service_health.get("model_fingerprint") != expected_model_fingerprint
    ):
        return {
            "enabled": True,
            "requested_pages": candidates,
            "pages": [],
            "failed_pages": candidates,
            "status": "model_fingerprint_mismatch"
            if service_health.get("ok")
            else "service_unavailable",
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
    if not re.fullmatch(r'[a-f0-9]{64}', str(service_health.get('pipeline_fingerprint') or '')):
        return {'enabled': True, 'status': 'pipeline_fingerprint_missing', 'requested_pages': candidates,
                'failed_pages': candidates, 'usable_for_rules': False}
    from src.services.ocr_tiled_pipeline import recognize_document_tiles
    return await recognize_document_tiles(pdf_path, candidates, artifact, service_health,
        checkpoint_path=checkpoint_path, progress_callback=progress_callback)

