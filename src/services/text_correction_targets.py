"""正文金额的可定位校正对象（2026-11-19）。

正文金额没有表格事实 ID，不能复用 ``fact``/``table_instance`` 校正目标。
这里把 CMM-011 等正文问题投影成 ``text_statement`` 目标：物理页、原文区间、
原句、当前读数、建议单位与已核出的原表科目依据。复核人确认单位/读数/科目后，
校正记录沿既有 corrections.jsonl → 重分析链路生效；规则只采用校正值重算，
不会把“核对无误”当成事实。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
# R3（2026-11-20 复审）：目标里的原读数必须保留完整异常字串（"54,65.41"、
# "5,465.41.00"），不能截成合法数字前缀（"54"、"5,465.41"）；合法性与
# 原文提取分开——先整段捕获数字/逗号/小数点连续段，再单独判是否合法金额。
_RAW_NUMBER_TOKEN = r"\d[\d,]*(?:\.\d+)*"
_QUOTED_AMOUNT = re.compile(rf"[“\"]([^”\"]*?(?P<amount>{_RAW_NUMBER_TOKEN})[^”\"]*?)[”\"]")
_QUOTED_LOCATION = re.compile(r"第\s*(\d+)\s*页\s*「([^」]+)」")
_VALID_AMOUNT = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


def _amount_raw_valid(raw: Any) -> bool:
    """原文读数是否为合法金额字面量（千分位/小数点形态完整）。"""
    return bool(_VALID_AMOUNT.fullmatch(str(raw or "").strip()))


def _issue_rule(issue: Mapping[str, Any]) -> str:
    return str(issue.get("rule_id") or issue.get("rule") or "")


def _issues(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
    issues = result.get("issues") if isinstance(result.get("issues"), Mapping) else {}
    for key in ("error", "warn", "info", "all"):
        for issue in issues.get(key) or []:
            if isinstance(issue, Mapping):
                yield issue
    # 2026-11-20：队列分析持久化形态是 result.rule_findings（扁平列表，含
    # rule_id/message/location/evidence）；人工校正触发的重分析历史路径会把
    # issues 分桶。两种形态都读，重复目标由 target_id 去重兜底。
    for source in (result.get("rule_findings"), payload.get("rule_findings")):
        for issue in source or []:
            if isinstance(issue, Mapping):
                yield issue


def _receipt_rule_ids(payload: Mapping[str, Any]) -> set[str]:
    """当前版本实际产出的检查规则集合（用于推导受影响检查）。"""
    report = payload.get("review_report") if isinstance(payload.get("review_report"), Mapping) else {}
    receipts = report.get("check_receipts")
    if not isinstance(receipts, list):
        result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
        meta = result.get("meta") if isinstance(result.get("meta"), Mapping) else {}
        receipts = meta.get("check_receipts")
    return {
        str(receipt.get("rule_id"))
        for receipt in receipts or []
        if isinstance(receipt, Mapping) and receipt.get("rule_id")
    }


def _affected_for_text_target(rule_id: str, present: set[str]) -> List[str]:
    """正文金额校正实际会重跑的检查：单项表文核对、分项加总与漏单位。

    只列当前版本真实产出过的规则，不写死与文书类型无关的清单。
    """
    if rule_id == "CMM-011":
        candidates = ["CMM-011", "V33-222", "V33-251"]
    elif rule_id == "V33-251":
        candidates = ["V33-251", "V33-222"]
    elif rule_id == "V33-222":
        candidates = ["V33-222", "V33-251"]
    else:
        candidates = [rule_id] if rule_id else []
    return [code for code in candidates if not present or code in present or code == rule_id]


def _compact(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _target_id(page: int, pos: Any, span: str, amount: str) -> str:
    seed = "|".join([str(page), str(pos), _compact(span), amount])
    return "text-stmt:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def _evidence(issue: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for item in issue.get("evidence") or []:
        if not isinstance(item, Mapping):
            continue
        page = item.get("physical_page") or item.get("page")
        if not isinstance(page, int) or isinstance(page, bool):
            continue
        rows.append({
            "page": page,
            "bbox": item.get("bbox"),
            "quote": item.get("quote") or item.get("span") or item.get("text_snippet"),
            "source_block_id": item.get("source_block_id"),
            "verification_status": item.get("verification_status"),
        })
    return rows


def build_text_correction_targets(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """列出当前版本可人工确认的正文金额；只消费已有页码/区间证据。"""
    targets: List[Dict[str, Any]] = []
    seen = set()
    present_rules = _receipt_rule_ids(payload)
    for issue in _issues(payload):
        rule = _issue_rule(issue)
        message = str(issue.get("message") or "")
        location = issue.get("location") if isinstance(issue.get("location"), Mapping) else {}
        evidence = _evidence(issue)
        page = location.get("page") or (evidence[0]["page"] if evidence else None)
        if not isinstance(page, int) or isinstance(page, bool):
            continue
        span = ""
        if evidence:
            span = str(evidence[0].get("quote") or "")
        if not span:
            match = _QUOTED_AMOUNT.search(message)
            span = match.group(1) if match else ""
        match = _QUOTED_AMOUNT.search(message) or _QUOTED_AMOUNT.search(span)
        amount = match.group("amount").replace(",", "") if match else None
        # V33-251 的未纳入项用「定位：第N页「…」」引用，金额在它自己的分支提取。
        if not amount and not (
            rule == "V33-251" and location.get("not_included_items")
        ):
            continue
        verified = location.get("verified_by") if isinstance(location.get("verified_by"), Mapping) else None

        if rule == "CMM-011" and "缺少单位" in message:
            raw_amount = match.group("amount")
            field_actions = [
                {"field": "unit", "label": "确认金额单位为万元", "value": "万元"},
                {"field": "raw_value", "label": "校正原文读数", "value": raw_amount},
            ]
            if verified and verified.get("subject_code"):
                field_actions.append({
                    "field": "subject_code",
                    "label": "确认对应科目",
                    "value": str(verified.get("subject_code")),
                })
            target = {
                "target_id": _target_id(page, location.get("pos"), span or match.group(0), amount),
                "target_type": "text_statement",
                "rule_id": rule,
                "issue_ids": [issue.get("id")],
                "title": issue.get("title") or "金额缺少单位“万元”",
                "page": page,
                "pos": location.get("pos"),
                "span": span or match.group(0),
                "amount_raw": raw_amount,
                # R3：原读数不是合法金额时如实标记；面板和提交入口据此禁止
                # 把截断前缀或异常字串当作有效读数重算。
                "raw_is_valid": _amount_raw_valid(raw_amount),
                "amount_wan_current": None,
                "unit_current": None,
                "suggested_unit": "万元",
                "subject": (
                    {
                        "code": verified.get("subject_code"),
                        "name": verified.get("subject_name"),
                    }
                    if verified else None
                ),
                "table_basis": verified,
                "actions": field_actions,
                "affected_check_ids": _affected_for_text_target(rule, present_rules),
                "evidence": evidence,
                "analysis_revision": payload.get("analysis_revision"),
                "verification_status": "verified" if evidence else "unverified",
            }
        elif rule == "V33-251" and location.get("not_included_items"):
            quoted = _QUOTED_LOCATION.search(message)
            if quoted is None:
                continue
            quoted_page = int(quoted.group(1))
            quoted_span = quoted.group(2)
            sub_match = re.search(_RAW_NUMBER_TOKEN, quoted_span)
            if sub_match is None:
                continue
            amount = sub_match.group(0).replace(",", "")
            target = {
                "target_id": _target_id(quoted_page, None, quoted_span, amount),
                "target_type": "text_statement",
                "rule_id": rule,
                "issue_ids": [issue.get("id")],
                "title": "分项金额缺单位，未纳入合计",
                "page": quoted_page,
                "pos": None,
                "span": quoted_span,
                "amount_raw": sub_match.group(0),
                "raw_is_valid": _amount_raw_valid(sub_match.group(0)),
                "amount_wan_current": None,
                "unit_current": None,
                "suggested_unit": "万元",
                "subject": None,
                "table_basis": None,
                "actions": [
                    {"field": "unit", "label": "确认金额单位为万元", "value": "万元"},
                    {"field": "raw_value", "label": "校正原文读数", "value": sub_match.group(0)},
                ],
                "affected_check_ids": _affected_for_text_target(rule, present_rules),
                "evidence": [{"page": quoted_page, "quote": quoted_span, "bbox": None}],
                "analysis_revision": payload.get("analysis_revision"),
                "verification_status": "verified",
            }
        else:
            continue
        if target["target_id"] in seen:
            continue
        seen.add(target["target_id"])
        targets.append(target)
    return targets


def text_corrections_from_records(
    records: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """把台账中的 text_statement 改动投影为规则可消费的校正对象。"""
    corrections: List[Dict[str, Any]] = []
    for record in records or []:
        if not isinstance(record, Mapping):
            continue
        for change in record.get("changes") or []:
            if not isinstance(change, Mapping):
                continue
            if change.get("target_type") != "text_statement":
                continue
            location = change.get("location") if isinstance(change.get("location"), Mapping) else {}
            page = location.get("physical_page") or location.get("page")
            if not isinstance(page, int) or isinstance(page, bool):
                continue
            corrections.append({
                "correction_id": record.get("record_id"),
                "target_id": change.get("target_id"),
                "field": change.get("field"),
                "value": change.get("value"),
                "page": page,
                "bbox": location.get("bbox"),
                # R5：原句页内位置随台账透传；同页同额两笔原句靠它各改各的。
                "pos": location.get("pos"),
                "span": (
                    change.get("original_text")
                    or location.get("quote")
                    or location.get("span")
                    or ""
                ),
                "actor": record.get("actor"),
                "analysis_basis_token": record.get("analysis_basis_token"),
                "reason": record.get("reason"),
            })
    return corrections


def duplicate_correction_record(
    records: Iterable[Mapping[str, Any]],
    *,
    basis_token: str,
    changes: List[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """同版本、同目标、同字段、同取值的重复提交返回既有记录，不再追加。"""
    fingerprint = _changes_fingerprint(changes)
    for record in records or []:
        if not isinstance(record, Mapping):
            continue
        if str(record.get("analysis_basis_token") or "") != basis_token:
            continue
        if _changes_fingerprint(record.get("changes") or []) == fingerprint:
            return record
    return None


def _changes_fingerprint(changes: Iterable[Mapping[str, Any]]) -> str:
    parts = []
    for change in changes or []:
        if not isinstance(change, Mapping):
            continue
        parts.append("|".join([
            str(change.get("target_type") or ""),
            str(change.get("target_id") or ""),
            str(change.get("field") or ""),
            str(change.get("value")),
        ]))
    seed = "\n".join(sorted(parts))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()
