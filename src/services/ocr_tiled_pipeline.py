"""OCR v3 页面流式适配：每个图块保留独立响应和变换，绝不伪装全页推理。"""

from __future__ import annotations

import hashlib
import io
import json
import re
import os
import time
from pathlib import Path

import httpx
from PIL import Image

from src.schemas.parse_artifact import OcrTableCandidate, ParsedTextBlock
from src.services.input_geometry import (Affine, GEOMETRY_VERSION, page_viewport_to_native,
                                         rendered_pixels_to_native)
from src.services.ocr_tiles import first_pass_mode, recognize_tiles, TILING_VERSION, tile_profile

ORIENTATION_VERSION = "ocr-orientation-probe-v1"
#: 方向探针只决定整页旋转角，低分辨率即可；其响应不作为任何文字证据。
ORIENTATION_PROBE_DPI = 100


def native_render_dpi(page, default=300, floor=150):
    """扫描页按嵌入图像的实际分辨率渲染。

    200 DPI 的扫描放大到 300 DPI 不增加任何信息，只让像素、图块和识别时间
    多出 1.25 倍。无整页图像（矢量页）时沿用默认 300 DPI。
    """
    area = abs(page.rect.width * page.rect.height) or 1.0
    best = None
    for info in page.get_images(full=True):
        width_px, height_px = info[2], info[3]
        for rect in page.get_image_rects(info[0]):
            if rect.width * rect.height < 0.5 * area:
                continue
            dpi = max(width_px, height_px) / (max(rect.width, rect.height) / 72)
            best = dpi if best is None else max(best, dpi)
    if best is None:
        return default
    return int(min(default, max(floor, round(best))))


