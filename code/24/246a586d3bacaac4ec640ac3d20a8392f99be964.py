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

TILING_VERSION = "fidelity-tiles-v10-source-strip-core-bands"
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


def sparse_wide_strips(ink, regular_width):
    """稀疏封面的长标题用低高度原图条带读取，不缩字，也不扩大全页推理。"""
    import numpy as np

    height, width = ink.shape
    # 有多个长横线的区域由表格格区调度，避免把整个金额行当成一个文本片段。
    if width <= regular_width or (ink.sum(axis=1) >= width * 0.6).sum() > 4:
        return []
    ys = np.flatnonzero(ink.sum(axis=1) >= max(8, width // 200))
    bands = []
    for y in ys:
        if not bands or y > bands[-1][-1] + 6:
            bands.append([])
        bands[-1].append(int(y))
    bands = [band for band in bands if len(band) >= 5]
    if len(bands) > 6:
        return []
    boxes = []
    band_boxes = []
    for band in bands:
        top, bottom = max(0, band[0] - 48), min(height, band[-1] + 49)
        xs = np.flatnonzero(ink[band[0]:band[-1] + 1].any(axis=0))
        if not len(xs):
            continue
        left, right = max(0, int(xs[0]) - 48), min(width, int(xs[-1]) + 49)
        band_boxes.append((left, top, right, bottom))
    for index, box in enumerate(band_boxes):
        left, top, right, bottom = box
        # 标题的“年度 + 主体”和“决算公开”可以分两行；段落仍受相同像素量约束。
        if right - left > regular_width:
            for following in band_boxes[index + 1 : index + 3]:
                union = (min(left, following[0]), top, max(right, following[2]), following[3])
                if (
                    following[1] - bottom > 2 * max(bottom - top, following[3] - following[1])
                    or (union[2] - union[0]) * (union[3] - union[1]) > 960 * 640
                ):
                    break
                left, top, right, bottom = union
        # 同等像素量内调整长宽，给可辨认长标题保留完整的横向上下文。
        if (
            regular_width < right - left <= MAX_WIDTH
            and (right - left) * (bottom - top) <= 960 * 640
        ):
            boxes.append((left, top, right, bottom))
    return boxes


def tile_boxes(image):
    import numpy as np

    gray = np.asarray(image.convert("L"))
    ink = gray < 210
    width = min(MAX_WIDTH, max(256, int(os.getenv("OCR_TILE_WIDTH", "960"))))
    height = min(MAX_HEIGHT, max(256, int(os.getenv("OCR_TILE_HEIGHT", "640"))))
    xs = list(_intervals(image.width, width, ink.sum(axis=0)))
    ys = list(_intervals(image.height, height, ink.sum(axis=1)))
    regular = [(x0, y0, x1, y1) for y0, y1 in ys for x0, x1 in xs]
    # 深色墨迹仅决定长标题的读取区域，识别仍用未经改色的原始 RGB 像素。
    return list(dict.fromkeys([*sparse_wide_strips(gray < 150, width), *regular]))


def ink_regions(image):
    """从原图提出独立候选，首轮无返回也能发现文字和空缺网格。"""
    import numpy as np

    try:
        import cv2
    except ImportError:
        # 本地纯 Python 验证不依赖 OCR 镜像的 OpenCV；按可见墨迹带提出补读区域。
        from src.services.ocr_image_grid import stroke_mask

        raw_ink = np.asarray(image.convert("L")) < 200
        ink = raw_ink & ~stroke_mask(raw_ink, max(24, image.width // 40))
        ink &= ~stroke_mask(raw_ink, max(24, image.height // 50), vertical=True)
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
            x_groups = []
            for x in xs:
                if not x_groups or x > x_groups[-1][-1] + 6:
                    x_groups.append([])
                x_groups[-1].append(int(x))
            for xs in x_groups:
                if len(xs) <= 2 or y1 - y0 < 3:
                    continue
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


def merge_recovery_regions(regions):
    """同一区域合并计数；相邻格仅因裁剪留边相交时仍分别补读。"""
    merged = []
    for region in regions:
        box = list(region)
        while True:
            match = None
            for old in merged:
                overlap = max(0, min(box[2], old[2]) - max(box[0], old[0])) * max(
                    0, min(box[3], old[3]) - max(box[1], old[1])
                )
                small = min(
                    (box[2] - box[0]) * (box[3] - box[1]), (old[2] - old[0]) * (old[3] - old[1])
                )
                if small > 0 and overlap / small >= 0.5:
                    match = old
                    break
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


def same_recovery_region(a, b):
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return small > 0 and overlap / small >= 0.5


def _visible_text_ink(image):
    import numpy as np

    ink = np.asarray(image.convert("L")) < 200
    # 网线不是数字。逐行/列删除长的连续直线，不能按整页占比忽略短表区。
    for axis, length in ((1, max(20, image.width // 25)), (0, max(20, image.height // 25))):
        view = ink if axis == 1 else ink.T
        for row in view:
            changes = np.diff(np.r_[False, row, False].astype("int8"))
            starts, ends = np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)
            for start, end in zip(starts, ends, strict=True):
                if end - start >= length:
                    row[start:end] = False
    return ink


def uncovered_regions(regions, blocks, image=None):
    """用原图墨迹覆盖检查完整性，不能因为片段覆盖区域中心就宣称读全。"""
    import numpy as np
    from src.engine.fiscal_lexeme import lex_numeric

    ink = _visible_text_ink(image) if image is not None else None
    result = []
    for region in regions:
        x0, y0, x1, y1 = [int(round(v)) for v in region]
        matches = [
            b
            for b in blocks
            if b.bbox and b.bbox[0] < x1 and b.bbox[2] > x0 and b.bbox[1] < y1 and b.bbox[3] > y0
        ]
        good = []
        for block in matches:
            token = lex_numeric(block.text)
            # 文字标题中的年份不是一个“非法金额”候选。
            numeric_like = any(c.isdigit() for c in block.text) and not any(
                "\u4e00" <= c <= "\u9fff"
                for c in block.text.replace("万元", "").replace("亿元", "").replace("元", "")
            )
            if block.score >= 0.98 and not (numeric_like and token.kind == "invalid"):
                good.append(block)
        if not good:
            result.append(region)
            continue
        if ink is None:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            complete = any(
                b.bbox[0] <= cx <= b.bbox[2] and b.bbox[1] <= cy <= b.bbox[3] for b in good
            )
        else:
            remaining = ink[max(0, y0) : max(0, y1), max(0, x0) : max(0, x1)].copy()
            for block in good:
                # 一像素栅格误差只扩展覆盖框，不修改片段坐标或原文。
                left = max(x0, int(block.bbox[0]) - 1)
                top = max(y0, int(block.bbox[1]) - 1)
                right = min(x1, int(block.bbox[2]) + 2)
                bottom = min(y1, int(block.bbox[3]) + 2)
                if right > left and bottom > top:
                    remaining[
                        max(0, top - y0) : max(0, bottom - y0),
                        max(0, left - x0) : max(0, right - x0),
                    ] = False
            complete = not np.any(remaining)
        if not complete:
            result.append(region)
    return result


@dataclass
class TileRequest:
    image: bytes
    context: dict


def make_tile(
    image,
    box,
    *,
    parent_sha,
    file_hash,
    page,
    pixel_to_pdf,
    attempt=0,
    operations=(),
    root_image_sha256=None,
):
    box = tuple(int(round(v)) for v in box)
    if not (0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height):
        raise ValueError("tile crop must stay inside original image")
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
        "parent_size_px": list(image.size),
        "input_sha256": hashlib.sha256(data).hexdigest(),
        "pixel_to_pdf": transform.values(),
        "pdf_to_pixel": transform.inverse().values(),
        "preprocessing": list(operations),
        "tiling_version": TILING_VERSION,
    }
    if root_image_sha256 is not None:
        # 补读的直接父图是高分辨率裁剪；根图仍是同一原页的 300 DPI 图像。
        context["root_image_sha256"] = root_image_sha256
    context["tile_id"] = digest(context)
    return TileRequest(data, context)


def save_checkpoint(path, payload):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    sealed = {key: value for key, value in payload.items() if key != "checkpoint_sha256"}
    sealed["checkpoint_sha256"] = digest(sealed)
    temporary.write_text(json.dumps(sealed, ensure_ascii=False, sort_keys=True, allow_nan=False))
    temporary.replace(path)


def seam_regions(context, response, image_size, padding=OVERLAP):
    """内部图块边缘的片段必须从原图扩区读全；高分截断也不能豁免。"""
    width, height = context["size_px"]
    left, top, right, bottom = context["crop_box_px"]
    page_width, page_height = image_size
    regions = []
    for block in response.blocks:
        if not block.bbox or not block.text.strip():
            continue
        x0, y0, x1, y1 = block.bbox
        if (
            (left > 0 and x0 <= 8)
            or (top > 0 and y0 <= 8)
            or (right < page_width and x1 >= width - 8)
            or (bottom < page_height and y1 >= height - 8)
        ):
            regions.append(
                [
                    max(0, left + x0 - padding),
                    max(0, top + y0 - padding),
                    min(page_width, left + x1 + padding),
                    min(page_height, top + y1 + padding),
                ]
            )
    return regions


def _restore_record(record, context):
    from src.services.ocr_client import OcrBlock, OcrPageResult

    if (
        not isinstance(record, dict)
        or record.get("context") != context
        or digest({"context": context, "result": record.get("result")}) != record.get("digest")
    ):
        return None
    raw = record["result"]
    return OcrPageResult(
        raw["page_number"],
        blocks=[OcrBlock(**b) for b in raw["blocks"]],
        tables=raw["tables"],
        provenance=raw["provenance"],
        error=raw.get("error"),
    )


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
    required_regions=(),
    operations=(),
    original_parent_sha=None,
    region_discovery=None,
):
    """逐请求及轮次落盘。预算只暂停调度，已耗尽的两轮不会在续跑中重新开始。"""
    from src.services.ocr_client import ocr_page, OcrBlock

    parent = original_parent_sha or hashlib.sha256(png_bytes(image)).hexdigest()
    boxes = tile_boxes(image)
    identity = {
        "parent": parent,
        "file": file_hash,
        "page": page,
        "model": model,
        "pipeline": pipeline,
        "tiling": TILING_VERSION,
        "tile_boxes": boxes,
        "pixel_to_pdf": pixel_to_pdf.values(),
        "required_regions": list(required_regions),
        "operations": list(operations),
        "recovery_contract": "source-root-and-semantic-ledger-v2-append-discovery",
    }
    # JSON 往返将 tuple 规范为 list，避免续跑身份因 Python 容器类型不同而丢失。
    identity = json.loads(json.dumps(identity))
    state = {
        "identity": identity,
        "completed": {},
        "gap_plan": None,
        "regions": {},
        "semantic_requests": {},
        "failures": {},
        "observed_empty": {},
        "pending": [],
        "unresolved": [],
        "status": "pending",
    }
    if checkpoint_path and Path(checkpoint_path).is_file():
        loaded = json.loads(Path(checkpoint_path).read_text())
        unsigned = {key: value for key, value in loaded.items() if key != "checkpoint_sha256"}
        if (
            loaded.get("checkpoint_sha256") == digest(unsigned)
            and loaded.get("identity") != identity
        ):
            # 不覆盖另一版本的断点，否则两轮上限会被静默重置。
            raise ValueError(
                "OCR checkpoint identity changed; preserve and explicitly migrate the journal"
            )
        if loaded.get("identity") == identity and loaded.get("checkpoint_sha256") == digest(
            unsigned
        ):
            state = unsigned
    started = time.monotonic()
    results, rechecks, page_blocks, pending, edges = [], [], [], [], []

    async def execute(request, *, retry=False):
        tile_id = request.context["tile_id"]
        record = state["completed"].get(tile_id)
        cached = _restore_record(record, request.context)
        if cached is not None:
            return cached
        if time.monotonic() - started >= soft_budget:
            pending.append(tile_id)
            return None
        response = await ocr_page(
            client,
            image_png=request.image,
            page_number=page,
            timeout_seconds=RETRY_TIMEOUT if retry else TILE_TIMEOUT,
            region_only=retry,
            tile_context=request.context,
        )
        if response.ok and response.provenance.get("pipeline_fingerprint", pipeline) != pipeline:
            response.error = "OCR response pipeline fingerprint mismatch"
        record = {"context": request.context, "result": response.to_dict()}
        record["digest"] = digest(record)
        # 失败响应也记录：补读轮次不可因重启而无限增加。
        if retry or response.ok:
            state["completed"][tile_id] = record
        if not response.ok:
            state.setdefault("failures", {}).setdefault(tile_id, []).append(record)
        save_checkpoint(checkpoint_path, state)
        if response.error and any(
            message in response.error
            for message in (
                "model fingerprint mismatch",
                "pipeline fingerprint mismatch",
                "tile identity mismatch",
            )
        ):
            raise ValueError("OCR identity guard rejected request; failure journal preserved")
        return response

    def page_block(block, context):
        if not block.bbox:
            return OcrBlock(block.text, block.score, None, page)
        box = Affine(*context["pixel_to_pdf"]).then(pixel_to_pdf.inverse()).box(block.bbox)
        return OcrBlock(block.text, block.score, box, page)

    for box in boxes:
        request = make_tile(
            image,
            box,
            parent_sha=parent,
            file_hash=file_hash,
            page=page,
            pixel_to_pdf=pixel_to_pdf,
            operations=operations,
            root_image_sha256=parent,
        )
        # 纯白原图块以独立测量日志记录，不能伪造一个“成功的 OCR 空响应”。
        crop = image.crop(box)
        if crop.convert("RGB").getextrema() == ((255, 255), (255, 255), (255, 255)):
            state.setdefault("observed_empty", {})[request.context["tile_id"]] = {
                "context": request.context,
                "criterion": "all_original_rgb_pixels_equal_255",
            }
            save_checkpoint(checkpoint_path, state)
            continue
        result = await execute(request)
        if result is None:
            continue
        results.append((request.context, result))
        if result.ok:
            page_blocks.extend(page_block(b, request.context) for b in result.blocks)
            edges.extend(seam_regions(request.context, result, image.size))
        else:
            pending.append(request.context["tile_id"])
    # 缺少首轮图块时先补全首轮；不能把未调度区域当成已判定的 OCR 漏格。
    if not pending and state["gap_plan"] is None:
        gaps = merge_recovery_regions(
            uncovered_regions(ink_regions(image), page_blocks, image=image)
            + edges
            + list(required_regions)
        )
        state["gap_plan"] = gaps
        save_checkpoint(checkpoint_path, state)
    if not pending and region_discovery is not None and not state.get("semantic_discovery_done"):
        discovered = region_discovery(results, [])
        if isinstance(discovered, dict):
            requests = discovered["requests"]
            layout = discovered.get("physical_cell_boxes", [])
            # 已有原图闭合格时，按格检查取代跨整行的大块墨迹/接缝请求。
            # 开口或非矩形部分没有闭合格，仍保留原墨迹恢复路径。
            state["gap_plan"] = [
                box
                for box in state["gap_plan"]
                if not any(
                    cell[0] <= (box[0] + box[2]) / 2 <= cell[2]
                    and cell[1] <= (box[1] + box[3]) / 2 <= cell[3]
                    for cell in layout
                )
            ]
        else:
            requests = discovered
        for request in requests:
            key = request["region_key"]
            old = state.setdefault("semantic_requests", {}).get(key)
            if old is not None and old["box"] != request["box"]:
                raise ValueError("semantic region source geometry changed")
            state["semantic_requests"][key] = request
            if request["box"] not in state["gap_plan"]:
                state["gap_plan"].append(request["box"])
        # 同一物理位置的墨迹/字段请求归为同一区域，不能各自另领两轮。
        state["gap_plan"] = merge_recovery_regions(state["gap_plan"])
        state["semantic_discovery_done"] = True
        save_checkpoint(checkpoint_path, state)
    unresolved = []
    for box in state["gap_plan"] or []:
        region_id = digest(box)
        region = state["regions"].setdefault(
            region_id, {"box": box, "rounds": {}, "status": "pending"}
        )
        recovered = region["status"] == "recovered" and bool(region.get("accepted_requests"))
        if recovered and any(
            _restore_record(state["completed"].get(c["tile_id"]), c) is None
            for c in region["accepted_requests"]
        ):
            recovered = False
            region["status"] = "pending"
        for attempt in (1, 2):
            if recovered:
                # 仍返回已恢复的补读来源，重建结果不能在续跑时丢失。
                for c in region.get("accepted_requests", []):
                    r = _restore_record(state["completed"].get(c["tile_id"]), c)
                    if r is not None:
                        rechecks.append((c, r))
                break
            if time.monotonic() - started >= soft_budget:
                pending.append("region:" + region_id)
                break
            retry_source = (
                high_resolution(box, attempt)
                if high_resolution
                else (image, box, pixel_to_pdf, operations)
            )
            retry_image, retry_box, retry_affine = retry_source[:3]
            retry_ops = retry_source[3] if len(retry_source) > 3 else operations
            retry_crop = retry_image.crop(retry_box)
            retry_parent = hashlib.sha256(png_bytes(retry_image)).hexdigest()
            pieces, round_requests, incomplete = [], [], False
            for subbox in tile_boxes(retry_crop):
                absolute = [
                    subbox[0] + retry_box[0],
                    subbox[1] + retry_box[1],
                    subbox[2] + retry_box[0],
                    subbox[3] + retry_box[1],
                ]
                request = make_tile(
                    retry_image,
                    absolute,
                    parent_sha=retry_parent,
                    file_hash=file_hash,
                    page=page,
                    pixel_to_pdf=retry_affine,
                    attempt=attempt,
                    operations=retry_ops,
                    root_image_sha256=parent,
                )
                response = await execute(request, retry=True)
                if response is None:
                    incomplete = True
                    break
                rechecks.append((request.context, response))
                round_requests.append(request.context)
                pieces.extend(
                    page_block(b, request.context) for b in response.blocks if response.ok
                )
            if incomplete:
                break
            # 覆盖判据来自原图墨迹，不是“有高分返回即恢复”或同模型投票。
            local_blocks = [
                OcrBlock(
                    b.text,
                    b.score,
                    [b.bbox[0] - box[0], b.bbox[1] - box[1], b.bbox[2] - box[0], b.bbox[3] - box[1]]
                    if b.bbox
                    else None,
                    page,
                )
                for b in pieces
            ]
            remaining = uncovered_regions(
                ink_regions(image.crop(box)), local_blocks, image=image.crop(box)
            )
            recovered = bool(pieces) and not remaining and all(b.score >= 0.98 for b in pieces)
            if recovered and region_discovery is not None:
                semantic_missing = region_discovery(results, rechecks)
                if isinstance(semantic_missing, dict):
                    semantic_missing = semantic_missing["requests"]
                # 原图覆盖通过后仍须通过字段结构判据；大框不能掩盖漏格/非法数值。
                recovered = not any(
                    same_recovery_region(request["box"], box) for request in semantic_missing
                )
            region["rounds"][str(attempt)] = {
                "requests": round_requests,
                "status": "recovered" if recovered else "unresolved",
                "uncovered": remaining,
            }
            region["status"] = (
                "recovered" if recovered else "exhausted" if attempt == 2 else "pending"
            )
            if recovered:
                region["accepted_requests"] = round_requests
                break
            save_checkpoint(checkpoint_path, state)
        if not recovered:
            if region["status"] == "exhausted":
                unresolved.append(region_id)
            elif not pending:
                pending.append("region:" + region_id)
    new_semantic_regions = 0
    if not pending and region_discovery is not None:
        discovered = region_discovery(results, rechecks)
        requests = discovered["requests"] if isinstance(discovered, dict) else discovered
        for request in requests:
            key = request["region_key"]
            if key in state.setdefault("semantic_requests", {}):
                continue
            # 表题补读后才显现的金额类型错误同样进入追加日志；不清空已耗轮次。
            state["semantic_requests"][key] = request
            existing = next(
                (
                    region
                    for region in state["regions"].values()
                    if same_recovery_region(region["box"], request["box"])
                ),
                None,
            )
            if existing is not None:
                existing.setdefault("semantic_keys", []).append(key)
                if existing["status"] == "exhausted":
                    continue
                existing["status"] = "pending"
            else:
                state["gap_plan"].append(request["box"])
            new_semantic_regions += 1
            pending.append("semantic:" + key)
    state.update(
        pending=pending,
        unresolved=unresolved,
        status="pending" if pending else "review_required" if unresolved else "completed",
        elapsed_seconds=time.monotonic() - started,
        gaps=state["gap_plan"] or [],
        new_semantic_regions=new_semantic_regions,
    )
    # 续跑也返回全部已保存的真实补读候选；不能只留下“获选”片段而丢掉冲突证据。
    retained = {context["tile_id"]: (context, response) for context, response in rechecks}
    for record in state["completed"].values():
        context = record["context"]
        if context.get("attempt", 0) <= 0:
            continue
        restored = _restore_record(record, context)
        if restored is not None:
            retained[context["tile_id"]] = (context, restored)
    rechecks = sorted(retained.values(), key=lambda item: (item[0]["attempt"], item[0]["tile_id"]))
    save_checkpoint(checkpoint_path, state)
    return results, rechecks, state
