"""空白/横线的计算解释与原始值分离。

生产默认口径（U-5，用户 2026-11-08 确认）：原件网格认证为空白的金额格
（``native_blank_verified``：格内无字形、无图形、非扫描/抽字失败/合并占位）
表示“无发生额”，按 0 参与计算。原格本身仍保持空白，不物化成金额事实。

仍保持未知、不按 0 计算的情形：
- 未经网格认证的空白（扫描件、OCR、文本拼表、格内有墨迹等）；
- 横线“—”、“无”、“不适用”、“/”等非空文字（上海语料未出现横线约定，暂不配置）。

``GOVBUDGET_EMPTY_VALUE_CONVENTIONS``：
- 未设置或为空：使用内置的 U-5 约定；
- ``off`` / ``none`` / ``false`` / ``0``：不使用任何约定（空白一律未知，供对照实验）；
- 文件路径：以文件内的约定替换内置约定（逐条须有来源与批准）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path

#: U-5 用户确认约定。范围：全部地区、年度、表种与单元格（“*”）；只认网格认证空白。
USER_CONFIRMED_BLANK_ZERO = {
    "id": "U-5-verified-blank-zero-20261108",
    "source_kind": "user_confirmed",
    "approved": True,
    "confirmation_reference": "用户 2026-11-08 确认：原件中经网格认证的空白金额格表示无发生额（=0），全系统默认",
    "region": "",
    "years": "*",
    "table_codes": "*",
    "cell_ranges": "*",
    "semantics": {"blank": "zero"},
}

_DISABLED_VALUES = {"off", "none", "false", "0", "disabled"}


def _policies():
    setting = str(os.environ.get("GOVBUDGET_EMPTY_VALUE_CONVENTIONS") or "").strip()
    if not setting:
        return [USER_CONFIRMED_BLANK_ZERO]
    if setting.lower() in _DISABLED_VALUES:
        return []
    return json.loads(Path(setting).read_text(encoding="utf-8"))


def _within(value, allowed):
    return allowed == "*" or value in (allowed or [])


def configured_convention(*, region, year, table_code, row, column, presence):
    matches = []
    for policy in _policies():
        kind = policy.get("source_kind")
        approved = (
            (
                kind == "official_document"
                and all(policy.get(key) for key in ("document", "issuer", "reference"))
            )
            or (
                kind == "user_confirmed"
                and policy.get("approved") is True
                and policy.get("confirmation_reference")
            )
            or (kind == "isolated_test" and os.environ.get("TESTING", "").lower() == "true")
        )
        if not approved or not policy.get("id") or policy.get("region") is None:
            continue
        if (
            not str(region or "").startswith(policy["region"])
            or not _within(year, policy.get("years"))
            or not _within(table_code, policy.get("table_codes"))
            or policy.get("semantics", {}).get(presence) != "zero"
        ):
            continue
        ranges = policy.get("cell_ranges", [])
        if ranges == "*" or any(
            bounds["rows"][0] <= row <= bounds["rows"][1]
            and bounds["columns"][0] <= column <= bounds["columns"][1]
            for bounds in ranges
        ):
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
    presence = (
        "blank"
        if not text
        else "dash"
        if text in {"-", "—", "–", "－"}
        else "not_applicable"
        if text in {"无", "不适用", "/", "未披露"}
        else "unrecognized"
    )
    if presence == "blank" and not native_blank_verified or presence not in {"blank", "dash"}:
        return None, presence, None
    convention = configured_convention(presence=presence, **identity)
    return (Decimal("0") if convention else None), presence, convention
