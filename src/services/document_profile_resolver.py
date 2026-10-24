"""报告画像的唯一解析器。

为什么必须"唯一"
----------------
整改前，材料识别结果由 8 处代码各自产出，而且兜底口径互相矛盾：
``src/engine/common_rules.py`` 识别不到时默认 ``"final"``，
``src/engine/pipeline.py`` 默认 ``"unknown"``，``api/runtime.extract_cover_metadata``
又是另一套"封面标签 > 封面标题 > 文种/文件名"的顺序。同一份 PDF 在上传、
规则执行、通用规则、入库、展示各环节可能得到不同文种，而"适用哪些检查"
正是由文种决定的——猜错文种等于整类专项检查静默不执行，结果里却看不出
任何异常。这就是 plan 要求"同一 PDF 在各环节使用同一个报告画像"的原因。

本模块产出 ``src/schemas/document_profile.DocumentProfile``，其他环节一律
消费它。既有函数（``api.runtime.normalize_report_kind``、
``api.runtime.extract_cover_metadata``、``pipeline._resolve_report_kind``、
``engine_rule_runner._resolve_report_kind``、``common_rules._infer_report_kind``）
保留原签名，内部改为调用本解析器，确保口径不可能再次漂移。

候选优先级
----------
显式传入 > 封面结构化标签 > 封面标题 > 上传文种 > 文件名 > 正文首页。

与旧实现的刻意差异（都是"减少静默猜错"的方向）：
1. 旧 ``normalize_report_kind`` 把文种与文件名放进同一个预算优先的判断里，
   于是"用户选了决算、文件名带预算"会静默判成预算。现在按来源优先级取值，
   并把落选候选写进 ``rejected``。
2. 旧 ``common_rules._infer_report_kind`` 在整条路径字符串上找关键词，
   仓库目录名 ``GovBudgetChecker`` 自带 "Budget"，很容易把任何材料判成预算。
   现在只看文件名基名，与 ``pipeline`` 的既有修复保持一致。
3. 旧 ``common_rules`` 兜底 ``"final"``。现在一律 ``"unknown"``，
   并让质量门显式提示"该报告类型尚未完整支持"。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.schemas.document_profile import (
    CALIBERS,
    FUND_SCOPE_GENERAL,
    FUND_SCOPE_GOVERNMENT_FUND,
    FUND_SCOPE_STATE_CAPITAL,
    PROFILE_RESOLVER_VERSION,
    PROFILE_STATUS_PARTIAL,
    PROFILE_STATUS_RESOLVED,
    PROFILE_STATUS_UNRESOLVED,
    REPORT_KINDS,
    SOURCE_COVER_LABEL,
    SOURCE_COVER_TITLE,
    SOURCE_DERIVED,
    SOURCE_DOC_TYPE,
    SOURCE_EXPLICIT,
    SOURCE_FILENAME,
    SOURCE_PAGE_TEXT,
    SOURCE_TABLE_TITLE,
    SOURCE_UNRESOLVED,
    DocumentProfile,
    ProfileField,
)
from src.utils.report_year import parse_report_year

# 封面机构标签：(标签, 主体层级, 文种)。词序即匹配优先级。
# 这四条是从 api/runtime 原实现逐字搬过来的，顺序不可改动——
# 改为更"自然"的顺序会改变既有材料的层级/文种取值（回归测试会挡住）。
# 政府层级的识别不走这里，见下面的 _GOVERNMENT_LABEL_TOKENS：
# 政府材料没有这种"标签：机构名"的封面写法，只能按关键词识别。
COVER_ORG_LABELS: Tuple[Tuple[str, str, str], ...] = (
    ("预算主管部门", "department", "budget"),
    ("预算单位", "unit", "budget"),
    ("决算主管部门", "department", "final"),
    ("决算单位", "unit", "final"),
)

#: 政府层级标签：出现"区人民政府/区政府/市财政局"等表述时按政府级处理。
_GOVERNMENT_LABEL_TOKENS = (
    "人民政府",
    "财政局",
    "政府本级",
    "政府预算",
    "政府决算",
)

#: 资金范围关键词（按资金口径归一值分组）。文档级扫描用，出现即登记。
_FUND_SCOPE_TOKENS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (FUND_SCOPE_GOVERNMENT_FUND, ("政府性基金",)),
    (FUND_SCOPE_STATE_CAPITAL, ("国有资本经营",)),
    (FUND_SCOPE_GENERAL, ("一般公共预算", "公共财政预算")),
)

#: 汇总口径关键词。``汇总`` 表示含下级单位，``本级``/``本单位`` 表示仅本级。
_CALIBER_SUMMARY_TOKENS = ("汇总", "含下级", "所属单位合计", "全区合计")
_CALIBER_SELF_TOKENS = ("本级", "本单位", "机关本级")

#: 行政区划名：``上海市普陀区`` 这类前缀。只取"省/市/区/县"结尾的连续词，
#: 识别不到就是 None——画像宁可空着，也不能编一个地区出来。
_JURISDICTION_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,8}(?:省|自治区|特别行政区))?"
    r"([\u4e00-\u9fa5]{2,6}(?:市|自治州|地区))?"
    r"([\u4e00-\u9fa5]{2,6}(?:区|县|旗|市))?"
)

#: 抽取质量判定：低于该比例即认为文本层覆盖不足（与 api/main 的门禁阈值同源口径）。
_DEFAULT_TEXT_COVERAGE_THRESHOLD = 0.8


def normalize_cover_line(raw: Any) -> str:
    """封面行归一：去掉全部空白，便于在中文标签中做包含判断。"""
    return re.sub(r"\s+", "", str(raw or "").strip())


def government_mixed_period_title(page_texts: Sequence[str]) -> str:
    """原件会议标题与受政府委托的正文共同证明政府混合期间报告。"""
    first = str(page_texts[0] or '') if page_texts else ''
    heading = normalize_cover_line('\n'.join(first.splitlines()[:12]))
    match = re.search(r'关于[\u4e00-\u9fff]{2,40}20\d{2}年(?:财政)?决算(?:和|及|与)'
                      r'20\d{2}年(?:上半年|下半年)?预算执行情况(?:的)?报告', heading)
    body = normalize_cover_line(first)
    if (match and '人民代表大会' in body
            and re.search(r'受[^。；]{0,20}人民政府委托', body)
            and not re.search(r'部门决算|单位决算|决算主管部门|决算单位', heading)):
        return match.group()
    return ''


def _split_basename(path: str) -> str:
    """跨平台取文件名基名。

    ``pathlib.Path(...).name`` 在 Linux 上处理 Windows 路径（``E:\\a\\b.pdf``）
    时会把整串当文件名返回，于是仓库目录名里的 "GovBudgetChecker" 会参与
    关键词判断。这里显式按两种分隔符切分，避免平台差异导致路由漂移。
    """
    text = str(path or "")
    if not text:
        return ""
    return re.split(r"[\\/]", text)[-1]


def _detect_cover_scope_hint(text: str) -> Optional[str]:
    compact = normalize_cover_line(text)
    if not compact:
        return None
    if (
        "\u4e3b\u7ba1\u90e8\u95e8" in compact
        or "\u90e8\u95e8\u9884\u7b97" in compact
        or "\u90e8\u95e8\u51b3\u7b97" in compact
    ):
        return "department"
    if "\u9884\u7b97\u5355\u4f4d" in compact or "\u51b3\u7b97\u5355\u4f4d" in compact:
        return "unit"
    if (
        "\u5355\u4f4d\u9884\u7b97" in compact
        or "\u5355\u4f4d\u51b3\u7b97" in compact
        or "\u672c\u7ea7" in compact
    ):
        return "unit"
    if "\u90e8\u95e8" in compact:
        return "department"
    if "\u5355\u4f4d" in compact:
        return "unit"
    return None


def _detect_cover_report_kind(text: str) -> Optional[str]:
    """从封面标题判定文种。

    "调整预算"归入预算口径：调整预算仍是预算文种，只是版本不同，
    适用同一套预算检查配置。
    """
    compact = normalize_cover_line(text)
    if not compact:
        return None
    if "预算" in compact or "budget" in compact.lower():
        return "budget"
    if "决算" in compact or "final" in compact.lower():
        return "final"
    return None


def _kind_from_doc_type(doc_type: Any) -> Optional[str]:
    text = str(doc_type or "").strip().lower()
    if not text:
        return None
    if "budget" in text or "预算" in text:
        return "budget"
    if "final" in text or "settlement" in text or "accounts" in text or "决算" in text:
        return "final"
    return None


def _kind_from_filename(filename: str) -> Optional[str]:
    """文件名判定。保留"预算优先于决算"的既有口径。

    文件名形如《…预算执行情况与决算草案…》时两种词都会出现，既有实现
    统一判为预算，这里保持一致以免引起路由回归；命中两个词时记入冲突。
    """
    lowered = str(filename or "").lower()
    if not lowered:
        return None
    has_budget = "budget" in lowered or "预算" in lowered
    has_final = "final" in lowered or "决算" in lowered
    if has_budget:
        return "budget"
    if has_final:
        return "final"
    return None


def _kind_from_page_texts(page_texts: Sequence[str]) -> Optional[str]:
    first_text = page_texts[0] if page_texts else ""
    if not first_text:
        return None
    has_budget = "预算" in first_text or "budget" in first_text.lower()
    has_final = "决算" in first_text or "final" in first_text.lower()
    if has_budget:
        return "budget"
    if has_final:
        return "final"
    return None


def _pick(
    name: str,
    candidates: Sequence[Tuple[str, Any, str]],
    *,
    normalize: Optional[Any] = None,
) -> ProfileField:
    """按给定顺序取第一个有值候选，其余进 ``rejected``。

    ``candidates`` 顺序即优先级；同一优先级出现互斥取值时不做静默取舍，
    全部保留在 ``rejected`` 里供人工确认。
    """
    chosen: Optional[Tuple[str, Any, str]] = None
    rejected: List[Dict[str, Any]] = []
    for source, value, evidence in candidates:
        if value is None or value == "" or value == []:
            continue
        normalized_value = normalize(value) if normalize else value
        if normalized_value is None or normalized_value == "":
            continue
        if chosen is None:
            chosen = (source, normalized_value, evidence)
            continue
        rejected.append({"source": source, "value": normalized_value, "evidence": evidence})
    if chosen is None:
        return ProfileField(value=None, source=SOURCE_UNRESOLVED)
    return ProfileField(value=chosen[1], source=chosen[0], evidence=chosen[2], rejected=rejected)


def _detect_fund_scopes(page_texts: Sequence[str], table_titles: Sequence[str]) -> List[str]:
    """扫描正文与表题，登记材料中出现的资金范围。

    空列表表示"未识别到任何资金范围关键词"，下游据此按"无法确认"处理，
    不会把空列表当成"确认没有政府性基金"。
    """
    haystack = "\n".join([*page_texts, *table_titles])
    if not haystack:
        return []
    found: List[str] = []
    for scope, tokens in _FUND_SCOPE_TOKENS:
        if any(token in haystack for token in tokens):
            found.append(scope)
    return found


def _detect_caliber(
    page_texts: Sequence[str], cover_title: str, table_titles: Sequence[str]
) -> Optional[str]:
    head = "\n".join([cover_title, *table_titles, *(page_texts[:2] if page_texts else [])])
    if not head:
        return None
    if any(token in head for token in _CALIBER_SUMMARY_TOKENS):
        return "summary"
    if any(token in head for token in _CALIBER_SELF_TOKENS):
        return "self"
    return None


def _detect_jurisdiction(*texts: str) -> Optional[str]:
    for text in texts:
        compact = normalize_cover_line(text)
        if not compact:
            continue
        match = _JURISDICTION_RE.search(compact)
        if not match:
            continue
        parts = [group for group in match.groups() if group]
        if parts:
            return "".join(parts)
    return None


def _detect_extraction_quality(
    page_assessment: Optional[Mapping[str, Any]],
    *,
    threshold: float = _DEFAULT_TEXT_COVERAGE_THRESHOLD,
) -> Optional[str]:
    """由页面抽取评估推导文本层质量。没有页面评估时不猜，返回 None。"""
    if not isinstance(page_assessment, Mapping):
        return None
    page_count = int(page_assessment.get("page_count") or 0)
    if page_count <= 0:
        return "unknown"
    try:
        coverage = float(page_assessment.get("page_coverage") or 0.0)
        scanned = int(page_assessment.get("scanned_page_count") or 0)
    except (TypeError, ValueError):
        return "unknown"
    if coverage <= 0.0:
        return "scanned"
    if scanned == 0 and coverage >= threshold:
        return "text"
    return "mixed"


def resolve_document_profile(
    *,
    explicit_report_kind: Optional[str] = None,
    doc_type: Optional[str] = None,
    filename: str = "",
    path: str = "",
    page_texts: Optional[Sequence[str]] = None,
    table_titles: Optional[Sequence[str]] = None,
    cover_title: str = "",
    cover_org_name: str = "",
    cover_org_label: str = "",
    cover_scope_hint: Optional[str] = None,
    preferred_year: Any = None,
    page_assessment: Optional[Mapping[str, Any]] = None,
    text_coverage_threshold: float = _DEFAULT_TEXT_COVERAGE_THRESHOLD,
) -> DocumentProfile:
    """解析一份材料的报告画像。

    ``path`` 与 ``filename`` 都接受：调用方历史上有的用完整路径、有的用文件名，
    解析器内部统一取基名，避免"仓库目录名参与判断"的老问题。
    """
    pages: List[str] = [str(item or "") for item in (page_texts or [])]
    titles: List[str] = [str(item or "") for item in (table_titles or [])]
    basename = _split_basename(filename) or _split_basename(path)

    # 封面原始事实由解析器自己抽取（而不是要求调用方先抽好再传进来）：
    # 规则引擎入口只拿得到 path + page_texts，若把封面抽取留在 api 层，
    # 那两处入口就只能按文件名/首页前几字判文种，又会与上传环节不一致。
    # 调用方已经抽好时直接复用，避免重复解析。
    if not cover_title and not cover_org_label and pages:
        facts = detect_cover_facts(pages)
        cover_title = facts["cover_title"] or cover_title
        cover_org_name = facts["cover_org_name"] or cover_org_name
        cover_org_label = facts["cover_org_label"] or cover_org_label
        cover_scope_hint = cover_scope_hint or (facts["cover_scope_hint"] or None)
    elif not cover_org_name and pages:
        facts = detect_cover_facts(pages)
        cover_org_name = facts["cover_org_name"]
        cover_org_label = cover_org_label or facts["cover_org_label"]

    explicit = str(explicit_report_kind or "").strip().lower()
    explicit_kind = explicit if explicit in REPORT_KINDS and explicit != "unknown" else None

    label_kind = None
    for label, _scope, kind in COVER_ORG_LABELS:
        if cover_org_label and label == cover_org_label and kind:
            label_kind = kind
            break

    report_kind = _pick(
        "report_kind",
        (
            (SOURCE_EXPLICIT, explicit_kind, "调用方显式指定"),
            (SOURCE_COVER_LABEL, label_kind, f"封面标签：{cover_org_label}"),
            (
                SOURCE_COVER_TITLE,
                _detect_cover_report_kind(cover_title),
                f"封面标题：{cover_title[:40]}",
            ),
            (SOURCE_DOC_TYPE, _kind_from_doc_type(doc_type), f"上传文种：{doc_type}"),
            (SOURCE_FILENAME, _kind_from_filename(basename), f"文件名：{basename[:60]}"),
            (
                SOURCE_PAGE_TEXT,
                _kind_from_page_texts(pages),
                "正文首页文本",
            ),
        ),
    )

    conflicts: List[Dict[str, Any]] = []
    if report_kind.rejected:
        distinct = {item["value"] for item in report_kind.rejected}
        if distinct - {report_kind.value}:
            conflicts.append(
                {
                    "field": "report_kind",
                    "selected": report_kind.value,
                    "source": report_kind.source,
                    "candidates": report_kind.rejected,
                    "note": "同一材料出现互斥文种候选，已按来源优先级取值，需人工确认",
                }
            )

    scope_hint = str(cover_scope_hint or "").strip() or (
        _detect_cover_scope_hint(cover_title) or ""
    )
    # 政府层级只从**封面标题**判定，不看机构名。
    # 机构名里出现"人民政府"极常见（如"…人民政府石泉路街道办事处（本级）"
    # 是街道单位的预算），据此判成政府级会把部门的材料错分成政府口径，
    # 比识别不到更危险。政府级材料的标题会直接写"政府预算/政府决算"。
    normalized_title = normalize_cover_line(cover_title)
    mixed_government_title = government_mixed_period_title(pages)
    if mixed_government_title and not cover_org_label:
        scope_hint = 'government'
    if any(token in normalized_title for token in _GOVERNMENT_LABEL_TOKENS):
        # ZC-02（SYS-17）：连写封面"上海市普陀区财政局2024年度部门决算"是
        # 财政局自身的**部门决算**，不是政府总决算——标题明示"部门"时，
        # 机构名里的"财政局"不得把主体覆盖成政府级。
        if "部门" not in normalized_title:
            scope_hint = "government"

    # 主体层级：政府级 > 封面标签/标题 > 机构名后缀推断。
    level_candidates: List[Tuple[str, Any, str]] = []
    if scope_hint:
        level_candidates.append((SOURCE_COVER_TITLE, scope_hint,
                                 f"原件会议报告标题：{mixed_government_title}" if mixed_government_title else f"封面提示：{scope_hint}"))
    subject_level = _pick(
        "subject_level",
        tuple(level_candidates),
        normalize=lambda value: value if value in {"department", "unit", "government"} else None,
    )

    year_value = preferred_year
    year_source = SOURCE_EXPLICIT
    parsed_year = parse_report_year(year_value) if year_value is not None else None
    if parsed_year is None:
        # 年度解析下沉到 src/utils/report_year.py 的单一实现，
        # 这里只是把"从哪来"记进画像（封面/文件名/正文按可信度排序）。
        for source, candidate in (
            (SOURCE_FILENAME, basename),
            (SOURCE_COVER_TITLE, cover_title),
            (SOURCE_PAGE_TEXT, pages[0] if pages else ""),
        ):
            parsed_year = parse_report_year(candidate)
            if parsed_year is not None:
                year_source = source
                break
    report_year = ProfileField(
        value=parsed_year,
        source=year_source if parsed_year is not None else SOURCE_UNRESOLVED,
        evidence="显式指定" if year_source == SOURCE_EXPLICIT and parsed_year else "",
    )

    table_org_name, table_org_pages = _table_header_org(pages)
    organization = _pick(
        "organization_name",
        ((SOURCE_COVER_LABEL if cover_org_label else SOURCE_PAGE_TEXT,
          str(cover_org_name or "").strip(),
          f"封面标签：{cover_org_label}" if cover_org_label else f"封面原文：{cover_org_name}"),
         (SOURCE_TABLE_TITLE, table_org_name, f"表页编制部门标签一致：{table_org_pages}页")),
    )
    caliber_value = _detect_caliber(pages, cover_title, titles)
    caliber = ProfileField(
        value=caliber_value if caliber_value in CALIBERS else None,
        source=SOURCE_COVER_TITLE if caliber_value else SOURCE_UNRESOLVED,
        evidence="封面或表题含汇总/本级口径词" if caliber_value else "",
    )

    fund_scope_values = _detect_fund_scopes(pages, titles)
    fund_scopes = ProfileField(
        value=fund_scope_values,
        source=SOURCE_PAGE_TEXT if fund_scope_values else SOURCE_UNRESOLVED,
        evidence="正文或表题中出现资金范围关键词" if fund_scope_values else "",
    )

    jurisdiction_value = _detect_jurisdiction(cover_title, cover_org_name, basename)
    jurisdiction = ProfileField(
        value=jurisdiction_value,
        source=SOURCE_COVER_TITLE if jurisdiction_value else SOURCE_UNRESOLVED,
        evidence="封面或文件名中的行政区划名" if jurisdiction_value else "",
    )

    quality_value = _detect_extraction_quality(page_assessment, threshold=text_coverage_threshold)
    extraction_quality = ProfileField(
        value=quality_value,
        source=SOURCE_DERIVED if quality_value else SOURCE_UNRESOLVED,
        evidence="由页面抽取评估推导" if quality_value else "",
    )

    resolved_kind = str(report_kind.value or "unknown")
    resolved_level = str(subject_level.value or "unknown")

    if mixed_government_title and resolved_level == 'government':
        profile_status = PROFILE_STATUS_PARTIAL
        unsupported_reason = 'government_mixed_period_report'
    elif resolved_kind == "unknown":
        profile_status = PROFILE_STATUS_UNRESOLVED
        unsupported_reason = "report_kind_unresolved"
    elif conflicts:
        # 文种有取值，但存在互斥候选：按优先级取的只是"候选选择"，
        # 不是"已确认"（独立验收 2026-09-17 conflicting_profile 反例：
        # 画像记录了"文种冲突，需人工确认"，profile_status 却是 resolved）。
        # conflicts 目前只有 report_kind 维度会写入；将来其他维度记录冲突
        # 时，这里按字段过滤，避免误伤。
        profile_status = PROFILE_STATUS_PARTIAL
        unsupported_reason = (
            "report_kind_conflict"
            if any(str(item.get("field")) == "report_kind" for item in conflicts)
            else None
        )
    elif resolved_level == "unknown":
        profile_status = PROFILE_STATUS_PARTIAL
        unsupported_reason = None
    else:
        profile_status = PROFILE_STATUS_RESOLVED
        unsupported_reason = None

    return DocumentProfile(
        report_kind=report_kind,
        report_year=report_year,
        organization_name=organization,
        organization_label=ProfileField(
            value=str(cover_org_label or "").strip() or None,
            source=SOURCE_COVER_LABEL if cover_org_label else SOURCE_UNRESOLVED,
        ),
        subject_level=subject_level,
        caliber=caliber,
        jurisdiction=jurisdiction,
        fund_scopes=fund_scopes,
        extraction_quality=extraction_quality,
        cover_title=ProfileField(
            value=str(cover_title or "").strip() or None,
            source=SOURCE_COVER_TITLE if cover_title else SOURCE_UNRESOLVED,
        ),
        profile_status=profile_status,
        unsupported_reason=unsupported_reason,
        resolver_version=PROFILE_RESOLVER_VERSION,
        conflicts=conflicts,
    )


def resolve_report_kind_from_path(
    path: str,
    page_texts: Sequence[str],
    explicit_report_kind: Optional[str] = None,
) -> str:
    """规则引擎入口用的轻量包装（只关心文种）。

    保留这个薄封装是为了让 ``pipeline`` / ``engine_rule_runner`` / ``common_rules``
    三处入口走同一个解析器，同时不必各自拼装完整画像。
    """
    profile = resolve_document_profile(
        path=path,
        page_texts=page_texts,
        explicit_report_kind=explicit_report_kind,
    )
    return profile.kind


_TABLE_ORG_LABEL_RE = re.compile(r"(?:编制部门|编制单位)[：:]\s*([^\s：:]+)")


def _table_header_org(page_texts: Sequence[str]) -> Tuple[str, int]:
    """封面没有主体时的标签兜底：各表页「编制部门：X」一致才采用。

    这是表页明示的标签，不是从正文提及或文件名推断；至少两页出现且只有
    一个取值，否则不取（多主体汇总材料由封面或人工确认）。
    """
    names = [match.group(1).strip()
             for text in list(page_texts)[1:]
             for match in _TABLE_ORG_LABEL_RE.finditer(str(text or ""))
             if match.group(1).strip()]
    if len(names) >= 2 and len(set(names)) == 1:
        return names[0], len(names)
    return "", 0


#: 封面单位名折行判断用的机构后缀：名称未以后缀收尾、接上下一行短残段后才收尾，就是折行。
_COVER_ORG_SUFFIX_RE = re.compile(
    r"(?:委员会|办公厅|办公室|局|厅|部|医院|学校|校|幼儿园|学院|大学|中心|院|所|站|馆|队|会|委|室|处|团|社|台|场|园|宫)\)?$")


def _open_brackets(text: str) -> int:
    return text.count("（") + text.count("(") - text.count("）") - text.count(")")


def detect_cover_facts(page_texts: Sequence[str]) -> Dict[str, str]:
    """从首页提取封面原始事实（标题、机构名、标签、层级提示）。

    只做事实抽取，不做文种/年度结论——结论一律由 ``resolve_document_profile``
    统一给出，避免"抽取"和"判定"两处分头演化。
    """
    pages = [str(text or "").strip() for text in (page_texts or [])]
    first_page_text = pages[0] if pages else ""
    lines = [line.strip() for line in first_page_text.splitlines() if line.strip()]

    cover_title = ""
    for index, line in enumerate(lines[:19]):
        if re.search(r"20\d{2}年度?(?:部门|单位)?$", normalize_cover_line(line)) and index + 1 < len(lines):
            following = normalize_cover_line(lines[index + 1])
            if following in {"预算", "决算", "部门预算", "部门决算", "单位预算", "单位决算"}:
                cover_title = line + "\n" + lines[index + 1]
                break
    for line in lines[:20]:
        if cover_title:
            break
        compact = normalize_cover_line(line)
        if not compact:
            continue
        if ("\u9884\u7b97" in compact or "\u51b3\u7b97" in compact) and (
            "\u90e8\u95e8" in compact or "\u5355\u4f4d" in compact
        ):
            cover_title = line.strip()
            break
    if not cover_title:
        for line in lines[:20]:
            compact = normalize_cover_line(line)
            if "\u9884\u7b97" in compact or "\u51b3\u7b97" in compact:
                cover_title = line.strip()
                break

    cover_org_name = ""
    cover_org_label = ""
    scope_hint: Optional[str] = None
    for index, line in enumerate(lines[:40]):
        compact = normalize_cover_line(line)
        if not compact:
            continue
        for label, label_scope, _label_kind in COVER_ORG_LABELS:
            if label not in compact:
                continue
            _, _, remainder = compact.partition(label)
            remainder = re.sub(r"^[\uff1a:]+", "", remainder).strip()
            if not remainder:
                continue
            # 长名称在括号内折行（「…上海渔港监督\n局）」）：只续接到括号闭合，续行不能另起标签。
            for following in lines[index + 1:index + 3]:
                following = normalize_cover_line(following)
                if re.search(r"[:：]", following) or len(following) > 30 or re.search(r"\d", following):
                    break
                # 「…综合执\n法大队」：括号已闭合时，只有接上短残段才以机构后缀收尾才续接。
                broken_name = (not _COVER_ORG_SUFFIX_RE.search(remainder) and len(following) <= 6
                               and _COVER_ORG_SUFFIX_RE.search(remainder + following))
                if _open_brackets(remainder) <= 0 and not broken_name:
                    break
                remainder += following
            cover_org_name = remainder
            cover_org_label = label
            scope_hint = label_scope
            break
        if cover_org_name:
            break

    # Publisher labels state an owner directly but do not, by themselves,
    # determine budget versus final. Keep the existing four labels first.
    if not cover_org_name:
        candidates = []
        for line in lines[:40]:
            match = re.fullmatch(
                r"(编制部门|编制单位|发布单位)[：:]([^：:]+)", normalize_cover_line(line)
            )
            if match and match.group(2).strip():
                candidates.append(match.groups())
        if len({name for _label, name in candidates}) == 1:
            cover_org_label, cover_org_name = candidates[0]
            scope_hint = {"编制部门": "department", "编制单位": "unit"}.get(cover_org_label)

    if not cover_org_name and cover_title:
        # Unlabelled covers put the actual publisher immediately before the
        # annual heading. Never infer it from a filename or later body mentions.
        title_index = next((i for i, line in enumerate(lines[:20]) if line == cover_title.splitlines()[0]), None)
        prefix = (re.split(r"20\d{2}", normalize_cover_line(cover_title), maxsplit=1)[0]
                  if re.search(r"20\d{2}", cover_title) else "")
        # 封面主体可能后接多行括号别名。先分离成对括号，再恢复相邻主名称；
        # 括号必须闭合，多个完整机构名并列时不擅自选择。
        previous = lines[:title_index] if title_index is not None else []
        block = "".join(normalize_cover_line(line) for line in previous if not line.isdigit()) + prefix
        block = block.replace("（", "(").replace("）", ")")
        # “室”与“局/所”等同为机构后缀；仍只接受年度标题前的封面原文，
        # 并沿用下方的多主体/括号完整性检查，不能从正文或文件名补出发布者。
        suffix = r"(?:委员会|办公厅|办公室|管理局|监督管理局|财政局|局|厅|部|医院|学校|中学|小学|校|幼儿园|红十字会|学院|大学|中心|人民法院|人民检察院|办事处|秘书处|街道|镇|乡|馆|队|会|委|宫|所|站|院|园|政府|室|监狱|团|社|场|台|基地)"
        def valid(name):
            return bool(re.fullmatch(r"[\u4e00-\u9fff]{3,100}" + suffix, name)) and not re.search(r"^关于|目录|附件|部分|项目支出|情况|说明|预算|决算", name)
        balanced = block.count("(") == block.count(")") and "(" not in re.sub(r"\([^()]*\)", "", block)
        clean = re.sub(r"\([^()]*\)", "", block) if balanced else ""
        clean = re.sub(r"本级$", "", clean)
        adjacent = [normalize_cover_line(line) for line in previous if not line.isdigit()]
        if prefix:
            adjacent.append(prefix)
        full_names = [name for name in adjacent if valid(name)]
        standalone_names = [name for name in adjacent
                            if re.fullmatch(r"[\u4e00-\u9fff]{1,100}" + suffix, name)
                            and not re.search(r"目录|附件|部分|项目支出|情况|说明|预算|决算", name)
                            # “办公室/研究室”等短后缀行不能独立指明机构，
                            # 否则给室后缀补覆盖会把原有“委员会\n办公室”误拆为两家。
                            and not (name.endswith("室") and not valid(name))
                            and not name.endswith(("人民政府", "区委", "县委", "市委", "省委", "党委", "街道", "镇", "乡"))]
        # 人大封面经常在“人民代表大会/常务委员会办公室”之间断行。
        # 后半是同一机构的职能后缀，不能当作第二个独立发布机构。
        if (len(adjacent) >= 2 and adjacent[-2].endswith("人民代表大会")
                and adjacent[-1] == "常务委员会办公室"):
            standalone_names = [clean]
        # 跨行主名称的前半可能本身也是机构后缀（基金会/幼儿园）。仅当
        # 该封面原文组合在后续概况/表目录的主体标题中再次明确出现才合并。
        corroborated = valid(clean) and any(re.search(
            r"(?:第一部分|第二部分|编制单位[:：]|发布单位[:：])" + re.escape(clean) + r"(?:概况|20\d{2})",
            normalize_cover_line(page),
        ) for page in pages[1:3])
        # 一个主名称加括号别名，或本级口径括号，主体取括号外原文。
        if balanced and "(" in block and valid(clean) and len(set(standalone_names)) <= 1:
            cover_org_name = clean
        elif (len(set(standalone_names)) <= 1 or corroborated) and valid(clean):
            # 单独的地域/人民政府行只允许与紧接的机构名组成原文。
            cover_org_name = clean
        elif len(set(full_names)) == 1:
            cover_org_name = full_names[0]

    return {
        "cover_title": cover_title,
        "cover_org_name": cover_org_name,
        "cover_org_label": cover_org_label,
        "cover_scope_hint": scope_hint or "",
    }
