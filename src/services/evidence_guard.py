"""证据链完整性校验与缺证据降级（缺口 P0-07）。

问题：AI 与规则都可能给出"说得像真的、但拿不出原文证据"的问题项。这类条目一旦
按正式结论输出，就是在制造不可复核的结论——与 M1 消除"虚假成功"的目标直接冲突。

本模块在结果落库前逐条校验证据，并按来源分别处理：

- **AI finding 缺证据**：降级为"待复核"（`severity=manual_review`），标记
  `evidence_status=degraded_missing_evidence`，**不计入正式问题数**；
- **规则 finding 缺证据**：保留为正式问题（规则是确定性判定，其 message 本身
  带有可复核的判定依据），但记录告警明细，供后续修规则用。

判定口径（与 PLAN "校验 page_number 与 evidence_text/bbox 非空" 对齐）：
页码可用 **且**（证据文本可用 **或** bbox 可用）才算证据完整。
bbox 与证据文本二者取其一，是因为大量规则命中能定位页码与原文片段但拿不到坐标，
而少数版式类问题只有坐标；只要能把人带回原文的某一处，就算可复核。

文档级规则单列（B1 口径调整，2026-08-28）：``BUD-001``（缺预算表/缺必备说明
章节）是结构性"有没有"判定，问题本身不指向某一页，页码天然缺失——把它们与
可定位问题混在同一个完整率分母里，会让证据留痕最完整的语料也被这类规则拖到
0%。因此完整率统计分两层：

- **可定位类完整率**（门禁指标）：剔除文档级 finding 后的正常口径；
- **文档级 finding**：单独计数（``document_level_total``），不参与可定位类分母。

识别锚是 ``rule_id == BUD-001``（引擎里缺表与缺章节两个分支共用该编号），
而不是"页码缺失"——``engine_rule_runner`` 会把缺失页码折算成 1，
页码信号在历史数据里不可靠。整体完整率（``completeness_rate``）保留全量
分母不变，供趋势对照。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: 缺证据的 AI 问题被降级后写入的状态值
EVIDENCE_STATUS_DEGRADED = "degraded_missing_evidence"
#: 证据完整的问题状态值
EVIDENCE_STATUS_COMPLETE = "complete"
#: 规则来源缺证据时的标记（仍计入正式问题，只是带告警）
EVIDENCE_STATUS_RULE_WARNING = "incomplete_rule_warning"

# ---- 引用真实性核验（ZC-00 / SYS-07）----
# "有证据字段"不等于"有真实证据"：页码必须落在真实页数内，原文引用必须能在
# 所称页面的文本层中找到。无法核验（无文本层/未提供页文本）不算失败，但要
# 如实记录，不允许把"未核验"当"已核验"输出。
EVIDENCE_VERIFICATION_VERIFIED = "verified"      # 原文引用已在所称页面文本中命中
EVIDENCE_VERIFICATION_UNVERIFIED = "unverified"  # 结构完整但无法核验（已记录原因）
EVIDENCE_VERIFICATION_FAILED = "failed"          # 核验失败：页码越界或原文不存在

#: 页码超出文档真实页数
REASON_PAGE_OUT_OF_RANGE = "page_out_of_range"
#: 原文引用在所称页面文本层中找不到
REASON_TEXT_NOT_FOUND_ON_PAGE = "text_not_found_on_page"

#: 文档级规则编号：结构性"缺章节/缺表/页数体量"判定，finding 天然
#: 没有单一页码；完整率统计时单独计数，不进入可定位类分母。
# 这些规则判定的是整份文档的结构/体量，不存在可绑定到某一页的
# 单一证据面：BUD-001 缺表/缺章节、V33-002 九表集合缺失/顺序、
# V33-003 文件页数/体积阈值。把它们硬塞进可定位分母会把“天然无
# 单页定位”伪装成证据缺口；其余规则缺页仍必须计入缺口。
DOCUMENT_LEVEL_RULE_IDS = frozenset({"BUD-001", "V33-002", "V33-003"})

#: 降级条目附加的标签，便于前端与导出识别
EVIDENCE_DEGRADED_TAG = "证据不足待复核"

#: 明细列表写入 meta 的条数上限，避免 status.json 无界膨胀
_DETAIL_LIMIT = 50

_EVIDENCE_TEXT_KEYS = ("text", "text_snippet", "quote", "original", "context")


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _is_valid_bbox(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    coordinates = []
    for item in value:
        if isinstance(item, bool):
            return False
        try:
            coordinate = float(item)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(coordinate):
            return False
        coordinates.append(coordinate)
    return coordinates[2] > coordinates[0] and coordinates[3] > coordinates[1]


def _evidence_items(finding: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    raw = finding.get("evidence")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _has_page(finding: Mapping[str, Any]) -> bool:
    if _positive_int(finding.get("page_number")) is not None:
        return True
    location = finding.get("location")
    if isinstance(location, Mapping):
        if _positive_int(location.get("page")) is not None:
            return True
        # ``pages`` 只有列表/元组形态才代表跨页定位。标量 ``pages`` 在
        # V33-003 中是“文档总页数”，不能误当作 finding 页码。
        raw_pages = location.get("pages")
        if isinstance(raw_pages, (list, tuple)) and any(
            _positive_int(page) is not None for page in raw_pages
        ):
            return True
        table_refs = location.get("table_refs")
        if isinstance(table_refs, (list, tuple)):
            for ref in table_refs:
                if not isinstance(ref, Mapping):
                    continue
                if _positive_int(ref.get("page")) is not None:
                    return True
                ref_pages = ref.get("pages")
                if isinstance(ref_pages, (list, tuple)) and any(
                    _positive_int(page) is not None for page in ref_pages
                ):
                    return True
    return any(
        _positive_int(item.get("page")) is not None for item in _evidence_items(finding)
    )


def _has_evidence_text(finding: Mapping[str, Any]) -> bool:
    if str(finding.get("text_snippet") or "").strip():
        return True
    for item in _evidence_items(finding):
        for key in _EVIDENCE_TEXT_KEYS:
            if str(item.get(key) or "").strip():
                return True
    # 注意：不查询 display.evidence_text——display 可能由 message 派生（历史快照），
    # 而 message 是问题说明不是原文。引用 display 判定证据完整就是让"说明充当原文"。
    return False


def _has_bbox(finding: Mapping[str, Any]) -> bool:
    if _is_valid_bbox(finding.get("bbox")):
        return True
    location = finding.get("location")
    if isinstance(location, Mapping) and _is_valid_bbox(location.get("bbox")):
        return True
    return any(_is_valid_bbox(item.get("bbox")) for item in _evidence_items(finding))


def _normalise_for_match(text: str) -> str:
    """引用匹配归一：只折叠空白，不改动数字、小数点、负号、单位或百分号。

    PDF 文本抽取会在任意位置插入换行/空格，引用与页面文本的逐字比较必须在
    去空白后进行；除此之外不做任何字符改写，避免把"核验通过"变成"改写通过"。
    """
    return "".join(str(text or "").split())


def _evidence_page_of(item: Mapping[str, Any]) -> Optional[int]:
    """证据条目页码：优先 ``page``，统一证据实体回退 ``physical_page``（RC-09）。

    统一证据（EvidenceRef 往返后）只带 ``physical_page``——守卫若只读
    ``page``，就会静默回退到顶层页码，把跨页证据绑到错误的页上核验。
    """
    page = _positive_int(item.get("page"))
    if page is not None:
        return page
    return _positive_int(item.get("physical_page"))


def _claimed_pages(finding: Mapping[str, Any]) -> List[int]:
    """收集 finding 声称的全部页码（页码信号 + 证据条目页码）。"""
    pages: List[int] = []
    primary = _positive_int(finding.get("page_number"))
    if primary is not None:
        pages.append(primary)
    location = finding.get("location")
    if isinstance(location, Mapping):
        page = _positive_int(location.get("page"))
        if page is not None:
            pages.append(page)
        raw_pages = location.get("pages")
        if isinstance(raw_pages, (list, tuple)):
            for item in raw_pages:
                page = _positive_int(item)
                if page is not None:
                    pages.append(page)
        table_refs = location.get("table_refs")
        if isinstance(table_refs, (list, tuple)):
            for ref in table_refs:
                if not isinstance(ref, Mapping):
                    continue
                page = _positive_int(ref.get("page"))
                if page is None:
                    page = _positive_int(ref.get("physical_page"))
                if page is not None:
                    pages.append(page)
    for item in _evidence_items(finding):
        page = _evidence_page_of(item)
        if page is not None:
            pages.append(page)
    return sorted(set(pages))


def _page_text(page_texts: Optional[Sequence[str]], page: int) -> str:
    """取指定物理页（1 起）的文本层；越界或未提供返回空串。"""
    if not page_texts:
        return ""
    if page < 1 or page > len(page_texts):
        return ""
    return str(page_texts[page - 1] or "")


def _quote_found_on_pages(quote: str, pages: Sequence[int], page_texts: Sequence[str]) -> bool:
    """在给定页面集合的文本层中查找归一化引用。"""
    needle = _normalise_for_match(quote)
    if not needle:
        return False
    for page in pages:
        haystack = _normalise_for_match(_page_text(page_texts, page))
        if haystack and needle in haystack:
            return True
    return False


def evaluate_finding_evidence(
    finding: Mapping[str, Any],
    *,
    document_page_count: Optional[int] = None,
    page_texts: Optional[Sequence[str]] = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """校验单条 finding 的证据链，含真实页数边界与原文引用核验。

    Args:
        finding: 待校验的 finding 字典。
        document_page_count: 文档真实物理页数；提供后页码越界视为证据无效。
        page_texts: 逐页文本层（下标 0 = 第 1 页）；提供后原文引用必须能在
            所称页面文本中命中，否则视为证据无效。扫描页等无文本层的情况
            记为"无法核验"，不判失败。

    Returns:
        ``(证据是否完整, 缺失原因码列表, 核验结果)``。结构缺失原因码：
        ``missing_page`` / ``missing_evidence_text`` / ``missing_bbox``；
        核验失败原因码：``page_out_of_range`` / ``text_not_found_on_page``。
        证据完整时也可能返回 ``missing_bbox``——它只说明"没有坐标"，
        不影响完整性判定。核验结果 ``status`` 取
        ``verified`` / ``unverified`` / ``failed``，``reasons`` 记录
        无法核验的具体原因。
    """
    missing: List[str] = []
    verification: Dict[str, Any] = {
        "status": EVIDENCE_VERIFICATION_UNVERIFIED,
        "reasons": [],
        "checked_pages": [],
    }

    claimed_pages = _claimed_pages(finding)
    has_page = _has_page(finding)

    # ---- 真实页数边界核验 ----
    out_of_range: List[int] = []
    if document_page_count is not None and document_page_count > 0:
        out_of_range = [page for page in claimed_pages if page > document_page_count]
        if out_of_range:
            # 页码越界 = 引用指向不存在的页面，页码信号不可信
            has_page = False
            missing.append(REASON_PAGE_OUT_OF_RANGE)
            verification["reasons"].append(
                f"{REASON_PAGE_OUT_OF_RANGE}: {out_of_range} > {document_page_count}"
            )

    # ---- 原文引用核验（AC-09：逐条证据分别绑定其自称页面） ----
    # 此前口径是"任一引文在任一所称页命中"——一条真实引文能掩盖另一条
    # 伪造引文，互换两条证据的页码也照样 verified。现在每条引文必须在其
    # **自己的**页码集合内命中；任一条失败，整体即失败。
    quote_bindings: List[Tuple[str, List[int]]] = []
    if str(finding.get("text_snippet") or "").strip():
        quote_bindings.append((str(finding["text_snippet"]).strip(), claimed_pages))
    for item in _evidence_items(finding):
        item_pages = _evidence_page_of(item)
        bound_pages = [item_pages] if item_pages is not None else list(claimed_pages)
        for key in _EVIDENCE_TEXT_KEYS:
            text = str(item.get(key) or "").strip()
            if text:
                quote_bindings.append((text, bound_pages))

    if page_texts:
        verification["checked_pages"] = list(claimed_pages)
        if not quote_bindings:
            # 无引用可核验（纯 bbox/纯页码）：保持 unverified，如实记录
            verification["reasons"].append("no_quote_text_to_verify")
        else:
            failed_bindings: List[int] = []
            unverifiable_bindings: List[int] = []
            for index, (quote, bound_pages) in enumerate(quote_bindings):
                if not bound_pages or not any(
                    _normalise_for_match(_page_text(page_texts, page)) for page in bound_pages
                ):
                    # 该引文绑定的页面无文本层（扫描页）：无法核验
                    unverifiable_bindings.append(index)
                    continue
                if not _quote_found_on_pages(quote, bound_pages, page_texts):
                    failed_bindings.append(index)
            if failed_bindings:
                # 任一引文在自称页面不存在：伪造原文，整体失败（不可被其他真引文掩盖）
                missing.append(REASON_TEXT_NOT_FOUND_ON_PAGE)
                verification["reasons"].append(
                    f"{REASON_TEXT_NOT_FOUND_ON_PAGE}: binding#{failed_bindings}"
                )
                verification["failed_bindings"] = failed_bindings
            elif unverifiable_bindings and len(unverifiable_bindings) == len(quote_bindings):
                # 全部引文绑定页都无文本层（扫描页）：无法核验，不算失败
                verification["reasons"].append("claimed_pages_have_no_text_layer")
            else:
                verification["status"] = EVIDENCE_VERIFICATION_VERIFIED
                if unverifiable_bindings:
                    # 混合形态：部分引文核验通过、部分无法核验——整体如实降为
                    # unverified，并指明哪些绑定未核验。
                    verification["reasons"].append(
                        f"partially_unverifiable_bindings:{unverifiable_bindings}"
                    )
                    verification["status"] = EVIDENCE_VERIFICATION_UNVERIFIED

    has_text = _has_evidence_text(finding)
    has_bbox = _has_bbox(finding)

    if not has_page:
        if REASON_PAGE_OUT_OF_RANGE not in missing:
            missing.append("missing_page")
    if not has_text:
        missing.append("missing_evidence_text")
    if not has_bbox:
        missing.append("missing_bbox")

    if REASON_TEXT_NOT_FOUND_ON_PAGE in missing or REASON_PAGE_OUT_OF_RANGE in missing:
        verification["status"] = EVIDENCE_VERIFICATION_FAILED

    # 核验失败（伪造原文/页码越界）直接使证据无效，即使结构字段齐全
    complete = (
        has_page
        and (has_text or has_bbox)
        and REASON_TEXT_NOT_FOUND_ON_PAGE not in missing
        and REASON_PAGE_OUT_OF_RANGE not in missing
    )
    return complete, missing, verification


def is_document_level_finding(finding: Mapping[str, Any]) -> bool:
    """该 finding 是否属于文档级规则（缺章节/缺表等结构性判定）。

    这类问题不指向某一页，页码天然缺失，证据完整率按可定位类口径单独统计。
    识别锚是规则编号（见 ``DOCUMENT_LEVEL_RULE_IDS``）：引擎会把缺失页码
    折算成 1，"页码缺失"在历史数据里既不充分（BUD-001 可能被折成 1）
    也不必要（其他规则的页码缺失是真证据缺口，不能豁免）。
    """
    rule_id = str(finding.get("rule_id") or finding.get("rule") or "").strip().upper()
    return rule_id in DOCUMENT_LEVEL_RULE_IDS


def is_formal_finding(finding: Any) -> bool:
    """该 finding 是否算作正式问题（缺证据被降级的不算）。

    历史快照没有 ``evidence_status`` 字段，按正式问题处理，保证旧任务计数不变。
    """
    if isinstance(finding, Mapping):
        status = str(finding.get("evidence_status") or "").strip()
    else:
        status = str(getattr(finding, "evidence_status", "") or "").strip()
    return status != EVIDENCE_STATUS_DEGRADED


def count_formal_findings(result: Any) -> int:
    """统计结果中的"正式问题"条数（缺证据被降级的不计入）。

    同时兼容双模式结构与 legacy 分桶结构。这是"正式问题数"的唯一口径，
    质量门禁与离线回放脚本都必须复用它，避免两处各算一套导致结论不一致。
    """
    if not isinstance(result, dict):
        return 0
    issues = result.get("issues")
    if isinstance(issues, dict):
        all_items = issues.get("all")
        if isinstance(all_items, list):
            return sum(1 for item in all_items if is_formal_finding(item))
        return sum(
            sum(1 for item in issues[key] if is_formal_finding(item))
            for key in ("error", "warn", "info")
            if isinstance(issues.get(key), list)
        )
    if isinstance(issues, list):
        return sum(1 for item in issues if is_formal_finding(item))
    total = 0
    for key in ("rule_findings", "ai_findings"):
        items = result.get(key)
        if isinstance(items, list):
            total += sum(1 for item in items if is_formal_finding(item))
    return total


def _degrade_ai_finding(finding: Dict[str, Any], missing: List[str]) -> None:
    """把缺证据的 AI 问题降级为待复核，而不是当作正式结论输出。"""
    finding["evidence_status"] = EVIDENCE_STATUS_DEGRADED
    finding["evidence_missing"] = list(missing)
    finding["original_severity"] = finding.get("severity")
    finding["severity"] = "manual_review"
    tags = finding.get("tags")
    tags = list(tags) if isinstance(tags, list) else []
    if EVIDENCE_DEGRADED_TAG not in tags:
        tags.append(EVIDENCE_DEGRADED_TAG)
    finding["tags"] = tags
    why_not = str(finding.get("why_not") or "").strip()
    marker = "EVIDENCE_INCOMPLETE: " + ",".join(missing)
    finding["why_not"] = f"{why_not}; {marker}" if why_not else marker


def iter_findings(result: Mapping[str, Any]) -> Iterable[Dict[str, Any]]:
    """遍历结果中的所有 finding 字典（同时兼容双模式与 legacy 分桶结构）。

    公开函数：证据校验与检查义务标注都必须走同一遍历口径，
    否则会出现"证据校验看到 10 条、义务标注只看到 5 条"的静默偏差。

    legacy 结构里 ``issues.all`` 与 ``issues.error/warn/info`` 持有同一批字典对象，
    因此只遍历 ``all``：既避免重复计数，原地修改也会同步反映到各分桶。
    """
    for key in ("ai_findings", "rule_findings"):
        items = result.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    yield item

    issues = result.get("issues")
    if isinstance(issues, dict):
        bucket = issues.get("all")
        if isinstance(bucket, list):
            for item in bucket:
                if isinstance(item, dict):
                    yield item
    elif isinstance(issues, list):
        for item in issues:
            if isinstance(item, dict):
                yield item


def _finding_ref(finding: Mapping[str, Any], missing: List[str]) -> Dict[str, Any]:
    return {
        "id": str(finding.get("id") or ""),
        "rule_id": str(finding.get("rule_id") or finding.get("rule") or ""),
        "source": str(finding.get("source") or ""),
        "page_number": finding.get("page_number"),
        "missing": list(missing),
    }


def apply_evidence_completeness(
    result: Dict[str, Any],
    *,
    document_page_count: Optional[int] = None,
    page_texts: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """就地校验结果中的全部 finding，并返回 ``evidence_completeness`` 度量。

    副作用：缺证据的 AI finding 会被降级（修改 severity/tags/evidence_status）。
    规则 finding 只打标记与告警，不改 severity。每条 finding 会写入
    ``evidence_verification``（verified/unverified/failed 及原因），
    "结构完整"与"引用已核实"分别记账。

    完整率两层口径（B1 调整）：
    - ``completeness_rate``：全量分母（含文档级 finding），趋势对照用；
    - ``locatable_completeness_rate``：可定位类分母（剔除文档级 finding），
      质量门禁 ``check_replay_thresholds`` 消费这个口径。
    分母为 0 时两个 rate 都是 None——"没有问题"不等于"证据完整"。
    """
    total = 0
    complete_count = 0
    locatable_total = 0
    locatable_complete_count = 0
    document_level_total = 0
    verified_count = 0
    unverified_count = 0
    failed_count = 0
    degraded: List[Dict[str, Any]] = []
    rule_warnings: List[Dict[str, Any]] = []

    for finding in iter_findings(result):
        total += 1
        complete, missing, verification = evaluate_finding_evidence(
            finding,
            document_page_count=document_page_count,
            page_texts=page_texts,
        )
        finding["evidence_verification"] = dict(verification)
        status = str(verification.get("status") or "")
        if status == EVIDENCE_VERIFICATION_VERIFIED:
            verified_count += 1
        elif status == EVIDENCE_VERIFICATION_FAILED:
            failed_count += 1
        else:
            unverified_count += 1

        source = str(finding.get("source") or "").strip().lower()

        if complete:
            complete_count += 1
            # 只在没有既有状态时补写，避免覆盖上一轮已判定的降级态
            finding.setdefault("evidence_status", EVIDENCE_STATUS_COMPLETE)
        else:
            blocking = [code for code in missing if code != "missing_bbox"] or list(missing)
            if source == "ai":
                _degrade_ai_finding(finding, blocking)
                degraded.append(_finding_ref(finding, blocking))
            else:
                finding["evidence_status"] = EVIDENCE_STATUS_RULE_WARNING
                finding["evidence_missing"] = list(blocking)
                rule_warnings.append(_finding_ref(finding, blocking))

        if is_document_level_finding(finding):
            document_level_total += 1
        else:
            locatable_total += 1
            if complete:
                locatable_complete_count += 1

    formal_total = sum(1 for finding in iter_findings(result) if is_formal_finding(finding))
    completeness_rate = round(complete_count / total, 4) if total else None

    return {
        "total": total,
        "complete": complete_count,
        "incomplete": total - complete_count,
        "completeness_rate": completeness_rate,
        "locatable_total": locatable_total,
        "locatable_complete": locatable_complete_count,
        "locatable_completeness_rate": round(locatable_complete_count / locatable_total, 4)
        if locatable_total
        else None,
        "document_level_total": document_level_total,
        "verified_count": verified_count,
        "unverified_count": unverified_count,
        "failed_count": failed_count,
        "degraded_count": len(degraded),
        "rule_warning_count": len(rule_warnings),
        "formal_issue_total": formal_total,
        "degraded": degraded[:_DETAIL_LIMIT],
        "rule_warnings": rule_warnings[:_DETAIL_LIMIT],
        "detail_truncated": max(len(degraded), len(rule_warnings)) > _DETAIL_LIMIT,
    }
