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
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .checklist_support import (
    CJK,
    compact,
    issue_at,
    unique_quote,
    iter_pages,
    jurisdiction,
    org_name,
    page_flat,
    flat_span,
    report_kind,
    report_year,
    subject_scope,
)
from .rule_outcome import RuleNotApplicable, RuleDeferred
from .rules_v33 import Document, Issue, Rule
from src.services.material_writing import identity_document, prepare_identity_document, budget_resolution_enabled

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


def _brackets_balanced(name: str) -> bool:
    depth = 0
    for ch in name:
        depth += 1 if ch in "（(" else -1 if ch in "）)" else 0
        if depth < 0:
            return False
    return depth == 0


def _independent_body_name(doc: Document) -> Optional[str]:
    """封面名称的正文独立依据：只取封面以外、括号完整的主体名称。

    “本级”是范围标注而非名称；折行残片或两个不同名称出现次数相同时不作依据，
    不按插入顺序硬选一个名称去核对封面。
    """
    counts: Counter = Counter()
    for name, page, *_ in name_mentions(doc):
        name = re.sub(r"本级$", "", name)
        if page > 1 and len(name) >= 4 and _brackets_balanced(name):
            counts[name] += 1
    ranked = counts.most_common(2)
    if not ranked or ranked[0][1] < 2 or (len(ranked) > 1 and ranked[1][1] == ranked[0][1]):
        return None
    return ranked[0][0]


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
        # S04：部门决算单位构成表中的“（本级）”行必须与规范全称一致。
        for page, text in iter_pages(doc, skip_toc=True):
            flat = re.sub(r"\s+", "", text or "")
            for match in re.finditer(r"([\u4e00-\u9fa5（）()]{4,40})[（(]本级[）)]", flat):
                name = re.sub(r"^第[一二三四五六七八九十]+部分", "", match.group(1))
                if name in reported or "部分" in name:
                    continue
                # 只处理“和/与”连词多写造成的本单位名称变体；不同单位名不在此规则范围。
                if name.replace("和", "").replace("与", "") != canonical.replace("和", "").replace("与", ""):
                    continue
                kind = _variant_kind(name, canonical)
                if not kind:
                    continue
                reported.add(name)
                start = text.find(name)
                issues.append(issue_at(
                    self, f"单位名称与规范全称不一致（{kind}）：此处写作“{name}”，全文规范全称为“{canonical}”。",
                    page, text, start if start >= 0 else 0,
                    (start + len(name)) if start >= 0 else len(name),
                    severity="warn", checklist_item="B-01", expected=canonical, actual=name,
                ))
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


def _uses_writing_resolution(doc):
    value = getattr(getattr(doc, "document_profile", None), "writing_scope", {}) or {}
    return (report_kind(doc) == "final" or (report_kind(doc) == "budget" and value.get("domain") == "budget"
        and budget_resolution_enabled(value.get("snapshot") or {})))


def body_scope(doc: Document) -> Optional[str]:
    """不看封面，只凭目录与正文标题判断部门/单位口径（封面本身是 A-02 的检查对象）。"""
    from src.services.fiscal_source_scope import heading_scope

    if _uses_writing_resolution(doc):
        resolution = getattr(getattr(doc, "document_profile", None), "writing_scope", {}) or {}
        return resolution.get("value") if resolution.get("status") == "confirmed" else None
    field = getattr(getattr(doc, "document_profile", None), "subject_level", None)
    if getattr(field, "source", None) == "human_confirmation":
        return field.value if field.value in {"unit", "department"} else None

    kind = report_kind(doc)
    if kind == "final":
        resolution = getattr(getattr(doc, "document_profile", None), "writing_scope", {}) or {}
        return resolution.get("value") if resolution.get("status") == "confirmed" else None
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


