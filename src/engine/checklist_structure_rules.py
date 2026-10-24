"""检查点清单：预决算通用的结构与版面规则（全系统默认检查指标，2026-10-22）。

| 规则 | 清单条目 | 内容 |
|------|----------|------|
| CMM-013 | 预算 A-05、决算 A-04/A-05/A-01 | 目录与正文标题逐项一致（名称、编号、顺序、“第X部分”字样） |
| CMM-014 | E-04/E-05 | 标题层级编号跳号、重号与同级编号体例混用 |
| CMM-015 | E-02 | 空白页、页码不连续 |
| CMM-016 | E-01 | 彩色字体、彩色底纹、全文横向排版（PyMuPDF 版面层） |

目录比对的口径（避免把模板固有差异当错误）：
- 目录写“一、部门主要职能”、正文写“××局主要职能”：目录中的“部门/单位”可由
  单位名称替换，不算不一致；
- 目录容器条目（“五、部门预算表”）正文无独立标题、但其下级条目都在：不报；
- 同编号标题只差个别字（相似度≥0.8，如“其他重要事项说明/其他重要事项的情况
  说明”）：属模板措辞差，不报；
- 编号不同、标题实质不同、缺“第X部分”字样、顺序颠倒：报告。
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .checklist_support import (
    CJK,
    is_table_like_line,
    is_toc_page,
    issue_at,
    iter_pages,
    org_name,
    page_has_table,
    page_narrative,
)
from .rule_outcome import RuleNotApplicable
from .rules_v33 import Document, Issue, Rule

_CN_NUM = "一二三四五六七八九十"
_NUM = (rf"(?:第[{_CN_NUM}]+部分|[{_CN_NUM}]+、|[（(][{_CN_NUM}]+[)）]"
        r"|\d{1,2}\s*[.．、](?:(?!\d)|(?=20\d{2}年)))")
_TOC_ENTRY_RE = re.compile(rf"^\s*(?P<num>{_NUM})\s*(?P<title>.*?)\s*$")
_TOC_TAIL_RE = re.compile(r"[.…·．\-_—\s]{3,}\s*\d*\s*$|\s+\d{1,3}\s*$")
_NUM_END_RE = re.compile(rf"({_NUM})$")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub(r"\s+|[“”\"'‘’]", "", text)
    return text.replace("(", "（").replace(")", "）")


def _norm_num(num: str) -> str:
    return _norm(num).replace("．", ".").replace("、", "、")


def _cn_to_int(text: str) -> Optional[int]:
    text = re.sub(r"[^\d" + _CN_NUM + "]", "", text)
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digits = {ch: i + 1 for i, ch in enumerate(_CN_NUM[:9])}
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + digits.get(text[1:], 0)
    if "十" in text:
        tens, _, ones = text.partition("十")
        return digits.get(tens, 0) * 10 + (digits.get(ones, 0) if ones else 0)
    return digits.get(text)


# ---------------------------------------------------------------------------
# CMM-013 目录与正文标题
# ---------------------------------------------------------------------------


def toc_pages(doc: Document) -> List[int]:
    """目录页（0 起）：前 6 页中首个目录页，加上紧随其后、全为目录条目的续页。"""
    texts = list(getattr(doc, "page_texts", None) or [])
    first = next((i for i, t in enumerate(texts[:6]) if is_toc_page(t)), None)
    if first is None:
        return []
    pages = [first]
    for index in range(first + 1, min(first + 3, len(texts))):
        lines = [line.strip() for line in texts[index].split("\n") if line.strip()]
        if not lines or len(lines) > 40:
            break
        if any(re.search(r"[。；，：]$", line) for line in lines):
            break
        hits = sum(1 for line in lines if _TOC_ENTRY_RE.match(line) and len(line) < 70)
        if hits / len(lines) < 0.6:
            break
        pages.append(index)
    return pages


def toc_entries(doc: Document, pages: Sequence[int]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    texts = doc.page_texts
    for index in pages:
        text = texts[index]
        offset = 0
        for line in text.split("\n"):
            stripped = line.strip()
            line_start = offset
            offset += len(line) + 1
            if not stripped or _norm(stripped) == "目录":
                continue
            match = _TOC_ENTRY_RE.match(stripped)
            if match and match.group("title"):
                entries.append({
                    "num": match.group("num"), "title": match.group("title"),
                    "page": index + 1, "start": line_start + line.find(stripped),
                    "end": line_start + len(line.rstrip()),
                })
            elif entries and not re.fullmatch(r"[\d\s.…·\-_—]+", stripped):
                entries[-1]["title"] += stripped
                entries[-1]["end"] = line_start + len(line.rstrip())
    for entry in entries:
        entry["title"] = _TOC_TAIL_RE.sub("", entry["title"]).strip()
    # 目录行内连写多个条目（“3. …总表4．2025年…”）时按编号再切开
    split: List[Dict[str, Any]] = []
    for entry in entries:
        parts = re.split(r"(?<=表)(?=\d{1,2}\s*[.．、]\s*20\d{2}年)", entry["title"])
        if len(parts) == 1:
            split.append(entry)
            continue
        split.append({**entry, "title": _TOC_TAIL_RE.sub("", parts[0]).strip()})
        for part in parts[1:]:
            match = _TOC_ENTRY_RE.match(part)
            if match:
                split.append({**entry, "num": match.group("num"),
                              "title": _TOC_TAIL_RE.sub("", match.group("title")).strip()})
    return [e for e in split if _norm(e["title"])]


class _Body:
    """目录之后的正文：去空白、NFKC 后的平铺文本，记录每个字符的页与行首位置。"""

    def __init__(self, doc: Document, start_page: int) -> None:
        chars: List[str] = []
        self.pages: List[int] = []
        self.line_starts: set = set()
        self.line_ends: set = set()
        self.headings: List[Tuple[int, str, str, int]] = []  # (pos, num, title, page)
        for index in range(start_page, len(doc.page_texts)):
            for line in doc.page_texts[index].split("\n"):
                normalized = _norm(line)
                if not normalized:
                    continue
                pos = len(chars)
                self.line_starts.add(pos)
                match = re.match(rf"({_NUM})(.{{1,60}})$", normalized)
                if (match and not is_table_like_line(line)
                        and not re.search(r"[。；，]$|\d\.\d", normalized)
                        and len(re.findall(rf"[{_CN_NUM}]+、", normalized)) < 2
                        and not re.search(r"(?:支出|收入|结余|结转|总计|合计|资金|拨款)$", normalized)):
                    self.headings.append((pos, match.group(1), match.group(2), index + 1))
                chars.extend(normalized)
                self.line_ends.add(len(chars))
                self.pages.extend([index + 1] * len(normalized))
        self.text = "".join(chars)

    def strong(self, pos: int, length: int) -> bool:
        """整行就是该标题（可带编号前缀）：排除“支出决算表”命中“一般公共预算财政拨款支出决算表”。"""
        return self.heading_like(pos) and (pos + length) in self.line_ends

    def heading_like(self, pos: int) -> bool:
        if pos in self.line_starts:
            return True
        match = _NUM_END_RE.search(self.text[max(0, pos - 8):pos])
        return bool(match) and (pos - len(match.group(1))) in self.line_starts

    def number_before(self, pos: int) -> str:
        match = _NUM_END_RE.search(self.text[max(0, pos - 8):pos])
        return match.group(1) if match else ""


def _relaxed(title: str, org: Optional[str]) -> str:
    core = _norm(title)
    if org:
        core = core.replace(_norm(org), "")
    core = re.sub(r"20\d{2}年度?|[（(]本级[)）]", "", core)
    return re.sub(r"^(?:部门|单位)", "", core)


class CMM013_TocHeadingAlignment(Rule):
    code, severity = "CMM-013", "warn"
    desc = "目录与正文标题逐项一致（名称、编号、顺序，预/决算通用，清单 A-05）"

    def apply(self, doc: Document) -> List[Issue]:
        pages = toc_pages(doc)
        if not pages:
            raise RuleNotApplicable(self.code, detail="未识别到目录页；目录缺失由章节结构规则 CMM-023 报告")
        entries = toc_entries(doc, pages)
        if len(entries) < 3:
            raise RuleNotApplicable(self.code, detail="目录条目不足 3 条，无法逐项比对")
        body = _Body(doc, max(pages) + 1)
        org = org_name(doc)
        issues: List[Issue] = []
        numdiff: List[Tuple[Dict[str, Any], str, int]] = []
        cursor = 0
        found_any = False
        for index, entry in enumerate(entries):
            title = _norm(entry["title"])
            num = _norm_num(entry["num"])
            toc_text = doc.page_texts[entry["page"] - 1]
            quote_args = (entry["page"], toc_text, entry["start"], entry["end"])
            positions = [m.start() for m in re.finditer(re.escape(title), body.text)]
            after = [p for p in positions if p >= cursor]
            pick = next((p for p in after if body.strong(p, len(title))), None)
            if pick is None:
                pick = next((p for p in after if body.heading_like(p)), None)
            if pick is None:
                earlier = [p for p in positions if p < cursor and body.strong(p, len(title))]
                if earlier and found_any:
                    issues.append(issue_at(
                        self, f"正文顺序与目录不一致：目录条目“{entry['num']}{entry['title']}”在正文中出现在前序条目之前"
                        f"（正文第{body.pages[earlier[0]]}页）。", *quote_args,
                        severity="warn", checklist_item="A-05", body_page=body.pages[earlier[0]],
                    ))
                    continue
                window_end = self._next_anchor(entries, index, body, cursor)
                self._unmatched(entry, num, title, index, entries, body, org, cursor, window_end,
                                quote_args, issues)
                continue
            found_any = True
            body_num = body.number_before(pick)
            if body_num and _norm_num(body_num) != num and not (
                num.startswith("第") and not body_num.startswith("第")
            ):
                numdiff.append((entry, body_num, body.pages[pick]))
            elif num.startswith("第") and not body_num.startswith("第") and body.heading_like(pick):
                issues.append(issue_at(
                    self, f"正文标题缺少“{entry['num']}”字样：目录为“{entry['num']} {entry['title']}”，"
                    f"正文第{body.pages[pick]}页标题未写“{entry['num']}”。", *quote_args,
                    severity="warn", checklist_item="A-01", body_page=body.pages[pick],
                ))
            cursor = pick + len(title)
        if numdiff:
            entry, body_num, body_page = numdiff[0]
            detail = "；".join(f"目录“{e['num']}{e['title']}”→正文“{b}”" for e, b, _ in numdiff[:3])
            more = f"等 {len(numdiff)} 处" if len(numdiff) > 3 else ""
            toc_text = doc.page_texts[entry["page"] - 1]
            issues.append(issue_at(
                self, f"目录与正文标题编号不一致：{detail}{more}。", entry["page"], toc_text,
                entry["start"], entry["end"], severity="warn", checklist_item="A-05",
                body_page=body_page, occurrences=len(numdiff),
            ))
        return issues

    @staticmethod
    def _next_anchor(entries, index, body, cursor) -> int:
        """下一个能在正文整行命中的目录条目位置：同编号标题只在此窗口内找。"""
        for later in entries[index + 1:]:
            title = _norm(later["title"])
            for match in re.finditer(re.escape(title), body.text):
                if match.start() >= cursor and body.strong(match.start(), len(title)):
                    return match.start()
        return len(body.text)

    def _unmatched(self, entry, num, title, index, entries, body, org, cursor, window_end,
                   quote_args, issues) -> None:
        # “第X部分”标题含单位名与年度，正是要核对的内容，不做放宽
        relaxed = title if num.startswith("第") else _relaxed(entry["title"], org)
        is_table_entry = title.endswith("表") and not num.startswith("第")
        same_num = [h for h in body.headings
                    if cursor <= h[0] < window_end and _norm_num(h[1]) == num]
        # 去掉单位名/年度后整行命中（“部门主要职能”→“××局主要职能”）且位于同编号标题之前：视为一致
        relaxed_pos = None
        if relaxed:
            for match in re.finditer(re.escape(relaxed), body.text):
                if match.start() >= cursor and match.end() in body.line_ends:
                    relaxed_pos = match.start()
                    break
        if relaxed_pos is not None and (not same_num or relaxed_pos <= same_num[0][0]):
            return
        if same_num and not is_table_entry:
            pos, body_num, body_title, page = same_num[0]
            if num.startswith("第"):
                # 部分标题常被版式拆成多行（“…情况”/“2024”/“说明”），按平铺正文取同长片段比较
                start = pos + len(_norm(body_num))
                extended = body.text[start:start + len(relaxed) + 8]
                toc_plain, body_plain = re.sub(r"\d+", "", relaxed), re.sub(r"\d+", "", extended)
                toc_years = set(re.findall(r"\d{3,4}", relaxed))
                body_years = set(re.findall(r"\d{3,4}", extended))
                if body_plain.startswith(toc_plain) and toc_years <= body_years:
                    return
                body_title = re.split(rf"[{_CN_NUM}]+、|[（(][{_CN_NUM}]+[)）]", extended[:len(relaxed) + 2])[0]
            body_relaxed = _norm(body_title) if num.startswith("第") else _relaxed(body_title, org)
            if relaxed and (relaxed == body_relaxed or (
                    not num.startswith("第") and (relaxed in body_relaxed or body_relaxed in relaxed))):
                return
            if not num.startswith("第") and SequenceMatcher(None, relaxed, body_relaxed).ratio() >= 0.8:
                return
            if self._is_container(index, entries, body):
                return
            issues.append(issue_at(
                self, f"目录与正文标题不一致：目录为“{entry['num']}{entry['title']}”，"
                f"正文第{page}页为“{body_num}{body_title}”。", *quote_args,
                severity="warn", checklist_item="A-05", body_page=page,
            ))
            return
        if relaxed_pos is not None or (relaxed and relaxed in body.text):
            return
        if is_table_entry or self._is_container(index, entries, body):
            return  # 缺表由 BUD-001 / V33-002 报告
        if num.startswith("第") or re.match(f"[{_CN_NUM}]+、", num):
            issues.append(issue_at(
                self, f"正文未找到目录所列标题“{entry['num']}{' ' if num.startswith('第') else ''}{entry['title']}”"
                "（标题文字不一致或该部分缺失）。",
                *quote_args, severity="warn", checklist_item="A-05",
            ))

    @staticmethod
    def _is_container(index: int, entries: Sequence[Dict[str, Any]], body: "_Body") -> bool:
        """目录容器条目（“五、部门预算表”下挂 1.~9.）：下级条目在正文出现即视为已有。"""
        children = []
        for entry in entries[index + 1:]:
            if re.match(r"\d", _norm(entry["num"])):
                children.append(entry)
            else:
                break
        return bool(children) and all(_norm(c["title"]) in body.text for c in children)


# ---------------------------------------------------------------------------
# CMM-014 标题层级编号
# ---------------------------------------------------------------------------

_LEVEL_PATTERNS: Sequence[Tuple[int, re.Pattern[str]]] = (
    (0, re.compile(rf"^第([{_CN_NUM}]+)部分")),
    (1, re.compile(rf"^([{_CN_NUM}]{{1,3}})、")),
    (2, re.compile(rf"^[（(]([{_CN_NUM}]{{1,3}})[)）]")),
    (3, re.compile(r"^(\d{1,2})\s*([.．、])(?!\d)")),
    (4, re.compile(r"^[（(](\d{1,2})[)）]")),
)


def _marker_variants(level: int, number: int) -> List[str]:
    cn = _int_to_cn(number)
    if level == 1:
        return [f"{cn}、"]
    if level == 2:
        return [f"（{cn}）", f"({cn})"]
    if level == 3:
        return [f"{number}.", f"{number}．", f"{number}、"]
    return [f"（{number}）", f"({number})"]


def _int_to_cn(number: int) -> str:
    digits = _CN_NUM[:9]
    if number <= 0:
        return ""
    if number < 10:
        return digits[number - 1]
    tens, ones = divmod(number, 10)
    head = "" if tens == 1 else digits[tens - 1]
    return f"{head}十{digits[ones - 1] if ones else ''}"


class CMM014_HeadingNumbering(Rule):
    code, severity = "CMM-014", "warn"
    desc = "标题层级编号连续、同级编号体例统一（预/决算通用，清单 E-04/E-05）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        last: Dict[int, Tuple[int, int]] = {}  # level -> (编号, 该标题行在 stream 中的起点)
        styles: Dict[int, Dict[str, int]] = {}
        reported_style: set = set()
        stream = ""  # 已扫过的全部正文行（含表格行），供跳号复核“缺的编号是否写在行中”
        skip = set(toc_pages(doc))
        for page, text in iter_pages(doc):
            if page - 1 in skip or page_has_table(doc, page - 1):
                # 表格页（含“一、一般公共服务支出”这类行标签）不参与，序列在此断开
                last.clear()
                styles.clear()
                continue
            narrative_offsets = {offset for offset, _ in page_narrative(doc, page, text)}
            line_offset = 0
            for line in text.split("\n"):
                offset, line_offset = line_offset, line_offset + len(line) + 1
                stripped = line.strip()
                if offset not in narrative_offsets:
                    stream += stripped
                    continue
                if _LEVEL_PATTERNS[0][1].match(stripped):
                    last.clear()
                    styles.clear()
                    stream += stripped
                    continue
                for level, pattern in _LEVEL_PATTERNS[1:]:
                    match = pattern.match(stripped)
                    if not match:
                        continue
                    number = _cn_to_int(match.group(1))
                    if number is None:
                        break
                    for deeper in [lvl for lvl in last if lvl > level]:
                        last.pop(deeper, None)
                        styles.pop(deeper, None)
                    start = offset + len(line) - len(line.lstrip())
                    end = start + match.end()
                    previous = last.get(level)
                    if previous is not None and number != 1:
                        prev_number, prev_pos = previous
                        between = stream[prev_pos:]
                        if number == prev_number:
                            issues.append(issue_at(
                                self, f"标题编号重号：编号“{match.group(0).strip()}”与上一条同级标题重复。",
                                page, text, start, end + 12, severity="warn", checklist_item="E-04",
                            ))
                        elif number > prev_number + 1 and not any(
                            marker in between for missing in range(prev_number + 1, number)
                            for marker in _marker_variants(level, missing)
                        ):
                            issues.append(issue_at(
                                self, f"标题编号跳号：上一条同级编号为{prev_number}，此处为{number}，"
                                f"缺少第{prev_number + 1}项。",
                                page, text, start, end + 12, severity="warn", checklist_item="E-04",
                            ))
                    if level == 3:
                        style = match.group(2).replace("．", ".")
                        bucket = styles.setdefault(level, {})
                        if number == 1:
                            bucket.clear()
                        bucket.setdefault(style, number)
                        if len(bucket) >= 2 and id(bucket) not in reported_style:
                            other = next(s for s in bucket if s != style)
                            reported_style.add(id(bucket))
                            issues.append(issue_at(
                                self, f"同级序号体例不统一：同一列表中既有“{bucket[other]}{other}”又有“{number}{style}”，"
                                "应统一为一种写法。",
                                page, text, start, end + 12, severity="info", checklist_item="E-05",
                            ))
                    last[level] = (number, len(stream))
                    break
                stream += stripped
        return issues


# ---------------------------------------------------------------------------
# CMM-015 空白页与页码
# ---------------------------------------------------------------------------

_PAGE_NUMBER_LINE_RE = re.compile(r"^\s*(?:[-—–－]\s*)?(\d{1,3})(?:\s*[-—–－])?\s*$|^\s*第\s*(\d{1,3})\s*页")


def _printed_page_number(text: str) -> Optional[Tuple[int, int, int]]:
    """页脚/页眉的印刷页码：(页码, 起, 止)。只看首尾两行。"""
    lines = str(text or "").split("\n")
    offset_lines = []
    offset = 0
    for line in lines:
        offset_lines.append((offset, line))
        offset += len(line) + 1
    candidates = [item for item in offset_lines if item[1].strip()]
    for line_offset, line in (candidates[-1:] + candidates[:1]) if candidates else []:
        match = _PAGE_NUMBER_LINE_RE.match(line)
        if match:
            number = int(match.group(1) or match.group(2))
            return number, line_offset, line_offset + len(line)
    return None


def _pdf_page_stats(path: str) -> Optional[List[Dict[str, Any]]]:
    import os

    if not path or not os.path.isfile(path):
        return None
    try:
        import pymupdf as fitz  # type: ignore  # 旧名 fitz 导入时会向 stdout 打弃用提示
    except Exception:
        return None
    try:
        document = fitz.open(path)
    except Exception:
        return None
    stats: List[Dict[str, Any]] = []
    try:
        for page in document:
            rect = page.rect
            entry: Dict[str, Any] = {
                "landscape": rect.width > rect.height * 1.05,
                "images": len(page.get_images(full=False)),
                "drawings": 0,
                "colored_text": [],
                "colored_fill": 0,
            }
            try:
                drawings = page.get_drawings()
            except Exception:
                drawings = []
            entry["drawings"] = len(drawings)
            for drawing in drawings:
                fill = drawing.get("fill")
                if fill and _is_vivid(fill) and _area(drawing.get("rect")) > 200:
                    entry["colored_fill"] += 1
            try:
                blocks = page.get_text("dict").get("blocks", [])
            except Exception:
                blocks = []
            for block in blocks:
                for line in block.get("lines", []) or []:
                    for span in line.get("spans", []) or []:
                        text = str(span.get("text") or "").strip()
                        color = span.get("color", 0)
                        if text and re.search(f"[{CJK}0-9]", text) and _is_vivid(_int_rgb(color)):
                            entry["colored_text"].append(text)
            stats.append(entry)
    finally:
        document.close()
    return stats


def _int_rgb(value: Any) -> Tuple[float, float, float]:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0)
    return (((value >> 16) & 255) / 255.0, ((value >> 8) & 255) / 255.0, (value & 255) / 255.0)


def _is_vivid(rgb: Any) -> bool:
    """彩色（非黑白灰）：饱和度高且不是近白/近黑。"""
    try:
        r, g, b = (float(x) for x in list(rgb)[:3])
    except Exception:
        return False
    high, low = max(r, g, b), min(r, g, b)
    if high < 0.15 or low > 0.92:
        return False
    return (high - low) >= 0.35


def _area(rect: Any) -> float:
    try:
        return abs(float(rect.width) * float(rect.height))
    except Exception:
        return 0.0


def _doc_pdf_stats(doc: Document) -> Optional[List[Dict[str, Any]]]:
    cached = getattr(doc, "_checklist_pdf_stats", None)
    if cached is not None:
        return cached or None
    path = str(getattr(doc, "path", "") or "")
    stats = _pdf_page_stats(path) if path.lower().endswith(".pdf") else None
    if stats is not None and len(stats) != len(doc.page_texts):
        stats = None
    try:
        doc._checklist_pdf_stats = stats or []
    except Exception:
        pass
    return stats


class CMM015_PageIntegrity(Rule):
    code, severity = "CMM-015", "warn"
    desc = "无空白页、页码连续（预/决算通用，清单 E-02）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        texts = list(doc.page_texts or [])
        stats = _doc_pdf_stats(doc)
        # 空白页：无文本、无表、无图片、无矢量图形；首页（封面）不判。
        for index, text in enumerate(texts):
            if index == 0 or str(text or "").strip():
                continue
            if page_has_table(doc, index):
                continue
            if stats is None or stats[index]["images"] or stats[index]["drawings"]:
                continue
            neighbour = next((i for i in range(index - 1, -1, -1) if str(texts[i] or "").strip()), None)
            if neighbour is None:
                continue
            neighbour_text = texts[neighbour]
            lines = [ln for ln in neighbour_text.split("\n") if ln.strip()]
            anchor = lines[-1] if lines else neighbour_text
            pos = neighbour_text.rfind(anchor)
            issues.append(issue_at(
                self, f"第{index + 1}页为空白页（无文字、表格与图形），请删除。",
                neighbour + 1, neighbour_text, max(pos, 0), max(pos, 0) + len(anchor),
                severity="warn", checklist_item="E-02", blank_page=index + 1,
            ))
        # 页码：至少 4 页印有页码时，检查印刷页码随物理页单调递增。
        numbered = []
        for index, text in enumerate(texts):
            found = _printed_page_number(text)
            if found:
                numbered.append((index, *found))
        if len(numbered) >= 4:
            for (i0, n0, *_), (i1, n1, s1, e1) in zip(numbered, numbered[1:], strict=False):
                expected = n0 + (i1 - i0)
                if n1 == expected or (n1 > n0 and n1 - n0 <= i1 - i0):
                    continue
                issues.append(issue_at(
                    self, f"页码不连续：第{i0 + 1}页印刷页码为{n0}，第{i1 + 1}页印刷页码为{n1}（按页序应为{expected}）。",
                    i1 + 1, texts[i1], s1, e1, severity="info", checklist_item="E-02",
                ))
        return issues


class CMM016_VisualStyle(Rule):
    code, severity = "CMM-016", "info"
    desc = "版面规范：无彩色字体、彩色底纹，正文不整体横排（预/决算通用，清单 E-01）"

    def apply(self, doc: Document) -> List[Issue]:
        stats = _doc_pdf_stats(doc)
        if stats is None:
            raise RuleNotApplicable(self.code, detail="原件版面层不可读（非 PDF 或无法打开），版面检查跳过")
        issues: List[Issue] = []
        texts = list(doc.page_texts or [])
        for index, entry in enumerate(stats):
            text = str(texts[index] or "")
            if entry["colored_text"]:
                sample = next((s for s in entry["colored_text"] if _norm(s) and _norm(s) in _norm(text)), None)
                if sample:
                    pos = text.find(sample)
                    if pos < 0:
                        pos = 0
                    issues.append(issue_at(
                        self, f"第{index + 1}页存在彩色字体（如“{sample[:20]}”），正式公开材料应统一为黑色字体。",
                        index + 1, text, pos, pos + len(sample), severity="info", checklist_item="E-01",
                    ))
            if entry["colored_fill"] and text.strip():
                first = next((ln for ln in text.split("\n") if ln.strip()), "")
                pos = text.find(first)
                issues.append(issue_at(
                    self, f"第{index + 1}页存在彩色底纹（{entry['colored_fill']}处），建议改为无底色。",
                    index + 1, text, max(pos, 0), max(pos, 0) + len(first), severity="info", checklist_item="E-01",
                ))
        narrative = [i for i, t in enumerate(texts) if str(t or "").strip() and not page_has_table(doc, i)]
        if len(narrative) >= 3 and all(stats[i]["landscape"] for i in narrative):
            first_page = narrative[0]
            text = texts[first_page]
            first = next((ln for ln in text.split("\n") if ln.strip()), "")
            pos = text.find(first)
            issues.append(issue_at(
                self, "报告文字页整体横向排版，不利于正式公开阅读，建议文字部分改为纵向版式。",
                first_page + 1, text, max(pos, 0), max(pos, 0) + len(first), severity="info", checklist_item="E-01",
            ))
        return issues


__all__ = [
    "CMM013_TocHeadingAlignment",
    "CMM014_HeadingNumbering",
    "CMM015_PageIntegrity",
    "CMM016_VisualStyle",
    "toc_entries",
    "toc_pages",
]
