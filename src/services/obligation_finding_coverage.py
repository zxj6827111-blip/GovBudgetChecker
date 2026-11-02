"""判断一条未完成的检查义务是否已被同源正式问题覆盖。

为什么需要这一层
----------------
引擎的"取数不足"有时不是新问题，而是**同一条问题导致检查做不下去**。
最典型的是 T5：说明里的款名写成旧称“行政事业单位离退休”，表格里是
“行政事业单位养老支出”。V33-227 已经把名称不一致作为正式问题报出来了；
但 V33-222 因为名称对不上绑不到金额，义务仍停在 ``insufficient_data``。
如果两条都进复核队列，用户会在"参考问题"里看到一次、在"检查补核"里又看到
一次，重复且没有新增信息。

本模块只做**去重**，不改变问题本身：义务被覆盖后不再阻塞补核门禁，
问题仍留在问题清单里，由人工按问题流程处理一次。

安全边界
--------
- 只处理 ``insufficient_data`` 的义务；``not_executed``（规则没实现）不隐藏；
- 只认**正式** finding（证据降级 / manual_review / info 不算）；
- finding 必须属于当前可见问题集合（被 legacy 忽略的问题不能用来关义务）；
- 义务 detail 里必须有**可辨识的引号短语**（如旧科目名），且该短语出现在
  finding 的 message/title/location 里；泛化词（“一般公共”“决算数”等）
  不参与匹配，避免把不相关的检查悄悄关掉；
- finding 的 checker 编号必须与义务的 checker 有交集。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from src.services.evidence_guard import is_formal_finding

#: detail 里的中文引号短语（旧科目名、说明名等）。
_QUOTED_RE = re.compile(r"[“「]([^”」]{2,40})[”」]")

#: 太泛的词不参与“同源”匹配，否则任何包含“一般公共”的 finding 都能关义务。
_GENERIC_TERMS: Set[str] = {
    "一般公共",
    "一般公共预算",
    "财政拨款",
    "年初预算",
    "决算数",
    "预算数",
    "基本支出",
    "项目支出",
    "合计",
    "本年",
    "上年",
    "三公经费",
    "机关运行经费",
    "支出决算",
    "收入决算",
}

#: 至少 4 个字的短语才当作可辨识科目/说明名。
_MIN_TERM_LENGTH = 4


def _json_value(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return raw
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001 - 解析失败按原值处理
            return raw
    return raw


def _json_dict(raw: Any) -> Dict[str, Any]:
    value = _json_value(raw)
    return value if isinstance(value, dict) else {}


def _json_list(raw: Any) -> List[Any]:
    value = _json_value(raw)
    return value if isinstance(value, list) else []


def distinctive_terms(detail: str) -> Set[str]:
    """从义务 detail 里取可辨识的引号短语；泛化词和短词剔除。"""
    terms = {
        match.group(1).strip()
        for match in _QUOTED_RE.finditer(str(detail or ""))
    }
    return {
        term
        for term in terms
        if len(term) >= _MIN_TERM_LENGTH and term not in _GENERIC_TERMS
    }


def _finding_text(finding: Dict[str, Any]) -> str:
    """finding 里可能带科目/说明名的字段拼成一段可搜索文本。"""
    location = _json_dict(finding.get("location"))
    display = _json_dict(finding.get("display"))
    parts: List[Any] = [
        finding.get("message"),
        finding.get("title"),
        finding.get("text_snippet"),
        finding.get("suggestion"),
    ]
    for key in (
        "actual_name",
        "expected_name",
        "subject",
        "subject_name",
        "section",
        "table",
        "row",
        "code",
    ):
        parts.append(location.get(key))
    parts.extend(_json_list(display.get("detail_lines")))
    return " ".join(str(part) for part in parts if part)


def obligation_covered_by_finding(
    item: Any,
    result_row: Any,
    visible_issue_ids: Optional[Iterable[str]] = None,
) -> bool:
    """该义务是否只是同一条正式问题的“检查做不下去”副本。

    ``visible_issue_ids`` 为当前问题集合里可见的 issue id；被 legacy 忽略的
    finding 不在其中，不能用来关闭义务。传 ``None`` 表示不校验可见性
    （仅供纯函数测试）。
    """
    if str(getattr(item, "status", "") or "") != "insufficient_data":
        return False
    checkers = {str(code).strip() for code in (getattr(item, "checkers", None) or [])}
    terms = distinctive_terms(str(getattr(item, "detail", None) or ""))
    if not checkers or not terms:
        return False

    visible = set(visible_issue_ids) if visible_issue_ids is not None else None
    findings = _json_list(_json_dict(result_row).get("rule_findings"))
    for finding in findings:
        if not isinstance(finding, dict) or not is_formal_finding(finding):
            continue
        issue_id = str(finding.get("id") or "").strip()
        if visible is not None and issue_id and issue_id not in visible:
            continue
        tags = {str(tag).strip() for tag in _json_list(finding.get("tags"))}
        rule_id = str(finding.get("rule_id") or finding.get("check") or "").strip()
        if not (checkers & tags or rule_id in checkers):
            continue
        text = _finding_text(finding)
        if any(term in text for term in terms):
            return True
    return False
