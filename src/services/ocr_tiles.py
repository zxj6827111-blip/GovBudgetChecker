"""原图切块、漏区发现和可恢复调度。布局或重试从不生成数字。"""

from __future__ import annotations

import hashlib
import io
import json
import math
import time
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.services.input_geometry import Affine

TILING_VERSION = "fidelity-tiles-v7-pixel-edges"
MAX_WIDTH, MAX_HEIGHT, OVERLAP = 2048, 1536, 128
#: 补读块在文字框外留的上下文。网格重建只采用离块内边 ≥8 像素的完整片段
#: （complete_first_pass_cell），补读块留得比这窄，读出的完整数字会被整块丢弃。
RECOVERY_CONTEXT_PX = 12
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
            # 限定搜索带，优先沿空隙或网线切，仍保证固定重叠和正向推进。
            lo = max(start + OVERLAP + 1, end - max(128, maximum // 3))
            end = min(range(lo, end + 1), key=lambda i: (projection[i - 1], -i))
        yield start, end
        if end == length:
            break
        start = end - OVERLAP


def tile_profile():
    """检测输入不缩放时，单块像素决定服务端峰值内存（无 MKLDNN 的 CPU 卷积按
    像素展开，实测 512×512 约 4.2GiB、1024×512 约 6.7GiB，超过约 160 万像素会
    崩溃）。默认取 512×512：比旧的 512×256 少一半请求和重叠冗余，仍在 5GiB 内。"""
    width=int(os.getenv('OCR_TILE_WIDTH','512'))
    height=int(os.getenv('OCR_TILE_HEIGHT','512'))
    if not 256<=width<=MAX_WIDTH or not 256<=height<=MAX_HEIGHT:
        raise ValueError('OCR tile size outside supported full-resolution limits')
    return width,height


def first_pass_mode():
    """首轮默认只做文字检测+识别。表格由独立像素网格重建（ocr_grid_reconstruction），
    小块上的版面与表格结构模型既看不到整表，又让每块多跑五个模型。"""
    mode=os.getenv('OCR_FIRST_PASS_MODE','text').strip().lower()
    if mode not in {'text','structure'}:
        raise ValueError('OCR_FIRST_PASS_MODE must be text or structure')
    return mode


def tile_boxes(image):
    # 切线投影只看文字墨迹：表格网线不是文字，沿网线切开不会截断任何字，
    # 按原始墨迹投影反而会躲开网线、切进格内数字。
    ink = _visible_text_ink(image, independent_lines=True)
    width,height=tile_profile()
    if image.height < height:
        # 服务端内存只随块面积增长；矮而宽的补读行区域按同面积加宽，
        # 比固定方块少一半以上的请求和切口。
        width = min(MAX_WIDTH, width * height // max(256, image.height))
        height = max(256, image.height)
    xs = list(_intervals(image.width, width, ink.sum(axis=0)))
    ys = list(_intervals(image.height, height, ink.sum(axis=1)))
    return [(x0, y0, x1, y1) for y0, y1 in ys for x0, x1 in xs]


def ink_regions(image, min_extent=0):
    """从原图提出独立候选，首轮无返回也能发现文字和空缺网格。

    ``min_extent``（像素）以下的孤立墨点是扫描噪点，不是可读字符；它们永远
    无法被识别结果覆盖，只会让整块区域耗尽两轮补读后仍判未恢复。
    """
    import numpy as np

    try:
        import cv2
    except ImportError:
        # 本地纯 Python 验证不依赖 OCR 镜像的 OpenCV；按可见墨迹带提出补读区域。
        if min_extent:
            # 表格竖线只有表高，达不到整页 80%；须逐段删线，否则每一行都残留
            # 竖线墨迹，整张表成了一条墨迹带、一个补读区域。
            ink = _visible_text_ink(image, independent_lines=True)
        else:
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
            if len(xs) > 2 and y1 - y0 >= 3 and max(int(xs[-1]) - int(xs[0]) + 1, y1 - y0) >= min_extent:
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
        if (w >= 3 and h >= 4 and max(w, h) >= min_extent
                and cv2.countNonZero(text_ink[y : y + h, x : x + w]) >= 5):
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


def merge_regions(regions, gap=3, min_overlap=None):
    """合并相邻补读区域。``min_overlap`` 给定时只合并重叠面积达到较小框该比例的
    区域：相邻表格格的发现区域只靠外边距相接，接触即合并会把整张表连成一块。"""
    def joins(b, box):
        if min_overlap is None:
            return (min(b[2], box[2]) + gap >= max(b[0], box[0])
                    and min(b[3], box[3]) + gap >= max(b[1], box[1]))
        width = min(b[2], box[2]) - max(b[0], box[0])
        height = min(b[3], box[3]) - max(b[1], box[1])
        smaller = min((b[2] - b[0]) * (b[3] - b[1]), (box[2] - box[0]) * (box[3] - box[1]))
        return width > 0 and height > 0 and width * height >= min_overlap * smaller

    merged = []
    for region in regions:
        box = list(region)
        while True:
            match = next((b for b in merged if joins(b, box)), None)
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


def _thin_line_mask(ink, length, thickness=6):
    """沿行方向的细长线：返回（线上的细墨迹，线所在的行带范围）。

    只看「细」墨迹（所在列的竖向连续长度不超过 ``thickness``），文字竖笔不在
    其内；相邻三行合并后检查连续性，斜线换行处的短段与主线连成一体。3 像素
    以内的断口视为同一条线，线段内墨迹须占 85% 以上。行带范围用于识别横竖
    网线交点：交点在两个方向上都不「细」，单靠细墨迹两遍都删不掉。
    """
    import numpy as np

    height = ink.shape[0]
    up = np.zeros(ink.shape, dtype=np.int32)
    down = np.zeros(ink.shape, dtype=np.int32)
    for y in range(height):
        up[y] = (up[y - 1] + 1) * ink[y] if y else ink[y]
    for y in range(height - 1, -1, -1):
        down[y] = (down[y + 1] + 1) * ink[y] if y < height - 1 else ink[y]
    flat = ink & (up + down - 1 <= thickness)
    band = flat.copy()
    band[1:] |= flat[:-1]
    band[:-1] |= flat[1:]
    mask = np.zeros(ink.shape, dtype=bool)
    for y in range(height):
        positions = np.flatnonzero(band[y])
        if not len(positions):
            continue
        for segment in np.split(positions, np.flatnonzero(np.diff(positions) > 4) + 1):
            span = int(segment[-1]) - int(segment[0]) + 1
            if span >= length and len(segment) >= 0.85 * span:
                mask[max(0, y - 1):y + 2, segment[0]:segment[-1] + 1] = True
    return mask & flat, mask


def _visible_text_ink(image, independent_lines=False):
    """去掉网线后的文字墨迹。

    ``independent_lines`` 为真时横、竖网线都在原始墨迹上识别：先删横线会把竖线
    在交点处切成格高的短段而漏删；扫描件的网线又断续、略斜，按逐行连续长度判线
    会在换行处留下几十像素的残段，被当成没读出的文字。网格重建的重放仍用原口径，
    保证既有产物逐格复验结果不变。
    """
    import numpy as np

    ink = np.asarray(image.convert("L")) < 200
    if independent_lines:
        horizontal, horizontal_band = _thin_line_mask(ink, max(20, image.width // 25))
        vertical, vertical_band = (mask.T for mask in _thin_line_mask(ink.T.copy(), max(20, image.height // 25)))
        crossings = horizontal_band & vertical_band
        crossings[1:] |= crossings[:-1].copy()
        crossings[:-1] |= crossings[1:].copy()
        crossings[:, 1:] |= crossings[:, :-1].copy()
        crossings[:, :-1] |= crossings[:, 1:].copy()
        return ink & ~horizontal & ~vertical & ~crossings
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


def _significant_ink(mask, min_extent):
    """剩余墨迹里是否有不小于 ``min_extent`` 的连片；更小的只是噪点。"""
    import numpy as np

    if not min_extent:
        return bool(np.any(mask))
    rows = np.flatnonzero(mask.any(axis=1))
    if not len(rows):
        return False
    bands = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
    for band in bands:
        cols = np.flatnonzero(mask[band[0]:band[-1] + 1].any(axis=0))
        for span in np.split(cols, np.flatnonzero(np.diff(cols) > 1) + 1):
            if max(len(band), int(span[-1]) - int(span[0]) + 1) >= min_extent:
                return True
    return False


def trusted_block(block):
    """可以证明原图已读全的片段：分数达到 0.98，且数字字面合法。"""
    from src.engine.fiscal_lexeme import lex_numeric

    token = lex_numeric(block.text)
    # 文字标题中的年份不是一个“非法金额”候选。
    numeric_like = any(c.isdigit() for c in block.text) and not any(
        "\u4e00" <= c <= "\u9fff"
        for c in block.text.replace("万元", "").replace("亿元", "").replace("元", "")
    )
    return block.score >= 0.98 and not (numeric_like and token.kind == "invalid")


def uncovered_regions(regions, blocks, image=None, min_extent=0):
    """用原图墨迹覆盖检查完整性，不能因为片段覆盖区域中心就宣称读全。"""
    import numpy as np

    ink = _visible_text_ink(image, independent_lines=bool(min_extent)) if image is not None else None
    result = []
    for region in regions:
        x0, y0, x1, y1 = [int(round(v)) for v in region]
        matches = [
            b
            for b in blocks
            if b.bbox and b.bbox[0] < x1 and b.bbox[2] > x0 and b.bbox[1] < y1 and b.bbox[3] > y0
        ]
        if ink is not None and min_extent and not _significant_ink(
                ink[max(0, y0):max(0, y1), max(0, x0):max(0, x1)], min_extent):
            continue  # 去掉网线和噪点后区域内没有文字墨迹：空格不是漏读
        good = [block for block in matches if trusted_block(block)]
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
                left = max(x0, math.floor(block.bbox[0]) - 1)
                top = max(y0, math.floor(block.bbox[1]) - 1)
                right = min(x1, math.ceil(block.bbox[2]) + 2)
                bottom = min(y1, math.ceil(block.bbox[3]) + 2)
                if right > left and bottom > top:
                    remaining[
                        max(0, top - y0) : max(0, bottom - y0),
                        max(0, left - x0) : max(0, right - x0),
                    ] = False
            complete = not _significant_ink(remaining, min_extent)
        if not complete:
            result.append(region)
    return result


@dataclass
class TileRequest:
    image: bytes
    context: dict


def make_tile(image, box, *, parent_sha, file_hash, page, pixel_to_pdf, attempt=0, operations=(), inference_mode='structure', original_page_render_sha=None):
    box = tuple(int(round(v)) for v in box)
    if not (0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height):
        raise ValueError("tile crop must stay inside original image")
    crop = image.crop(box)
    data = png_bytes(crop)
    transform = Affine(e=box[0], f=box[1]).then(pixel_to_pdf)
    context = {
        'inference_mode': inference_mode,
        "source_sha256": file_hash,
        "physical_page": page,
        "parent_image_sha256": parent_sha,
        "original_page_render_sha256": original_page_render_sha or parent_sha,
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
    context["tile_id"] = digest(context)
    return TileRequest(data, context)


def save_checkpoint(path, payload):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sealed = {key: value for key, value in payload.items() if key != "checkpoint_sha256"}
    sealed["checkpoint_sha256"] = digest(sealed)
    descriptor,name=tempfile.mkstemp(dir=path.parent,prefix='.ocr-checkpoint-')
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'w')as stream:
            json.dump(sealed,stream,ensure_ascii=False,sort_keys=True,allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def save_input_png(path,data,expected_hash):
    """内容寻址输入原子创建；其他页不能读到正在写入的半个PNG。"""
    if hashlib.sha256(data).hexdigest()!=expected_hash:raise ValueError('OCR input hash differs')
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,name=tempfile.mkstemp(dir=path.parent,prefix='.ocr-input-')
    try:
        with os.fdopen(descriptor,'wb')as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        try:os.link(name,path)
        except FileExistsError:pass
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected_hash:
            raise ValueError('saved OCR input differs from request hash')
    finally:Path(name).unlink(missing_ok=True)


def seam_regions(context, response, image_size, padding=OVERLAP, covered=(), extend=None):
    """内部图块边缘的片段必须从原图扩区读全；高分截断也不能豁免。

    ``covered`` 是其他首轮图块里完整读出（离本块内部边缘 8 像素以上）的高分
    片段页面框。截断片段整段落在这样的完整片段里时，相邻块已经从原图读全
    同一段文字，不再扩区重读；其余墨迹是否读全仍由墨迹覆盖检查判定。
    """
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
            page_box = (left + x0, top + y0, left + x1, top + y1)
            if any(c[0] - 2 <= page_box[0] and c[1] - 2 <= page_box[1]
                   and page_box[2] <= c[2] + 2 and page_box[3] <= c[3] + 2 for c in covered):
                continue
            if extend is not None:
                regions.append(extend(page_box))
                continue
            # 横向补足整行上下文；纵向只需补足被截的字高，外扩一整块重叠会把
            # 上下多行并成整页大区域，补读时任一行略低于 0.98 就整块判失败。
            vertical = min(padding, max(16, int(y1 - y0)))
            regions.append(
                [
                    max(0, left + x0 - padding),
                    max(0, top + y0 - vertical),
                    min(page_width, left + x1 + padding),
                    min(page_height, top + y1 + vertical),
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


async def recognize_tiles(image,**kwargs):
    path=kwargs.get('checkpoint_path')
    if path is None:return await _recognize_tiles(image,**kwargs)
    # 同一原件同一配置的两个任务可共享断点，但不能同时修改同页的轮次和响应。
    import fcntl
    lock=Path(str(path)+'.lock');lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('a+b')as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return [],[],{'status':'pending','pending':['checkpoint_in_use'],'unresolved':[],
                'regions':{},'completed':{},'identity':{'tile_boxes':tile_boxes(image)},'elapsed_seconds':0}
        try:return await _recognize_tiles(image,**kwargs)
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


async def _recognize_tiles(
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
    discovery_regions=(),
    operations=(),
    original_parent_sha=None,
    dpi=None,
):
    """逐请求及轮次落盘。预算只暂停调度，已耗尽的两轮不会在续跑中重新开始。"""
    from src.services.ocr_client import ocr_page, OcrBlock

    parent = hashlib.sha256(png_bytes(image)).hexdigest()
    original_render = original_parent_sha or parent
    boxes = tile_boxes(image)
    # 由调度方给出渲染 DPI 时：0.8mm 以下孤立墨点按扫描噪点处理，补读以未被
    # 切断的片段判定；直接调用（无 DPI）保持逐像素、全部片段的原口径。
    scheduled = dpi is not None
    min_extent = round(dpi * 0.8 / 25.4) if scheduled else 0
    identity = {
        "parent": parent,
        "original_page_render": original_render,
        "file": file_hash,
        "page": page,
        "model": model,
        "pipeline": pipeline,
        "tiling": TILING_VERSION,
        "tile_boxes": boxes,
        "pixel_to_pdf": pixel_to_pdf.values(),
        "required_regions": list(required_regions),
        "discovery_regions": list(discovery_regions),
        "operations": list(operations),
    }
    # JSON 往返将 tuple 规范为 list，避免续跑身份因 Python 容器类型不同而丢失。
    identity = json.loads(json.dumps(identity))
    state = {
        "identity": identity,
        "completed": {},
        "gap_plan": None,
        "regions": {},
        "pending": [],
        "unresolved": [],
        "status": "pending",
    }
    if checkpoint_path and Path(checkpoint_path).is_file():
        saved=Path(checkpoint_path).read_bytes()
        try:loaded=json.loads(saved)
        except (ValueError,UnicodeDecodeError):loaded={}
        unsigned = {key: value for key, value in loaded.items() if key != "checkpoint_sha256"}
        if loaded.get("identity") == identity and loaded.get("checkpoint_sha256") == digest(
            unsigned
        ):
            state = unsigned
        else:
            archive=Path(checkpoint_path).parent/'checkpoint_conflicts'/(hashlib.sha256(saved).hexdigest()+'.json')
            save_input_png(archive,saved,hashlib.sha256(saved).hexdigest())
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
        if checkpoint_path:
            # 保留模型实际收到的PNG，重放不能只有输入摘要而没有对应字节。
            inputs = Path(checkpoint_path).parent / 'ocr_inputs'
            inputs.mkdir(parents=True, exist_ok=True)
            target = inputs / (request.context['input_sha256'] + '.png')
            save_input_png(target,request.image,request.context['input_sha256'])
        response = await ocr_page(
            client,
            image_png=request.image,
            page_number=page,
            timeout_seconds=RETRY_TIMEOUT if retry else TILE_TIMEOUT,
            region_only=request.context['inference_mode'] == 'text',
            tile_context=request.context,
        )
        if response.ok and response.provenance.get("pipeline_fingerprint", pipeline) != pipeline:
            response.error = "OCR response pipeline fingerprint mismatch"
        record = {"context": request.context, "result": response.to_dict()}
        record["digest"] = digest(record)
        if not response.ok:
            state.setdefault('failed_requests',[]).append(record)
        # 失败响应也记录：补读轮次不可因重启而无限增加。
        if retry or response.ok:
            state["completed"][tile_id] = record
        save_checkpoint(checkpoint_path, state)
        return response

    def page_block(block, context):
        if not block.bbox:
            return OcrBlock(block.text, block.score, None, page)
        box = Affine(*context["pixel_to_pdf"]).then(pixel_to_pdf.inverse()).box(block.bbox)
        return OcrBlock(block.text, block.score, box, page)

    mode = first_pass_mode()
    for box in boxes:
        request = make_tile(
            image,
            box,
            parent_sha=parent,
            file_hash=file_hash,
            page=page,
            pixel_to_pdf=pixel_to_pdf,
            operations=operations,
            inference_mode=mode,
            original_page_render_sha=original_render,
        )
        result = await execute(request)
        if result is None:
            continue
        results.append((request.context, result))
        if result.ok:
            page_blocks.extend(page_block(b, request.context) for b in result.blocks)
        else:
            pending.append(request.context["tile_id"])
    from src.services.ocr_qualification import complete_first_pass_cell
    complete = [
        (context["crop_box_px"][0] + b.bbox[0], context["crop_box_px"][1] + b.bbox[1],
         context["crop_box_px"][0] + b.bbox[2], context["crop_box_px"][1] + b.bbox[3])
        for context, result in results if result.ok
        for b in result.blocks
        if b.bbox and b.text.strip() and b.score >= 0.98
        and complete_first_pass_cell({"fragments": [{"bbox": b.bbox}]}, context)
    ]
    extend = None
    if scheduled:
        text_ink = _visible_text_ink(image, independent_lines=True)
        stop_gap = max(8, round(dpi * 1.5 / 25.4))

        def extend(box):
            """被切断的文字沿本行墨迹补全：遇到约 1.5mm 空白即停，最多一块重叠宽。"""
            x0, y0, x1, y1 = [int(round(v)) for v in box]
            columns = text_ink[max(0, y0):max(y0 + 1, y1)].any(axis=0)
            left, gap = x0, 0
            while left > 0 and x0 - left < OVERLAP and gap <= stop_gap:
                left -= 1
                gap = 0 if columns[left] else gap + 1
            right, gap = min(x1, image.width - 1), 0
            while right < image.width - 1 and right - x1 < OVERLAP and gap <= stop_gap:
                right += 1
                gap = 0 if columns[right] else gap + 1
            pad = RECOVERY_CONTEXT_PX
            return [max(0, left - pad), max(0, y0 - pad), min(image.width, right + pad + 1),
                    min(image.height, y1 + pad)]
    for context, result in results:
        if result.ok:
            edges.extend(seam_regions(context, result, image.size, covered=complete, extend=extend))
    # 缺少首轮图块时先补全首轮；不能把未调度区域当成已判定的 OCR 漏格。
    if not pending and state["gap_plan"] is None and scheduled:
        # 只对首轮读出但不可信（低于 0.98 或数字字面非法）的完整文字框补读，
        # 外扩 RECOVERY_CONTEXT_PX；被切断的碰边片段由接缝区域处理。原图上完全没有文字框
        # 的墨迹，同一模型重读几乎从不成功，直接记为未读区域交人工复核。
        low = []
        for context, result in results:
            if not result.ok:
                continue
            left, top = context["crop_box_px"][:2]
            for b in result.blocks:
                if (b.bbox and b.text.strip() and not trusted_block(b)
                        and complete_first_pass_cell({"fragments": [{"bbox": b.bbox}]}, context)):
                    page_box = (left + b.bbox[0], top + b.bbox[1], left + b.bbox[2], top + b.bbox[3])
                    # 相邻块已有覆盖同一位置的高分完整读数时，不必重读这个低分重影。
                    if any(c[0] - 2 <= page_box[0] and c[1] - 2 <= page_box[1]
                           and page_box[2] <= c[2] + 2 and page_box[3] <= c[3] + 2 for c in complete):
                        continue
                    pad = RECOVERY_CONTEXT_PX
                    low.append([max(0, page_box[0] - pad), max(0, page_box[1] - pad),
                                min(image.width, page_box[2] + pad), min(image.height, page_box[3] + pad)])
        plan = merge_regions(low + edges + list(required_regions), min_overlap=0.5)
        # 网格发现区域为了补读不切掉贴线数字，向格外扩了外边距；判定「有无未读
        # 文字」只看格线以内，格线交点、断口处的残墨不算文字。
        inset = max(4, round(dpi / 40)) + max(2, round(dpi / 100)) + 1
        cells = [[r[0] + inset, r[1] + inset, r[2] - inset, r[3] - inset]
                 for r in discovery_regions if r[2] - r[0] > 2 * inset and r[3] - r[1] > 2 * inset]
        unread = [
            region for region in uncovered_regions(
                ink_regions(image, min_extent) + cells, page_blocks,
                image=image, min_extent=min_extent)
            if not any(min(region[2], box[2]) > max(region[0], box[0])
                       and min(region[3], box[3]) > max(region[1], box[1]) for box in plan)
        ]
        state["unread_regions"] = merge_regions(unread, min_overlap=0.5)
        state["gap_plan"] = plan
        save_checkpoint(checkpoint_path, state)
    elif not pending and state["gap_plan"] is None:
        gaps = merge_regions(
            uncovered_regions(ink_regions(image)+list(discovery_regions), page_blocks, image=image)
            + edges
            + list(required_regions)
        )
        state["gap_plan"] = gaps
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
        # 第 2 轮把区域倒转 180° 重读，是缺少整页方向判定时的兜底；调度方已用
        # 识别探针定好方向时，倒读必然失败，只做第 1 轮补读。
        for attempt in ((1,) if scheduled else (1, 2)):
            if recovered:
                # 仍返回已恢复的补读来源，重建结果不能在续跑时丢失。
                for c in region.get("accepted_requests", []):
                    r = _restore_record(state["completed"].get(c["tile_id"]), c)
                    if r is not None:
                        rechecks.append((c, r))
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
            whole = []  # 未被本轮内部切线截断的片段
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
                    inference_mode='text',
                    original_page_render_sha=original_render,
                )
                response = await execute(request, retry=True)
                if response is None:
                    incomplete = True
                    break
                rechecks.append((request.context, response))
                round_requests.append(request.context)
                for b in response.blocks if response.ok else []:
                    piece = page_block(b, request.context)
                    pieces.append(piece)
                    width, height = request.context["size_px"]
                    if b.bbox and not ((subbox[0] > 0 and b.bbox[0] <= 8)
                                       or (subbox[1] > 0 and b.bbox[1] <= 8)
                                       or (subbox[2] < retry_crop.width and b.bbox[2] >= width - 8)
                                       or (subbox[3] < retry_crop.height and b.bbox[3] >= height - 8)):
                        whole.append(piece)
            if incomplete:
                break
            if scheduled:
                # 补读区域本身也要切块，切线处的残片置信度必然偏低；与首轮同口径，
                # 只以未被切断的片段判定覆盖与分数，残片由相邻块的完整片段替代。
                pieces = whole
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
                ink_regions(image.crop(box), min_extent), local_blocks, image=image.crop(box),
                min_extent=min_extent,
            )
            recovered = bool(pieces) and not remaining and all(b.score >= 0.98 for b in pieces)
            region["rounds"][str(attempt)] = {
                "requests": round_requests,
                "status": "recovered" if recovered else "unresolved",
                "uncovered": remaining,
            }
            region["status"] = (
                "recovered" if recovered else "exhausted" if attempt == (1 if scheduled else 2) else "pending"
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
    unresolved.extend("unread:" + digest(box) for box in state.get("unread_regions") or [])
    state.update(
        pending=pending,
        unresolved=unresolved,
        status="pending" if pending else "review_required" if unresolved else "completed",
        elapsed_seconds=time.monotonic() - started,
        gaps=state["gap_plan"] or [],
    )
    save_checkpoint(checkpoint_path, state)
    return results, rechecks, state
