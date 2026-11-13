"""从原始分辨率墨迹测量物理表格；不识字、不补数、不绘制缺失内容。"""

from collections import defaultdict
import hashlib
import json

import numpy as np

from src.schemas.parse_artifact import CellPresence, ParsedCell, ParsedTable
from src.services.ocr_tiles import png_bytes

IMAGE_GRID_VERSION = "observed-image-grid-v2-ink-runs"


def groups(values):
    result = []
    for value in values:
        value = int(value)
        if not result or value > result[-1][-1] + 1:
            result.append([])
        result[-1].append(value)
    return result


def stroke_mask(ink, length, *, vertical=False):
    """滑动窗仅测量已有长墨迹，允许小扫描断点，不对原图作修复。"""
    pixels = ink.T if vertical else ink
    if pixels.shape[1] < length:
        return np.zeros_like(ink, dtype=bool)
    counts = np.pad(np.cumsum(pixels, axis=1, dtype=np.int32), ((0, 0), (1, 0)))
    windows = (counts[:, length:] - counts[:, :-length]) >= length * 0.95
    coverage = np.zeros((pixels.shape[0], pixels.shape[1] + 1), dtype=np.int32)
    coverage[:, : windows.shape[1]] += windows
    coverage[:, length : length + windows.shape[1]] -= windows
    mask = np.cumsum(coverage[:, :-1], axis=1, dtype=np.int32) > 0
    return mask.T if vertical else mask


def line_segments(mask, *, vertical=False, minimum=24):
    values = mask.T if vertical else mask
    segments = []
    for band in groups(np.flatnonzero(values.any(axis=1))):
        coverage = values[band].any(axis=0)
        for run in groups(np.flatnonzero(coverage)):
            if len(run) < minimum:
                continue
            segments.append(
                {
                    "axis": "vertical" if vertical else "horizontal",
                    "coordinate": sum(band) / len(band),
                    "start": run[0],
                    "end": run[-1] + 1,
                    "thickness": len(band),
                }
            )
    return segments


def touches(horizontal, vertical, epsilon=3):
    return (
        horizontal["start"] - epsilon <= vertical["coordinate"] <= horizontal["end"] + epsilon
        and vertical["start"] - epsilon <= horizontal["coordinate"] <= vertical["end"] + epsilon
    )


def edge_present(segments, coordinate, start, end, epsilon=3):
    # 每个边界有实际长墨迹支持，不能仅因相邻两列看起来对齐就画出一条边。
    covered = []
    for line in segments:
        if abs(line["coordinate"] - coordinate) <= epsilon:
            a, b = max(start, line["start"]), min(end, line["end"])
            if b > a:
                covered.append((a, b))
    covered.sort()
    total = 0
    last = None
    for a, b in covered:
        total += max(0, b - max(a, last if last is not None else a))
        last = max(last if last is not None else b, b)
    return total >= max(1, end - start - 2 * epsilon) * 0.95


