"""AI 审校窗口：按句子/段落边界切分，并保留原文偏移、页码和边界来源。

窗口只是输入组织，不改写原文；所有 span 都是 ``full_text`` 的全局字符偏移，
跨页、软换行和重叠窗口不得改变页码映射。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

_SENTENCE_END = "。！？；\n"
_MIN_WINDOW_RATIO = 0.55
_CONTEXT_CHARS = 160


@dataclass
class TextWindow:
    window_id: int
    start: int
    end: int
    text: str
    pages: List[int] = field(default_factory=list)
    boundary: str = "sentence"
    hard_split: bool = False
    prefix_context: str = ""
    suffix_context: str = ""

    def to_dict(self, full_text: str) -> dict:
        return {
            "window_id": self.window_id,
            "start": self.start,
            "end": self.end,
            "pages": list(self.pages),
            "boundary": self.boundary,
            "hard_split": self.hard_split,
            "chars": len(self.text),
            "text_sha1": _sha1(self.text),
            "prefix_chars": len(self.prefix_context),
            "suffix_chars": len(self.suffix_context),
            "prefix_sha1": _sha1(self.prefix_context) if self.prefix_context else None,
            "suffix_sha1": _sha1(self.suffix_context) if self.suffix_context else None,
        }


def build_text_windows(
    full_text: str,
    page_offsets: Sequence[Tuple[int, int, int]],
    *,
    window_chars: int,
    overlap_chars: int,
    max_windows: int,
) -> List[TextWindow]:
    text = full_text or ""
    if not text:
        return []
    window_chars = max(200, int(window_chars))
    overlap_chars = max(0, min(int(overlap_chars), window_chars // 2))
    max_windows = max(1, int(max_windows))
    text_len = len(text)
    windows: List[TextWindow] = []
    start = 0
    while start < text_len and len(windows) < max_windows:
        target_end = min(start + window_chars, text_len)
        end, boundary, hard_split = _window_end(text, start, target_end)
        page_list = pages_for_range(page_offsets, start, end)
        prefix = text[max(0, start - _CONTEXT_CHARS):start]
        suffix = text[end:min(text_len, end + _CONTEXT_CHARS)]
        windows.append(
            TextWindow(
                window_id=len(windows),
                start=start,
                end=end,
                text=text[start:end],
                pages=page_list,
                boundary=boundary,
                hard_split=hard_split,
                prefix_context=prefix,
                suffix_context=suffix,
            )
        )
        if end >= text_len:
            break
        next_start = max(0, end - overlap_chars)
        if next_start <= start:
            next_start = min(text_len, start + max(1, window_chars // 2))
        # 上一窗口在句子边界收尾时，跳到下一句边界不会漏内容：
        # 重叠区已经完整进入上一窗口。硬切窗口必须保留重叠起点，避免漏半句。
        if not hard_split:
            boundary_start = _next_sentence_start(text, next_start)
            if next_start < boundary_start < end:
                next_start = boundary_start
        start = next_start
    return windows


def _window_end(text: str, start: int, target_end: int) -> Tuple[int, str, bool]:
    text_len = len(text)
    if target_end >= text_len:
        return text_len, "end_of_text", False
    min_end = start + max(1, int((target_end - start) * _MIN_WINDOW_RATIO))
    # 优先在当前目标之前找句末，保证窗口以完整句收尾。
    for index in range(target_end - 1, min_end - 1, -1):
        if text[index] in _SENTENCE_END:
            return index + 1, "sentence", False
    # 当前句比窗口长：向后找一个近的句末，避免把一句话切成两半。
    forward_limit = min(text_len, target_end + max(400, (target_end - start) // 2))
    for index in range(target_end, forward_limit):
        if text[index] in _SENTENCE_END:
            return index + 1, "sentence_extended", False
    return target_end, "hard_split", True


def _next_sentence_start(text: str, index: int) -> int:
    pos = max(0, index)
    while pos < len(text) and text[pos] not in _SENTENCE_END:
        pos += 1
    while pos < len(text) and text[pos] in _SENTENCE_END:
        pos += 1
    return pos


def pages_for_range(
    page_offsets: Sequence[Tuple[int, int, int]], start: int, end: int
) -> List[int]:
    pages: List[int] = []
    for page_start, page_end, page in page_offsets:
        if page_start < end and page_end > start:
            pages.append(int(page))
    return pages


def map_offset_to_page(
    page_offsets: Sequence[Tuple[int, int, int]], offset: int
) -> Optional[int]:
    for page_start, page_end, page in page_offsets:
        if page_start <= offset < page_end:
            return int(page)
    # 偏移落在页间分隔符时归入下一页，而不是退回第一页。
    for page_start, page_end, page in page_offsets:
        if offset == page_end:
            return int(page)
    return None


def window_page_map(
    window_start: int,
    window_length: int,
    page_offsets: Sequence[Tuple[int, int, int]],
) -> str:
    lines = [
        "【当前原文窗口的 PDF 物理页码映射】",
        "以下区间为当前窗口从 0 开始的字符偏移 [start,end)，页码从 PDF 第一张页面计为 1。",
        "span 必须对应逐字引文；page 根据区间填写，不使用页脚、目录页码或窗口序号。无法确定填 null。",
    ]
    for page_start, page_end, page in page_offsets:
        lo = max(window_start, page_start)
        hi = min(window_start + window_length, page_end)
        if lo < hi:
            lines.append(f"[{lo - window_start},{hi - window_start}) = PDF 第 {page} 页")
    return "\n".join(lines) + "\n【页码映射结束】\n"


def uncovered_ranges(
    windows: Sequence[TextWindow], total_chars: int
) -> List[dict]:
    """Return ranges never included in any window; overlap is not a gap."""
    if total_chars <= 0:
        return []
    covered = bytearray(total_chars)
    for window in windows:
        for index in range(max(0, window.start), min(total_chars, window.end)):
            covered[index] = 1
    gaps: List[dict] = []
    index = 0
    while index < total_chars:
        if covered[index]:
            index += 1
            continue
        start = index
        while index < total_chars and not covered[index]:
            index += 1
        gaps.append({"start": start, "end": index, "chars": index - start})
    return gaps


def _sha1(text: str) -> str:
    import hashlib

    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


__all__ = [
    "TextWindow",
    "build_text_windows",
    "map_offset_to_page",
    "pages_for_range",
    "uncovered_ranges",
    "window_page_map",
]
