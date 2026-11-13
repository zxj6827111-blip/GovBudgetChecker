"""发现原页读取缺口、安排补识别，并约束受影响的金额事实。

这是原件线索与解析结果的覆盖检查，不是独立识别准确率证明。
不根据勾稽关系补金额，不用 OCR 覆盖已有原生数据。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from typing import Any

from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text

READING_VERSION = "table-reading-v1"


def number(text: Any) -> Decimal | None:
    value = normalize_text(str(text or ""))
    if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", value):
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def valid_box(value):
    try:
        box = [float(v) for v in value]
        return (
            box
            if len(box) == 4
            and all(math.isfinite(v) for v in box)
            and box[0] < box[2]
            and box[1] < box[3]
            else None
        )
    except (TypeError, ValueError):
        return None


def intersects(a, b):
    return bool(a and b and min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]))


def contains(box, token):
    x, y = (token[0] + token[2]) / 2, (token[1] + token[3]) / 2
    return box[0] - 1 <= x <= box[2] + 1 and box[1] - 1 <= y <= box[3] + 1


def collect_page_probe(page, page_number: int, text: str, words: list) -> dict:
    """在同一受资源限制的 PDF 读取进程内采集，不重开原件。"""
    hints = []
    for line in text.splitlines():
        line = normalize_text(line)
        if len(line) > 90 or any(w in line for w in ("说明", "目录", "反映", "无此")):
            continue
        candidates = [
            (len(normalize_text(alias)), code)
            for code, rule in NINE_TABLE_RULES.items()
            for alias in rule.aliases
            if line.endswith(normalize_text(alias))
        ]
        if candidates:
            hints.append(max(candidates)[1])
    # 目录中的表名不构成真实表格区域。
    if "目录" in normalize_text(text[:100]) or len(set(hints)) >= 3:
        hints = []
    amounts = []
    for word in words:
        raw = str(word.get("text") or "")
        # 编码、序号与百分比不是金额线索；整数金额由原格和事实绑定回执检查。
        if "." not in raw or number(raw) is None:
            continue
        box = valid_box([word.get(k) for k in ("x0", "top", "x1", "bottom")])
        if box:
            amounts.append({"raw_text": raw, "bbox": box})
    area = max(float(page.width) * float(page.height), 1)
    images = []
    for image in getattr(page, "images", []) or []:
        box = valid_box([image.get(k) for k in ("x0", "top", "x1", "bottom")])
        if box and (box[2] - box[0]) * (box[3] - box[1]) / area >= 0.08:
            images.append(box)
    blank = not text.strip() and not any(
        getattr(page, name, []) for name in ("chars", "images", "lines", "rects", "curves")
    )
    return {
        "page": page_number,
        "width": float(page.width),
        "height": float(page.height),
        "blank_verified": blank,
        "table_hints": sorted(set(hints)),
        "amounts": amounts,
        "image_regions": images,
    }


def selected_tables(artifact):
    return [
        t
        for t in getattr(artifact, "table_fragments", [])
        if t.extraction_method != "page_text_fallback"
    ]


def qualified_cell(table, cell):
    return (
        table.extraction_method != "ocr"
        or cell.ocr_provenance.get("qualification", {}).get("status") == "qualified"
    )


def usable_table_region(table):
    if table.extraction_method != "ocr":
        return True
    nonempty = [cell for cell in table.cells if cell.raw_text.strip()]
    # 图片没有原生金额可逐个核对，不能仅凭一个合格表头就宣告整块图片已读完。
    return (
        bool(nonempty)
        and any(not cell.is_header for cell in nonempty)
        and all(qualified_cell(table, cell) for cell in nonempty)
    )


def assess_table_reading(artifact) -> dict:
    stored = getattr(artifact, "reading_assessment", {}) or {}
    if stored.get("version") != READING_VERSION:
        return {
            "status": "not_assessed",
            "version": READING_VERSION,
            "gaps": [],
            "original_pdf_full_coverage_verified": False,
        }
    by_page = defaultdict(list)
    for table in selected_tables(artifact):
        for cell in table.cells:
            if cell.bbox:
                by_page[cell.page_number].append((table, cell))
    gaps, page_records = [], []
    for probe in stored.get("pages", []):
        page = probe["page"]
        cells = by_page[page]
        hints = probe["table_hints"]
        page_gaps = []
        usable = [(t, c) for t, c in cells if qualified_cell(t, c)]
        for table, cell in usable:
            unresolved_tails = [p for p in cell.native_text_sources
                                if p.get("recovery_kind") == "unresolved_cross_grid_decimal_tail"]
            if unresolved_tails:
                page_gaps.append({"reason": "native_decimal_tail_unresolved", "bbox": cell.bbox,
                                  "table_code": table.table_code, "source_proof": unresolved_tails})
        for code in hints:
            if not any(t.table_code == code and usable_table_region(t) for t, c in usable):
                page_gaps.append(
                    {
                        "reason": "table_title_without_usable_cells",
                        "table_code": code,
                        "bbox": [0, 0, probe["width"], probe["height"]],
                    }
                )
        # 普通说明正文里的金额不参与表格漏格判断。
        fiscal = bool(hints) or any(t.table_code in NINE_TABLE_RULES for t, c in cells)
        if fiscal:
            values = defaultdict(list)
            for table, cell in usable:
                value = number(cell.raw_text)
                if value is not None:
                    values[value].append(cell)
            for token in probe["amounts"]:
                if any(contains(c.bbox, token["bbox"]) for c in values[number(token["raw_text"])]):
                    continue
                # 原格内的小数尾数换行已由原字符连续性和右对齐位置确认。
                # 原始词会只含金额前半段，仍须逐词保留覆盖证明，不能误当成另一笔金额。
                if any(
                    t.extraction_method != "ocr"
                    and number(c.raw_text) is not None
                    and c.native_text_sources
                    and normalize_text("".join(s["text"] for s in c.native_text_sources))
                    == normalize_text(c.raw_text)
                    and all(s.get("recovery_kind") in {"decimal_wrap", "cross_grid_decimal_tail"}
                            for s in c.native_text_sources)
                    and any(
                        contains(s["bbox"], token["bbox"]) and token["raw_text"] in s["text"]
                        for s in c.native_text_sources
                    )
                    for t, c in usable
                ):
                    continue
                # 无表题的续页只检查原表的区域，避免把脚注当作丢格。
                if not hints and not any(intersects(t.bbox, token["bbox"]) for t, c in cells):
                    continue
                page_gaps.append({"reason": "native_amount_not_extracted", **token})
        for box in probe["image_regions"]:
            # 首封面的徽标/背景不是表格；有明确表题时仍须检查。
            if page == 1 and not hints:
                continue
            area = (box[2] - box[0]) * (box[3] - box[1])
            covering = []
            for table, cell in usable:
                tb = valid_box(table.bbox)
                if tb and usable_table_region(table) and intersects(tb, box):
                    overlap = (min(tb[2], box[2]) - max(tb[0], box[0])) * (
                        min(tb[3], box[3]) - max(tb[1], box[1])
                    )
                    if overlap / area >= 0.8:
                        covering.append(table)
            if not covering:
                page_gaps.append({"reason": "image_region_not_structured", "bbox": box})
        for gap in page_gaps:
            affected = {
                t.fragment_uid
                for t, c in cells
                if intersects(t.bbox, gap["bbox"])
                and (not gap.get("table_code") or t.table_code == gap["table_code"])
            }
            if not affected and gap["reason"] == "native_amount_not_extracted":
                # 开口续行可能位于闭合矩形外，但仍影响该页唯一的一张财政表。
                # 不能因为缺格没有与旧矩形相交，就让其余金额继续伪装为完整表。
                fiscal_tables = {
                    t.fragment_uid for t, c in cells if t.table_code in NINE_TABLE_RULES
                }
                if len(fiscal_tables) == 1:
                    affected = fiscal_tables
            identity = json.dumps([page, gap], sort_keys=True, ensure_ascii=False)
            uid = hashlib.sha1(identity.encode()).hexdigest()[:12]
            gaps.append(
                {
                    "id": f"reading:p{page}:{uid}",
                    "page": page,
                    "affected_fragment_uids": sorted(affected),
                    **gap,
                }
            )
        page_records.append(
            {"page": page, "blank_verified": probe["blank_verified"], "gap_count": len(page_gaps)}
        )
    return {
        "version": READING_VERSION,
        "status": "partial" if gaps else "complete_for_detected_regions",
        "pages": page_records,
        "gaps": gaps,
        "recovery": stored.get("recovery", {}),
        "original_pdf_full_coverage_verified": False,
    }


def enrich_page_assessment(assessment: dict, artifact) -> dict:
    result = dict(assessment)
    reading = assess_table_reading(artifact)
    result["table_reading"] = reading
    result["table_recovery_pages"] = sorted({g["page"] for g in reading["gaps"]})
    blank = {p["page"] for p in reading.get("pages", []) if p["blank_verified"]}
    if blank:
        result["blank_pages"] = sorted(blank)
        for field in ("low_text_pages", "scanned_pages"):
            result[field] = [p for p in result.get(field, []) if p not in blank]
        result["low_text_page_count"] = len(result.get("low_text_pages", []))
        result["scanned_page_count"] = len(result.get("scanned_pages", []))
        count = max(0, int(result.get("page_count") or 0) - len(blank))
        result["content_page_count"] = count
        result["text_page_count"] = max(0, count - result["low_text_page_count"])
        result["page_coverage"] = result["text_page_count"] / count if count else 0
    return result


def recovery_pages(assessment: dict, artifact=None) -> list[int]:
    maximum = getattr(artifact, "page_count", None) or assessment.get("page_count")
    blank = set(assessment.get("blank_pages") or [])
    pages = set()
    for field in ("low_text_pages", "scanned_pages", "table_recovery_pages"):
        for page in assessment.get(field) or []:
            if (
                type(page) is int
                and page > 0
                and (not maximum or page <= maximum)
                and page not in blank
            ):
                pages.add(page)
    return sorted(pages)


def constrain_reading_facts(artifact, facts):
    reading = assess_table_reading(artifact)
    blocked = {uid for gap in reading["gaps"] for uid in gap["affected_fragment_uids"]}
    blocked.update(
        instance.instance_uid
        for instance in getattr(artifact, "logical_tables", [])
        if blocked.intersection(instance.fragment_uids)
    )
    constrained = []
    for fact in facts:
        if fact.instance_uid in blocked:
            fact = replace(
                fact,
                recognition_status="low_confidence",
                extra_dims={
                    **fact.extra_dims,
                    "table_reading_status": "incomplete",
                    "table_reading_gaps": [g["id"] for g in reading["gaps"]],
                },
            )
        constrained.append(fact)
    return constrained


def native_region_conflicts(artifact, candidate) -> bool:
    """混合页仅在 OCR 区域与原生文字、原生表均不相交时允许独立晋升。"""
    states = [p for p in artifact.page_states if p.page_number == candidate.page_number]
    if not any(p.text_chars for p in states):
        return False
    box = valid_box(candidate.page_bbox)
    blocks = [
        b
        for b in artifact.text_blocks
        if b.page_number == candidate.page_number
        and b.extraction_method == "native_text"
        and b.text.strip()
    ]
    if not box or not blocks or any(not b.bbox or intersects(box, b.bbox) for b in blocks):
        return True
    return any(
        t.extraction_method != "ocr"
        and t.extraction_method != "page_text_fallback"
        and any(c.page_number == candidate.page_number for c in t.cells)
        and intersects(box, t.bbox)
        for t in selected_tables(artifact)
    )
