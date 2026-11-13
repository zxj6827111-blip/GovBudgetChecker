"""原图切块、漏区发现和可恢复调度。布局或重试从不生成数字。"""

from __future__ import annotations

import hashlib
import io
import json
import time
import os
from dataclasses import dataclass
from pathlib import Path

from src.services.input_geometry import Affine

TILING_VERSION = "fidelity-tiles-v1"
MAX_WIDTH, MAX_HEIGHT, OVERLAP = 2048, 1536, 128
TILE_TIMEOUT, RETRY_TIMEOUT, PAGE_SOFT_BUDGET = 120, 30, 600


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _intervals(length, maximum, projection=None):
    start = 0
    while True:
        end = min(length, start + maximum)
        if end < length and projection is not None:
            # 限定搜索带，优先沿空隙切，仍保证固定重叠和正向推进。
            lo = max(start + OVERLAP + 1, end - 128)
            end = min(range(lo, end + 1), key=lambda i: (projection[i - 1], -i))
        yield start, end
        if end == length:
            break
        start = end - OVERLAP


def tile_boxes(image):
    import numpy as np

    gray = np.asarray(image.convert("L"))
    ink = gray < 210
    width = min(MAX_WIDTH, max(256, int(os.getenv("OCR_TILE_WIDTH", "960"))))
    height = min(MAX_HEIGHT, max(256, int(os.getenv("OCR_TILE_HEIGHT", "640"))))
    xs = list(_intervals(image.width, width, ink.sum(axis=0)))
    ys = list(_intervals(image.height, height, ink.sum(axis=1)))
    return [(x0, y0, x1, y1) for y0, y1 in ys for x0, x1 in xs]


