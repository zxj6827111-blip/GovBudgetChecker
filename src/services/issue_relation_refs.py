"""正式问题引用契约：直接检查关系，或显式列出的来源诊断关系组。

来源异常组描述多条实际检查的共同症状，不虚构一条金额恒等式。
"""
from __future__ import annotations


def issue_relation_ids(issue):
    location = issue.get("location") or {}
    if location.get("reference_kind") == "source_diagnostic_group":
        values = location.get("relation_ids")
        rule = str(issue.get("rule_id") or issue.get("rule") or "")
        if (not isinstance(values, list) or len(values) < 2 or len(set(values)) != len(values)
                or any(not isinstance(v, str) or not v.startswith(rule + ":") for v in values)):
            return []
        declared = {r.get("relation_id") for r in location.get("relations") or [] if isinstance(r, dict)}
        return values if set(values) == declared else []
    value = issue.get("check_relation_id")
    return [value] if isinstance(value, str) and value else []


def unresolved_issue_relations(issue, receipts):
    ids = issue_relation_ids(issue)
    if not ids:
        return [str(issue.get("check_relation_id") or "missing_relation")]
    available = {f"{r.get('rule_id')}:{r.get('sub_check_id')}" for r in receipts}
    return [value for value in ids if value not in available]
