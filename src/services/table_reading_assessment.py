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
import os
import re
from typing import Any

from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text

READING_VERSION = "table-reading-v1"

#: 回退开关：设置后恢复"任何缺口都作废整表"的旧口径，仅用于对照实验。
ZERO_GAP_PASS_ENABLED = os.environ.get(
    "INTAKE_DISABLE_ZERO_GAP_PASS", ""
).strip().lower() not in {"1", "true", "yes", "on"}

#: 回退开关：关闭目录续页识别（恢复只认「目录」二字与 ≥3 条表名提示）。
TOC_HINT_GUARD_ENABLED = os.environ.get(
    "INTAKE_DISABLE_TOC_HINT_GUARD", ""
).strip().lower() not in {"1", "true", "yes", "on"}

#: 章节枚举行：`一、收入决算表` / `第三部分 …说明`。
_SECTION_ENUM_RE = re.compile(r"^(第[一二三四五六七八九十]+部分|[一二三四五六七八九十]{1,3}[、.．])")


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


def _cross_border_word(token: dict, cells: list) -> bool:
    """原页取词把紧贴竖线两侧的字符拼成一个词，词内各段都已是原格文字。

    金额右对齐顶到格线、右邻格的编码左对齐贴着格线时，取词得到
    "18,140.77302"（"18,140.77" + "302"），它不是一笔漏读的金额。
    只在该词能按从左到右的顺序被相交的两个以上原格逐段完整拼出、
    且其中有一段本身是已读出的金额时放行；拼不出就仍按缺口报。
    """
    box = token["bbox"]
    remaining = normalize_text(re.sub(r"\s+", "", token["raw_text"]))
    parts = sorted((c for t, c in cells if c.bbox and intersects(c.bbox, box) and c.raw_text.strip()),
                   key=lambda c: c.bbox[0])
    if len(parts) < 2:
        return False
    used, amount = 0, False
    for cell in parts:
        text = normalize_text(re.sub(r"\s+", "", cell.raw_text))
        if remaining.startswith(text):
            remaining = remaining[len(text):]
        elif remaining and text.startswith(remaining):
            remaining = ""  # 末段只取到右邻格开头的几个字
        else:
            return False
        used += 1
        amount = amount or number(text) is not None and "." in text
        if not remaining:
            break
    return not remaining and used >= 2 and amount


def _glued_neighbour_glyph(token: dict, cells: list) -> bool:
    """取词把同列上一行（或下一行）金额的末位字形粘到本行金额后面。

    两行基线落在取词的同行容差里时出现：本格 "19.96"、上一格 "253.16"，取词得到
    "19.966"。只在词 = 某个相交原格的完整金额 + 1～2 个字符，且这几个字符正是同列
    另一相交原格文字的结尾时放行；两格的金额都已由网格完整读出。
    """
    box = token["bbox"]
    text = normalize_text(re.sub(r"\s+", "", token["raw_text"]))
    hits = [c for t, c in cells if c.bbox and intersects(c.bbox, box) and c.raw_text.strip()]
    for cell in hits:
        own = normalize_text(re.sub(r"\s+", "", cell.raw_text))
        tail = text[len(own):]
        if number(own) is None or not text.startswith(own) or not 1 <= len(tail) <= 2:
            continue
        if any(other is not cell and abs(other.bbox[0] - cell.bbox[0]) <= 1
               and abs(other.bbox[2] - cell.bbox[2]) <= 1
               and normalize_text(re.sub(r"\s+", "", other.raw_text)).endswith(tail)
               for other in hits):
            return True
    return False


def _is_enumeration_page(text: str, amounts: list) -> bool:
    """目录/章节枚举页：整页只有「序号+标题」行，且没有任何金额。

    上海 2023 的目录跨页续排（首页有「目录」，次页只剩「八、九、…」），
    表名出现在这种页面上只是目录条目，不构成真实表格区域。若照常按表名
    提示判缺口，每份材料都会凭空多出两条无归属缺口。
    """
    if amounts:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    enum = [line for line in lines if _SECTION_ENUM_RE.match(normalize_text(line))]
    return len(enum) >= 3 and len(enum) >= 0.6 * len(lines)


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
    amounts = []
    for word in words:
        raw = str(word.get("text") or "")
        # 编码、序号与百分比不是金额线索；整数金额由原格和事实绑定回执检查。
        if "." not in raw or number(raw) is None:
            continue
        box = valid_box([word.get(k) for k in ("x0", "top", "x1", "bottom")])
        if box:
            amounts.append({"raw_text": raw, "bbox": box})
    # 目录中的表名不构成真实表格区域。
    if (
        "目录" in normalize_text(text[:100])
        or len(set(hints)) >= 3
        or (TOC_HINT_GUARD_ENABLED and _is_enumeration_page(text, amounts))
    ):
        hints = []
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
        # 字形转曲的表格没有文字层，只剩大量曲线；它是 OCR 能补的少数文字页。
        "vector_curves": len(getattr(page, "curves", []) or []),
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
        or cell.extraction_method == "manual"
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


_OCR_AMOUNT = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d{2}")


def _ocr_page_recovery(artifact, page):
    """整页切块识别跑完后留下的未读区域；没跑过整页识别时返回 None。"""
    records = [t["page_recovery"] for t in (getattr(artifact, "coordinate_transforms", {}) or {}).values()
               if isinstance(t, dict) and t.get("physical_page") == page and isinstance(t.get("page_recovery"), dict)]
    if not records:
        return None
    return [box for record in records for box in (record.get("unresolved_pdf_regions") or [])]