def _archive_writing_contrast(doc: Document) -> Optional[dict]:
    """仅当写法基准来自"上传所选层级"（source=archive）时判定"写法全面相反"。

    人工决定、原文明示与模板推断保持原有行为；全面相反时主体/封面文种子项
    转为一条待人工复核，不再逐条报 warn。
    """
    resolution = getattr(getattr(doc, "document_profile", None), "writing_scope", {}) or {}
    if resolution.get("source") != "archive" or resolution.get("status") != "confirmed":
        return None
    value = resolution.get("value")
    if value not in ("unit", "department"):
        return None
    from src.services.template_scope import comprehensive_writing_contrast

    categories = (("budget_financial_table", "budget_function_table")
                  if report_kind(doc) == "budget" else ("heading", "table_note"))
    return comprehensive_writing_contrast(
        evidence=resolution.get("evidence") or (),
        selected_scope=value,
        page_texts=getattr(doc, "writing_page_texts", None) or getattr(doc, "page_texts", None) or [],
        required_categories=categories,
    )


def _contrast_summary(contrast: dict) -> dict:
    """问题引用里保留页码与判定要点，不塞入完整证据列表。"""
    opposite_pages = sorted({
        item["page"]
        for rows in (contrast.get("opposite") or {}).values()
        for item in rows
        if item.get("page")
    })
    return {"comprehensive_contrast": True, "opposite_pages": opposite_pages,
            "other_scope": contrast.get("other_scope")}


def _contrast_label(scope: Optional[str]) -> str:
    return "单位" if scope == "unit" else "部门"


def _cover_scope(doc: Document) -> Optional[str]:
    """封面文种口径：只认封面页明确的“部门决算/单位决算”。"""
    text = str((doc.page_texts or [""])[0] or "")
    has_unit = "单位决算" in text
    has_department = "部门决算" in text
    if has_department and not has_unit:
        return "department"
    if has_unit and not has_department:
        return "unit"
    if not has_department and re.search(r"20\d{2}\s*年度?\s*决算", text):
        # 单位模板封面只写“年度决算”，不带“部门”。
        return "unit"
    return None


