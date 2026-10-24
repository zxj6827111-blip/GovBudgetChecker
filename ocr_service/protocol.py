"""Conservative mapping of structure HTML, cell geometry and real OCR tokens.

No score is synthesised. Spatial/structural ambiguity is reported explicitly.
This adapter must be validated against the pinned runtime before deployment.
"""

from __future__ import annotations

import math
from html.parser import HTMLParser
from typing import Any

PROTOCOL_VERSION = "gbc.ocr.cells.v3"
HISTORICAL_PROTOCOLS = frozenset({"gbc.ocr.cells.v2"})


def valid_bbox(raw: Any) -> list[float] | None:
    try:
        if len(raw) == 4 and all(isinstance(point, (list, tuple)) for point in raw):
            xs, ys = zip(*raw, strict=True)
            box = [min(xs), min(ys), max(xs), max(ys)]
        else:
            box = list(raw)
        values = [float(x) for x in box]
        if len(values) != 4 or not all(math.isfinite(x) for x in values):
            return None
        return values if values[2] > values[0] and values[3] > values[1] else None
    except (TypeError, ValueError):
        return None


def valid_score(raw: Any) -> float | None:
    try:
        value = float(raw)
        return value if math.isfinite(value) and 0 <= value <= 1 else None
    except (TypeError, ValueError):
        return None


class CellHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells = []
        self.row = -1
        self.col = 0
        self.occupied = set()
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row += 1
            self.col = 0
        elif tag in {"td", "th"}:
            while (self.row, self.col) in self.occupied:
                self.col += 1
            attributes = dict(attrs)
            row_span = int(attributes.get("rowspan", "1"))
            col_span = int(attributes.get("colspan", "1"))
            if self.row < 0 or not 1 <= row_span <= 100 or not 1 <= col_span <= 100:
                raise ValueError("invalid HTML cell structure")
            self.current = dict(
                row_idx=self.row,
                col_idx=self.col,
                row_span=row_span,
                col_span=col_span,
                raw_text="",
            )
            for row in range(self.row, self.row + row_span):
                for col in range(self.col, self.col + col_span):
                    if (row, col) in self.occupied:
                        raise ValueError("overlapping HTML cells")
                    self.occupied.add((row, col))

    def handle_data(self, text):
        if self.current is not None:
            self.current["raw_text"] += text

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.current is not None:
            self.cells.append(self.current)
            self.col += self.current["col_span"]
            self.current = None


def _axes(values: list[float]) -> list[float]:
    groups = []
    for value in sorted(values):
        if groups and abs(value - groups[-1][-1]) <= 3:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def _overlap_fraction(token, cell):
    area = (token[2] - token[0]) * (token[3] - token[1])
    intersect = max(0, min(token[2], cell[2]) - max(token[0], cell[0])) * max(
        0, min(token[3], cell[3]) - max(token[1], cell[1])
    )
    return intersect / area


def map_table_cells(table: dict[str, Any]) -> dict[str, Any]:
    parser = CellHTMLParser()
    try:
        parser.feed(str(table.get("pred_html") or ""))
        boxes = [valid_bbox(raw) for raw in table.get("cell_box_list", [])]
        if not parser.cells or len(boxes) != len(parser.cells) or any(box is None for box in boxes):
            raise ValueError("missing_or_mismatched_cell_geometry")
        xs = _axes([box[i] for box in boxes for i in (0, 2)])
        ys = _axes([box[i] for box in boxes for i in (1, 3)])
        geometries = {}
        for box in boxes:
            x0, x1 = [min(range(len(xs)), key=lambda i: abs(xs[i] - box[j])) for j in (0, 2)]
            y0, y1 = [min(range(len(ys)), key=lambda i: abs(ys[i] - box[j])) for j in (1, 3)]
            key = (y0, x0, y1 - y0, x1 - x0)
            if key in geometries or y1 <= y0 or x1 <= x0:
                raise ValueError("ambiguous_cell_geometry")
            geometries[key] = box
        cells = []
        for cell in parser.cells:
            key = tuple(cell[k] for k in ("row_idx", "col_idx", "row_span", "col_span"))
            if key not in geometries:
                raise ValueError("HTML_geometry_structure_conflict")
            cells.append({**cell, "render_bbox": geometries[key], "fragments": []})
        ocr = table.get("table_ocr_pred") or {}
        texts, scores = ocr.get("rec_texts", []), ocr.get("rec_scores", [])
        token_boxes = ocr.get("rec_polys", []) or ocr.get("rec_boxes", [])
        if not len(texts) == len(scores) == len(token_boxes):
            raise ValueError("OCR_array_length_mismatch")
        for text, score, raw_box in zip(texts, scores, token_boxes, strict=True):
            box, score = valid_bbox(raw_box), valid_score(score)
            if box is None or score is None:
                raise ValueError("nonfinite_or_invalid_OCR_token")
            owners = [cell for cell in cells if _overlap_fraction(box, cell["render_bbox"]) >= 0.8]
            if len(owners) != 1:
                raise ValueError("ambiguous_token_cell_mapping")
            owners[0]["fragments"].append(dict(text=str(text), score=score, render_bbox=box))
        for cell in cells:
            cell["fragments"].sort(
                key=lambda item: (item["render_bbox"][1], item["render_bbox"][0])
            )
            actual = "".join("".join(item["text"].split()) for item in cell["fragments"])
            expected = "".join(cell["raw_text"].split())
            if expected != actual:
                raise ValueError("HTML_OCR_text_conflict")
            cell["score"] = min((item["score"] for item in cell["fragments"]), default=None)
        nrows, ncols = (
            max(y for y, x in parser.occupied) + 1,
            max(x for y, x in parser.occupied) + 1,
        )
        rows = [["" for _ in range(ncols)] for _ in range(nrows)]
        for cell in cells:
            rows[cell["row_idx"]][cell["col_idx"]] = cell["raw_text"]
        return dict(rows=rows, cells=cells, mapping_status="mapped", mapping_reasons=[])
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return dict(cells=[], mapping_status="ambiguous", mapping_reasons=[str(exc)])


