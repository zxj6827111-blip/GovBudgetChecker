"""空白/横线的计算解释与原始值分离。生产默认没有“空白按零”约定。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path


def configured_convention(*, region, year, table_code, row, column, presence):
    path = os.environ.get("GOVBUDGET_EMPTY_VALUE_CONVENTIONS")
    if not path:
        return None
    policies = json.loads(Path(path).read_text(encoding="utf-8"))
    matches = []
    for policy in policies:
        kind = policy.get("source_kind")
        approved = (kind == "official_document" and all(policy.get(key) for key in ("document", "issuer", "reference"))) or (
            kind == "user_confirmed" and policy.get("approved") is True and policy.get("confirmation_reference")) or (
            kind == "isolated_test" and os.environ.get("TESTING", "").lower() == "true")
        if not approved or not policy.get("id") or not policy.get("region"):
            continue
        if (not str(region or "").startswith(policy["region"]) or year not in policy.get("years", [])
                or table_code not in policy.get("table_codes", []) or policy.get("semantics", {}).get(presence) != "zero"):
            continue
        ranges = policy.get("cell_ranges", [])
        if any(bounds["rows"][0] <= row <= bounds["rows"][1]
               and bounds["columns"][0] <= column <= bounds["columns"][1] for bounds in ranges):
            matches.append(policy)
    return matches[0] if len(matches) == 1 else None


def interpret_cell_value(raw, *, native_blank_verified=False, **identity):
    """返回 (数值或 None, 原格状态, 解释依据)；不修改原格。"""
    text = "" if raw is None else str(raw).strip()
    if text:
        try:
            number = Decimal(text.replace(",", ""))
            if number.is_finite():
                return number, "zero" if number == 0 else "value", None
        except InvalidOperation:
            pass
    presence = "blank" if not text else "dash" if text in {"-", "—", "–", "－"} else "not_applicable" if text in {"无", "不适用", "/", "未披露"} else "unrecognized"
    if presence == "blank" and not native_blank_verified or presence not in {"blank", "dash"}:
        return None, presence, None
    convention = configured_convention(presence=presence, **identity)
    return (Decimal("0") if convention else None), presence, convention
