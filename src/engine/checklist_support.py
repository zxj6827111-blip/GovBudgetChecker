"""检查点清单规则的共享工具（2026-10-22 起清单作为全系统默认检查指标）。

清单来源：《普陀区财政预算/决算报告检查点清单（系统核查用）v1.0》。用户决议
（2026-10-22）：清单不按地区单列，直接作为整个系统的检查指标，见
docs/PUTUO_CHECKLIST_GAP_ANALYSIS_20261022.md 与 rules/checklists/。

这里集中放三类工具，供 ``checklist_text_rules`` / ``checklist_structure_rules``
/ ``checklist_budget_rules`` / ``checklist_final_rules`` 共用：

1. **材料画像**：文种、报告年度、部门/单位口径、编制单位名称——统一从执行
   入口挂到 Document 上的 document_profile / fact_context 读取，取不到才回退
   到首页文本推断；推断不出就返回 None，由规则自行判定不适用或证据不足。
2. **引文定位**：证据守卫要求引文在所称页面（去空白、NFKC 后）**唯一命中**，
   否则来源绑定失败、整份材料转人工复核。``unique_quote`` 从命中位置向两侧
   扩展上下文，直到引文在本页唯一。
3. **叙述行判别**：页面文本里混有表格行，文字类检查只看叙述行。
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Any, Iterable, List, Optional, Sequence, Tuple

CJK = "一-鿿"
_CJK_RE = re.compile(f"[{CJK}]")
_SENTENCE_PUNCT_RE = re.compile(r"[，。；：、！？,;:]")
_NUMBER_TOKEN_RE = re.compile(r"(?<![\d.])\d[\d,，]*(?:\.\d+)?(?![\d.])")
_TOC_LEADER_RE = re.compile(r"(?:\.{4,}|…{2,}|·{4,}|-{6,}|_{6,})")


def report_kind(doc: Any) -> str:
    kind = str(getattr(doc, "report_kind", "") or "").strip().lower()
    if kind in {"budget", "final"}:
        return kind
    from src.services.document_profile_resolver import resolve_report_kind_from_path

    return resolve_report_kind_from_path(
        str(getattr(doc, "path", "") or ""), list(getattr(doc, "page_texts", None) or [])
    )


def _profile_value(doc: Any, name: str) -> Any:
    profile = getattr(doc, "document_profile", None)
    field = getattr(profile, name, None) if profile is not None else None
    return getattr(field, "value", None) if field is not None else None


def report_year(doc: Any) -> Optional[int]:
    """报告年度：画像 > 统一事实 > 首页加权推断（与 BUD-003 同一推断器）。"""
    for value in (
        _profile_value(doc, "report_year"),
        getattr(getattr(doc, "fact_context", None), "fiscal_year", None),
    ):
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if 2000 <= year <= 2099:
            return year
    from .budget_rules import _infer_report_year

    return _infer_report_year(doc)


def subject_scope(doc: Any) -> Optional[str]:
    """部门/单位口径，只认明确证据（文件名、封面与目录前三页的文种字样）。

    决算封面只写“××年度决算”时不推断为单位——部门决算漏写“部门”二字正是
    清单 A-02 要查的错误，拿它反推口径会把错误当成依据。
    """
    path = str(getattr(doc, "path", "") or "")
    head = "\n".join((getattr(doc, "page_texts", None) or [])[:3])
    compact = re.sub(r"\s+", "", f"{path}\n{head}")
    unit = re.search(r"单位(?:预算|决算)", compact)
    dept = re.search(r"部门(?:预算|决算)(?!单位构成)", compact)
    if unit and not dept:
        return "unit"
    if dept and not unit:
        return "department"
    if unit and dept:
        # 两者都出现（如“部门决算单位构成”已排除后仍并存），以封面为准
        cover = re.sub(r"\s+", "", ((getattr(doc, "page_texts", None) or [""])[0]))
        if re.search(r"单位(?:预算|决算)", cover):
            return "unit"
        if re.search(r"部门(?:预算|决算)", cover):
            return "department"
    level = _profile_value(doc, "subject_level")
    if level in {"department", "unit"}:
        return level
    return None


def org_name(doc: Any) -> Optional[str]:
    for value in (
        _profile_value(doc, "organization_name"),
        getattr(getattr(doc, "fact_context", None), "org_name", None),
    ):
        text = re.sub(r"\s+", "", str(value or ""))
        if len(text) >= 4:
            return text
    return None


def jurisdiction(doc: Any) -> Optional[str]:
    value = re.sub(r"\s+", "", str(_profile_value(doc, "jurisdiction") or ""))
    return value or None


# ---------------------------------------------------------------------------
# 引文定位
# ---------------------------------------------------------------------------


def _normalize_for_binding(text: str) -> str:
    """与 rule_source_binding._indexed_text 同口径：逐字 NFKC、去空白。"""
    out: List[str] = []
    for char in str(text or ""):
        for normalized in unicodedata.normalize("NFKC", char):
            if not normalized.isspace():
                out.append(normalized)
    return "".join(out)


def unique_quote(page_text: str, start: int, end: int, *, min_len: int = 10, max_len: int = 160) -> str:
    """返回包含 [start, end) 的原文片段，保证去空白、NFKC 后在本页唯一。

    证据守卫只认“所称页面唯一命中”的引文；重复出现的短语（如模板句）若只
    截取命中词本身，来源绑定会失败，整份材料被判证据不完整。片段不跨行
    扩展到表格区：优先在当前行内扩展，行内仍不唯一再跨行。
    """
    text = str(page_text or "")
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    haystack = _normalize_for_binding(text)
    left, right = start, end
    line_left = text.rfind("\n", 0, start) + 1
    line_right = text.find("\n", end)
    line_right = len(text) if line_right < 0 else line_right

    def _ok(a: int, b: int) -> bool:
        needle = _normalize_for_binding(text[a:b])
        return len(needle) >= min(min_len, len(haystack)) and haystack.count(needle) == 1

    step = 4
    while not _ok(left, right) and (right - left) < max_len:
        grew = False
        if left > line_left:
            left = max(line_left, left - step)
            grew = True
        if right < line_right:
            right = min(line_right, right + step)
            grew = True
        if not grew:
            if left > 0:
                left = max(0, left - step)
                grew = True
            if right < len(text):
                right = min(len(text), right + step)
                grew = True
        if not grew:
            break
    return text[left:right].strip()


# ---------------------------------------------------------------------------
# 页面与叙述行
# ---------------------------------------------------------------------------


def is_toc_page(text: str) -> bool:
    head = re.sub(r"\s+", "", "\n".join(str(text or "").splitlines()[:6]))
    return head.startswith("目录") or "目录" in head[:12]


#: 表格单元里的裸数值：后面不跟“年/万元/元/%/个/人”等叙述单位。
_BARE_NUMBER_RE = re.compile(
    r"(?<![\d.,，])\d[\d,，]*(?:\.\d+)?(?![\d.,，]|\s*(?:年|万|亿|元|%|％|个|人|辆|台|家|次|批|项|类|款|月|日|号|分|平方))"
)
_CJK_PUNCT_RE = re.compile(r"[，。；：、！？（）“”]")


def is_table_like_line(line: str) -> bool:
    """表格行：至少两个裸数值且没有中文句读，或是目录引导线——叙述类检查跳过。"""
    stripped = str(line or "").strip()
    if not stripped:
        return True
    if _TOC_LEADER_RE.search(stripped):
        return True
    bare = _BARE_NUMBER_RE.findall(stripped)
    if len(bare) < 2 or re.search(r"[，。；：、]", stripped):
        return False
    # 叙述行折行时也可能没有句读，但会带“万元”或“预算数为/比/较”等叙述用语
    return not re.search(r"万元|(?:预算|决算)数?为|比\s*20\d{2}|较\s*20\d{2}|增加|减少", stripped)


def page_has_table(doc: Any, page_index: int) -> bool:
    tables = getattr(doc, "page_tables", None) or []
    return 0 <= page_index < len(tables) and bool(tables[page_index])


def narrative_lines(page_text: str, *, table_page: bool = False) -> Iterable[Tuple[int, str]]:
    """逐行产出 (行首偏移, 行文本)，跳过表格行与目录引导线。

    ``table_page``：本页含表格时，表头/单元格折行（“2025年预算数比”）没有句读
    又很短，只保留带中文句读或足够长的行。
    """
    offset = 0
    for line in str(page_text or "").split("\n"):
        keep = not is_table_like_line(line) and (_CJK_RE.search(line) or _CJK_PUNCT_RE.search(line))
        if keep and table_page and not re.search(r"[，。；：、]", line) and len(line.strip()) < 24:
            keep = False
        if keep:
            yield offset, line
        offset += len(line) + 1


def narrative_flat(page_text: str, *, table_page: bool = False) -> Tuple[str, List[int]]:
    """本页叙述行去空白后拼成一串，并给出每个字符在原页文本中的偏移。

    跨软换行的词（“其他行\n政事业单位”）在逐行检查里会被切断；拼接后再
    匹配，命中位置经偏移表映射回原文，引文仍取自原页。
    """
    chars: List[str] = []
    positions: List[int] = []
    for offset, line in narrative_lines(page_text, table_page=table_page):
        for index, char in enumerate(line):
            if not char.isspace():
                chars.append(char)
                positions.append(offset + index)
    return "".join(chars), positions


def flat_span(positions: Sequence[int], start: int, end: int) -> Tuple[int, int]:
    """把 narrative_flat 上的 [start, end) 映射回原页偏移。"""
    if not positions:
        return 0, 0
    start = min(max(start, 0), len(positions) - 1)
    end = min(max(end, start + 1), len(positions))
    return positions[start], positions[end - 1] + 1


def iter_pages(doc: Any, *, skip_toc: bool = True) -> Iterable[Tuple[int, str]]:
    for index, text in enumerate(getattr(doc, "page_texts", None) or [], start=1):
        text = str(text or "")
        if not text.strip():
            continue
        if skip_toc and is_toc_page(text):
            continue
        yield index, text


def page_narrative(doc: Any, page: int, text: str) -> Iterable[Tuple[int, str]]:
    return narrative_lines(text, table_page=page_has_table(doc, page - 1))


def page_flat(doc: Any, page: int, text: str) -> Tuple[str, List[int]]:
    return narrative_flat(text, table_page=page_has_table(doc, page - 1))


def compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def first_page_of(doc: Any, needle: str, *, start_page: int = 1) -> Optional[int]:
    target = compact(needle)
    if not target:
        return None
    for index, text in enumerate(getattr(doc, "page_texts", None) or [], start=1):
        if index < start_page:
            continue
        if target in compact(text):
            return index
    return None


def issue_at(rule: Any, message: str, page: int, page_text: str, start: int, end: int, *,
             severity: Optional[str] = None, **location: Any):
    """在原文命中位置出一条带唯一引文的问题。"""
    loc = {"page": page, "pos": start}
    loc.update({k: v for k, v in location.items() if v is not None})
    return rule._issue(message, loc, severity=severity, evidence_text=unique_quote(page_text, start, end))


def display_half_unit(raw: str) -> Decimal:
    """按原文显示精度取半个末位单位：“328,661”→0.5，“279,285.70”→0.005。"""
    text = str(raw or "").replace(",", "").replace("，", "")
    decimals = len(text.split(".", 1)[1]) if "." in text else 0
    return Decimal(5) / (Decimal(10) ** (decimals + 1))


def display_envelope(raws: Sequence[str]) -> Decimal:
    """总额与各分项按各自显示精度四舍五入的累计包络 + 0.01 万元政策容差。"""
    return sum((display_half_unit(r) for r in raws), Decimal(0)) + Decimal("0.01")


def dedupe_spans(spans: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """去掉被更长命中覆盖的短命中。"""
    kept: List[Tuple[int, int]] = []
    for span in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
        if kept and span[0] < kept[-1][1]:
            continue
        kept.append(span)
    return kept