def ink_regions(image):
    """从原图提出独立候选，首轮无返回也能发现文字和空缺网格。"""
    import numpy as np

    try:
        import cv2
    except ImportError:
        # 本地纯 Python 验证不依赖 OCR 镜像的 OpenCV；按可见墨迹带提出补读区域。
        gray = np.asarray(image.convert("L"))
        ink = gray < 200
        ink[:, (ink.sum(axis=0) > image.height * 0.8)] = False
        ink[(ink.sum(axis=1) > image.width * 0.8), :] = False
        ys = np.flatnonzero(ink.any(axis=1))
        regions = []
        groups = []
        for y in ys:
            if not groups or y > groups[-1][-1] + 3:
                groups.append([])
            groups[-1].append(int(y))
        for group in groups:
            y0, y1 = group[0], group[-1] + 1
            xs = np.flatnonzero(ink[y0:y1].any(axis=0))
            if len(xs) > 2 and y1 - y0 >= 3:
                regions.append(
                    [
                        max(0, int(xs[0]) - 4),
                        max(0, y0 - 4),
                        min(image.width, int(xs[-1]) + 5),
                        min(image.height, y1 + 4),
                    ]
                )
        return regions
    gray = np.asarray(image.convert("L"))
    binary = (gray < 200).astype("uint8") * 255
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, np.ones((1, max(20, image.width // 25)), np.uint8)
    )
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, np.ones((max(20, image.height // 25), 1), np.uint8)
    )
    text_ink = cv2.subtract(binary, cv2.bitwise_or(horizontal, vertical))
    words = cv2.dilate(text_ink, np.ones((3, 9), np.uint8))
    contours, _ = cv2.findContours(words, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w >= 3 and h >= 4 and cv2.countNonZero(text_ink[y : y + h, x : x + w]) >= 5:
            regions.append(
                [
                    max(0, x - 4),
                    max(0, y - 4),
                    min(image.width, x + w + 4),
                    min(image.height, y + h + 4),
                ]
            )
    return sorted(regions, key=lambda b: (b[1], b[0]))


def text_layer_quality(chars):
    reasons = []
    seen = set()
    for char in chars:
        text = str(char.get("text") or "")
        if (
            "\ufffd" in text
            or "(cid:" in text
            or any(ord(c) < 32 and not c.isspace() for c in text)
        ):
            reasons.append("font_decode_anomaly")
        key = (text, char.get("x0"), char.get("top"), char.get("x1"), char.get("bottom"))
        if key in seen:
            reasons.append("duplicate_overlay")
        seen.add(key)
        if char.get("render_mode") == 3 or char.get("alpha") == 0:
            reasons.append("hidden_text")
    return {"trusted": not reasons, "reasons": sorted(set(reasons))}


def merge_regions(regions, gap=3):
    merged = []
    for region in regions:
        box = list(region)
        while True:
            match = next(
                (
                    b
                    for b in merged
                    if min(b[2], box[2]) + gap >= max(b[0], box[0])
                    and min(b[3], box[3]) + gap >= max(b[1], box[1])
                ),
                None,
            )
            if match is None:
                break
            merged.remove(match)
            box = [
                min(box[0], match[0]),
                min(box[1], match[1]),
                max(box[2], match[2]),
                max(box[3], match[3]),
            ]
        merged.append(box)
    return merged


def uncovered_regions(regions, blocks):
    from src.engine.fiscal_lexeme import lex_numeric

    result = []
    for region in regions:
        cx, cy = (region[0] + region[2]) / 2, (region[1] + region[3]) / 2
        matches = [
            b
            for b in blocks
            if b.bbox and b.bbox[0] <= cx <= b.bbox[2] and b.bbox[1] <= cy <= b.bbox[3]
        ]
        if not matches or any(
            b.score < 0.98
            or lex_numeric(b.text).kind == "invalid"
            and any(c.isdigit() for c in b.text)
            for b in matches
        ):
            result.append(region)
    return result


@dataclass
class TileRequest:
    image: bytes
    context: dict


def make_tile(image, box, *, parent_sha, file_hash, page, pixel_to_pdf, attempt=0, operations=()):
    crop = image.crop(box)
    data = png_bytes(crop)
    transform = Affine(e=box[0], f=box[1]).then(pixel_to_pdf)
    context = {
        "source_sha256": file_hash,
        "physical_page": page,
        "parent_image_sha256": parent_sha,
        "source_region": pixel_to_pdf.box(box),
        "crop_box_px": list(box),
        "attempt": attempt,
        "size_px": [crop.width, crop.height],
        "input_sha256": hashlib.sha256(data).hexdigest(),
        "pixel_to_pdf": transform.values(),
        "pdf_to_pixel": transform.inverse().values(),
        "preprocessing": list(operations),
        "tiling_version": TILING_VERSION,
    }
    context["tile_id"] = digest(context)
    return TileRequest(data, context)


def save_checkpoint(path, payload):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False))
    temporary.replace(path)


async def recognize_tiles(
    image,
    *,
    file_hash,
    page,
    pixel_to_pdf,
    client,
    model,
    pipeline,
    checkpoint_path=None,
    soft_budget=PAGE_SOFT_BUDGET,
    high_resolution=None,
):
    """单推理、逐块落盘；软预算结束是 pending，续跑复用原响应，禁止伪装完成。"""
    from src.services.ocr_client import ocr_page, OcrBlock, OcrPageResult

    parent = hashlib.sha256(png_bytes(image)).hexdigest()
    identity = {
        "parent": parent,
        "file": file_hash,
        "page": page,
        "model": model,
        "pipeline": pipeline,
        "tiling": TILING_VERSION,
        "pixel_to_pdf": pixel_to_pdf.values(),
    }
    state = {"identity": identity, "completed": {}, "pending": [], "status": "pending"}
    if checkpoint_path and Path(checkpoint_path).is_file():
        loaded = json.loads(Path(checkpoint_path).read_text())
        if loaded.get("identity") == identity:
            state = loaded
    started = time.monotonic()
    results = []
    page_blocks = []
    pending = []
    for box in tile_boxes(image):
        request = make_tile(
            image, box, parent_sha=parent, file_hash=file_hash, page=page, pixel_to_pdf=pixel_to_pdf
        )
        tile_id = request.context["tile_id"]
        cached = state["completed"].get(tile_id)
        if cached and cached.get("context") == request.context:
            raw = cached["result"]
            result = OcrPageResult(
                page,
                blocks=[OcrBlock(**b) for b in raw["blocks"]],
                tables=raw["tables"],
                provenance=raw["provenance"],
                error=raw.get("error"),
            )
            if digest(raw) != cached.get("digest"):
                cached = None
        if not cached:
            if time.monotonic() - started >= soft_budget:
                pending.append(tile_id)
                continue
            result = await ocr_page(
                client,
                image_png=request.image,
                page_number=page,
                timeout_seconds=TILE_TIMEOUT,
                tile_context=request.context,
            )
        record = {
            "context": request.context,
            "result": {
                "blocks": [b.to_dict() for b in result.blocks],
                "tables": result.tables,
                "provenance": result.provenance,
                "error": result.error,
            },
        }
        record["digest"] = digest(record["result"])
        if result.ok:
            state["completed"][tile_id] = record
            for b in result.blocks:
                page_blocks.append(
                    OcrBlock(
                        b.text,
                        b.score,
                        [
                            b.bbox[0] + box[0],
                            b.bbox[1] + box[1],
                            b.bbox[2] + box[0],
                            b.bbox[3] + box[1],
                        ]
                        if b.bbox
                        else None,
                        page,
                    )
                )
        else:
            pending.append(tile_id)
        results.append((request.context, result))
        state["pending"] = pending
        save_checkpoint(checkpoint_path, state)
    gaps = uncovered_regions(ink_regions(image), page_blocks)
    rechecks = []
    for index, box in enumerate(gaps):
        recovered = False
        for attempt in (1, 2):
            if time.monotonic() - started >= soft_budget:
                break
            # 高分辨率只可来自原件重新渲染，不能靠插值冒充新证据。
            retry_image, retry_box, retry_affine = (
                high_resolution(box, attempt) if high_resolution else (image, box, pixel_to_pdf)
            )
            # 高分辨率补读区域也必须继续切块；尺寸超限不是放弃恢复的理由。
            retry_crop = retry_image.crop(retry_box)
            retry_parent = hashlib.sha256(png_bytes(retry_image)).hexdigest()
            recovered_parts = []
            for subbox in tile_boxes(retry_crop):
                absolute = (
                    subbox[0] + retry_box[0],
                    subbox[1] + retry_box[1],
                    subbox[2] + retry_box[0],
                    subbox[3] + retry_box[1],
                )
                request = make_tile(
                    retry_image,
                    absolute,
                    parent_sha=retry_parent,
                    file_hash=file_hash,
                    page=page,
                    pixel_to_pdf=retry_affine,
                    attempt=attempt,
                )
                response = await ocr_page(
                    client,
                    image_png=request.image,
                    page_number=page,
                    region_only=True,
                    timeout_seconds=RETRY_TIMEOUT,
                    tile_context=request.context,
                )
                rechecks.append((request.context, response))
                recovered_parts.append(
                    bool(
                        response.ok
                        and response.blocks
                        and all(block.score >= 0.98 for block in response.blocks)
                    )
                )
                save_checkpoint(checkpoint_path, state)
            if recovered_parts and all(recovered_parts):
                recovered = True
                break
        if not recovered:
            pending.append(f"gap:{index}:{box}")
    state.update(
        pending=pending,
        status="pending" if pending else "completed",
        elapsed_seconds=time.monotonic() - started,
        gaps=gaps,
        rechecks=[
            {"context": c, "provenance": r.provenance, "error": r.error} for c, r in rechecks
        ],
    )
    save_checkpoint(checkpoint_path, state)
    return results, rechecks, state
