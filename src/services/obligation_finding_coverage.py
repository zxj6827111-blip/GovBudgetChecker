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

#: U-7：金额/数值类子检查的 checker。义务含这些 checker 时，只有同属金额族的问题
#: 才可能覆盖“金额检查做不下去”的副本；名称/单位类问题只合并展示，不得关闭、
#: 覆盖或隐藏未完成的金额子检查。
_AMOUNT_CHECKERS: Set[str] = {
    "V33-005",
    "V33-106",
    "V33-110",
    "V33-117",
    "V33-202",
    "V33-222",
    "V33-224",
    "V33-234",
    "V33-240",
    "V33-241",
    "V33-243",
    "V33-244",
    "V33-246",
    "V33-249",
    "V33-251",
    "V33-TREND-COMPLETION-RATE",
    "V33-SG-COMPLETION",
    "OBL-TREND-COMPLETION-RATE",
    "CMM-007",
    "CMM-020",
}


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


#: 单位/名称/格式类措辞：即使规则编号属于金额族，也不能当作金额检查已完成。
_NON_AMOUNT_MARKERS = (
    "单位口径", "单位错误", "单位错", "折合", "名称不一致", "名称差异",
    "科目名称", "漏字", "多字", "表名", "小数位", "千分位", "格式",
)
#: 明确指向金额复算/勾稽的措辞；无以上非金额措辞时才可作为金额覆盖。
_AMOUNT_MARKERS = (
    "金额", "差额", "合计", "不等于", "不一致", "相差", "完成率", "占比",
    "预算数", "决算数", "复算", "勾稽", "加总",
)
#: 义务 detail/missing_operands 里表示“金额取数未完成”的措辞。
_AMOUNT_OBLIGATION_MARKERS = (
    "金额", "差额", "合计", "取数", "绑定", "复算", "完成率", "占比",
    "决算数", "预算数", "加总",
)


def _requires_amount_evidence(item: Any) -> bool:
    """义务是否在等一个金额子检查结果。

    优先看 missing_operands 的金额列与 detail 语义；只有两者都没有时才回退
    到 checker 家族（仅作兼容兜底，不单独作为覆盖依据）。
    """
    for operand in getattr(item, "missing_operands", None) or []:
        if not isinstance(operand, dict):
            continue
        text = " ".join(str(operand.get(key) or "") for key in ("column", "role", "row_key"))
        if any(marker in text for marker in _AMOUNT_OBLIGATION_MARKERS):
            return True
    detail = str(getattr(item, "detail", None) or "")
    if any(marker in detail for marker in _AMOUNT_OBLIGATION_MARKERS):
        return True
    return False


def _finding_amount_kind(finding: Dict[str, Any]) -> str:
    """判断 finding 是金额结论、非金额结论还是无法判定。

    先看 check_relation_id/sub_check_id 的实际子检查；没有显式子检查时按
    正文措辞判定。无法判定返回 ``unknown``（金额义务下按不覆盖处理）。
    """
    location = _json_dict(finding.get("location"))
    relation = str(finding.get("check_relation_id") or location.get("check_relation_id")
                   or location.get("sub_check_id") or "").strip()
    if relation:
        tail = re.split(r"[:/]", relation)[-1].lower()
        if any(token in tail for token in ("unit", "name", "format", "structure", "scope")):
            return "non_amount"
        if any(token in tail for token in ("amount", "total", "sum", "diff", "balance", "value")):
            return "amount"
    # 只用问题正文判定：标题/位置字段可能沿用“名称”类通用模板，不能把
    # “金额不一致”正文误判成名称问题。
    text = str(finding.get("message") or finding.get("text_snippet") or "")
    if not text:
        text = _finding_text(finding)
    if any(marker in text for marker in _NON_AMOUNT_MARKERS):
        return "non_amount"
    if any(marker in text for marker in _AMOUNT_MARKERS):
        return "amount"
    return "unknown"


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
    # U-7：义务含金额子检查时，只有金额族问题才可能覆盖；名称/单位类问题
    # 只合并展示，不得关闭、覆盖或隐藏未完成的金额检查。
    amount_checkers = checkers & _AMOUNT_CHECKERS
    requires_amount = bool(amount_checkers) or _requires_amount_evidence(item)
    # DH-03a：新结果读冻结短语列表；只有历史结果没有该字段时才回退解析 detail。
    frozen_terms = getattr(item, "legacy_coverage_terms", None)
    if frozen_terms is None:
        terms = distinctive_terms(str(getattr(item, "detail", None) or ""))
    else:
        terms = {str(term) for term in frozen_terms if str(term)}
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
        matched = (checkers & tags) | ({rule_id} if rule_id in checkers else set())
        if not matched:
            continue
        if requires_amount and _finding_amount_kind(finding) != "amount":
            # 名称/单位/结构类问题不覆盖金额子检查；子检查性质不明时同样
            # fail-closed，不能仅凭规则编号族把未完成金额检查关掉。
            continue
        text = _finding_text(finding)
        if any(term in text for term in terms):
            return True
    return False
