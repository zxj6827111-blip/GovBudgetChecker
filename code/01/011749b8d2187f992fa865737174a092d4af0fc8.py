"""用同一离线识别器选择正交方向；原图探针逐请求留证，不作为独立投票。"""

from pathlib import Path
import hashlib
import json
import time

from src.services.input_geometry import Affine
from src.services.ocr_tiles import (
    make_tile,
    png_bytes,
    digest,
    save_checkpoint,
    ink_regions,
    _restore_record,
)
from src.services.ocr_preprocessing import rotate
from src.services.ocr_client import ocr_page

ORIENTATION_VERSION = "source-orientation-probes-v2-horizontal-baselines"


def probe_boxes(image, layout):
    """150 DPI 只定位墨迹，探针始终从 300 DPI 原图裁剪。"""
    sx, sy = image.width / layout.width, image.height / layout.height
    regions = ink_regions(layout)
    if not regions:
        return []
    left = min(region[0] for region in regions) * sx
    right = max(region[2] for region in regions) * sx
    top = min(region[1] for region in regions) * sy
    bottom = max(region[3] for region in regions) * sy
    width, height = min(640, image.width), min(480, image.height)
    center = ((left + right - width) / 2, (top + bottom - height) / 2)
    anchors = (
        [center]
        if right - left <= width and bottom - top <= height
        else [
            (left, top),
            (right - width, bottom - height),
            (right - width, top),
            (left, bottom - height),
            center,
        ]
    )
    boxes = []
    for x, y in anchors:
        x0 = max(0, min(image.width - width, int(x)))
        y0 = max(0, min(image.height - height, int(y)))
        box = [x0, y0, x0 + width, y0 + height]
        if box not in boxes:
            boxes.append(box)
    return boxes


def content_quality(response):
    """方向选择看可定位的高分汉字；不利用金额差额、词典纠错或多轮票数。"""
    if not response.ok:
        return 0
    score = 0
    for block in response.blocks:
        if not block.bbox or block.score < 0.98:
            continue
        chinese = sum("\u4e00" <= char <= "\u9fff" for char in block.text)
        width, height = block.bbox[2] - block.bbox[0], block.bbox[3] - block.bbox[1]
        # 识别器会局部转正竖直词框；只有横向词框才为整页方向提供证据。
        # 单字可能正反对称，也不承担方向证明。
        if chinese >= 2 and width >= height * 1.2:
            score += min(80, chinese)
    return score


def choose_angle(scores):
    best = max(scores, key=lambda angle: (scores[angle], -angle))
    second = max((value for angle, value in scores.items() if angle != best), default=0)
    certain = scores[best] >= 8 and scores[best] >= second * 1.3 and scores[best] - second >= 4
    return (best, "resolved") if certain else (0, "ambiguous")


async def select_orientation(
    image,
    layout,
    *,
    file_hash,
    page,
    pixel_to_pdf,
    model,
    pipeline,
    client,
    checkpoint_path=None,
    soft_budget=600,
):
    parent = hashlib.sha256(png_bytes(image)).hexdigest()
    boxes = probe_boxes(image, layout)
    identity = {
        "version": ORIENTATION_VERSION,
        "file_hash": file_hash,
        "page": page,
        "model": model,
        "pipeline": pipeline,
        "parent": parent,
        "boxes": boxes,
        "pixel_to_pdf": pixel_to_pdf.values(),
    }
    identity = json.loads(json.dumps(identity))
    state = {"identity": identity, "completed": {}, "status": "pending"}
    if checkpoint_path and Path(checkpoint_path).is_file():
        saved = json.loads(Path(checkpoint_path).read_text())
        unsigned = {k: v for k, v in saved.items() if k != "checkpoint_sha256"}
        if saved.get("checkpoint_sha256") != digest(unsigned) or saved.get("identity") != identity:
            raise ValueError("orientation checkpoint identity or digest mismatch; preserve journal")
        state = unsigned
    if not boxes:
        # 定位没有文字不代表原图白；只有逐像素纯白才直接确认方向无关。
        white = image.convert("RGB").getextrema() == ((255, 255), (255, 255), (255, 255))
        state.update(angle=0, status="resolved" if white else "ambiguous", scores={})
        save_checkpoint(checkpoint_path, state)
        return state
    started = time.monotonic()
    scores = {angle: 0 for angle in (0, 90, 180, 270)}
    for stage in (boxes[:2], boxes[2:]):
        if not stage:
            continue
        for angle in scores:
            for box in stage:
                crop = image.crop(box)
                cropped, inverse, operations = rotate(crop, angle)
                affine = inverse.then(Affine(e=box[0], f=box[1])).then(pixel_to_pdf)
                context_operations = [
                    {
                        "operation": "source_crop",
                        "root_image_sha256": parent,
                        "source_crop_box_px": box,
                    },
                    *operations,
                ]
                request = make_tile(
                    cropped,
                    (0, 0, *cropped.size),
                    parent_sha=hashlib.sha256(png_bytes(cropped)).hexdigest(),
                    file_hash=file_hash,
                    page=page,
                    pixel_to_pdf=affine,
                    root_image_sha256=parent,
                    operations=context_operations,
                )
                record = state["completed"].get(request.context["tile_id"])
                result = _restore_record(record, request.context)
                if result is None:
                    if time.monotonic() - started >= soft_budget:
                        state.update(status="pending", elapsed_seconds=time.monotonic() - started)
                        save_checkpoint(checkpoint_path, state)
                        return state
                    result = await ocr_page(
                        client,
                        image_png=request.image,
                        page_number=page,
                        timeout_seconds=120,
                        region_only=True,
                        tile_context=request.context,
                    )
                    if (
                        result.ok
                        and result.provenance.get("pipeline_fingerprint", pipeline) != pipeline
                    ):
                        result.error = "OCR response pipeline fingerprint mismatch"
                    record = {"context": request.context, "result": result.to_dict()}
                    record["digest"] = digest(record)
                    state["completed"][request.context["tile_id"]] = record
                    save_checkpoint(checkpoint_path, state)
                if result.error:
                    state.update(status="pending", error=result.error)
                    save_checkpoint(checkpoint_path, state)
                    return state
                scores[angle] += content_quality(result)
        _, status = choose_angle(scores)
        if status == "resolved":
            break
    chosen, status = choose_angle(scores)
    state.update(
        angle=chosen,
        status=status,
        scores={str(angle): score for angle, score in scores.items()},
        elapsed_seconds=time.monotonic() - started,
        evidence_role="same_model_direction_selection_not_independent_accuracy_vote",
    )
    save_checkpoint(checkpoint_path, state)
    return state