def validate_tile_context(context, data, size):
    """校验请求内容绑定与可逆变换；旧协议不能作为新的推理结果。"""
    import hashlib
    import json

    if not isinstance(context, dict):
        raise ValueError("v3 tile context required")
    required = {
        "tile_id",
        "parent_image_sha256",
        "source_sha256",
        "physical_page",
        "source_region",
        "crop_box_px",
        "size_px",
        "input_sha256",
        "pixel_to_pdf",
        "pdf_to_pixel",
        "preprocessing",
        'inference_mode',
    }
    if not required.issubset(context):
        raise ValueError("incomplete v3 tile context")
    if context['inference_mode'] not in {'structure', 'text'}:
        raise ValueError('invalid OCR inference mode')
    if list(size) != context["size_px"] or not (0 < size[0] <= 2048 and 0 < size[1] <= 1536):
        raise ValueError("oversized tile must be split, never downscaled")
    for key in ("tile_id", "parent_image_sha256", "source_sha256", "input_sha256"):
        value = context[key]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ValueError("invalid SHA-256 identity")
    if hashlib.sha256(data).hexdigest() != context["input_sha256"]:
        raise ValueError("tile image hash mismatch")
    unsigned = {key: value for key, value in context.items() if key != "tile_id"}
    actual = hashlib.sha256(
        json.dumps(
            unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    if actual != context["tile_id"]:
        raise ValueError("tile identity mismatch")
    if (
        not isinstance(context["physical_page"], int)
        or isinstance(context["physical_page"], bool)
        or context["physical_page"] < 1
    ):
        raise ValueError("invalid physical page")
    for key in ("pixel_to_pdf", "pdf_to_pixel"):
        matrix = context[key]
        if len(matrix) != 6 or not all(math.isfinite(v) for v in matrix):
            raise ValueError("invalid affine transform")
    a, b, c, d, e, f = context["pixel_to_pdf"]
    g, h, i, j, k, inverse_f = context["pdf_to_pixel"]
    composed = [
        g * a + i * b,
        h * a + j * b,
        g * c + i * d,
        h * c + j * d,
        g * e + i * f + k,
        h * e + j * f + inverse_f,
    ]
    if any(
        abs(actual - expected) > 1e-6
        for actual, expected in zip(composed, [1, 0, 0, 1, 0, 0], strict=True)
    ):
        raise ValueError("affine inverse mismatch")
    if valid_bbox(context["source_region"]) is None or valid_bbox(context["crop_box_px"]) is None:
        raise ValueError("invalid tile region")
    a, b, c, d, e, f = context["pixel_to_pdf"]
    points = [(a * x + c * y + e, b * x + d * y + f) for x in (0, size[0]) for y in (0, size[1])]
    region = [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]
    if any(
        abs(left - right) > 1e-6
        for left, right in zip(region, context["source_region"], strict=True)
    ):
        raise ValueError("tile source region does not match affine transform")
    crop = context["crop_box_px"]
    if [crop[2] - crop[0], crop[3] - crop[1]] != list(size):
        raise ValueError("tile crop size mismatch")
    return context