def detect_image_grids(image):
    """返回实测网格和闭合格。非矩形/开口区域保留为 incomplete_regions。"""
    gray = np.asarray(image.convert("L"))
    ink = gray < 180
    blank_ink = gray < 245
    height, width = ink.shape
    hmask = stroke_mask(ink, max(24, width // 40))
    vmask = stroke_mask(ink, max(24, height // 50), vertical=True)
    horizontal = line_segments(hmask)
    vertical = line_segments(vmask, vertical=True)
    lines = horizontal + vertical
    parents = list(range(len(lines)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for h, hs in enumerate(horizontal):
        for v, vs in enumerate(vertical, len(horizontal)):
            if touches(hs, vs):
                parents[root(v)] = root(h)
    components = defaultdict(list)
    for index, line in enumerate(lines):
        components[root(index)].append(line)
    grids = []
    for component in components.values():
        hs = [line for line in component if line["axis"] == "horizontal"]
        vs = [line for line in component if line["axis"] == "vertical"]
        xs = sorted({line["coordinate"] for line in vs})
        ys = sorted({line["coordinate"] for line in hs})
        if len(xs) < 2 or len(ys) < 2:
            continue
        cols, rows = len(xs) - 1, len(ys) - 1
        slots = list(range(rows * cols))

        def owner(index, slots=slots):
            while slots[index] != index:
                slots[index] = slots[slots[index]]
                index = slots[index]
            return index

        for row in range(rows):
            for col in range(cols):
                index = row * cols + col
                if col + 1 < cols and not edge_present(vs, xs[col + 1], ys[row], ys[row + 1]):
                    slots[owner(index + 1)] = owner(index)
                if row + 1 < rows and not edge_present(hs, ys[row + 1], xs[col], xs[col + 1]):
                    slots[owner(index + cols)] = owner(index)
        areas = defaultdict(list)
        for row in range(rows):
            for col in range(cols):
                areas[owner(row * cols + col)].append((row, col))
        cells = []
        incomplete = []
        for positions in areas.values():
            r0 = min(p[0] for p in positions)
            r1 = max(p[0] for p in positions) + 1
            c0 = min(p[1] for p in positions)
            c1 = max(p[1] for p in positions) + 1
            box = [xs[c0], ys[r0], xs[c1], ys[r1]]
            rectangular = len(positions) == (r1 - r0) * (c1 - c0)
            closed = (
                rectangular
                and edge_present(hs, ys[r0], xs[c0], xs[c1])
                and edge_present(hs, ys[r1], xs[c0], xs[c1])
                and edge_present(vs, xs[c0], ys[r0], ys[r1])
                and edge_present(vs, xs[c1], ys[r0], ys[r1])
            )
            if not closed:
                incomplete.append(
                    {"bbox_px": box, "reason": "physical_boundary_not_closed_or_not_rectangular"}
                )
                continue
            x0, y0, x1, y1 = [int(round(v)) for v in box]
            # 白格判定看原图内部墨迹；不能把格内破折号等长笔画当网格线抹掉。
            interior = blank_ink[
                min(height, y0 + 3) : max(y0 + 3, y1 - 3), min(width, x0 + 3) : max(x0 + 3, x1 - 3)
            ]
            ink_runs = []
            # 原图像素游程用于检查词框是否漏掉可见笔画，不表示逐字坐标。
            for dy, line in enumerate(ink[y0 + 3 : y1 - 3, x0 + 3 : x1 - 3]):
                changes = np.diff(np.r_[False, line, False].astype("int8"))
                for start, end in zip(
                    np.flatnonzero(changes == 1), np.flatnonzero(changes == -1), strict=True
                ):
                    ink_runs.append([y0 + 3 + dy, x0 + 3 + int(start), x0 + 3 + int(end)])
            cells.append(
                {
                    "row_idx": r0,
                    "col_idx": c0,
                    "row_span": r1 - r0,
                    "col_span": c1 - c0,
                    "bbox_px": box,
                    "interior_ink_pixels": int(interior.sum()),
                    "image_blank_verified": bool(interior.size and not interior.any()),
                    "observed_ink_runs_px": ink_runs,
                }
            )
        if not cells:
            continue
        grids.append(
            {
                "bbox_px": [xs[0], ys[0], xs[-1], ys[-1]],
                "x_cuts": xs,
                "y_cuts": ys,
                "row_count": rows,
                "column_count": cols,
                "cells": cells,
                "observed_lines": component,
                "incomplete_regions": incomplete,
            }
        )
    return sorted(grids, key=lambda grid: (grid["bbox_px"][1], grid["bbox_px"][0]))


def physical_grid_tables(image, *, file_hash, page, pixel_to_pdf, parent_image_sha256=None):
    """统一 ParsedTable 中保存原图物理格；格内字面留待来源片段联合重建。"""
    image_sha = hashlib.sha256(png_bytes(image)).hexdigest()
    tables = []
    for index, grid in enumerate(detect_image_grids(image)):
        identity = {
            "version": IMAGE_GRID_VERSION,
            "file_hash": file_hash,
            "page": page,
            "image_sha256": image_sha,
            "bbox_px": grid["bbox_px"],
        }
        uid = (
            "image-grid-"
            + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        )
        rows = [[None] * grid["column_count"] for _ in range(grid["row_count"])]
        cells = []
        for data in grid["cells"]:
            row, col = data["row_idx"], data["col_idx"]
            rows[row][col] = ""
            x0, y0, x1, y1 = data["bbox_px"]
            polygon = [
                pixel_to_pdf.point(x, y) for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            ]
            cells.append(
                ParsedCell(
                    row,
                    col,
                    "",
                    "",
                    None,
                    page,
                    tuple(pixel_to_pdf.box(data["bbox_px"])),
                    False,
                    0.0,
                    None,
                    "image_grid",
                    row_span=data["row_span"],
                    col_span=data["col_span"],
                    presence=CellPresence.BLANK
                    if data["image_blank_verified"]
                    else CellPresence.UNRECOGNIZED,
                    ocr_provenance={
                        "physical_grid": {
                            **identity,
                            "parent_image_sha256": parent_image_sha256 or image_sha,
                            "pixel_to_pdf": pixel_to_pdf.values(),
                            "image_size_px": list(image.size),
                            "polygon_pdf": polygon,
                            "cell": data,
                            "incomplete_regions": grid["incomplete_regions"],
                        }
                    },
                )
            )
        tables.append(
            ParsedTable(
                "UNKNOWN_IMAGE_GRID",
                "",
                page,
                index,
                tuple(pixel_to_pdf.box(grid["bbox_px"])),
                0,
                0.0,
                "image_grid",
                None,
                rows,
                cells,
                fragment_uid=uid,
            )
        )
    return tables