def _densest_text_window(image, size):
    """取去掉网线后文字墨迹最多的方窗，避免探针只看到表格线。"""
    import numpy as np
    from src.services.ocr_tiles import _visible_text_ink

    ink = _visible_text_ink(image, independent_lines=True)
    height, width = ink.shape
    if width <= size and height <= size:
        return (0, 0, width, height)
    integral = np.pad(ink.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    step = max(16, size // 8)
    best, box = -1, (0, 0, min(width, size), min(height, size))
    for y in range(0, max(1, height - size + 1), step):
        for x in range(0, max(1, width - size + 1), step):
            x1, y1 = min(width, x + size), min(height, y + size)
            total = integral[y1, x1] - integral[y, x1] - integral[y1, x] + integral[y, x]
            if total > best:
                best, box = int(total), (x, y, x1, y1)
    return box


async def detect_orthogonal_rotation(client, page, *, file_hash, page_number, hint=0):
    """整页 0/90/180/270 方向由识别模型自身判定，不看数字或勾稽结果。

    一个方向成立须同时满足：文字框以横向为主（文字行是横的）且识别置信度高。
    0° 不成立时，竖长框多则先试两个侧向、再试 180°，否则先试 180°；上一页的
    结论作为优先尝试的侧向，命中即停。
    """
    import fitz
    from src.services.ocr_client import ocr_page
    from src.services.ocr_preprocessing import rotate
    from src.services.ocr_tiles import TILE_TIMEOUT, make_tile, png_bytes

    pix = page.get_pixmap(matrix=fitz.Matrix(ORIENTATION_PROBE_DPI / 72, ORIENTATION_PROBE_DPI / 72))
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    affine = rendered_pixels_to_native(page, pix, ORIENTATION_PROBE_DPI)
    box = _densest_text_window(image, min(tile_profile()))
    crop = image.crop(box)
    crop_affine = Affine(e=box[0], f=box[1]).then(affine)
    probes = {}

    async def probe(angle):
        if angle in probes:
            return probes[angle]
        rotated, inverse, operations = rotate(crop, angle)
        request = make_tile(rotated, (0, 0, rotated.width, rotated.height),
                            parent_sha=hashlib.sha256(png_bytes(rotated)).hexdigest(),
                            file_hash=file_hash, page=page_number,
                            pixel_to_pdf=inverse.then(crop_affine), operations=operations,
                            inference_mode="text")
        response = await ocr_page(client, image_png=request.image, page_number=page_number,
                                  timeout_seconds=TILE_TIMEOUT, region_only=True,
                                  tile_context=request.context)
        blocks = [b for b in response.blocks if b.bbox and b.text.strip()] if response.ok else []
        words = [b for b in blocks if len(b.text.strip()) >= 2]
        chars = sum(len(b.text.strip()) for b in words)
        tall = sum((b.bbox[3] - b.bbox[1]) > 1.3 * (b.bbox[2] - b.bbox[0]) for b in blocks)
        probes[angle] = {
            "degrees_counterclockwise": angle, "input_sha256": request.context["input_sha256"],
            "text_chars": chars,
            "mean_score": round(sum(b.score * len(b.text.strip()) for b in words) / chars, 4) if chars else None,
            "tall_block_fraction": round(tall / len(blocks), 4) if blocks else None,
            "error": response.error,
        }
        return probes[angle]

    def score(result):
        return result["mean_score"] or 0.0

    def upright(result):
        # 竖长框多说明文字行仍是竖的；识别器会把竖长框自行转正，置信度照样
        # 很高（实测侧放页按 180° 读也有 0.96），所以分数之外还须文字行横排。
        return result["mean_score"] is not None and (result["tall_block_fraction"] or 0) < 0.35

    first = await probe(0)
    chosen = 0
    if not (upright(first) and score(first) >= 0.9):
        preferred = hint if hint in {90, 270} else 270
        sideways = [preferred, 90 if preferred == 270 else 270]
        order = sideways + [180] if (first["tall_block_fraction"] or 0) >= 0.35 else [180] + sideways
        for angle in order:
            result = await probe(angle)
            if upright(result) and score(result) >= 0.9:
                chosen = angle
                break
        else:
            candidates = [p for p in probes.values() if upright(p)]
            if candidates:
                chosen = max(candidates, key=score)["degrees_counterclockwise"]
    return chosen, {
        "version": ORIENTATION_VERSION, "probe_dpi": ORIENTATION_PROBE_DPI,
        "probe_box_px": list(box), "probes": list(probes.values()),
        "chosen_degrees_counterclockwise": chosen, "used_as_text_evidence": False,
    }


def checkpoint_epoch_path(path, artifact, health):
    """代码、模型或配置换代后另存断点，保留旧响应以供回放和排障。"""
    from src.services.input_processing_identity import content_hash
    from src.services.raster_grid_candidates import RASTER_GRID_VERSION
    epoch = content_hash({
        'source': artifact.source_file_hash, 'parser': artifact.parser_fingerprint,
        'config': artifact.config_fingerprint, 'model': health['model_fingerprint'],
        'pipeline': health['pipeline_fingerprint'], 'grid': RASTER_GRID_VERSION,
        'geometry': GEOMETRY_VERSION, 'tiling': TILING_VERSION,
    })
    return Path(str(path) + '.versions') / epoch


async def recognize_document_tiles(
    pdf_path, candidates, artifact, health, *, checkpoint_path=None, progress_callback=None
):
    import fitz

    pages = []
    pending = []
    pass_budget=float(os.getenv('OCR_DOCUMENT_SOFT_BUDGET_SEC','600'))
    if not 1<=pass_budget<=6000:raise ValueError('OCR document pass budget outside supported limits')
    pass_started=time.monotonic()
    from src.services.ocr_preprocessing import estimate_layout_angle, rotate, PREPROCESSING_VERSION
    from src.services.ocr_tiles import png_bytes

    if checkpoint_path is None:
        checkpoint_path = (Path(os.getenv('UPLOAD_DIR', 'uploads')) / '.ocr_checkpoints'
                           / artifact.source_file_hash / 'checkpoint.json')

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
                    "dpi": "native_image_dpi_150_300",
                    "preprocessing": PREPROCESSING_VERSION,
                    "orientation": ORIENTATION_VERSION,
                    "geometry": GEOMETRY_VERSION,
                    "tile_size": tile_profile(),
                    "first_pass_mode": first_pass_mode(),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )
    checkpoint_root = checkpoint_epoch_path(checkpoint_path, artifact, health)
    orientation_hint = 0
    async with httpx.AsyncClient() as client:
        with fitz.open(pdf_path) as pdf:
            for page_index,page_number in enumerate(candidates):
                if time.monotonic()-pass_started>=pass_budget:
                    remaining=candidates[page_index:]
                    pending.extend(remaining)
                    for state in artifact.page_states:
                        if state.page_number in remaining:state.ocr_status='pending'
                    pages.extend({'page':number,'status':'pending','reason':'document_pass_soft_budget',
                                  'tiles':0,'regional_rechecks':0}for number in remaining)
                    break
                page_started = time.monotonic()
                page = pdf[page_number - 1]
                # 先定整页正交方向，再在转正的 150 DPI 布局图上估小角度倾斜。
                quarter, orientation = await detect_orthogonal_rotation(
                    client, page, file_hash=artifact.source_file_hash,
                    page_number=page_number, hint=orientation_hint)
                orientation_hint = quarter or orientation_hint
                # 150 DPI 只用于布局，不交给文字识别器。
                layout = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72))
                layout_sha = hashlib.sha256(layout.tobytes("png")).hexdigest()
                layout_image = Image.open(io.BytesIO(layout.tobytes("png"))).convert("RGB")
                angle = estimate_layout_angle(rotate(layout_image, quarter)[0])
                layout = layout_image = None
                dpi = native_render_dpi(page)
                pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
                image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                affine = rendered_pixels_to_native(page, pix, dpi)
                original_parent_sha = hashlib.sha256(png_bytes(image)).hexdigest()
                original_size = list(image.size)
                # 正交旋转是无插值转置，倾斜另做一步；两步各自记录精确反变换。
                image, inverse_quarter, operations = rotate(image, quarter)
                if operations:
                    operations[0]["orientation_evidence"] = orientation
                image, inverse_skew, skew_operations = rotate(image, angle)
                operations = operations + skew_operations
                affine = inverse_skew.then(inverse_quarter).then(affine)
                angle = (quarter + angle) % 360
                pix = None
                from src.services.raster_grid_candidates import discover_grid_cells
                from src.schemas.source_reconstruction import CellCandidate
                grid=discover_grid_cells(image,dpi=dpi)
                grid_prefix=f'image-grid-p{page_number}-'
                artifact.cell_candidates=[c for c in artifact.cell_candidates
                                          if not c.candidate_uid.startswith(grid_prefix)]
                artifact.reconstruction_decisions=[d for d in artifact.reconstruction_decisions
                    if not (d.get('kind')=='independent_image_grid'and d.get('physical_page')==page_number)]
                parent_grid_sha=hashlib.sha256(png_bytes(image)).hexdigest()
                for index,cell in enumerate(grid['cells']):
                    uid=grid_prefix+hashlib.sha256(json.dumps([parent_grid_sha,cell['bbox']]).encode()).hexdigest()[:24]
                    artifact.cell_candidates.append(CellCandidate(uid,uid,grid_prefix+parent_grid_sha[:24],
                        page_number,affine.box(cell['bbox']),'','image_grid',cell['row_idx'],cell['col_idx'],
                        cell['row_span'],cell['col_span']))
                artifact.reconstruction_decisions.append({'kind':'independent_image_grid','physical_page':page_number,
                    'source_sha256':artifact.source_file_hash,'render_sha256':parent_grid_sha,
                    'pixel_to_pdf_matrix':affine.values(),**grid})

                def high_resolution(box, attempt, affine=affine, page=page, angle=angle, page_dpi=dpi,
                                    original_parent_sha=original_parent_sha):
                    native_to_viewport = page_viewport_to_native(page).inverse()
                    clip = fitz.Rect(native_to_viewport.box(affine.box(box))) & page.rect
                    # 补读按首轮分辨率的 1.5/2 倍放大（矢量页仍是 450/600 DPI）；
                    # 200 DPI 扫描放大到 600 DPI 只是插值，却让补读块数翻近十倍。
                    dpi = min(450 if attempt == 1 else 600, round(page_dpi * (1.5 if attempt == 1 else 2)))
                    zoom = dpi / 72
                    raster = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
                    cropped = Image.open(io.BytesIO(raster.tobytes("png"))).convert("RGB")
                    # clip 栅格原点使用 pixmap 整数坐标，避免四舍五入偏移。
                    transform = rendered_pixels_to_native(page, raster, dpi)
                    # 最后一轮同时尝试正交倒置，仍使用同一个离线模型。
                    cropped, inverse, prep = rotate(cropped, angle + (180 if attempt == 2 else 0))
                    transform = inverse.then(transform)
                    prep=[{'operation':'high_resolution_render','dpi':dpi,
                           'clip_viewport':list(clip),'original_page_render_sha256':original_parent_sha}]+prep
                    return cropped, (0, 0, cropped.width, cropped.height), transform, prep

                checkpoint = checkpoint_root / f'page-{page_number}.v3.json'
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
                    discovery_regions=grid['discovery_regions'],
                    dpi=dpi,
                )
                unfinished = [
                    affine.box(region["box"])
                    for region in state.get("regions", {}).values()
                    if region.get("status") != "recovered"
                ] + [affine.box(box) for box in state.get("unread_regions") or []]
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
                    "max_recheck_rounds": 1,
                }
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
                            "dpi": dpi,
                            "original_size_px": original_size,
                            "rotation": page.rotation,
                            "cropbox": list(page.cropbox),
                            "rotation_matrix": list(page.rotation_matrix),
                            "derotation_matrix": list(page.derotation_matrix),
                            "preprocessing_version": PREPROCESSING_VERSION,
                            "geometry_version": GEOMETRY_VERSION,
                            "mediabox": list(page.mediabox),
                            "viewport_to_native_matrix": page_viewport_to_native(page).values(),
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
                from src.services.ocr_grid_reconstruction import attach_grid_candidates
                grid_tables = attach_grid_candidates(artifact, page_number, image, affine, dpi=dpi)
                image = None
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
                    "page_seconds": round(time.monotonic() - page_started, 2),
                    "render_dpi": dpi,
                    "rotation_degrees_counterclockwise": angle,
                    "orientation_probes": len(orientation["probes"]),
                    "tiles": len(results),
                    "regional_rechecks": len(rechecks),
                    "independent_grid_tables": grid_tables,
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
    from src.services.input_processing_identity import artifact_content_hash
    artifact.parse_run_uid = artifact_content_hash(artifact)[:32]
    return {
        "enabled": True,
        "status": "pending" if pending else "review_required",
        "requested_pages": candidates,
        "pages": pages,
        "failed_pages": pending,
        'table_candidates': sum(c.page_number in candidates for c in artifact.ocr_table_candidates),
        "usable_for_rules": False,
        "execution": {"mode": "v3_tiled", "page_budget_seconds": 600, "budget_is_soft": True,
                      "checkpoint_epoch": checkpoint_root.name,"tile_size_px":list(tile_profile()),
                      'document_pass_soft_budget_seconds':pass_budget},
    }
