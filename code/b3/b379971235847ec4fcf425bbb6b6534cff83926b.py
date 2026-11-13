"""OCR v3 页面流式适配：每个图块保留独立响应和变换，绝不伪装全页推理。"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
from pathlib import Path

import httpx
from PIL import Image

from src.schemas.parse_artifact import OcrTableCandidate, ParsedTextBlock, ParseArtifact
from src.services.input_geometry import Affine
from src.services.ocr_tiles import recognize_tiles, TILING_VERSION
from src.services.ocr_orientation import select_orientation, ORIENTATION_VERSION
from src.services.ocr_schedule import (
    PAGE_SOFT_BUDGET_SECONDS,
    DOCUMENT_SOFT_BUDGET_SECONDS,
    SCHEDULE_VERSION,
)


def append_tile_results(artifact, records, *, page_number, layout_sha, page_recovery, page_render):
    """用每个真实请求的独立原始响应建立来源投影；供采集和补读判据共用。"""
    for context, response in records:
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
            "page_render": page_render,
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
                    "raw_prediction_sha256": response.provenance["raw_prediction_sha256"],
                }
            )
        for index, table in enumerate(response.tables):
            box = matrix.box(table["bbox"]) if table.get("bbox") else None
            cells = [
                {
                    **c,
                    "page_bbox": matrix.box(c["render_bbox"]) if c.get("render_bbox") else None,
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


async def recognize_document_tiles(
    pdf_path,
    candidates,
    artifact,
    health,
    *,
    checkpoint_path=None,
    progress_callback=None,
    document_soft_budget=DOCUMENT_SOFT_BUDGET_SECONDS,
):
    import fitz
    from src.services.ocr_client import ocr_base_url

    service_url = ocr_base_url()
    if health.get("service_url", service_url).rstrip("/") != service_url.rstrip("/"):
        raise ValueError("OCR health endpoint differs from inference endpoint")
    endpoint = httpx.URL(service_url)

    async def endpoint_guard(request):
        if (request.url.scheme, request.url.host, request.url.port) != (
            endpoint.scheme,
            endpoint.host,
            endpoint.port,
        ):
            raise ValueError("OCR inference endpoint changed after health binding")

    pages = []
    pending = []
    document_started = time.monotonic()
    document_soft_budget = max(0, float(document_soft_budget))
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
                    "orientation": ORIENTATION_VERSION,
                    "schedule": SCHEDULE_VERSION,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )
    async with httpx.AsyncClient(event_hooks={"request": [endpoint_guard]}) as client:
        with fitz.open(pdf_path) as pdf:
            for page_number in candidates:
                page_started = time.monotonic()
                remaining_document = document_soft_budget - (page_started - document_started)
                if remaining_document <= 0:
                    pending.append(page_number)
                    pages.append(
                        {
                            "page": page_number,
                            "status": "pending",
                            "pending_regions": 0,
                            "unresolved_regions": 0,
                            "elapsed_seconds": 0,
                            "tiles": 0,
                            "regional_rechecks": 0,
                            "pending_phase": "document_schedule",
                        }
                    )
                    for page_state in artifact.page_states:
                        if page_state.page_number == page_number:
                            page_state.ocr_status = "pending"
                    continue
                # 一轮调度可在页间和图块间落盘，未调度页必须保持待续跑。
                page_budget = min(PAGE_SOFT_BUDGET_SECONDS, remaining_document)
                page = pdf[page_number - 1]
                # 150 DPI 只用于布局，不交给文字识别器。
                layout = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72))
                layout_sha = hashlib.sha256(layout.tobytes("png")).hexdigest()
                layout_image = Image.open(io.BytesIO(layout.tobytes("png"))).convert("RGB")
                layout = None
                pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
                image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                affine = Affine(a=72 / 300, d=72 / 300, e=pix.x * 72 / 300, f=pix.y * 72 / 300)
                original_parent_sha = hashlib.sha256(png_bytes(image)).hexdigest()
                original_size = list(image.size)
                orientation_path = (
                    Path(str(checkpoint_path) + f".page-{page_number}.orientation.json")
                    if checkpoint_path
                    else None
                )
                decision = await select_orientation(
                    image,
                    layout_image,
                    file_hash=artifact.source_file_hash,
                    page=page_number,
                    pixel_to_pdf=affine,
                    model=health["model_fingerprint"],
                    pipeline=health["pipeline_fingerprint"],
                    client=client,
                    checkpoint_path=orientation_path,
                    soft_budget=max(0, page_budget - (time.monotonic() - page_started)),
                )
                orientation_ref = f"ocr_orientation_page_{page_number}"
                artifact.coordinate_transforms[orientation_ref] = decision
                if decision["status"] == "pending":
                    pending.append(page_number)
                    pages.append(
                        {
                            "page": page_number,
                            "status": "pending",
                            "pending_regions": 1,
                            "unresolved_regions": 0,
                            "elapsed_seconds": time.monotonic() - page_started,
                            "tiles": 0,
                            "regional_rechecks": 0,
                            "pending_phase": "orientation",
                        }
                    )
                    for page_state in artifact.page_states:
                        if page_state.page_number == page_number:
                            page_state.ocr_status = "pending"
                    image = layout_image = pix = None
                    if progress_callback:
                        await progress_callback(
                            {
                                "phase": "pending",
                                "physical_page": page_number,
                                "completed_count": len(pages) - len(pending),
                                "total_pages": len(candidates),
                                "page_budget_seconds": PAGE_SOFT_BUDGET_SECONDS,
                                "pending_phase": "orientation",
                            }
                        )
                    continue
                orthogonal = decision["angle"]
                image, inverse_orthogonal, orthogonal_ops = rotate(image, orthogonal)
                layout_image, _, _ = rotate(layout_image, orthogonal)
                skew = estimate_layout_angle(layout_image)
                layout_image = None
                image, inverse_skew, skew_ops = rotate(image, skew)
                affine = inverse_skew.then(inverse_orthogonal).then(affine)
                operations = orthogonal_ops + skew_ops
                angle = orthogonal + skew
                pix = None

                def high_resolution(box, attempt, affine=affine, page=page, angle=angle):
                    clip = fitz.Rect(affine.box(box))
                    dpi = 450 if attempt == 1 else 600
                    zoom = dpi / 72
                    raster = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
                    cropped = Image.open(io.BytesIO(raster.tobytes("png"))).convert("RGB")
                    # clip 栅格原点使用 pixmap 整数坐标，避免四舍五入偏移。
                    transform = Affine(a=1 / zoom, d=1 / zoom, e=raster.x / zoom, f=raster.y / zoom)
                    # 方向已经由原图探针确定，第二轮不得任意倒置 6/9 等数字。
                    cropped, inverse, prep = rotate(cropped, angle)
                    transform = inverse.then(transform)
                    return cropped, (0, 0, cropped.width, cropped.height), transform, prep

                from src.services.ocr_image_grid import physical_grid_tables
                from src.services.ocr_grid_fragments import grid_recovery_regions

                grids = physical_grid_tables(
                    image,
                    file_hash=artifact.source_file_hash,
                    page=page_number,
                    pixel_to_pdf=affine,
                    parent_image_sha256=original_parent_sha,
                )
                existing_grids = {grid.fragment_uid for grid in artifact.source_grids}
                artifact.source_grids.extend(
                    grid for grid in grids if grid.fragment_uid not in existing_grids
                )
                page_render = {
                    "dpi": 300,
                    "original_size_px": original_size,
                    "rotation": page.rotation,
                    "cropbox": list(page.cropbox),
                    "rotation_matrix": list(page.rotation_matrix),
                    "derotation_matrix": list(page.derotation_matrix),
                    "preprocessing_version": PREPROCESSING_VERSION,
                    "original_parent_image_sha256": original_parent_sha,
                    "orientation_decision_ref": orientation_ref,
                }

                def discover_regions(
                    first,
                    retries,
                    grids=grids,
                    page_number=page_number,
                    page_render=page_render,
                    layout_sha=layout_sha,
                ):
                    view = ParseArtifact(
                        artifact.source_file_hash,
                        page_count=artifact.page_count,
                        ocr_model_fingerprint=artifact.ocr_model_fingerprint,
                    )
                    append_tile_results(
                        view,
                        first + retries,
                        page_number=page_number,
                        layout_sha=layout_sha,
                        page_recovery={},
                        page_render=page_render,
                    )
                    return {
                        "requests": grid_recovery_regions(
                            view, grids, requested_profile=artifact.reconstruction_profile
                        ),
                        "physical_cell_boxes": [
                            c.ocr_provenance["physical_grid"]["cell"]["bbox_px"]
                            for grid in grids
                            for c in grid.cells
                        ],
                    }

                checkpoint = (
                    Path(str(checkpoint_path) + f".page-{page_number}.v3.json")
                    if checkpoint_path
                    else None
                )
                tile_options = dict(
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
                    region_discovery=discover_regions,
                )
                results, rechecks, state = await recognize_tiles(
                    image,
                    **tile_options,
                    soft_budget=max(0, page_budget - (time.monotonic() - page_started)),
                )
                # 新表头可能暴露下一批非法金额；仍在同一断点和页预算中推进。
                while (
                    state.get("new_semantic_regions")
                    and time.monotonic() - page_started < page_budget
                ):
                    results, rechecks, state = await recognize_tiles(
                        image,
                        **tile_options,
                        soft_budget=max(0, page_budget - (time.monotonic() - page_started)),
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
                completed_boxes.update(
                    tuple(record["context"]["crop_box_px"])
                    for record in state.get("observed_empty", {}).values()
                )
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
                append_tile_results(
                    artifact,
                    results + rechecks,
                    page_number=page_number,
                    layout_sha=layout_sha,
                    page_recovery=page_recovery,
                    page_render=page_render,
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
                    "elapsed_seconds": time.monotonic() - page_started,
                    "tiling_elapsed_seconds": state["elapsed_seconds"],
                    "orientation_status": decision["status"],
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
                            "page_budget_seconds": PAGE_SOFT_BUDGET_SECONDS,
                        }
                    )
    return {
        "enabled": True,
        "status": "pending" if pending else "review_required",
        "requested_pages": candidates,
        "pages": pages,
        "failed_pages": pending,
        "usable_for_rules": False,
        "execution": {
            "mode": "v3_tiled",
            "page_budget_seconds": PAGE_SOFT_BUDGET_SECONDS,
            "document_budget_seconds": document_soft_budget,
            "budget_is_soft": True,
            "schedule_version": SCHEDULE_VERSION,
            "elapsed_seconds": time.monotonic() - document_started,
        },
    }
