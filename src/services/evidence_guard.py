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
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

#: 缺证据的 AI 问题被降级后写入的状态值
EVIDENCE_STATUS_DEGRADED = "degraded_missing_evidence"
#: 证据完整的问题状态值
EVIDENCE_STATUS_COMPLETE = "complete"
#: 规则来源缺证据时的标记（仍计入正式问题，只是带告警）
EVIDENCE_STATUS_RULE_WARNING = "incomplete_rule_warning"

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
    for item in value:
        if isinstance(item, bool):
            return False
        try:
            float(item)
        except (TypeError, ValueError):
            return False
    return True


def _evidence_items(finding: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    raw = finding.get("evidence")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _has_page(finding: Mapping[str, Any]) -> bool:
    if _positive_int(finding.get("page_number")) is not None:
        return True
    location = finding.get("location")
    if isinstance(location, Mapping) and _positive_int(location.get("page")) is not None:
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
    display = finding.get("display")
    if isinstance(display, Mapping) and str(display.get("evidence_text") or "").strip():
        return True
    return False


def _has_bbox(finding: Mapping[str, Any]) -> bool:
    if _is_valid_bbox(finding.get("bbox")):
        return True
    location = finding.get("location")
    if isinstance(location, Mapping) and _is_valid_bbox(location.get("bbox")):
        return True
    return any(_is_valid_bbox(item.get("bbox")) for item in _evidence_items(finding))


def evaluate_finding_evidence(finding: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    """校验单条 finding 的证据链。

    Returns:
        ``(证据是否完整, 缺失原因码列表)``。原因码取值：
        ``missing_page`` / ``missing_evidence_text`` / ``missing_bbox``。
        证据完整时也可能返回 ``missing_bbox``——它只说明"没有坐标"，不影响完整性判定。
    """
    missing: List[str] = []
    has_page = _has_page(finding)
    has_text = _has_evidence_text(finding)
    has_bbox = _has_bbox(finding)

    if not has_page:
        missing.append("missing_page")
    if not has_text:
        missing.append("missing_evidence_text")
    if not has_bbox:
        missing.append("missing_bbox")

    complete = has_page and (has_text or has_bbox)
    return complete, missing


def is_formal_finding(finding: Any) -> bool:
    """该 finding 是否算作正式问题（缺证据被降级的不算）。

    历史快照没有 ``evidence_status`` 字段，按正式问题处理，保证旧任务计数不变。
    """
    if isinstance(finding, Mapping):
        status = str(finding.get("evidence_status") or "").strip()
    else:
        status = str(getattr(finding, "evidence_status", "") or "").strip()
    return status != EVIDENCE_STATUS_DEGRADED


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


def _iter_findings(result: Mapping[str, Any]) -> Iterable[Dict[str, Any]]:
    """遍历结果中的所有 finding 字典（同时兼容双模式与 legacy 分桶结构）。

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


def apply_evidence_completeness(result: Dict[str, Any]) -> Dict[str, Any]:
    """就地校验结果中的全部 finding，并返回 ``evidence_completeness`` 度量。

    副作用：缺证据的 AI finding 会被降级（修改 severity/tags/evidence_status）。
    规则 finding 只打标记与告警，不改 severity。
    """
    total = 0
    complete_count = 0
    degraded: List[Dict[str, Any]] = []
    rule_warnings: List[Dict[str, Any]] = []

    for finding in _iter_findings(result):
        total += 1
        complete, missing = evaluate_finding_evidence(finding)
        source = str(finding.get("source") or "").strip().lower()

        if complete:
            complete_count += 1
            # 只在没有既有状态时补写，避免覆盖上一轮已判定的降级态
            finding.setdefault("evidence_status", EVIDENCE_STATUS_COMPLETE)
            continue

        blocking = [code for code in missing if code != "missing_bbox"] or list(missing)
        if source == "ai":
            _degrade_ai_finding(finding, blocking)
            degraded.append(_finding_ref(finding, blocking))
        else:
            finding["evidence_status"] = EVIDENCE_STATUS_RULE_WARNING
            finding["evidence_missing"] = list(blocking)
            rule_warnings.append(_finding_ref(finding, blocking))

    formal_total = sum(1 for finding in _iter_findings(result) if is_formal_finding(finding))
    completeness_rate = round(complete_count / total, 4) if total else 1.0

    return {
        "total": total,
        "complete": complete_count,
        "incomplete": total - complete_count,
        "completeness_rate": completeness_rate,
        "degraded_count": len(degraded),
        "rule_warning_count": len(rule_warnings),
        "formal_issue_total": formal_total,
        "degraded": degraded[:_DETAIL_LIMIT],
        "rule_warnings": rule_warnings[:_DETAIL_LIMIT],
        "detail_truncated": max(len(degraded), len(rule_warnings)) > _DETAIL_LIMIT,
    }