class CMM018_ScopeTerminology(Rule):
    code, severity = "CMM-018", "warn"
    desc = "部门/单位主体口径与层级口径一致（预/决算通用，清单 B-02）"

    def apply(self, doc: Document) -> List[Issue]:
        doc = prepare_identity_document(doc) if _uses_writing_resolution(doc) else doc
        scope = body_scope(doc) if _uses_writing_resolution(doc) else (body_scope(doc) or subject_scope(doc))
        issues: List[Issue] = []
        contrast = _archive_writing_contrast(doc)
        contrast_hits: List[Tuple[int, str, int, int]] = []
        page1 = str((doc.page_texts or [""])[0] or "")
        bare_cover = (_cover_scope(doc) == "unit" and "部门" not in page1 and "单位" not in page1)
        term_scope = "unit" if (bare_cover and scope == "department") else scope
        if term_scope in _SCOPE_WRONG_TERMS:
            label = "部门" if term_scope == "department" else "单位"
            for term, pattern in _SCOPE_WRONG_TERMS[term_scope]:
                hits: List[Tuple[int, str, int, int]] = []
                for page, text in iter_pages(doc):
                    flat, positions = page_flat(doc, page, text)
                    for match in pattern.finditer(flat):
                        resolution = getattr(getattr(doc, "document_profile", None), "writing_scope", {}) or {}
                        snapshot = resolution.get("snapshot") or {}
                        config = snapshot.get("effective_config") or (snapshot.get("content") or {}).get("config") or {}
                        native = config.get("native") or {}
                        section_before = compact("\n".join(list(doc.page_texts or [])[:page-1]) + text[:positions[match.start()]])
                        perf_start = section_before.rfind("预算绩效管理")
                        other_start = section_before.rfind("其他重要事项")
                        native_here = (report_kind(doc) == "final" and scope == "unit" and term == "本部门"
                            and snapshot.get("available") and config.get("d2") == "low"
                            and native.get("sentence") and flat[match.start():].startswith(native["sentence"])
                            and flat[match.start()+len(native["sentence"]):].startswith(("：", ":"))
                            and perf_start > other_start and perf_start >= 0)
                        if native_here:
                            start, end = flat_span(positions, match.start(), match.end())
                            issues.append(issue_at(self, "单位材料使用模板原句“本部门建立了…”；按主体用语清单提示统一写法（low），该句来自已审核模板。",
                                page, text, start, end, severity="low", checklist_item="B-02", template_package=snapshot.get("package_id")))
                        else:
                            hits.append((page, text, *flat_span(positions, match.start(), match.end())))
                if hits:
                    # 全面相反时不再逐条报反向主体用语，合并为一条待人工复核。
                    if contrast and contrast.get("comprehensive_contrast"):
                        contrast_hits.extend(hits)
                        continue
                    page, text, start, end = hits[0]
                    more = f"（全文共 {len(hits)} 处）" if len(hits) > 1 else ""
                    issues.append(issue_at(
                        self, f"主体口径混用：本材料为{label}{'预算' if report_kind(doc) == 'budget' else '决算'}，"
                        f"正文出现“{term}”表述{more}，应统一为“{'本部门' if term_scope == 'department' else '本单位'}”口径。",
                        page, text, start, end, severity="warn", checklist_item="B-02", occurrences=len(hits),
                        locations=[{'page':p,'text':unique_quote(t,a,b),'start':a,'end':b} for p,t,a,b in hits],
                        table_refs=[{'page':p,'span':unique_quote(t,a,b),'role':'反向主体用语'} for p,t,a,b in hits],
                    ))
        # R21：表注/构成段的部门-单位口径必须与封面/选定层级一致。
        caption_scope = _cover_scope(doc) or scope
        caption_re = {
            "department": re.compile(r"本表反映单位(?:本年度|年度)|单位本年度没有|纳入[^，。；]{0,40}单位决算"),
            "unit": re.compile(r"本表反映部门(?:本年度|年度)|部门本年度没有|纳入[^，。；]{0,40}部门决算|部门决算单位构成"),
        }
        if caption_scope in caption_re:
            hits: List[Tuple[int, str, int, int]] = []
            for page, text in iter_pages(doc, skip_toc=True):
                if page == 1:
                    continue
                flat, positions = page_flat(doc, page, text)
                for match in caption_re[caption_scope].finditer(flat):
                    hits.append((page, text, *flat_span(positions, match.start(), match.end())))
            if len(hits) >= 4:
                page, text, start, end = hits[0]
                expected_label = "本部门/部门" if caption_scope == "department" else "本单位/单位"
                issues.append(issue_at(
                    self, f"材料口径与封面不一致：正文出现相反口径表述（共{len(hits)}处），"
                    f"当前封面为{'部门' if caption_scope == 'department' else '单位'}口径，应统一为“{expected_label}”。",
                    page, text, start, end, severity="warn", checklist_item="B-02", occurrences=len(hits),
                    locations=[{'page':p,'text':unique_quote(t,a,b),'start':a,'end':b} for p,t,a,b in hits],
                ))

        if contrast_hits and contrast and contrast.get("comprehensive_contrast"):
            summary = _contrast_summary(contrast)
            pages_text = "、".join(str(p) for p in summary["opposite_pages"]) or "未定位"
            first_page, first_text, first_start, first_end = contrast_hits[0]
            issues.append(issue_at(
                self,
                f"材料标题与表注均为{_contrast_label(summary['other_scope'])}写法，与所选层级（{_contrast_label(scope)}）相反，"
                f"请核对归档（反向写法见第{pages_text}页）。",
                first_page, first_text, first_start, first_end, severity="manual_review", checklist_item="B-02",
                occurrences=len(contrast_hits),
                locations=[{'page':p,'text':unique_quote(t,a,b),'start':a,'end':b} for p,t,a,b in contrast_hits],
                writing_contrast=summary,
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
        if not scope and _uses_writing_resolution(doc):
            detail = "写法口径未确认；主体用语子项未完成"
            resolution = getattr(getattr(doc, "document_profile", None), "writing_scope", {}) or {}
            if resolution.get("archive_subject_mismatch"):
                detail += "；封面主体与所选组织不一致，请核对归档"
            raise RuleDeferred(self.code, detail=detail, partial_issues=issues)
        if not scope and not issues:
            raise RuleNotApplicable(self.code, detail="未能从目录、正文或文件名确定部门/单位口径")
        return issues


# ---------------------------------------------------------------------------
# CMM-019 封面要素
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
#: 表题/说明标题关键词。年份与关键词只要求在同一页的邻近窗口内，
#: 不再要求同一行——PDF 的标题经常在“2025”和“年度决算表”之间换行。
_TITLE_CONTEXT_RE = re.compile(r"(?:决算|预算)(?:表|情况说明|编制说明)|预算总表|分类预算表")
_TITLE_WINDOW = 20


def body_year(doc: Document) -> Optional[int]:
    """表题与说明标题中的年度众数（不看封面）。

    旧实现逐行扫描，``2025\\n年度决算表`` 这种跨行标题会被漏计，导致
    “封面年度缺少正文独立依据”的假取数不足。改为按页 compact 后，
    只要求年份与标题关键词落在同一页的 ±20 字窗口内；仍然不看封面页，
    也仍然要求至少出现 2 次，避免单个残留年份被当成报告年度。
    """
    counts: Counter = Counter()
    for text in list(doc.page_texts or [])[1:]:
        flat = compact(text)
        for match in _YEAR_RE.finditer(flat):
            start = max(0, match.start() - _TITLE_WINDOW)
            end = min(len(flat), match.end() + _TITLE_WINDOW)
            if _TITLE_CONTEXT_RE.search(flat[start:end]):
                counts[int(match.group(1))] += 1
    if not counts:
        return None
    year, count = counts.most_common(1)[0]
    return year if count >= 2 else None


class CMM019_CoverElements(Rule):
    code, severity = "CMM-019", "warn"
    desc = "封面要素：单位全称、年度、文种与区级标识（预/决算通用，清单 A-02）"

    def apply(self, doc: Document) -> List[Issue]:
        doc = prepare_identity_document(doc) if _uses_writing_resolution(doc) else doc
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
        incomplete: List[str] = []

        def _emit(message: str, start: int, end: int, severity: str = "warn", **extra: Any) -> None:
            issues.append(issue_at(self, message, 1, cover, start, end, severity=severity, checklist_item="A-02", **extra))

        # 年度
        cover_years = {int(y) for y in _YEAR_RE.findall(flat)}
        expected_year = body_year(doc) if _uses_writing_resolution(doc) else (body_year(doc) or report_year(doc))
        if _uses_writing_resolution(doc) and not expected_year:
            incomplete.append("封面年度缺少正文独立依据")
        if not cover_years:
            _emit("封面未写明年度（应为“××××年（度）…预算/决算”）。", *anchor)
        elif expected_year and expected_year not in cover_years:
            year = sorted(cover_years)[0]
            pos = cover.find(str(year))
            _emit(f"封面年度有误：封面写作{year}年，正文表题与说明为{expected_year}年。", max(pos, 0), max(pos, 0) + 4)

        # 单位全称
        canonical = canonical_org_name(doc)
        if _uses_writing_resolution(doc):
            canonical = _independent_body_name(doc)
            if not canonical:
                incomplete.append("封面单位名称缺少正文独立依据")
        if canonical and canonical not in flat:
            variant = next((n for n, *_ in name_mentions(doc) if n in flat and _variant_kind(n, canonical)), None)
            if variant:
                pos = cover.find(variant[:2])
                _emit(f"封面单位名称“{variant}”与规范全称“{canonical}”不一致。", max(pos, 0), max(pos, 0) + len(variant))
            else:
                _emit(f"封面未出现单位全称“{canonical}”。", *anchor)

        # 文种（部门/单位）
        resolution = getattr(getattr(doc, "document_profile", None), "writing_scope", {}) or {}
        scope = resolution.get("cover_independent_scope") if _uses_writing_resolution(doc) else body_scope(doc)
        contrast = _archive_writing_contrast(doc)
        noun = "预算" if kind == "budget" else "决算"
        bare_cover = _cover_scope(doc) == "unit" and "部门" not in flat and "单位" not in flat
        if bare_cover and scope == "department" and kind == "final":
            # 封面只写“年度决算”且口径来自正文：文种子项证据不足，不据此判“漏写部门”，也不新增待核义务。
            pass
        elif contrast and contrast.get("comprehensive_contrast"):
            # 全面相反：文种子项不出 warn，改出一条待人工复核；年度、名称等其余子项照常执行。
            summary = _contrast_summary(contrast)
            pages_text = "、".join(str(p) for p in summary["opposite_pages"]) or "未定位"
            _emit(f"材料标题与表注均为{_contrast_label(summary['other_scope'])}写法，"
                  f"与所选层级（{_contrast_label(scope)}）相反，请核对归档（反向写法见第{pages_text}页）。",
                  *anchor, severity="manual_review", writing_contrast=summary)
        elif scope == "department":
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
        if _uses_writing_resolution(doc) and not scope:
            incomplete.append("封面文种缺少独立依据")
        if incomplete:
            detail = "；".join(incomplete)
            if resolution.get("archive_subject_mismatch"):
                detail += "；封面主体与所选组织不一致，请核对归档"
            raise RuleDeferred(self.code, detail=detail, partial_issues=issues, unresolved_reasons=incomplete)
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


class CMM027_TemplatePositionScope(Rule):
    code, severity = 'CMM-027', 'warn'
    desc = '模板写法位置一致性（决算，清单 B-02）'

    def apply(self, doc: Document) -> List[Issue]:
        doc = prepare_identity_document(doc) if _uses_writing_resolution(doc) else doc
        if report_kind(doc) != 'final' and not _uses_writing_resolution(doc):
            raise RuleNotApplicable(self.code, detail='该材料未接入经批准的模板位置检查')
        resolution = getattr(getattr(doc, 'document_profile', None), 'writing_scope', {}) or {}
        snapshot = resolution.get('snapshot') or {}
        if not snapshot.get('available') and '校验失败' in str(snapshot.get('reason')):
            raise RuleDeferred(self.code, detail='模板依据校验失败，位置检查未完成：'+snapshot['reason'])
        if (snapshot.get('compatibility_pending') or {}).get('position_checks'):
            raise RuleDeferred(self.code, detail='模板位置能力兼容依据待核：'+snapshot['compatibility_pending']['position_checks'])
        if not snapshot.get('available') or not (snapshot.get('capability_scope') or {}).get('position_checks'):
            raise RuleNotApplicable(self.code, detail='该年度模板位置检查未启用；既有规范检查继续执行；'+str(snapshot.get('reason') or '年度能力未获准'))
        if resolution.get('status') != 'confirmed':
            raise RuleDeferred(self.code, detail='模板位置冲突或写法依据不足，待人工分维度确认')
        value = resolution['value']
        groups = {}
        for item in resolution.get('evidence', []):
            # 封面缺陷由 CMM-019 统一报告，保留本规则回执以免重复确认。
            if item.get('page', 0) <= 1 or item.get('scope') == value or item.get('kind') not in ('heading','table_note','budget_financial_table','budget_function_table'):
                continue
            groups.setdefault(item['kind'], []).append(item)
        issues=[]
        for kind, positions in groups.items():
            first=positions[0]
            page=first['page']
            text=(doc.page_texts or [])[page-1]
            issues.append(issue_at(self, f"模板口径位置混用：{'表注' if kind == 'table_note' else '标题'}采用反向写法，共{len(positions)}处；写法基准为{'单位' if value == 'unit' else '部门'}。",
                page,text,first.get('start',0),first.get('end',len(first['text'])),checklist_item='B-02',
                occurrences=len(positions),locations=positions,
                table_refs=[{'page':p['page'],'span':p['text'],'role':'反向写法原文'} for p in positions],template_package=snapshot.get('package_id'),
                binding_revision=snapshot.get('revision'),writing_source=resolution.get('source')))
        return issues
