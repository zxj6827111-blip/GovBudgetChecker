"""OCR v3 页面流式适配：每个图块保留独立响应和变换，绝不伪装全页推理。"""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

import httpx
from PIL import Image

from src.schemas.parse_artifact import OcrTableCandidate, ParsedTextBlock
from src.services.input_geometry import Affine
from src.services.ocr_tiles import recognize_tiles, TILING_VERSION


async def recognize_document_tiles(
    pdf_path, candidates, artifact, health, *, checkpoint_path=None, progress_callback=None
):
    import fitz

    pages = []
    pending = []
    from src.services.ocr_preprocessing import estimate_layout_angle, rotate, PREPROCESSING_VERSION
    from src.services.ocr_tiles import png_bytes

    artifact.ocr_model_fingerprint = health["model_fingerprint"]
    artifact.parser_fingerprint = artifact.parser_fingerprint.split("+ocr-", 1)[0] + "+ocr-tiles-v3"
    basis = re.sub(r":ocr-v3:[0-9a-f]{64}", "", artifact.config_fingerprint)
    artifact.config_fingerprint = (
        basis
        + ":ocr-v3:"
        + hashlib.sha256(
            json.dumps(
                {
                    "pipeline": health["pipeline_fingerprint"],
                    "tiling": TILING_VERSION,
                    "dpi": 300,
                    "preprocessing": PREPROCESSING_VERSION,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )
    async with httpx.AsyncClient() as client:
        with fitz.open(pdf_path) as pdf:
            for page_number in candidates:
                page = pdf[page_number - 1]
                # 150 DPI 只用于布局，不交给文字识别器。
                layout = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72))
                layout_sha = hashlib.sha256(layout.tobytes("png")).hexdigest()
                layout_image = Image.open(io.BytesIO(layout.tobytes("png"))).convert("RGB")
                angle = estimate_layout_angle(layout_image)
                layout = layout_image = None
                pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
                image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                affine = Affine(a=72 / 300, d=72 / 300, e=pix.x * 72 / 300, f=pix.y * 72 / 300)
                original_parent_sha = hashlib.sha256(png_bytes(image)).hexdigest()
                original_size = list(image.size)
                image, inverse_rotation, operations = rotate(image, angle)
                affine = inverse_rotation.then(affine)
                pix = None

                def high_resolution(box, attempt, affine=affine, page=page, angle=angle):
                    clip = fitz.Rect(affine.box(box))
                    dpi = 450 if attempt == 1 else 600
                    zoom = dpi / 72
                    raster = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
                    cropped = Image.open(io.BytesIO(raster.tobytes("png"))).convert("RGB")
                    # clip 栅格原点使用 pixmap 整数坐标，避免四舍五入偏移。
                    transform = Affine(a=1 / zoom, d=1 / zoom, e=raster.x / zoom, f=raster.y / zoom)
                    # 最后一轮同时尝试正交倒置，仍使用同一个离线模型。
                    cropped, inverse, prep = rotate(cropped, angle + (180 if attempt == 2 else 0))
                    transform = inverse.then(transform)
                    return cropped, (0, 0, cropped.width, cropped.height), transform, prep

                checkpoint = (
                    Path(str(checkpoint_path) + f".page-{page_number}.v3.json")
                    if checkpoint_path
                    else None
                )
                results, rechecks, state = await recognize_tiles(
                    image,
                    file_hash=artifact.source_file_hash,
                    page=page_number,
                    pixel_to_pdf=affine,
                    client=client,
                    model=health["model_fingerprint"],
                    pipeline=health["pipeline_fingerprint"],
                    checkpoint_path=checkpoint,
                    high_resolution=high_resolution,
                    operations=operations,
                    original_parent_sha=original_parent_sha,
                )
                unfinished = [
                    affine.box(region["box"])
                    for region in state.get("regions", {}).values()
                    if region.get("status") != "recovered"
                ]
                completed_boxes = {
                    tuple(record["context"]["crop_box_px"])
                    for record in state.get("completed", {}).values()
                    if record["context"].get("attempt") == 0
                }
                unfinished.extend(
                    affine.box(box)
                    for box in state.get("identity", {}).get("tile_boxes", [])
                    if tuple(box) not in completed_boxes
                )
                page_recovery = {
                    "status": state["status"],
                    "unresolved_pdf_regions": unfinished,
                    "budget_is_soft": True,
                    "max_recheck_rounds": 2,
                }
                image = None
                # 同一断点产物可多次续跑；按物理页更新 OCR 投影，不能重复追加正文。
                artifact.text_blocks = [
                    b
                    for b in artifact.text_blocks
                    if not (b.page_number == page_number and b.block_uid.startswith("ocr_tile_"))
                ]
                artifact.source_fragments = [
                    f
                    for f in artifact.source_fragments
                    if not (f.get("physical_page") == page_number and f.get("tile_id"))
                ]
                artifact.ocr_table_candidates = [
                    c
                    for c in artifact.ocr_table_candidates
                    if not (
                        c.page_number == page_number
                        and c.coordinate_transform_id.startswith("ocr_tile_")
                    )
                ]
                for context, response in results + rechecks:
                    if not response.ok:
                        continue
                    transform_id = "ocr_tile_" + context["tile_id"]
                    matrix = Affine(*context["pixel_to_pdf"])
                    artifact.coordinate_transforms[transform_id] = {
                        "transform_version": "pdf-tile-v3",
                        "physical_page": page_number,
                        "source_sha256": artifact.source_file_hash,
                        "render_sha256": context["input_sha256"],
                        "render_size_px": context["size_px"],
                        "bbox_transform_verified": True,
                        "pixel_to_pdf_matrix": context["pixel_to_pdf"],
                        "pdf_to_pixel_matrix": context["pdf_to_pixel"],
                        "tile_context": context,
                        "ocr_response": response.provenance,
                        "layout_sha256": layout_sha,
                        "page_recovery": page_recovery,
                        "page_render": {
                            "dpi": 300,
                            "original_size_px": original_size,
                            "rotation": page.rotation,
                            "cropbox": list(page.cropbox),
                            "rotation_matrix": list(page.rotation_matrix),
                            "derotation_matrix": list(page.derotation_matrix),
                            "preprocessing_version": PREPROCESSING_VERSION,
                        },
                        "page_request": {
                            "physical_page": page_number,
                            "source_sha256": artifact.source_file_hash,
                            "render_sha256": context["input_sha256"],
                            "input_sha256": context["input_sha256"],
                            **{
                                k: response.provenance.get(k)
                                for k in (
                                    "schema_version",
                                    "raw_prediction_sha256",
                                    "model_fingerprint",
                                    "pipeline_fingerprint",
                                )
                            },
                        },
                    }
                    for index, block in enumerate(response.blocks):
                        box = matrix.box(block.bbox) if block.bbox else None
                        uid = f"{transform_id}:block-{index}"
                        artifact.text_blocks.append(
                            ParsedTextBlock(
                                uid,
                                page_number,
                                block.text,
                                tuple(box) if box else None,
                                block.score,
                                "ocr",
                            )
                        )
                        artifact.source_fragments.append(
                            {
                                "source_id": uid,
                                "physical_page": page_number,
                                "text": block.text,
                                "bbox": box,
                                "granularity": "fragment",
                                "tile_id": context["tile_id"],
                                "raw_prediction_sha256": response.provenance[
                                    "raw_prediction_sha256"
                                ],
                            }
                        )
                    for index, table in enumerate(response.tables):
                        box = matrix.box(table["bbox"]) if table.get("bbox") else None
                        cells = [
                            {
                                **c,
                                "page_bbox": matrix.box(c["render_bbox"])
                                if c.get("render_bbox")
                                else None,
                            }
                            for c in table.get("cells", [])
                        ]
                        artifact.ocr_table_candidates.append(
                            OcrTableCandidate(
                                f"{transform_id}:table-{index}",
                                page_number,
                                table.get("rows", []),
                                tuple(table["bbox"]) if table.get("bbox") else None,
                                tuple(box) if box else None,
                                coordinate_transform_id=transform_id,
                                cells=cells,
                                mapping_status=table.get("mapping_status", "unavailable"),
                                provenance={
                                    **response.provenance,
                                    "source_sha256": artifact.source_file_hash,
                                    "render_sha256": context["input_sha256"],
                                },
                            )
                        )
                if state["status"] == "pending":
                    pending.append(page_number)
                for page_state in artifact.page_states:
                    if page_state.page_number == page_number:
                        page_state.ocr_status = (
                            "pending" if page_number in pending else "candidate_only"
                        )
                entry = {
                    "page": page_number,
                    "status": state["status"],
                    "pending_regions": len(state["pending"]),
                    "unresolved_regions": len(state["unresolved"]),
                    "elapsed_seconds": state["elapsed_seconds"],
                    "tiles": len(results),
                    "regional_rechecks": len(rechecks),
                }
                pages.append(entry)
                if progress_callback:
                    await progress_callback(
                        {
                            "phase": entry["status"],
                            "physical_page": page_number,
                            "completed_count": len(pages) - len(pending),
                            "total_pages": len(candidates),
                            "page_budget_seconds": 600,
                        }
                    )
    return {
        "enabled": True,
        "status": "pending" if pending else "review_required",
        "requested_pages": candidates,
        "pages": pages,
        "failed_pages": pending,
        "usable_for_rules": False,
        "execution": {"mode": "v3_tiled", "page_budget_seconds": 600, "budget_is_soft": True},
    }
