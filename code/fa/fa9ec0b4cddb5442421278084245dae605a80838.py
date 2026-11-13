#!/usr/bin/env python3
"""B1 小数复算 v3（R-04）：来源、算术、规则适用、材料判定分开。

v1 的 ``result=PASS`` 只验证“操作数之和等于硬编码 expected”和“硬编码 diff
自洽”，从不比较容差；v2 又把 0.01 政策容差与显示包络相加，放宽到 0.025。

v3 口径（独立复核 20261104 R-04）：
- 容差：单项 = 0.01；多项加总 = (n+1)*0.005（不再加 0.01）；百分比 =
  0.1 个百分点；元→万元换算 = 0（精确）。
- 来源核验：每个操作数与 reported 都必须有页码+引文，且引文的数值等于
  声明值；单位声明与引文/annotation 不一致即拒收。来源不足 = PENDING。
- 算术复算：操作数之和必须等于 expected；不等 = PENDING（spec 错误）。
- 规则适用：``applicable=false`` 或未知表达式 = PENDING，不静默放过。
- 材料判定：差 ≤ 容差 = PASS；差 > 容差但给出完整子缺陷链 = PENDING_REVIEW
  （不冒充 PASS）；否则 FAIL。
- 输出分开 ``arithmetic_ok`` 与 ``material_verdict``：已知 defect 的差额
  算对，只说明算术复算正确，不等于材料 PASS。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional

AMOUNT_SINGLE_TOLERANCE = Decimal("0.01")
PERCENT_TOLERANCE = Decimal("0.1")
_MONEY_UNITS = ("亿元", "万元", "元")


def _dec(value) -> Optional[Decimal]:
    try:
        return Decimal(str(value).replace(",", "").replace("，", ""))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _numbers(text: str) -> List[Decimal]:
    values = []
    for token in re.findall(r"-?\d[\d,]*(?:\.\d+)?", str(text or "").replace("，", ",")):
        number = _dec(token)
        if number is not None:
            values.append(number)
    return values


def _unit_of(text: str) -> str:
    for unit in _MONEY_UNITS:
        if unit in str(text or ""):
            return unit
    if "%" in str(text or "") or "％" in str(text or ""):
        return "%"
    return ""


def _annotation_values(annotation: dict) -> Dict[str, List[dict]]:
    result: Dict[str, List[dict]] = {}
    for check in annotation.get("checks") or []:
        rows = []
        for item in check.get("items") or []:
            for value in item.get("values") or []:
                if isinstance(value, dict):
                    rows.append(value)
        result[check.get("check_id")] = rows
    return result


def _match_source(source: Optional[dict], expected_value: Decimal, values: List[dict],
                  declared_unit: str = "") -> tuple[bool, str]:
    """页码+引文必须存在，且引文数值/单位与声明一致。"""
    if not isinstance(source, dict):
        return False, "缺少页码+引文来源"
    page, quote = source.get("page"), str(source.get("quote") or "")
    if not isinstance(page, int) or not quote:
        return False, "缺少页码+引文来源"
    if not any(value.get("page") == page and str(value.get("quote") or "") == quote
               for value in values):
        return False, "引文未在 annotation 的页码/原文中找到"
    numbers = _numbers(quote)
    if len(numbers) != 1:
        return False, "引文包含多个数值，无法唯一绑定"
    if numbers[0] != expected_value:
        return False, f"引文数值 {numbers[0]} ≠ 声明值 {expected_value}"
    quote_unit = _unit_of(quote)
    if declared_unit:
        if quote_unit and quote_unit != declared_unit:
            return False, f"引文单位 {quote_unit} ≠ 声明单位 {declared_unit}"
        if not quote_unit:
            # 单位只在表头声明时允许缺省，但 annotation 的 unit 必须一致。
            matched = [value for value in values
                       if value.get("page") == page and str(value.get("quote") or "") == quote]
            meta_unit = str((matched[0].get("unit") if matched else "") or "")
            if meta_unit and meta_unit != declared_unit:
                return False, f"annotation 单位 {meta_unit} ≠ 声明单位 {declared_unit}"
    return True, ""


def evaluate(row: dict, annotations: dict) -> dict:
    sample = str(row.get("sample_id") or "")
    check_id = str(row.get("check_id") or "")
    kind = str(row.get("kind") or "amount")
    operands = row.get("operands") or []
    values = _annotation_values(annotations.get(sample) or {}).get(check_id, [])
    result = {
        "source_ok": False,
        "arithmetic_ok": False,
        "rule_applicable": bool(row.get("applicable", True)),
        "material_verdict": "PENDING",
        "result": "PENDING",
        "difference": "",
        "tolerance": "",
        "envelope": "",
        "n": len(operands),
        "reasons": [],
    }
    if kind not in {"amount", "percent", "conversion"}:
        result["reasons"].append(f"未知表达式类型 {kind}")
        return result
    if not result["rule_applicable"]:
        result["reasons"].append(str(row.get("applicability_note") or "规则适用性未确认"))
        return result
    if row.get("n") is not None and int(row["n"]) != len(operands):
        result["reasons"].append(f"声明 n={row['n']} ≠ 实际操作数 {len(operands)}")
        return result
    if not operands:
        result["reasons"].append("没有操作数")
        return result

    numbers = []
    source_ok = True
    for index, operand in enumerate(operands):
        value = _dec(operand.get("value"))
        if value is None:
            source_ok = False
            result["reasons"].append(f"操作数[{index}] 无法解析")
            continue
        numbers.append(value)
        ok, reason = _match_source(operand, value, values, str(operand.get("unit") or ""))
        if not ok:
            source_ok = False
            result["reasons"].append(f"操作数[{index}]：{reason}")
    expected = _dec(row.get("expected"))
    reported = _dec(row.get("reported"))
    if expected is None or reported is None:
        source_ok = False
        result["reasons"].append("expected/reported 无法解析")
    if expected is not None:
        ok, reason = _match_source(row.get("reported_source"), reported, values,
                                   str(row.get("reported_unit") or ""))
        if not ok:
            source_ok = False
            result["reasons"].append(f"reported：{reason}")
    result["source_ok"] = source_ok
    if not source_ok:
        return result

    total = sum(numbers, Decimal("0"))
    result["arithmetic_ok"] = total == expected
    if not result["arithmetic_ok"]:
        result["reasons"].append(f"操作数之和 {total} ≠ expected {expected}")
        return result

    difference = abs(expected - reported)
    result["difference"] = str(difference)
    n = len(operands)
    if kind == "percent":
        tolerance = PERCENT_TOLERANCE
        envelope = None
    elif kind == "conversion":
        tolerance = Decimal("0")
        envelope = None
    elif n > 1:
        envelope = (Decimal(n) + 1) * Decimal("0.005")
        tolerance = envelope
    else:
        envelope = None
        tolerance = AMOUNT_SINGLE_TOLERANCE
    result["tolerance"] = str(tolerance)
    result["envelope"] = str(envelope) if envelope is not None else ""

    if difference <= tolerance:
        result["material_verdict"] = "PASS"
        result["result"] = "PASS"
        return result
    explained = row.get("explained_by") or []
    if explained:
        chain_ok = _chain_explains(explained, difference, annotations)
        if chain_ok:
            result["material_verdict"] = "PENDING_REVIEW"
            result["result"] = "PENDING_REVIEW"
            result["reasons"].append("差额由完整子缺陷链解释，仍不冒充 PASS")
            return result
        result["reasons"].append("子缺陷链不完整或合计不等于差额")
    result["material_verdict"] = "FAIL"
    result["result"] = "FAIL"
    result["reasons"].append(f"差 {difference} 超过容差 {tolerance}")
    return result


def _chain_explains(entries: List[dict], difference: Decimal, annotations: dict) -> bool:
    total = Decimal("0")
    for entry in entries:
        verdict = evaluate(entry, annotations)
        # 子链只接受“算术成立、来源完整”的条目；子项自身是否 PASS 不影响
        # 汇总差额，但来源不完整则整链不可信。
        if not (verdict.get("source_ok") and verdict.get("arithmetic_ok")):
            return False
        value = _dec(entry.get("reported"))
        expected = _dec(entry.get("expected"))
        if value is None or expected is None:
            return False
        total += abs(expected - value)
    return total == difference


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    annotations = {}
    for path in sorted(args.annotations.glob("*/annotation.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        annotations[payload.get("sample_id") or path.parent.name] = payload

    rows = []
    for row in spec.get("rows") or []:
        verdict = evaluate(row, annotations)
        rows.append({
            "sample_id": row.get("sample_id"),
            "check_id": row.get("check_id"),
            "item": row.get("item"),
            "kind": row.get("kind") or "amount",
            "operands": " + ".join(f"{op.get('value')}@{op.get('page')}" for op in row.get("operands") or []),
            "n": verdict.get("n"),
            "expected": row.get("expected"),
            "reported": row.get("reported"),
            "difference": verdict.get("difference", ""),
            "tolerance": verdict.get("tolerance", ""),
            "envelope": verdict.get("envelope", ""),
            "source_ok": verdict.get("source_ok"),
            "arithmetic_ok": verdict.get("arithmetic_ok"),
            "rule_applicable": verdict.get("rule_applicable"),
            "material_verdict": verdict.get("material_verdict"),
            "result": verdict.get("result"),
            "reasons": "；".join(verdict.get("reasons") or []),
        })
    with (args.out / "decimal_checks_v3.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    counts = {}
    for row in rows:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    (args.out / "decimal_checks_v3_summary.json").write_text(
        json.dumps({"rows": len(rows), "counts": counts,
                    "note": "PASS 只表示材料差在冻结容差内；PENDING_REVIEW 是差额有子链解释但未 PASS；算术正确不等于材料 PASS。"},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "counts": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
