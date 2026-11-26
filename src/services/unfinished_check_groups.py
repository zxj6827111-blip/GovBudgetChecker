"""把未完成的检查投影为有证据的原文位置，供任务列表和复核工作台共用。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_TABLE_SOURCE = re.compile(r"第\s*(\d+)\s*页\s*(?:table:)?([A-Z]+_\w+)")
_QUOTED_SOURCE = re.compile(r"定位[：:]\s*第\s*(\d+)\s*页\s*「([^」]+)」")
_COLUMN_GROUP = re.compile(r"「([^」]{1,24})」列段")
_SUBJECT_CODE = re.compile(r"科目\s*(\d{7}|\d{5}|\d{3})(?!\d)")
_MAPPING_REASONS = {"table_mapping_incomplete", "column_identity_unconfirmed", "amount_column_unmapped"}
_NORM_RE = re.compile(r"[\s“”‘’\"'《》「」【】\[\]()（）·,，。；;：:、—–\-]+")
_QUOTED_PHRASE = re.compile(r"[“「《]([^”」》]{4,40})[”」》]")
_GENERIC_QUOTES = {"本年支出", "本年收入", "基本支出", "项目支出", "合计", "总计"}
_AMOUNT_CELL_COUNT = re.compile(r"(\d+)\s*个金额格")
_PAGE_WRITTEN_TERM = re.compile(r"正文第\s*(\d+)\s*页写[“\"「]([^”\"」]{2,36})[”\"」]")

_TABLE_ALIAS_MAP: Dict[str, Tuple[str, ...]] = {
    "FIN_01_income_expenditure_total": ("收入支出决算总表", "收支总表"),
    "FIN_02_income": ("收入决算表",),
    "FIN_03_expenditure": ("支出决算表",),
    "FIN_04_fiscal_grant_total": ("财政拨款收入支出决算总表", "财政拨款收支总表"),
    "FIN_05_general_public_expenditure": ("一般公共预算财政拨款支出决算表", "一般公共预算支出决算表", "一般公共预算支出"),
    "FIN_06_basic_expenditure": ("一般公共预算财政拨款基本支出决算表", "基本支出决算表", "基本支出明细", "财政拨款经济分类表"),
    "FIN_07_three_public": ("财政拨款“三公”经费支出决算表", "三公经费支出决算表", "三公经费表", "三公经费"),
    "FIN_08_gov_fund": ("政府性基金预算财政拨款收入支出决算表", "政府性基金表"),
    "FIN_09_state_capital": ("国有资本经营预算财政拨款收入支出决算表", "国有资本经营表"),
}

_OBLIGATION_DEFAULT_TABLE: Dict[str, str] = {
    "OBL-CROSS-T1-IDENTITY": "FIN_01_income_expenditure_total",
    "OBL-INTERNAL-T1-TOTAL": "FIN_01_income_expenditure_total",
    "OBL-CROSS-T1-T2": "FIN_01_income_expenditure_total",
    "OBL-CROSS-T1-T3": "FIN_01_income_expenditure_total",
    "OBL-TXT-T1": "FIN_01_income_expenditure_total",
    "OBL-INTERNAL-T2-TOTAL": "FIN_02_income",
    "OBL-CROSS-T2-T4": "FIN_02_income",
    "OBL-INTERNAL-T3-TOTAL": "FIN_03_expenditure",
    "OBL-CROSS-FISCAL-TOTAL": "FIN_04_fiscal_grant_total",
    "OBL-CROSS-T4-T5": "FIN_04_fiscal_grant_total",
    "OBL-CROSS-T4-FUNDS": "FIN_04_fiscal_grant_total",
    "OBL-TXT-T4": "FIN_04_fiscal_grant_total",
    "OBL-TXT-T5": "FIN_05_general_public_expenditure",
    "OBL-CROSS-T5-T6": "FIN_05_general_public_expenditure",
    "OBL-INTERNAL-BASIC-EXPENSE": "FIN_06_basic_expenditure",
    "OBL-TXT-T6": "FIN_06_basic_expenditure",
    "OBL-SG-ITEMS": "FIN_07_three_public",
    "OBL-SG-TOTAL": "FIN_07_three_public",
    "OBL-SG-COMPLETION": "FIN_07_three_public",
    "OBL-SG-COUNTS": "FIN_07_three_public",
    "OBL-TXT-SAN-GONG": "FIN_07_three_public",
    "OBL-CROSS-SAN-GONG-ECON": "FIN_07_three_public",
    "OBL-TXT-FUND-DETAIL": "FIN_08_gov_fund",
}

_OBLIGATION_SECTION_NEEDLES: Dict[str, Tuple[str, ...]] = {
    "OBL-TXT-T1": ("收入支出决算总体情况说明", "收支决算总体情况说明", "决算情况说明"),
    "OBL-TXT-FINAL-DETAIL": ("支出决算具体情况", "一般公共预算财政拨款支出决算具体情况"),
    "OBL-TXT-T2": ("收入决算情况说明",),
    "OBL-TXT-T3": ("支出决算情况说明",),
    "OBL-TXT-T4": ("财政拨款收入支出决算总体情况说明",),
    "OBL-TXT-T5": ("一般公共预算财政拨款支出决算情况说明", "支出决算具体情况"),
    "OBL-TXT-T6": ("一般公共预算财政拨款基本支出决算情况说明", "基本支出决算情况说明"),
    "OBL-TXT-SAN-GONG": ("“三公”经费支出决算情况说明", "三公经费支出决算情况说明"),
    "OBL-SG-COMPLETION": ("“三公”经费支出决算情况说明", "三公经费支出决算情况说明"),
    "OBL-SG-COUNTS": ("“三公”经费支出决算情况说明", "三公经费支出决算情况说明"),
    "OBL-NARRATIVE-AMOUNT": ("绩效评级为", "评级为", "预算绩效管理", "决算情况说明", "总体情况说明"),
}

_PAGE_ONE_OBLIGATIONS = {
    "OBL-STRUCT-COVER",
    "OBL-PROFILE-KIND-CONFLICT",
    "OBL-STRUCT-TERMINOLOGY",
}

_TOTAL_ROW_KEYS = {"total", "合计", "总计", "本年收入合计", "本年支出合计"}

_MEASURE_HUMAN_LABELS: Dict[str, str] = {
    "general_public_budget": "一般公共预算",
    "government_fund_budget": "政府性基金预算",
    "state_capital_budget": "国有资本经营预算",
    "fiscal_allocation": "财政拨款收入",
    "superior_subsidy_income": "上级补助收入",
    "business_income": "事业收入",
    "operational_income": "经营收入",
    "subsidiary_income": "附属单位上缴收入",
    "other_income": "其他收入",
    "total_actual": "合计决算",
    "basic_actual": "基本支出决算",
    "project_actual": "项目支出决算",
    "income_actual": "本年收入决算",
    "expenditure_actual": "本年支出决算",
    "to_superior_actual": "上缴上级支出",
    "operational_actual": "经营支出",
    "subsidiary_actual": "对附属单位补助支出",
    "total_budget": "年初预算",
    "budget_amount": "年初预算",
}

_BLOCK_DICT_LABELS: Dict[str, str] = {
    "rule_admission_blocked": "处单元格因编码或行结构异常未纳入自动勾稽",
    "amount_column_unmapped": "个金额格未识别到子表头",
    "fact_unbound": "个金额格数值归属待确认",
    "column_identity_unconfirmed": "处列头归属未确认",
    "cell_low_confidence": "个单元格识别置信度低",
}


def _compact_nfkc(text: Any) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", str(text or "")) if not ch.isspace())


@lru_cache(maxsize=128)
def _page_char_index(pdf_path: str, mtime_ns: int, page_num: int) -> Tuple[str, Tuple[Tuple[float, float, float, float], ...]]:
    """缓存单页 NFKC 去空白字符序列及逐字 PDF 坐标，用于跨行精确框选。"""
    del mtime_ns  # 仅作缓存失效键
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # type: ignore[no-redef]
        except ImportError:
            return "", ()
    chars: List[str] = []
    boxes: List[Tuple[float, float, float, float]] = []
    try:
        with fitz.open(pdf_path) as doc:
            if page_num < 1 or page_num > len(doc):
                return "", ()
            page = doc[page_num - 1]
            rawdict = page.get_text("rawdict") or {}
            for block in rawdict.get("blocks") or []:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines") or []:
                    for span in line.get("spans") or []:
                        for ch_obj in span.get("chars") or []:
                            c = ch_obj.get("c")
                            bbox = ch_obj.get("bbox")
                            if not c or not bbox or len(bbox) != 4:
                                continue
                            norm = unicodedata.normalize("NFKC", str(c))
                            for sub in norm:
                                if sub.isspace():
                                    continue
                                chars.append(sub)
                                boxes.append((float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])))
    except Exception:
        return "", ()
    return "".join(chars), tuple(boxes)


def _line_union_boxes(
    boxes: Sequence[Tuple[float, float, float, float]],
    start: int,
    end: int,
) -> List[List[float]]:
    """把跨行字符 bbox 按视觉行合并，每行输出 1 个紧贴文字的矩形。"""
    if start < 0 or end <= start or end > len(boxes):
        return []
    line_groups: List[List[Tuple[float, float, float, float]]] = []
    for box in boxes[start:end]:
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        mid_y = (box[1] + box[3]) * 0.5
        if line_groups:
            prev = line_groups[-1][-1]
            prev_mid_y = (prev[1] + prev[3]) * 0.5
            # 同一视觉行且未大幅向左折行
            if abs(mid_y - prev_mid_y) <= 6.5 and box[0] >= prev[0] - 8.0:
                line_groups[-1].append(box)
                continue
        line_groups.append([box])
    merged: List[List[float]] = []
    for group_boxes in line_groups:
        x0 = max(0.0, min(b[0] for b in group_boxes) - 2.0)
        y0 = max(0.0, min(b[1] for b in group_boxes) - 1.5)
        x1 = max(b[2] for b in group_boxes) + 2.0
        y1 = max(b[3] for b in group_boxes) + 1.5
        if x1 > x0 and y1 > y0:
            merged.append([round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)])
    return merged


def _find_span_in_compact(
    compact: str,
    boxes: Sequence[Tuple[float, float, float, float]],
    group: Mapping[str, Any],
    details: Sequence[str],
) -> Optional[Tuple[int, int]]:
    if not compact or len(compact) != len(boxes):
        return None
    page = group.get("page")
    subject = str(group.get("subject") or "").strip()
    title = str(group.get("title") or "").strip()
    table_name = str(group.get("table_name") or "").strip()
    joined = "；".join(d for d in details if d)

    # 1. 显式“定位：第N页「…」”引文（最精确）
    for text in details:
        m = _QUOTED_SOURCE.search(text)
        if m and int(m[1]) == page:
            needle = _compact_nfkc(m[2])
            if len(needle) >= 3:
                idx = compact.find(needle)
                if idx >= 0:
                    return idx, idx + len(needle)

    # 2. 科目编码（纯数字，如 210）或中文科目名（如 事业运行 / 文化产业发展专项支出）
    if subject:
        subj_c = _compact_nfkc(subject)
        if subj_c.isdigit():
            def _is_cell_boundary(left_idx: int, right_idx: int) -> bool:
                lb, rb = boxes[left_idx], boxes[right_idx]
                lmy = (lb[1] + lb[3]) * 0.5
                rmy = (rb[1] + rb[3]) * 0.5
                return abs(rmy - lmy) > 5.0 or rb[0] < lb[0] - 5.0 or (rb[0] - lb[2]) > 5.0

            fallback_hit: Optional[Tuple[int, int]] = None
            pos = 0
            while True:
                start = compact.find(subj_c, pos)
                if start < 0:
                    break
                end = start + len(subj_c)
                pos = start + 1
                left_ok = start == 0 or not compact[start - 1].isdigit() or _is_cell_boundary(start - 1, start)
                right_ok = end == len(compact) or not compact[end].isdigit() or _is_cell_boundary(end - 1, end)
                if not (left_ok and right_ok):
                    continue
                if end < len(compact) and compact[end] == ".":
                    continue  # 排除金额数字（如 210.00）
                ref_y = (boxes[start][1] + boxes[start][3]) * 0.5
                ext = end
                has_cjk = False
                while ext < min(len(compact), end + 24):
                    by = (boxes[ext][1] + boxes[ext][3]) * 0.5
                    if abs(by - ref_y) > 6.5:
                        break
                    ch = compact[ext]
                    if "\u4e00" <= ch <= "\u9fff":
                        has_cjk = True
                    elif ch.isdigit() and ext > end:
                        break
                    ext += 1
                if has_cjk:
                    return start, ext
                if fallback_hit is None:
                    fallback_hit = (start, ext)
            if fallback_hit is not None:
                return fallback_hit
        else:
            # 优先匹配带层级后缀的条目头：如 事业运行(项) / 归口管理的行政单位离退休(项)
            for suffix in ("(项)", "(款)", "(类)", ""):
                needle = subj_c + suffix
                idx = compact.find(needle)
                if idx >= 0:
                    span_start, span_end = idx, idx + len(needle)
                    # 若同句说明里指明了前面紧邻的错写款/类名，把两者一起框出更直观
                    for pm in _PAGE_WRITTEN_TERM.finditer(joined):
                        if int(pm[1]) == page:
                            term = _compact_nfkc(pm[2])
                            if term and term != subj_c:
                                lookback = max(0, span_start - 70)
                                t_idx = compact.find(term, lookback, span_start)
                                if t_idx >= 0:
                                    span_start = t_idx
                                    break
                    return span_start, span_end

    # 3. 绩效评级类叙事句
    if "评级" in title or "评级" in joined:
        for anchor in ("绩效评级为", "评级为", "绩效评级", "绩效自评"):
            needle = _compact_nfkc(anchor)
            idx = compact.find(needle)
            if idx >= 0:
                # 扩展到该评级句结束（右括号或句号，最多 80 字）
                end = min(len(compact), idx + len(needle))
                for stop in range(end, min(len(compact), idx + 90)):
                    end = stop + 1
                    if compact[stop] in "。)）":
                        break
                return idx, end

    # 4. 诊断正文里的引号短语（排除泛称与表名）
    for text in details:
        for phrase in _QUOTED_PHRASE.findall(text):
            if phrase in _GENERIC_QUOTES or phrase.endswith("表"):
                continue
            needle = _compact_nfkc(phrase)
            if len(needle) >= 3:
                idx = compact.find(needle)
                if idx >= 0:
                    return idx, idx + len(needle)

    # 5. 表名标题兜底
    if table_name:
        needle = _compact_nfkc(table_name)
        if len(needle) >= 4:
            idx = compact.find(needle)
            if idx >= 0:
                return idx, idx + len(needle)

    # 6. 章节标题兜底（例如“收入决算情况说明”“基本支出决算情况说明”“支出决算具体情况”）
    section = str(group.get("section") or "").strip()
    section_candidates: List[str] = []
    if section:
        section_candidates.append(section)
    for cid in group.get("check_ids") or ():
        section_candidates.extend(_OBLIGATION_SECTION_NEEDLES.get(str(cid), ()))
    for sec in section_candidates:
        needle = _compact_nfkc(sec)
        if len(needle) >= 4:
            idx = compact.find(needle)
            if idx >= 0:
                return idx, idx + len(needle)

    # 7. 封面页（第 1 页）标题兜底：框出封面核心标题文字
    if page == 1 and len(compact) >= 4:
        return 0, min(len(compact), 36)
    return None


def _resolve_group_pdf_bboxes(
    pdf_path: Optional[str | Path],
    group: Mapping[str, Any],
    details: Sequence[str],
) -> List[List[float]]:
    page = group.get("page")
    if not pdf_path or not isinstance(page, int) or page < 1:
        return []
    path = Path(pdf_path)
    try:
        stat = path.stat()
    except OSError:
        return []
    compact, boxes = _page_char_index(str(path), stat.st_mtime_ns, page)
    span = _find_span_in_compact(compact, boxes, group, details)
    if span is None:
        return []
    return _line_union_boxes(boxes, span[0], span[1])


def _records(value: Any) -> List[Mapping[str, Any]]:
    return [record for record in value if isinstance(record, Mapping)] if isinstance(value, list) else []


def _page(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _bbox(value: Any) -> list | None:
    if (isinstance(value, (tuple, list)) and len(value) == 4
            and all(type(v) in (int, float) and isfinite(v) for v in value)
            and value[2] > value[0] and value[3] > value[1]):
        return list(value)
    return None


def _box_overlap(left, right) -> bool:
    if not left or not right:
        return False
    return not (
        left[2] <= right[0] or right[2] <= left[0]
        or left[3] <= right[1] or right[3] <= left[1]
    )


def _norm(text: Any) -> str:
    return _NORM_RE.sub("", str(text or ""))


def _description(check: Mapping[str, Any]) -> str:
    return str(check.get("detail") or check.get("message") or check.get("title") or "").split("；结构：", 1)[0]


def _named_table(text: str, table_names) -> str:
    quoted = {name for name in re.findall(r"《([^》]+表)》", text)}
    names = quoted or {name for name in table_names if name in text}
    complete = {name for name in names if not any(name != other and name in other for other in names)}
    return next(iter(complete)) if len(complete) == 1 else ""


def _format_block_dict(raw_dict: str) -> str:
    parts: List[str] = []
    for reason_key, count in re.findall(r"'([a-z_]+)'\s*:\s*(\d+)", raw_dict):
        label = _BLOCK_DICT_LABELS.get(reason_key)
        if label:
            parts.append(f"{count} {label}")
    return f"（{'，'.join(parts)}）" if parts else ""


def _table_label_for_code(code: str, table_names: Optional[Mapping[str, str]] = None) -> str:
    if table_names and table_names.get(code):
        return str(table_names[code])
    aliases = _TABLE_ALIAS_MAP.get(code)
    return aliases[0] if aliases else ""


def _humanize_role_title(role: str, subject: str = "") -> str:
    raw = str(role or "").strip()
    if not raw:
        return ""
    m = re.match(r"^([A-Za-z]+_[A-Za-z0-9_]*)/(.*)$", raw)
    if not m:
        return raw
    prefix, rest = m.group(1), m.group(2).strip()
    col_label = _MEASURE_HUMAN_LABELS.get(prefix, "")
    rest_clean = rest.replace("操作数", "数值待核对")
    if col_label.endswith("决算") and rest_clean.startswith("决算"):
        rest_clean = rest_clean[2:]
    if col_label.endswith("预算") and rest_clean.startswith("预算"):
        rest_clean = rest_clean[2:]
    combined = f"{col_label}{rest_clean}".strip() or "金额数值待核对"
    if subject in _TOTAL_ROW_KEYS and not combined.startswith("合计"):
        return f"合计行 · {combined}"
    return combined


def _humanize_detail(text: str, table_names: Optional[Mapping[str, str]] = None) -> str:
    cleaned = str(text or "").split("；结构：", 1)[0].strip()
    cleaned = re.sub(r"\{[^{}]*\}", lambda m: _format_block_dict(m.group(0)), cleaned)
    cleaned = re.sub(
        r"(?:table:)?((?:FIN|BUD)_\d+_[A-Za-z0-9_]+)",
        lambda m: f"《{_table_label_for_code(m.group(1), table_names)}》"
        if _table_label_for_code(m.group(1), table_names)
        else "",
        cleaned,
    )
    cleaned = re.sub(r"单元格\s*[0-9a-f]{8,}:r(\d+)c(\d+)", r"第\1行第\2列单元格", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[0-9a-f]{8,}:r(\d+)c(\d+)", r"第\1行第\2列单元格", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"([A-Za-z]+_[A-Za-z0-9_]*)/(?=决算|预算)",
        lambda m: _MEASURE_HUMAN_LABELS.get(m.group(1), "")
        if m.group(1) in {
            "general_public_budget", "government_fund_budget", "state_capital_budget",
            "fiscal_allocation", "superior_subsidy_income", "business_income",
            "operational_income", "subsidiary_income", "other_income",
        }
        else "",
        cleaned,
    )
    cleaned = re.sub(
        r"([A-Za-z]+_[A-Za-z0-9_]*)/",
        lambda m: _MEASURE_HUMAN_LABELS.get(m.group(1), ""),
        cleaned,
    )
    cleaned = re.sub(r"/(?:total|basic|project|budget|final)_[a-z_]+", "", cleaned)
    cleaned = re.sub(r"\btotal的", "合计行的", cleaned)
    cleaned = cleaned.replace("（ambiguous）", "（原件数字重叠或存在歧义）")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ；;，,：:")
    clauses: List[str] = []
    for part in re.split(r"[；;]+", cleaned):
        part_s = part.strip()
        if part_s and part_s not in clauses:
            clauses.append(part_s)
    return "；".join(clauses)


def _build_table_index(
    reading_tables: Sequence[Mapping[str, Any]],
    table_names: Mapping[str, str],
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    entries: List[tuple[int, str, Dict[str, Any]]] = []
    by_code: Dict[str, Dict[str, Any]] = {}
    code_by_name: Dict[str, str] = {}
    for code, name in table_names.items():
        normed = _norm(name)
        if normed:
            code_by_name[normed] = code
    for row in reading_tables:
        if not isinstance(row, Mapping):
            continue
        code = str(row.get("table_code") or "").strip()
        page = _page(row.get("page"))
        if not code or not page:
            continue
        info = {
            "page": page,
            "bbox": _bbox(row.get("bbox")),
            "title": str(row.get("title") or "").strip(),
            "end_page": None,
        }
        if code not in by_code or page < by_code[code]["page"]:
            by_code[code] = info
            entries.append((page, code, info))
        normed_title = _norm(info["title"])
        if normed_title and normed_title not in code_by_name:
            code_by_name[normed_title] = code
    entries.sort(key=lambda item: item[0])
    for idx, (page, _, info) in enumerate(entries):
        next_pages = [other_page for other_page, _, _ in entries[idx + 1:] if other_page > page]
        info["end_page"] = (next_pages[0] - 1) if next_pages else page + 8
    return by_code, code_by_name


def _is_toc_page(text: str, page_number: int) -> bool:
    if page_number > 6:
        return False
    compact = _norm(text)
    if "目录" in compact[:30] or (compact.count("决算表") >= 5 and compact.count("说明") >= 3):
        return True
    if len(re.findall(r"(?:\.{4,}|…{2,}|·{4,}|-{4,})\s*\d{1,3}(?:\s|$)", text)) >= 2:
        return True
    if (
        page_number >= 2
        and "。" not in text
        and not re.search(r"\d+\.\d{2}", text)
        and (
            compact.count("情况说明") >= 3
            or len(re.findall(r"(?:^|\n)\s*(?:[一二三四五六七八九十]+、|第[一二三四五六七八九十]+部分)", text)) >= 3
        )
    ):
        return True
    return False


def _find_explicit_page_in_details(details: Sequence[str], page_texts: Sequence[str]) -> int | None:
    for detail in details:
        for m in re.finditer(r"第\s*(\d+)\s*页", str(detail or "")):
            pnum = int(m.group(1))
            if pnum <= 0:
                continue
            if page_texts:
                if pnum > len(page_texts):
                    continue
                if _is_toc_page(str(page_texts[pnum - 1] or ""), pnum):
                    continue
            return pnum
    return None


def _find_subject_page(
    page_texts: Sequence[str],
    subject: str,
    start_page: int | None = None,
    end_page: int | None = None,
) -> int | None:
    if not page_texts or not subject:
        return None
    line_pattern = re.compile(rf"(?:^|\n)\s*{re.escape(subject)}(?:\s|$)")
    token_pattern = re.compile(rf"(?<!\d){re.escape(subject)}(?!\d)")

    def search_range(lo: int, hi: int, pattern: re.Pattern[str]) -> int | None:
        for page_num in range(max(1, lo), min(len(page_texts), hi) + 1):
            text = str(page_texts[page_num - 1] or "")
            if _is_toc_page(text, page_num):
                continue
            if pattern.search(text):
                return page_num
        return None

    if start_page:
        hi = end_page if end_page and end_page >= start_page else start_page + 8
        hit = search_range(start_page, hi, line_pattern) or search_range(start_page, hi, token_pattern)
        if hit:
            return hit
    return search_range(1, len(page_texts), line_pattern)


def _find_narrative_page(
    page_texts: Sequence[str],
    title: str,
    details: Sequence[str],
) -> int | None:
    if not page_texts:
        return None
    combined = " ".join([title, *details])
    norm_pages = [(idx + 1, _norm(text), _is_toc_page(str(text or ""), idx + 1)) for idx, text in enumerate(page_texts)]

    def search_needles(needles: Sequence[str]) -> int | None:
        for needle in needles:
            target = _norm(needle)
            if len(target) < 2:
                continue
            body_hits = [p for p, normed, is_toc in norm_pages if not is_toc and target in normed]
            if body_hits:
                return body_hits[0]
        return None

    if "评级" in combined or "绩效" in title:
        hit = search_needles(("绩效评级为", "评级为", "绩效评级", "绩效自评"))
        if hit:
            return hit

    quotes = [
        phrase for text in details for phrase in _QUOTED_PHRASE.findall(text)
        if phrase not in _GENERIC_QUOTES and not phrase.endswith("表")
    ]
    if quotes:
        hit = search_needles(quotes)
        if hit:
            return hit
    return None


def _compose_guidance(
    group: Mapping[str, Any],
    group_checks: Sequence[Mapping[str, Any]],
    descriptions: Mapping[str, str],
    *,
    table_names: Optional[Mapping[str, str]] = None,
    covered_by_issue: bool = False,
) -> tuple[str, str]:
    title = str(group.get("title") or "")
    table_name = str(group.get("table_name") or "")
    subject = str(group.get("subject") or "")
    page = group.get("page")
    page_note = f"第 {page} 页" if page else "对应页"
    details = [descriptions.get(str(check["id"]), "") for check in group_checks]
    joined = "；".join(d for d in details if d)
    covered_hint = "该项已在「必须处理」中生成对应问题，点击下方关联问题去处理即可，无需在此重复补核。"

    if (
        any(check.get("reason") == "amount_column_unmapped" for check in group_checks)
        or "amount_column_unmapped" in joined
        or "子表头" in joined
    ):
        col_labels = {label for d in details for label in _COLUMN_GROUP.findall(d)}
        col_label = next(iter(col_labels)) if len(col_labels) == 1 else ""
        cell_match = _AMOUNT_CELL_COUNT.search(joined) or re.search(r"'amount_column_unmapped'\s*:\s*(\d+)", joined)
        cell_note = f"有 {cell_match.group(1)} 个金额格" if cell_match else "部分金额格"
        prefix = f"原表「{col_label}」列下" if col_label else (f"《{table_name}》" if table_name else "原表")
        plain = f"{prefix}{cell_note}未识别到子列名称，系统无法自动完成该表及相关勾稽核对。"
        hint = covered_hint if covered_by_issue else f"请对照左侧{page_note} PDF 原表，确认未印子列名的各列含义，并核对合计与分项、表间及表文金额是否一致。"
        return plain, hint

    if "金额单位未知" in joined:
        target = f"《{table_name}》" if table_name else "对应决算表"
        plain = f"{target}（{page_note}）未标注金额单位（如“单位：万元”），系统按审慎原则未直接猜测换算口径。"
        hint = covered_hint if covered_by_issue else f"请对照左侧{page_note}原表确认金额单位，并核对正文中对应章节披露金额是否一致。"
        return plain, hint

    if "经济分类表" in joined and "科目编码无法唯一识别" in joined:
        target = f"《{table_name}》" if table_name else "《一般公共预算财政拨款基本支出决算表》"
        plain = f"{target}（{page_note}）部分行的经济分类编码在原件中截断或折行，系统已完成合计行核对，5位款项明细归集及“三公”经济分类对照需人工确认。"
        hint = covered_hint if covered_by_issue else f"请对照左侧{page_note}原表，核对人员经费、公用经费各款项编码与分项加总，以及“三公”对应经济分类行金额。"
        return plain, hint

    if title == "目标表待定位" or (
        any("table_missing" in (check.get("reason_codes") or ()) for check in group_checks)
        and "rule_admission_blocked" not in joined
        and "cell_low_confidence" not in joined
    ):
        target = f"《{table_name}》" if table_name else "目标表格"
        plain = f"系统未能唯一识别{target}的可用金额数据（常见原因：表内全部为空白/脱敏、表题写法不规范或缺表）。"
        hint = covered_hint if covered_by_issue else f"请查看左侧{page_note}原文确认{target}是否存在、数据是否完整，并人工核对相关跨表关系。"
        return plain, hint

    if title == "绩效评级类别待确认" or "评级类别" in joined:
        plain = "正文中的绩效自评评级档次未被识别为同一套标准互斥评级，系统未能自动复算各档项目数之和。"
        hint = covered_hint if covered_by_issue else f"请对照左侧{page_note}绩效说明原文，核对各评级档次项目数加总是否等于绩效自评项目总数。"
        return plain, hint

    if title == "科目名称对应关系" or ("科目名称差异" in joined and "金额" not in title):
        subject_note = f"（涉及「{subject}」）" if subject else ""
        plain = f"正文说明与对应表格中的科目名称不完全一致{subject_note}，系统未直接按金额自动绑定核对。"
        hint = covered_hint if covered_by_issue else f"请对照左侧{page_note}正文与对应表格，核对科目名称写法及对应金额是否一致。"
        return plain, hint

    if "显式决算金额" in title or "缺少可归一单位" in joined:
        subject_note = f"「{subject}」" if subject else "该条目"
        plain = f"正文{subject_note}的金额缺少规范单位或格式异常，系统未能自动提取并核对。"
        hint = covered_hint if covered_by_issue else f"请对照左侧{page_note}正文核对该条目的金额数字及单位（万元）是否完整、与表格一致。"
        return plain, hint

    if "存在冲突" in title or "多个不一致披露" in joined or "完成率" in joined:
        subject_note = f"「{subject}」" if subject else "该科目"
        plain = f"正文中{subject_note}出现多处不一致的预算/决算金额披露，系统无法唯一确定计算口径。"
        hint = covered_hint if covered_by_issue else f"请对照左侧{page_note}原文核对预算数、决算数及预算完成率计算是否正确。"
        return plain, hint

    if title == "科目明细待核对" or "直接层级明细不完整" in joined:
        target = f"《{table_name}》" if table_name else "对应表格"
        plain = f"{target}科目 {subject} 的直接下级明细不完整（常见原因：下级科目编码误写、错位或漏列款/项级行）。"
        hint = covered_hint if covered_by_issue else f"请对照左侧{page_note}，逐行核对科目 {subject} 下级科目编码及金额加总是否正确。"
        return plain, hint

    if (
        "ambiguous" in joined
        or "rule_admission_blocked" in joined
        or "没有唯一当前事实" in joined
        or any("cell_low_confidence" in (check.get("reason_codes") or ()) for check in group_checks)
    ):
        target = f"《{table_name}》" if table_name else "原表"
        plain = f"{target}在{page_note}存在局部单元格数字重叠、右缘尾数裁切或行结构异常，其余清晰科目已自动核对，仅保留受影响检查待确认。"
        hint = covered_hint if covered_by_issue else f"请对照左侧{page_note}原表查看受影响单元格原始数字，确认本表合计及相关表间/表文金额是否一致。"
        return plain, hint

    first_clean = _humanize_detail(details[0], table_names) if details else ""
    plain = first_clean or "系统在该位置取数不足，未能自动完成相关检查。"
    if page == 1 and any(cid in _PAGE_ONE_OBLIGATIONS for cid in (group.get("check_ids") or ())):
        hint = covered_hint if covered_by_issue else "请对照左侧第 1 页封面与归档单位名称、报告文种（部门/单位、预算/决算）核对一致后表态。"
    else:
        hint = covered_hint if covered_by_issue else f"请对照左侧{page_note}原文核对对应数据及勾稽关系后表态。"
    return plain, hint


_AMOUNT_FOR_LINK = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2}")
_BARE_TABLE_CODE_RE = re.compile(r"^(?:table:)?((?:FIN|BUD)_\d+_[A-Za-z0-9_]+)$")


def build_unfinished_check_groups(
    checks: List[Dict[str, Any]],
    table_names: Mapping[str, str],
    findings: Sequence[Mapping[str, Any]] = (),
    *,
    reading_tables: Sequence[Mapping[str, Any]] = (),
    page_texts: Sequence[str] = (),
    blocking_obligation_ids: Sequence[str] = (),
    covered_obligation_ids: Sequence[str] = (),
    pdf_path: Optional[str | Path] = None,
    analysis_revision: Optional[str] = None,
) -> List[Dict[str, Any]]:
    groups: Dict[tuple, Dict[str, Any]] = {}
    group_table_codes: Dict[tuple, str] = {}
    column_sources: Dict[tuple, List[tuple]] = defaultdict(list)
    descriptions = {str(check["id"]): _description(check) for check in checks}
    check_by_id = {str(check["id"]): check for check in checks}
    blocking_set = {str(item) for item in blocking_obligation_ids if str(item)}
    covered_set = {str(item) for item in covered_obligation_ids if str(item)} - blocking_set
    table_by_code, code_by_name = _build_table_index(reading_tables, table_names)
    original_quotes: Dict[int, list] = defaultdict(list)
    for finding in findings:
        for ref in _records((finding.get("location") or {}).get("table_refs")):
            page = _page(ref.get("physical_page")) or _page(ref.get("page"))
            box = _bbox(ref.get("bbox"))
            quote = re.sub(r"\s+", "", str(ref.get("quote") or ref.get("span") or ""))
            if page and box and len(quote) >= 6 and ref.get("verification_status") == "verified":
                original_quotes[page].append((quote, tuple(box)))

    def add(key, check, *, page=None, boxes=(), table_code="", table_name="", section="", subject="", title=""):
        group = groups.setdefault(key, {
            "id": f"source-{len(groups) + 1}", "check_ids": [],
            "title": title or check.get("title") or "核对未完成",
            "page": page, "table_name": table_name or table_names.get(table_code, table_code),
            "section": section, "subject": subject,
            "location_level": "exact" if page and boxes else "table" if page and table_code else "page" if page else "unlocated",
            "evidence": [{"page": page, "bbox": box} for box in boxes] if page else [],
        })
        if page and group.get("page") is None:
            group["page"] = page
            if group["location_level"] == "unlocated":
                group["location_level"] = "table" if table_code else "page"
        if page and boxes:
            existing_boxes = [item.get("bbox") for item in group["evidence"]]
            for box in boxes:
                if box and box not in existing_boxes:
                    group["evidence"].append({"page": page, "bbox": box})
                    existing_boxes.append(box)
            if group["evidence"]:
                group["location_level"] = "exact"
        if table_code and not group_table_codes.get(key):
            group_table_codes[key] = table_code
        if not group.get("table_name") and (table_name or table_code):
            group["table_name"] = table_name or table_names.get(table_code, table_code)
        if check["id"] not in group["check_ids"]:
            group["check_ids"].append(check["id"])
        return group

    for check in checks:
        if check.get("record_type") != "structured_review":
            continue
        code = str(check.get("table_code") or "")
        location = check.get("location") or {}
        source_page = _page(check.get("page_number")) or _page(location.get("page"))
        by_page: Dict[int, list] = defaultdict(list)
        for cell in _records(check.get("cells")):
            page = _page(cell.get("page"))
            if page:
                by_page[page]
                box = _bbox(cell.get("bbox"))
                if box and box not in by_page[page]:
                    by_page[page].append(box)
        amount_columns = check.get("reason") == "amount_column_unmapped"
        for page, boxes in (by_page.items() if by_page else [(source_page, [])]):
            key = ("table-region", page, code, check.get("reason"), tuple(sorted(map(tuple, boxes)))) if boxes else (
                "table-review", check["id"], page,
            )
            add(key, check, page=page, boxes=boxes, table_code=code,
                title="金额子列未确认" if amount_columns else "")
            if page and code and amount_columns and key not in column_sources[(page, code)]:
                column_sources[(page, code)].append(key)

    # 识别未被 structured_review 覆盖的表级阻断源（同一页同一张表因缺子列头、编码截断、
    # 右缘裁切或整表空白导致多项检查受阻时，归并为同一待办，人工只需核对一次）。
    unstructured_blocked_tables: set[Tuple[Optional[int], str]] = set()
    bare_blocked_codes: set[str] = set()
    for check in checks:
        if check.get("record_type") == "structured_review":
            continue
        for blocker in _records(check.get("blocked_by")):
            ref_str = str(blocker.get("ref") or "").strip()
            for p_str, b_code in _TABLE_SOURCE.findall(ref_str):
                p_int = int(p_str)
                if not column_sources.get((p_int, b_code)):
                    unstructured_blocked_tables.add((p_int, b_code))
            bare_m = _BARE_TABLE_CODE_RE.match(ref_str)
            if bare_m:
                bare_blocked_codes.add(bare_m.group(1))
    for b_code in bare_blocked_codes:
        already_paged = [p for (p, c) in unstructured_blocked_tables if c == b_code and p is not None]
        if already_paged:
            continue
        fallback_page = table_by_code[b_code]["page"] if b_code in table_by_code else None
        if fallback_page is None or not column_sources.get((fallback_page, b_code)):
            unstructured_blocked_tables.add((fallback_page, b_code))

    def _unstructured_targets_for(
        blocked_tables: set[Tuple[int, str]],
        blockers: Sequence[Mapping[str, Any]],
    ) -> List[Tuple[Optional[int], str]]:
        targets: set[Tuple[Optional[int], str]] = set()
        for p_int, b_code in blocked_tables:
            if (p_int, b_code) in unstructured_blocked_tables:
                targets.add((p_int, b_code))
        for blocker in blockers:
            bare_m = _BARE_TABLE_CODE_RE.match(str(blocker.get("ref") or "").strip())
            if bare_m:
                b_code = bare_m.group(1)
                code_targets = [(p, c) for (p, c) in unstructured_blocked_tables if c == b_code]
                targets.update(code_targets)
        return sorted(targets, key=lambda item: (item[0] is None, item[0] or 0, item[1]))

    blocked_table_keys: set[tuple] = set()

    for check in checks:
        if check.get("record_type") == "structured_review":
            continue
        detail = descriptions[str(check["id"])]
        operands = _records(check.get("missing_operands"))
        blockers = _records(check.get("blocked_by"))
        blocked_tables = {
            (int(page), code) for blocker in blockers
            for page, code in _TABLE_SOURCE.findall(str(blocker.get("ref") or ""))
        }
        reasons = set(check.get("reason_codes") or ())
        reasons.update(str(blocker.get("reason_code") or "") for blocker in blockers)
        matched = set()
        absorbed_operands = set()
        if reasons & _MAPPING_REASONS:
            explicit = blocked_tables | {
                (_page(op.get("page")), op.get("table_code"))
                for op in operands if op.get("table_code") and not _bbox(op.get("bbox"))
            }
            for target in explicit:
                candidates = column_sources.get(target, [])
                if len(candidates) == 1:
                    matched.add(candidates[0])
                    absorbed_operands.add(target)
            if not explicit:
                # 历史规则可能只在诊断正文指明目标表。表名精确匹配且只找到一处才归并。
                name = _named_table(detail, table_names.values())
                candidates = [
                    key for (_, code), keys in column_sources.items()
                    if name and table_names.get(code) == name for key in keys
                ]
                if len(candidates) == 1:
                    matched.add(candidates[0])
            for key in sorted(matched):
                add(key, check)

        unstructured_targets = _unstructured_targets_for(blocked_tables, blockers)
        if not matched and unstructured_targets and not operands:
            for t_page, t_code in unstructured_targets:
                b_key = ("blocked-table", t_page, t_code)
                blocked_table_keys.add(b_key)
                add(b_key, check, page=t_page, table_code=t_code)
                matched.add(b_key)
                absorbed_operands.add((t_page, t_code))

        for op in operands:
            page = _page(op.get("page"))
            code = str(op.get("table_code") or "")
            section, subject, column, role = (str(op.get(k) or "") for k in ("section", "row_key", "column", "role"))
            box = _bbox(op.get("bbox"))
            if (page, code) in absorbed_operands and not box:
                continue
            if code:
                op_blocked = (
                    [(page, code)]
                    if (page, code) in unstructured_blocked_tables
                    else [(p, c) for (p, c) in unstructured_blocked_tables if page is None and c == code]
                )
                if op_blocked:
                    for t_page, t_code in op_blocked:
                        b_key = ("blocked-table", t_page, t_code)
                        blocked_table_keys.add(b_key)
                        add(
                            b_key,
                            check,
                            page=t_page or page,
                            boxes=[box] if (box and (t_page == page or t_page is None)) else [],
                            table_code=t_code,
                        )
                        matched.add(b_key)
                    continue
            if not any((page, code, section, subject)):
                continue
            if box and page:
                key = ("coordinates", page, tuple(box))
            elif page and code and not section and subject in _TOTAL_ROW_KEYS:
                key = ("operand-total", page, code, "total")
            elif page and (code or section) and subject:
                key = ("operand", page, code, section, subject, column, role)
            else:
                key = ("check", check["id"], page, code, section, subject, column, role)
            add(
                key,
                check,
                page=page,
                boxes=[box] if box else [],
                table_code=code,
                section=section,
                subject=subject,
                title=_humanize_role_title(role, subject) if subject else "",
            )
            matched.add(key)

        if not matched and unstructured_targets:
            for t_page, t_code in unstructured_targets:
                b_key = ("blocked-table", t_page, t_code)
                blocked_table_keys.add(b_key)
                add(b_key, check, page=t_page, table_code=t_code)
                matched.add(b_key)

        if not matched and ("基本支出表" in detail or "经济分类表" in detail):
            fin06_targets = sorted(
                ((p, c) for (p, c) in unstructured_blocked_tables if c == "FIN_06_basic_expenditure"),
                key=lambda item: (item[0] is None, item[0] or 0),
            )
            if fin06_targets:
                for t_page, t_code in fin06_targets:
                    b_key = ("blocked-table", t_page, t_code)
                    blocked_table_keys.add(b_key)
                    add(b_key, check, page=t_page, table_code=t_code)
                    matched.add(b_key)

        if not matched:
            location = check.get("location") or {}
            page = _page(check.get("page_number")) or _page(location.get("page"))
            code = str(check.get("table_code") or location.get("table") or "")
            if blocked_tables:
                blocked_page, blocked_code = min(blocked_tables)
                if page in (None, blocked_page) and code in ("", blocked_code):
                    page, code = blocked_page, blocked_code
            subject_match = _SUBJECT_CODE.search(detail)
            named_table = table_names.get(code, "") or _named_table(detail, table_names.values())
            title = ""
            if "评级类别" in detail:
                title = "绩效评级类别待确认"
            elif named_table and "未定位" in detail:
                title = "目标表待定位"
            elif subject_match:
                title = "科目明细待核对"
            quoted = _QUOTED_SOURCE.search(detail)
            boxes = []
            if quoted:
                page = int(quoted[1])
                quote = re.sub(r"\s+", "", quoted[2])
                matches = {box for original, box in original_quotes[page] if original in quote}
                if len(matches) == 1:
                    boxes = [list(matches.pop())]
                key = ("coordinates", page, tuple(boxes[0])) if boxes else ("quote", page, quote)
            elif subject_match and (code or named_table):
                key = ("subject-detail", page, code or named_table, subject_match[1])
            else:
                key = ("unlocated", check["id"])
            add(key, check, page=page, boxes=boxes, table_code=code, table_name=named_table,
                section=str(location.get("section") or ""),
                subject=subject_match[1] if subject_match else "", title=title)

    for location_keys in column_sources.values():
        for key in location_keys:
            group = groups[key]
            labels = {label for check_id in group["check_ids"] for label in _COLUMN_GROUP.findall(descriptions[check_id])}
            if len(labels) == 1:
                group["title"] = f"{next(iter(labels))}子列未确认"

    for key in blocked_table_keys:
        group = groups.get(key)
        if not group:
            continue
        t_code = group_table_codes.get(key, "")
        joined_desc = "；".join(descriptions.get(cid, "") for cid in group["check_ids"])
        labels = {label for cid in group["check_ids"] for label in _COLUMN_GROUP.findall(descriptions.get(cid, ""))}
        if len(labels) == 1:
            group["title"] = f"{next(iter(labels))}子列未确认"
        elif "amount_column_unmapped" in joined_desc:
            tname = group.get("table_name") or table_names.get(t_code, "")
            group["title"] = f"《{tname}》金额子列未确认" if tname else "金额子列未确认"
        elif t_code == "FIN_06_basic_expenditure" and ("科目编码" in joined_desc or "经济分类" in joined_desc):
            group["title"] = "基本支出经济分类编码待核对"
        elif (
            any("table_missing" in (check_by_id.get(cid, {}).get("reason_codes") or ()) for cid in group["check_ids"])
            and "rule_admission_blocked" not in joined_desc
            and "cell_low_confidence" not in joined_desc
            and "没有唯一当前事实" not in joined_desc
        ):
            group["title"] = "目标表待定位"
        elif len(group["check_ids"]) > 1:
            tname = group.get("table_name") or table_names.get(t_code, "")
            group["title"] = f"《{tname}》局部数据待核对" if tname else "表格局部数据待核对"

    # P1 定位兜底链 + 阻塞/白话说明增强：
    # 1) 已有单元格/操作数坐标（exact）保持不动；
    # 2) 封面/文种/主体口径义务 → 第 1 页；显式“第N页”文本 → 对应非目录页；
    #    table_code/表名/义务默认表/标题别名 → reading_import.tables（有科目号时反查科目行所在页）；
    # 3) 科目号 → 正文逐页文本；
    # 4) 正文叙事关键词/章节标题/引文 → 逐页文本；
    # 5) 仍找不到时保留 unlocated，并写明缺口原因。
    tables_by_page: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for info in table_by_code.values():
        if info.get("page") and info.get("bbox"):
            tables_by_page[info["page"]].append(info)

    def _infer_table_code(group: Mapping[str, Any], check_ids: Sequence[str], titles_and_details: str) -> Optional[str]:
        for cid in check_ids:
            default_code = _OBLIGATION_DEFAULT_TABLE.get(str(cid))
            if default_code and default_code in table_by_code:
                return default_code
        norm_text = _norm(titles_and_details)
        best: Optional[Tuple[int, str]] = None
        for code, aliases in _TABLE_ALIAS_MAP.items():
            if code not in table_by_code:
                continue
            for alias in aliases:
                na = _norm(alias)
                if na and na in norm_text:
                    if best is None or len(na) > best[0]:
                        best = (len(na), code)
        return best[1] if best else None

    for key, group in groups.items():
        group_checks = [check_by_id[cid] for cid in group["check_ids"] if cid in check_by_id]
        check_details = [descriptions.get(str(check["id"]), "") for check in group_checks]
        check_titles = [str(check.get("title") or "") for check in group_checks]
        if group["page"] is None:
            if any(cid in _PAGE_ONE_OBLIGATIONS for cid in group["check_ids"]) or "封面" in str(group.get("title") or ""):
                group["page"] = 1
                group["location_level"] = "page"
            if group["page"] is None:
                explicit_page = _find_explicit_page_in_details(check_details, page_texts)
                if explicit_page:
                    group["page"] = explicit_page
                    group["location_level"] = "table" if group.get("table_name") else "page"
            resolved_code = (
                group_table_codes.get(key)
                or code_by_name.get(_norm(group.get("table_name")))
                or _infer_table_code(group, group["check_ids"], " ".join([str(group.get("title") or ""), *check_titles, *check_details]))
            )
            table_info = table_by_code.get(resolved_code) if resolved_code else None
            if group["page"] is None and table_info:
                if not group.get("table_name") and resolved_code:
                    group["table_name"] = table_names.get(resolved_code) or table_info.get("title") or ""
                subject_page = (
                    _find_subject_page(page_texts, group["subject"], table_info["page"], table_info["end_page"])
                    if group.get("subject") else None
                )
                if subject_page:
                    group["page"] = subject_page
                    group["location_level"] = "page"
                else:
                    group["page"] = table_info["page"]
                    group["location_level"] = "table"
                    if table_info.get("bbox"):
                        group["evidence"] = [{"page": table_info["page"], "bbox": table_info["bbox"]}]
            if group["page"] is None and group.get("subject") and page_texts:
                subject_page = _find_subject_page(page_texts, group["subject"])
                if subject_page:
                    group["page"] = subject_page
                    group["location_level"] = "page"
            if group["page"] is None and page_texts:
                section_hints = [
                    needle for cid in group["check_ids"]
                    for needle in _OBLIGATION_SECTION_NEEDLES.get(str(cid), ())
                ]
                table_hints: List[str] = []
                for cid in group["check_ids"]:
                    dc = _OBLIGATION_DEFAULT_TABLE.get(str(cid))
                    if dc:
                        table_hints.extend(_TABLE_ALIAS_MAP.get(dc, ()))
                if group.get("table_name"):
                    table_hints.insert(0, str(group["table_name"]))
                narrative_page = _find_narrative_page(
                    page_texts,
                    str(group.get("title") or ""),
                    [*check_details, *[f"「{h}」" for h in (*section_hints, *table_hints)]],
                )
                if narrative_page:
                    group["page"] = narrative_page
                    group["location_level"] = "page"
                elif table_hints:
                    for hint in table_hints:
                        target = _norm(hint)
                        if len(target) < 4:
                            continue
                        for pnum, ptext in enumerate(page_texts, 1):
                            if not _is_toc_page(str(ptext or ""), pnum) and target in _norm(ptext):
                                group["page"] = pnum
                                group["location_level"] = "page"
                                if not group.get("table_name"):
                                    group["table_name"] = hint
                                break
                        if group["page"] is not None:
                            break
            if group["page"] is None and table_by_code and any(
                cid in {"OBL-INTERNAL-TABLE-TOTAL", "OBL-STRUCT-NUMBER-VALIDITY", "OBL-EMPTY-TABLE-DISCLOSURE"}
                for cid in group["check_ids"]
            ):
                first_tbl = min(table_by_code.values(), key=lambda info: info["page"])
                group["page"] = first_tbl["page"]
                group["location_level"] = "table"
                if not group.get("table_name") and first_tbl.get("title"):
                    group["table_name"] = first_tbl["title"]
                if first_tbl.get("bbox"):
                    group["evidence"] = [{"page": first_tbl["page"], "bbox": first_tbl["bbox"]}]
            if group["page"] is None:
                if group.get("table_name"):
                    group["unlocated_reason"] = f"当前 PDF 中未识别到《{group['table_name']}》的页码位置"
                else:
                    group["unlocated_reason"] = "诊断信息未携带页码，且未在正文中唯一匹配到对应段落"

        # 若已确定页码但尚无坐标框，直接从 PDF 原件定位对应文字/科目行/章节坐标；
        # 仍无文字匹配时若该页存在唯一结构化表框，用表框高亮兜底。
        if pdf_path and group.get("page") and not group.get("evidence"):
            resolved_boxes = _resolve_group_pdf_bboxes(
                pdf_path,
                group,
                check_details,
            )
            if resolved_boxes:
                group["evidence"] = [{"page": group["page"], "bbox": box} for box in resolved_boxes]
                group["location_level"] = "exact"
        if group.get("page") and not group.get("evidence"):
            page_tables = tables_by_page.get(group["page"], [])
            if len(page_tables) == 1 and page_tables[0].get("bbox"):
                group["evidence"] = [{"page": group["page"], "bbox": page_tables[0]["bbox"]}]
                group["location_level"] = "table"

        blocking_ids = [cid for cid in group["check_ids"] if cid in blocking_set]
        covered_ids = [cid for cid in group["check_ids"] if cid in covered_set]
        obligation_ids = [
            cid for cid in group["check_ids"]
            if cid in blocking_set or cid in covered_set or str(cid).startswith("OBL-")
        ]
        if blocking_ids:
            handling_mode = "manual_required"
        elif covered_ids:
            handling_mode = "covered_by_issue"
        else:
            handling_mode = "notice_only"
        plain_reason, action_hint = _compose_guidance(
            group,
            group_checks,
            descriptions,
            table_names=table_names,
            covered_by_issue=(handling_mode == "covered_by_issue"),
        )
        group["blocking_obligation_ids"] = blocking_ids
        group["covered_obligation_ids"] = covered_ids
        group["obligation_ids"] = obligation_ids
        group["handling_mode"] = handling_mode
        group["blocks_completion"] = handling_mode == "manual_required"
        group["covered_by_issue"] = handling_mode == "covered_by_issue"
        group["plain_reason"] = plain_reason
        group["action_hint"] = action_hint

    # 同一版本已确认问题与未完成检查的原文关联：同一页、同一金额/引文或
    # 坐标相交时，一处疑点同时影响材料问题和算式。页面据此显示“影响 N 项”，
    # 但待确认处数仍按 group 计一处，不因问题卡片重复计数。
    finding_links = []
    for finding in findings:
        location = finding.get("location") if isinstance(finding.get("location"), Mapping) else {}
        page = _page(location.get("page"))
        refs = _records(location.get("table_refs"))
        if page is None:
            for ref in refs:
                page = _page(ref.get("physical_page")) or _page(ref.get("page"))
                if page:
                    break
        if page is None:
            continue
        quotes = [
            re.sub(r"\s+", "", str(ref.get("quote") or ref.get("span") or ""))
            for ref in refs
        ]
        quotes = [quote for quote in quotes if len(quote) >= 4]
        amounts = _AMOUNT_FOR_LINK.findall(str(finding.get("message") or ""))
        boxes = [
            box for ref in refs
            if (box := _bbox(ref.get("bbox")))
        ]
        finding_links.append((finding, page, quotes, amounts, boxes))
    for group in groups.values():
        related = []
        if group.get("page"):
            text = " ".join([
                str(group.get("title") or ""),
                str(group.get("subject") or ""),
                str(group.get("plain_reason") or ""),
                *[descriptions.get(cid, "") for cid in group["check_ids"]],
            ])
            compact_text = re.sub(r"\s+", "", text)
            subject = _compact_nfkc(group.get("subject") or "")
            raw_clauses = [
                part
                for text_part in (
                    str(group.get("title") or ""),
                    str(group.get("plain_reason") or ""),
                    *(descriptions.get(cid, "") for cid in group["check_ids"]),
                )
                for part in re.split(r"[；;]+", text_part)
                if part and subject and subject in _compact_nfkc(part)
            ]
            subject_clause_text = _compact_nfkc("；".join(raw_clauses)) if raw_clauses else subject
            existing_boxes = [
                box for item in group.get("evidence") or []
                if (box := _bbox(item.get("bbox")))
            ]
            for finding, finding_page, quotes, amounts, boxes in finding_links:
                if finding_page != group["page"]:
                    continue
                location = finding.get("location") if isinstance(finding.get("location"), Mapping) else {}
                compact_msg = _compact_nfkc(finding.get("message") or "")
                compact_actual = _compact_nfkc(location.get("actual_name") or "")
                code_level = str(location.get("code_level") or "")
                quote_has_subject = any(subject in _compact_nfkc(quote) for quote in quotes)
                is_suffix_only_name_issue = (
                    str(finding.get("severity") or "").lower() == "low"
                    or "名称多/少" in compact_msg
                )
                is_parent_level_name_issue = (
                    code_level in ("类", "款")
                    or "类级科目名称" in compact_msg
                    or "款级科目名称" in compact_msg
                )
                direct_subject_hit = (
                    not subject
                    or subject in compact_msg
                    or subject in compact_actual
                )
                parent_hierarchy_hit = bool(
                    subject
                    and compact_actual
                    and not is_suffix_only_name_issue
                    and quote_has_subject
                    and (
                        compact_actual in subject_clause_text
                        or is_parent_level_name_issue
                    )
                )
                # 同页不同科目分别计数：有明确科目时，问题必须指向同一科目
                # （或明确阻塞本条目的上级类/款名称差异）；无科目才按引文/坐标关联。
                if subject and not (direct_subject_hit or parent_hierarchy_hit):
                    continue
                if (
                    parent_hierarchy_hit
                    or any(quote in compact_text for quote in quotes)
                    or any(
                        re.sub(r"\s+", "", amount) in compact_text
                        for amount in amounts
                        if amount
                    )
                    or any(_box_overlap(box, existing) for box in boxes for existing in existing_boxes)
                ):
                    related.append(finding)
        ids = [str(item.get("id")) for item in related if item.get("id")]
        group["related_issue_ids"] = list(dict.fromkeys(ids))
        group["related_issue_rules"] = list(dict.fromkeys(
            str(item.get("rule_id") or item.get("rule") or "")
            for item in related
            if item.get("rule_id") or item.get("rule")
        ))
        # R5（2026-11-20 独立复核）：影响检查数按真实子检查去重；关联材料
        # 问题另记，不能把“问题卡片个数”算成“受影响的检查项数”。
        group["affected_check_count"] = len(dict.fromkeys(group["check_ids"]))
        group["related_issue_count"] = len(group["related_issue_ids"])
        # 稳定表态范围：同一义务下同页不同科目是两处独立阻塞，表态与
        # 门禁都按该范围识别，不能共用 obligation_id 一处表态全部放行。
        # R5（2026-11-20 复审）：种子不得包含 bbox——复核台与队列摘要对
        # bbox 解析口径不同（队列 resolve_pdf_bboxes=False），含坐标会让
        # 两边算出不同 scope_key，已表态位置在队列里永远扣不掉。
        scope_seed = "|".join([
            *sorted(str(value) for value in group.get("obligation_ids") or []),
            str(group.get("page") or ""),
            str(group.get("subject") or ""),
            str(group.get("section") or ""),
            *sorted(str(value) for value in group.get("check_ids") or []),
            str(group.get("table_name") or ""),
        ])
        group["decision_scope"] = "grp:" + hashlib.sha256(
            scope_seed.encode("utf-8")
        ).hexdigest()[:20]
    for group in groups.values():
        group["analysis_revision"] = analysis_revision

    _mode_rank = {"manual_required": 0, "covered_by_issue": 1, "notice_only": 2}
    return sorted(groups.values(), key=lambda group: (
        _mode_rank.get(group["handling_mode"], 2),
        group["location_level"] == "unlocated",
        group["location_level"] != "exact",
        group["page"] is None,
        group["page"] or 0,
    ))
