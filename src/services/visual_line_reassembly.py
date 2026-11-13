"""原生词块的视觉行重组（阅读顺序修复，D1）。

为什么需要它
------------
pdfplumber 的行聚类按 ``top`` 数值以固定容差分簇（默认 3pt）。同一视觉行内
粗体/异字号词块的基线会整体下沉几磅（例如三公说明中加粗的金额），``top``
差一旦超过容差，该词块就被划成下一"行"，并被排到正文行之后，产生
"公务用车运行维护支出 → 万元。… → 2.22" 的错序：金额与单位在正文里分离，
下游金额声明模式与披露完整性检查随之取不到数、甚至误报"未披露"。

做法
----
只做**行级**重组，禁止跨栏/跨条目的全文坐标排序：

1. 词块按 ``top`` 排序后逐个并入"垂直区间重叠率"最高的现有行
   （重叠高度 / min(两侧高度) ≥ ``LINE_OVERLAP_RATIO``）；
   粗体基线偏移（重叠率通常 > 0.7）被并回同一行，
   相邻正文行（行距 ≥ 字高，重叠 ≈ 0）不会误并。
2. 行内按 ``x0`` 排序、行间按行首 ``top`` 排序，完全由原件几何决定，
   不引入字体/字号假设。
3. 每个词块仍是独立证据块（bbox、页码、原始文本不变），只改变排列顺序，
   保证回原文映射可审计。

本模块不修改原件内容：只重排词块、并在拼接行文本时按词间空隙插入空格，
与 ``extract_text`` 在词边界插入空格的行为一致。
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

# 垂直区间重叠率阈值：同线粗体/基线偏移 > 0.7，相邻正文行 < 0.35，0.5 居中分界。
LINE_OVERLAP_RATIO = 0.5


def _span(word: Dict[str, Any]) -> tuple:
    top = word.get("top")
    bottom = word.get("bottom")
    try:
        return float(top), float(bottom)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _overlap_ratio(a: tuple, b: tuple) -> float:
    """两垂直区间的重叠高度 / min(两区间高度)；无重叠为 0。"""
    height_a = max(a[1] - a[0], 0.0)
    height_b = max(b[1] - b[0], 0.0)
    smaller = min(height_a, height_b)
    if smaller <= 0:
        return 0.0
    overlap = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return overlap / smaller


def cluster_words_into_lines(words: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """把 pdfplumber 词块聚类成视觉行。

    输入词字典需含 ``x0/top/x1/bottom``。返回行列表：行内按 ``x0`` 排序，
    行间按行首 ``top``（再按 ``x0``）排序。词块本身不被修改。
    """
    ordered = sorted(
        (w for w in words if (w.get("text") or "").strip()),
        key=lambda w: (float(w.get("top") or 0.0), float(w.get("x0") or 0.0)),
    )
    lines: List[List[Dict[str, Any]]] = []
    spans: List[tuple] = []
    for word in ordered:
        span = _span(word)
        best_index, best_ratio = -1, 0.0
        for index, line_span in enumerate(spans):
            ratio = _overlap_ratio(span, line_span)
            if ratio > best_ratio:
                best_index, best_ratio = index, ratio
        if best_index >= 0 and best_ratio >= LINE_OVERLAP_RATIO:
            lines[best_index].append(word)
            low, high = spans[best_index]
            spans[best_index] = (min(low, span[0]), max(high, span[1]))
        else:
            lines.append([word])
            spans.append(span)
    for line in lines:
        line.sort(key=lambda w: (float(w.get("x0") or 0.0), float(w.get("top") or 0.0)))
    lines.sort(key=lambda line: (float(line[0].get("top") or 0.0), float(line[0].get("x0") or 0.0)))
    return lines


def join_line_text(line: Sequence[Dict[str, Any]], *, space_gap: float = 0.0) -> str:
    """行内词块拼接为行文本：词间空隙 > ``space_gap`` 时插入一个空格。

    pdfplumber 的 ``extract_words`` 只在字符间隙超过其 x 容差时才分词，
    ``extract_text`` 在同样的间隙插入空格；按词边界一律加空格与两者一致，
    把容差值交给调用方以便必要时与既有行为对齐。
    """
    parts: List[str] = []
    previous_x1 = None
    for word in line:
        text = str(word.get("text") or "")
        if not text:
            continue
        if parts and previous_x1 is not None:
            try:
                gap = float(word.get("x0") or 0.0) - previous_x1
            except (TypeError, ValueError):
                gap = space_gap + 1.0
            if gap > space_gap:
                parts.append(" ")
        parts.append(text)
        try:
            previous_x1 = float(word.get("x1") or 0.0)
        except (TypeError, ValueError):
            previous_x1 = None
    return "".join(parts)


def reassemble_page_text(words: Sequence[Dict[str, Any]]) -> str:
    """词块 → 视觉行重组后的整页文本（行间以 ``\\n`` 连接）。"""
    return "\n".join(join_line_text(line) for line in cluster_words_into_lines(words))
