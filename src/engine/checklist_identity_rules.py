"""检查点清单：主体身份类规则（全系统默认检查指标，2026-10-22）。

| 规则 | 清单条目 | 内容 |
|------|----------|------|
| CMM-017 | B-01 | 单位名称全篇一致且为规范全称、“编制部门/编制单位”栏不得为空 |
| CMM-018 | B-02 | 部门/单位主体口径不混用；区（县）级材料不得套用“市级”名词解释 |
| CMM-019 | 预算 A-02、决算 A-02 | 封面要素：单位全称、年度、文种（部门/单位）、区级标识 |

单位名称的“规范全称”以全文出现最多的写法为准（画像名称打平手时优先），
只把与之构成“漏字 / 多字 / 语序颠倒”关系的写法当作不一致——字面不同的另一
个单位（“上海市财政局”之于“上海市数据局”）不是同一主体，不报。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from .checklist_support import (
    CJK,
    compact,
    issue_at,
    iter_pages,
    jurisdiction,
    org_name,
    page_flat,
    flat_span,
    report_kind,
    report_year,
    subject_scope,
)
from .rule_outcome import RuleNotApplicable
from .rules_v33 import Document, Issue, Rule

_NAME = rf"[{CJK}（）()]{{4,40}}?"
_NAME_CONTEXTS: Sequence[re.Pattern[str]] = (
    re.compile(rf"编制(?:部门|单位)[:：](?P<n>{_NAME})(?=单位[:：]|金额单位|$)"),
    re.compile(rf"^(?:第[一二三四]部分)?(?P<n>{_NAME})(?:主要职能|机构设置|基本概况|概况|项目经费情况说明)$"),
    re.compile(rf"^(?:第[一二三四]部分)?(?P<n>{_NAME})20\d{{2}}年度?(?:部门|单位)?(?:决算|预算)"),
    re.compile(rf"截至20\d{{2}}年\d{{1,2}}月\d{{1,2}}日[，,](?P<n>{_NAME})(?:共有|实有)"),
    re.compile(rf"(?:预算主管部门|预算单位)[:：](?P<n>{_NAME})$"),
)
_LEADING_VERBS_RE = re.compile(r"^(?:纳入|按照|照|根据|经|由|对|向|与|在|是)")
_TRAILING_SCOPE_RE = re.compile(r"(?:部门|单位|[（(]本级[)）])$")
_EMPTY_COMPILER_RE = re.compile(r"编制(?:部门|单位)\s*[:：]\s*(?=单位\s*[:：]|金额单位|\n|$)")


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


_ORG_START_RE = re.compile(r"(?:上海|中共|中国|北京|天津|重庆|[\u4e00-\u9fff]{2}(?:省|市|县|区))")


def _variant_kind(candidate: str, canonical: str) -> Optional[str]:
    """候选名称与规范全称的关系：漏字 / 多字 / 语序不一致；无关的另一个单位返回 None。"""
    if not candidate or candidate == canonical:
        return None
    if len(candidate) < len(canonical):
        if (_is_subsequence(candidate, canonical) and len(candidate) >= 0.75 * len(canonical)
                and (candidate[:1] == canonical[:1] or _ORG_START_RE.match(candidate))):
            return "漏字"
        return None
    if len(candidate) > len(canonical):
        if _is_subsequence(canonical, candidate) and len(candidate) <= len(canonical) + 6:
            return "多字"
        return None
    if sorted(candidate) == sorted(canonical):
        return "语序不一致"
    return None


def name_mentions(doc: Document) -> List[Tuple[str, int, str, int, int]]:
    """(名称, 页码, 页文本, 起, 止)：封面、编制栏、标题行、车辆说明等主体位置。"""
    mentions: List[Tuple[str, int, str, int, int]] = []
    for page, text in iter_pages(doc, skip_toc=False):
        offset = 0
        for line in text.split("\n"):
            line_start, offset = offset, offset + len(line) + 1
            chars = [(i, ch) for i, ch in enumerate(line) if not ch.isspace()]
            flat = "".join(ch for _, ch in chars)
            if not flat:
                continue
            for pattern in _NAME_CONTEXTS:
                for match in pattern.finditer(flat):
                    name = _TRAILING_SCOPE_RE.sub("", _LEADING_VERBS_RE.sub("", match.group("n")))
                    if len(name) < 4:
                        continue
                    lead = len(match.group("n")) - len(_LEADING_VERBS_RE.sub("", match.group("n")))
                    s = chars[match.start("n") + lead][0]
                    e = chars[min(len(chars) - 1, match.start("n") + lead + len(name) - 1)][0] + 1
                    mentions.append((name, page, text, line_start + s, line_start + e))
    return mentions


def canonical_org_name(doc: Document, mentions: Optional[Sequence[Tuple[str, int, str, int, int]]] = None) -> Optional[str]:
    mentions = list(mentions if mentions is not None else name_mentions(doc))
    profile = org_name(doc)
    counts = Counter(m[0] for m in mentions if m[0] == profile or _ORG_START_RE.match(m[0]))
    if not counts:
        return profile
    best, best_count = counts.most_common(1)[0]
    if profile and counts.get(profile, 0) >= best_count:
        return profile
    return best if best_count >= 2 else (profile or best)


class CMM017_OrgNameConsistency(Rule):
    code, severity = "CMM-017", "warn"
    desc = "单位名称全篇一致且为规范全称、编制栏不得为空（预/决算通用，清单 B-01）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        for page, text in iter_pages(doc, skip_toc=False):
            for match in _EMPTY_COMPILER_RE.finditer(text):
                issues.append(issue_at(
                    self, "表头“编制部门/编制单位”栏为空，应填写编制单位全称。",
                    page, text, match.start(), match.end() + 6, severity="warn", checklist_item="B-01",
                ))
        mentions = name_mentions(doc)
        canonical = canonical_org_name(doc, mentions)
        if not canonical:
            if issues:
                return issues
            raise RuleNotApplicable(self.code, detail="未识别到编制单位名称，名称一致性无法比对")
        reported: set = set()
        for name, page, text, start, end in mentions:
            kind = _variant_kind(name, canonical)
            if not kind or name in reported:
                continue
            reported.add(name)
            issues.append(issue_at(
                self, f"单位名称与规范全称不一致（{kind}）：此处写作“{name}”，全文规范全称为“{canonical}”。",
                page, text, start, end, severity="warn", checklist_item="B-01",
                expected=canonical, actual=name,
            ))
        return issues


# ---------------------------------------------------------------------------
# CMM-018 主体口径
# ---------------------------------------------------------------------------


def body_scope(doc: Document) -> Optional[str]:
    """不看封面，只凭目录与正文标题判断部门/单位口径（封面本身是 A-02 的检查对象）。"""
    from src.services.fiscal_source_scope import heading_scope

    kind = report_kind(doc)
    if kind not in {"final", "budget"}:
        return None
    return heading_scope("\n".join(list(doc.page_texts or [])[1:]), kind)


#: 部门材料中不应出现、单位材料中不应出现的主体自称（“部门/单位预算安排”“决算安排”由
#: BUD-113 / V33-236 报告，这里不重复）。
_SCOPE_WRONG_TERMS: Dict[str, Sequence[Tuple[str, re.Pattern[str]]]] = {
    "department": (
        ("本单位", re.compile(r"本单位(?![（(]部门)")),
        ("单位财政拨款", re.compile(r"(?<=年)单位财政拨款")),
        ("单位共有车辆", re.compile(r"(?<![各下所基层预算])单位共有(?:车辆|公务用车)")),
    ),
    "unit": (
        ("本部门", re.compile(r"本部门(?![（(]单位)")),
        ("部门财政拨款", re.compile(r"(?<=年)部门财政拨款")),
        ("部门共有车辆", re.compile(r"部门共有(?:车辆|公务用车)")),
    ),
}
_CITY_LEVEL_TERMS_RE = re.compile(r"市级预算主管部门|与市级财政有经费领拨关系|使用市级财政拨款|市级财政(?=部门|拨款)")


class CMM018_ScopeTerminology(Rule):
    code, severity = "CMM-018", "warn"
    desc = "部门/单位主体口径与层级口径一致（预/决算通用，清单 B-02）"

    def apply(self, doc: Document) -> List[Issue]:
        scope = body_scope(doc) or subject_scope(doc)
        issues: List[Issue] = []
        if scope in _SCOPE_WRONG_TERMS:
            label = "部门" if scope == "department" else "单位"
            for term, pattern in _SCOPE_WRONG_TERMS[scope]:
                hits: List[Tuple[int, str, int, int]] = []
                for page, text in iter_pages(doc):
                    flat, positions = page_flat(doc, page, text)
                    for match in pattern.finditer(flat):
                        hits.append((page, text, *flat_span(positions, match.start(), match.end())))
                if hits:
                    page, text, start, end = hits[0]
                    more = f"（全文共 {len(hits)} 处）" if len(hits) > 1 else ""
                    issues.append(issue_at(
                        self, f"主体口径混用：本材料为{label}{'预算' if report_kind(doc) == 'budget' else '决算'}，"
                        f"正文出现“{term}”表述{more}，应统一为“{'本部门' if scope == 'department' else '本单位'}”口径。",
                        page, text, start, end, severity="warn", checklist_item="B-02", occurrences=len(hits),
                    ))
        area = jurisdiction(doc) or org_name(doc) or ""
        if re.search(r"[区县]", area.replace("社区", "")) and not re.search(r"^(?:北京|上海|天津|重庆)市$", area):
            for page, text in iter_pages(doc):
                flat, positions = page_flat(doc, page, text)
                match = _CITY_LEVEL_TERMS_RE.search(flat)
                if match:
                    start, end = flat_span(positions, match.start(), match.end())
                    issues.append(issue_at(
                        self, f"层级口径错误：区（县）级材料出现“{match.group(0)}”，应为“区级”（如“区级预算主管部门”“区级财政”）。",
                        page, text, start, end, severity="warn", checklist_item="B-02",
                    ))
                    break
        if not scope and not issues:
            raise RuleNotApplicable(self.code, detail="未能从目录、正文或文件名确定部门/单位口径")
        return issues


# ---------------------------------------------------------------------------
# CMM-019 封面要素
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def body_year(doc: Document) -> Optional[int]:
    """表题与说明标题中的年度众数（不看封面）。"""
    counts: Counter = Counter()
    for text in list(doc.page_texts or [])[1:]:
        for line in str(text or "").split("\n"):
            flat = compact(line)
            if re.search(r"(?:决算|预算)(?:表|情况说明|编制说明)|预算总表|分类预算表", flat):
                for year in _YEAR_RE.findall(flat):
                    counts[int(year)] += 1
    if not counts:
        return None
    year, count = counts.most_common(1)[0]
    return year if count >= 2 else None


class CMM019_CoverElements(Rule):
    code, severity = "CMM-019", "warn"
    desc = "封面要素：单位全称、年度、文种与区级标识（预/决算通用，清单 A-02）"

    def apply(self, doc: Document) -> List[Issue]:
        texts = list(doc.page_texts or [])
        if not texts or not str(texts[0] or "").strip():
            raise RuleNotApplicable(self.code, detail="首页无文本层，封面要素无法核验")
        kind = report_kind(doc)
        if kind not in {"budget", "final"}:
            raise RuleNotApplicable(self.code, detail="文种未识别，封面文种标识无法核验")
        cover = str(texts[0])
        flat = compact(cover)
        first_line = next((ln for ln in cover.split("\n") if ln.strip()), "")
        anchor = (0, len(first_line))
        issues: List[Issue] = []

        def _emit(message: str, start: int, end: int, severity: str = "warn") -> None:
            issues.append(issue_at(self, message, 1, cover, start, end, severity=severity, checklist_item="A-02"))

        # 年度
        cover_years = {int(y) for y in _YEAR_RE.findall(flat)}
        expected_year = body_year(doc) or report_year(doc)
        if not cover_years:
            _emit("封面未写明年度（应为“××××年（度）…预算/决算”）。", *anchor)
        elif expected_year and expected_year not in cover_years:
            year = sorted(cover_years)[0]
            pos = cover.find(str(year))
            _emit(f"封面年度有误：封面写作{year}年，正文表题与说明为{expected_year}年。", max(pos, 0), max(pos, 0) + 4)

        # 单位全称
        canonical = canonical_org_name(doc)
        if canonical and canonical not in flat:
            variant = next((n for n, *_ in name_mentions(doc) if n in flat and _variant_kind(n, canonical)), None)
            if variant:
                pos = cover.find(variant[:2])
                _emit(f"封面单位名称“{variant}”与规范全称“{canonical}”不一致。", max(pos, 0), max(pos, 0) + len(variant))
            else:
                _emit(f"封面未出现单位全称“{canonical}”。", *anchor)

        # 文种（部门/单位）
        scope = body_scope(doc)
        noun = "预算" if kind == "budget" else "决算"
        if scope == "department":
            if kind == "final" and "部门决算" not in flat:
                _emit("部门决算封面应写明“××××年度部门决算”，当前封面漏写“部门”二字。", *anchor)
            if kind == "budget" and ("单位预算" in flat or re.search(r"预算单位[:：]", flat)):
                pos = max(cover.find("预算单位"), cover.find("单位预算"))
                _emit("部门预算封面误用单位预算标识（应为“区级部门预算”“预算主管部门：××”）。", max(pos, 0), max(pos, 0) + 4)
        elif scope == "unit":
            if f"部门{noun}" in flat or (kind == "budget" and re.search(r"预算主管部门[:：]", flat)):
                pos = max(cover.find(f"部门{noun}"), cover.find("预算主管部门"))
                _emit(f"单位{noun}封面误写“部门”字样（单位{noun}封面应为“××××年度{'单位预算' if kind == 'budget' else '决算'}”）。",
                      max(pos, 0), max(pos, 0) + 4)
        # 编制主体后多“单位/（部门）”字样
        label = re.search(rf"(?:预算主管部门|预算单位)[:：](?P<n>[{CJK}（）()]+)", flat)
        if label and canonical and re.search(r"(?:[（(](?:部门|单位)[)）]|单位)$", label.group("n")) \
                and not canonical.endswith("单位"):
            pos = cover.find("预算")
            _emit(f"封面编制主体名称后多出“单位/（部门）”字样：“{label.group('n')}”。", max(pos, 0), max(pos, 0) + 8, "info")
        # 区级标识：封面写“区级部门/单位预算”时须带区名
        if kind == "budget" and "区级" in flat:
            area = jurisdiction(doc) or canonical or ""
            district = re.search(rf"([{CJK}]{{2,4}}区)", area)
            title_line = next((compact(ln) for ln in cover.split("\n") if "区级" in compact(ln)), "")
            if district and district.group(1) not in title_line:
                pos = cover.find("区级")
                _emit(f"封面标题漏写区名：“{title_line}”应写明“{district.group(1)}”。", max(pos - 12, 0), max(pos, 0) + 6)
        return issues


__all__ = [
    "CMM017_OrgNameConsistency",
    "CMM018_ScopeTerminology",
    "CMM019_CoverElements",
    "body_scope",
    "body_year",
    "canonical_org_name",
    "name_mentions",
]
