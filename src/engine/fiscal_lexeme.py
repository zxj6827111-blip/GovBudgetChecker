"""财政数值的保真词法；只识别原文，不修复数字或推算金额。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

LEXER_VERSION = "fiscal-lexeme-v1"
FACTORS = {
    "元": Decimal("0.0001"),
    "千元": Decimal("0.1"),
    "万元": Decimal(1),
    "百万元": Decimal(100),
    "亿元": Decimal(10000),
}
_NUMBER = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_UNIT = re.compile(r"(百万元|亿元|万元|千元|元)$")


@dataclass(frozen=True)
class NumericLexeme:
    raw: str
    kind: str
    value: Decimal | None = None
    unit: str | None = None
    reason: str | None = None


def lex_numeric(raw: object, *, expected: str | None = None) -> NumericLexeme:
    text = "" if raw is None else str(raw)
    token = unicodedata.normalize("NFKC", text).strip().replace("−", "-")
    if not token:
        return NumericLexeme(text, "blank")
    if token in {"-", "—", "–", "--", "──", "―"}:
        return NumericLexeme(text, "dash")
    if token in {"无此项", "不涉及", "无", "不适用"}:
        return NumericLexeme(text, "not_applicable")
    if re.fullmatch(r"(?:X+|×+|某某|\.{2,}|…+)", token, re.I):
        return NumericLexeme(text, "placeholder")
    if re.fullmatch(r"\d{4}[-年/]\d{1,2}(?:[-月/]\d{1,2}日?)?", token):
        return NumericLexeme(text, "date")
    if expected == "code":
        return (
            NumericLexeme(text, "code")
            if re.fullmatch(r"\d+(?:-\d+)*", token)
            else NumericLexeme(text, "invalid", reason="invalid_code")
        )
    # 内部数字空格可能来自相邻列，不能删除后制造新的金额。
    if re.search(r"\d\s+\d", token):
        return NumericLexeme(text, "invalid", reason="separated_digits")
    unit_match = _UNIT.search(token)
    unit = unit_match.group(1) if unit_match else None
    if unit:
        token = token[: unit_match.start()].rstrip()
    kind = "amount" if unit else "number"
    if token.endswith("%"):
        if unit:
            return NumericLexeme(text, "invalid", reason="mixed_types")
        kind, token = "ratio", token[:-1].rstrip()
    negative = token.startswith("(") and token.endswith(")")
    if negative:
        token = token[1:-1].strip()
        if token.startswith(("+", "-")):
            return NumericLexeme(text, "invalid", reason="duplicate_sign")
    if not _NUMBER.fullmatch(token):
        return NumericLexeme(text, "invalid", unit=unit, reason="invalid_numeric_literal")
    value = Decimal(token.replace(",", ""))
    if negative:
        value = -value
    if expected == "quantity" and not unit and kind != "ratio":
        kind = "quantity"
    return NumericLexeme(text, kind, value, unit)


def amount_in_wan(raw: object, unit: str | None = None) -> Decimal | None:
    lexeme = lex_numeric(raw)
    if lexeme.kind not in {"number", "amount"}:
        return None
    # 显式单位冲突必须暴露，缺省优先级不能吞掉冲突。
    if lexeme.unit and unit and lexeme.unit != unit:
        return None
    factor = FACTORS.get(lexeme.unit or unit)
    return lexeme.value * factor if factor is not None else None


@dataclass(frozen=True)
class UnitResolution:
    unit: str | None
    source: str | None
    conflicts: tuple[tuple[str, str], ...] = ()


def resolve_unit(*, cell=None, column=None, table=None, continuation=None) -> UnitResolution:
    scopes = [
        (name, value)
        for name, value in (
            ("cell", cell),
            ("column", column),
            ("table", table),
            ("continuation", continuation),
        )
        if value
    ]
    if not scopes:
        return UnitResolution(None, None)
    source, unit = scopes[0]
    conflicts = tuple((name, value) for name, value in scopes[1:] if value != unit)
    return UnitResolution(unit if unit in FACTORS and not conflicts else None, source, conflicts)