def _scan_page_structured(artifact, page, box, usable):
    """整页扫描图里，表格外还有表题、单位行和页边，表格不可能覆盖整图的 80%。

    改为：整页识别已跑完、页内识别表格全部合格、未读区域都不落在表格内、
    表格外也没有像金额的识别文字（防止无网线的第二张表被漏掉），才算这张图已结构化。
    """
    unresolved = _ocr_page_recovery(artifact, page)
    if unresolved is None:
        return False
    tables = {id(t): t for t, _ in usable if t.extraction_method == "ocr" and t.page_number == page}
    boxes = [valid_box(t.bbox) for t in tables.values()]
    if not tables or not all(usable_table_region(t) for t in tables.values()) or not all(boxes):
        return False
    if not any(intersects(b, box) for b in boxes):
        return False
    if any(intersects(region, b) for region in unresolved for b in boxes):
        return False
    outside = [block for block in getattr(artifact, "text_blocks", []) or []
               if block.page_number == page and block.extraction_method == "ocr" and block.bbox
               and _OCR_AMOUNT.search(block.text or "") and not any(contains(b, block.bbox) for b in boxes)]
    return not outside


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
                if _cross_border_word(token, usable) or _glued_neighbour_glyph(token, usable):
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
            if not covering and not _scan_page_structured(artifact, page, box, usable):
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
    """只为没有可用原生文字层的页安排 OCR。

    有文字层的页即使字少（封面、章节页）或存在原生读取缺口，OCR 也读不出
    比文字层更可靠的内容，只会白白占用识别预算（实测一张有文字层的封面
    被切成 280 块排队）。原生缺口仍由读取门禁报出待复核。有页面探针证据时
    只收这几类页：
    - 无任何字符的扫描页；
    - 字少且页内有大幅图像的页（文字层只是页眉，正文是图片）；
    - 读取缺口为「图像区域未结构化」的页（文字页中嵌入的表格图片）；
    - 有表题却无可用格、且页内是大量曲线的页（表格字形被转曲）。
    缺少探针或缺口原因时保持原口径全收，不在没有证据时静默跳过。
    """
    maximum = getattr(artifact, "page_count", None) or assessment.get("page_count")
    blank = set(assessment.get("blank_pages") or [])
    probes = {
        probe.get("page"): probe
        for probe in ((getattr(artifact, "reading_assessment", None) or {}).get("pages") or [])
        if isinstance(probe, dict)
    }
    reading = assessment.get("table_reading")
    gap_reasons = defaultdict(set)
    for gap in (reading or {}).get("gaps") or []:
        gap_reasons[gap.get("page")].add(gap.get("reason"))

    def needs_ocr(field, page):
        if field == "scanned_pages":
            return True
        if field == "low_text_pages":
            probe = probes.get(page)
            return probe is None or bool(probe.get("image_regions"))
        if reading is None:
            return True
        reasons = gap_reasons[page]
        return "image_region_not_structured" in reasons or (
            "table_title_without_usable_cells" in reasons
            and (probes.get(page) or {}).get("vector_curves", 0) >= 200
        )

    pages = set()
    for field in ("low_text_pages", "scanned_pages", "table_recovery_pages"):
        for page in assessment.get(field) or []:
            if (
                type(page) is int
                and page > 0
                and (not maximum or page <= maximum)
                and page not in blank
                and needs_ocr(field, page)
            ):
                pages.add(page)
    return sorted(pages)


def _gap_blocks_facts(gap: dict) -> bool:
    """判断一个读取缺口是否应当作废整表事实。

    零值缺口改变不了任何加减关系：原页那个 0.00 抽到与否，参与的所有
    勾稽结果都相同。这类缺口应记录为"观察到缺失"，而不是让整张表的事实
    退出检查——实测中一个 0.00 的边界丢格就卡住了 6 条表级检查，
    代价与影响严重不对称。

    非零缺口、无字面值的结构性缺口（表题无可信单元格等）仍按原口径阻塞，
    fail-closed 语义不变。
    """
    if not ZERO_GAP_PASS_ENABLED:
        return True
    raw = str(gap.get("raw_text") or "").strip()
    if not raw:
        return True
    value = number(raw)
    return not (value is not None and value == 0)


def constrain_reading_facts(artifact, facts):
    reading = assess_table_reading(artifact)
    blocking_gaps = [gap for gap in reading["gaps"] if _gap_blocks_facts(gap)]
    zero_gaps = [gap for gap in reading["gaps"] if not _gap_blocks_facts(gap)]
    blocked = {uid for gap in blocking_gaps for uid in gap["affected_fragment_uids"]}
    blocked.update(
        instance.instance_uid
        for instance in getattr(artifact, "logical_tables", [])
        if blocked.intersection(instance.fragment_uids)
    )
    # 零值缺口的表仍需在事实里留痕，便于复核与统计，但不影响可用性。
    zero_affected = {uid for gap in zero_gaps for uid in gap["affected_fragment_uids"]}
    zero_affected.update(
        instance.instance_uid
        for instance in getattr(artifact, "logical_tables", [])
        if zero_affected.intersection(instance.fragment_uids)
    )
    zero_ids = [gap.get("id") for gap in zero_gaps if gap.get("id")]
    constrained = []
    for fact in facts:
        if fact.instance_uid in blocked:
            fact = replace(
                fact,
                recognition_status="low_confidence",
                extra_dims={
                    **fact.extra_dims,
                    "table_reading_status": "incomplete",
                    "table_reading_gaps": [g["id"] for g in blocking_gaps],
                },
            )
        elif zero_ids and fact.instance_uid in zero_affected:
            fact = replace(
                fact,
                extra_dims={
                    **fact.extra_dims,
                    "table_reading_status": "complete_with_zero_gap",
                    "table_reading_zero_gaps": zero_ids,
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
