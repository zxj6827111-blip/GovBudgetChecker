#!/usr/bin/env python3
"""B1 小数复算 v4（R2-04）：先绑定单位和表达式，再规范化数值。

v1 只验证硬编码 expected/diff 自洽；v2 把 0.01 与显示包络相加；v3 分了
来源/算术/规则适用/材料判定，但直接比较裸数：100.00 元 对 100.00 万元
也返回 PASS。

v4 口径（第三轮独立复核 2026-11-04）：
- 单位先行：每个操作数和 reported 都必须能解析出单位（声明字段、引文单位、
  annotation 表头 unit 三者之一）；缺失或互相冲突 = PENDING，不推测填充。
- 表达式绑定：``amount`` 要求所有操作数与 expected 同单位，reported 同单位；
  跨单位比较必须显式声明 ``kind=conversion``（并给出 expected_unit /
  reported_unit），否则 PENDING，不能拿裸数比较。
- conversion 实际换算到 reported 单位，按 ``target_precision``（缺省取
  reported 字面量小数位）做 ROUND_HALF_UP；截尾/错误进位不能通过。
- 容差不变：单项 0.01；多项 (n+1)*0.005；百分比 0.1 个百分点；换算 0。
- 来源、算术、规则适用、材料判定继续分栏；算术正确不等于材料 PASS。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, List, Optional, Tuple

AMOUNT_SINGLE_TOLERANCE = Decimal("0.01")
PERCENT_TOLERANCE = Decimal("0.1")
_MONEY_UNITS = ("亿元", "万元", "元")
#: 换算到万元的比例；元→万元多 4 位，亿元→万元少 4 位。
_MONEY_TO_WAN = {
    "亿元": Decimal("10000"),
    "万元": Decimal("1"),
    "元": Decimal("0.0001"),
}


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


def _decimals_of(text: str) -> Optional[int]:
    """字面量的小数位；只有一个数值时用于推断目标精度。"""
    text = str(text or "")
    if len(re.findall(r"-?\d[\d,]*(?:\.\d+)?", text)) != 1:
        return None
    match = re.search(r"-?\d[\d,]*(?:\.(\d+))?", text)
    return len(match.group(1)) if match and match.group(1) else 0


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
                  declared_unit: str = "") -> Tuple[bool, str, str]:
    """页码+引文必须存在；返回 (ok, 单位, 原因)，单位无法解析时 ok=False。

    ``source_kind=rule_constant`` 只用于“应有值”本身不是原文数值的规则常量
    （如并列占比合计 100%）；必须给出规则依据和单位，不能拿它替代材料数值。
    """
    if isinstance(source, dict) and source.get("source_kind") == "rule_constant":
        basis = str(source.get("rule_basis") or "").strip()
        unit = str(source.get("unit") or "").replace("％", "%")
        declared = str(declared_unit or "").replace("％", "%")
        if not basis:
            return False, "", "规则常量缺少 rule_basis"
        if not unit:
            return False, "", "规则常量缺少单位"
        if declared and declared != unit:
            return False, "", f"规则常量单位 {unit} ≠ 声明单位 {declared}"
        return True, unit, ""
    if not isinstance(source, dict):
        return False, "", "缺少页码+引文来源"
    page, quote = source.get("page"), str(source.get("quote") or "")
    if not isinstance(page, int) or not quote:
        return False, "", "缺少页码+引文来源"
    matched = [value for value in values
               if value.get("page") == page and str(value.get("quote") or "") == quote]
    if not matched:
        return False, "", "引文未在 annotation 的页码/原文中找到"
    numbers = _numbers(quote)
    if len(numbers) != 1:
        return False, "", "引文包含多个数值，无法唯一绑定"
    if numbers[0] != expected_value:
        return False, "", f"引文数值 {numbers[0]} ≠ 声明值 {expected_value}"
    declared = str(declared_unit or "").replace("％", "%")
    quote_unit = _unit_of(quote)
    meta_units = {str(value.get("unit") or "").replace("％", "%")
                  for value in matched if value.get("unit")}
    if len(meta_units) > 1:
        return False, "", f"annotation 表头单位冲突：{sorted(meta_units)}"
    meta_unit = next(iter(meta_units), "")
    resolved = {unit for unit in (declared, quote_unit, meta_unit) if unit}
    if len(resolved) > 1:
        return False, "", f"单位冲突：声明 {declared or '-'}、引文 {quote_unit or '-'}、表头 {meta_unit or '-'}"
    if not resolved:
        return False, "", "单位未绑定（声明/引文/表头均无单位）"
    return True, next(iter(resolved)), ""


def _factor(from_unit: str, to_unit: str) -> Optional[Decimal]:
    if from_unit == to_unit:
        return Decimal("1")
    if from_unit == "%" or to_unit == "%":
        return None
    if from_unit not in _MONEY_TO_WAN or to_unit not in _MONEY_TO_WAN:
        return None
    return _MONEY_TO_WAN[from_unit] / _MONEY_TO_WAN[to_unit]


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
        "units": "",
        "reported_unit": "",
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

    numbers: List[Decimal] = []
    units: List[str] = []
    source_ok = True
    for index, operand in enumerate(operands):
        value = _dec(operand.get("value"))
        if value is None:
            source_ok = False
            result["reasons"].append(f"操作数[{index}] 无法解析")
            continue
        numbers.append(value)
        ok, unit, reason = _match_source(operand, value, values,
                                         str(operand.get("unit") or ""))
        if not ok:
            source_ok = False
            result["reasons"].append(f"操作数[{index}]：{reason}")
            continue
        units.append(unit)
    expected = _dec(row.get("expected"))
    reported = _dec(row.get("reported"))
    if expected is None or reported is None:
        source_ok = False
        result["reasons"].append("expected/reported 无法解析")
    reported_unit = ""
    if reported is not None:
        ok, reported_unit, reason = _match_source(
            row.get("reported_source"), reported, values,
            str(row.get("reported_unit") or ""))
        if not ok:
            source_ok = False
            result["reasons"].append(f"reported：{reason}")
    result["source_ok"] = source_ok
    result["reported_unit"] = reported_unit
    if not source_ok:
        return result
    if len(units) != len(numbers):
        result["reasons"].append("存在未绑定单位的操作数")
        return result
    distinct_operand_units = sorted(set(units))
    result["units"] = ",".join(distinct_operand_units)

    expected_unit = str(row.get("expected_unit") or "").replace("％", "%")
    if kind == "percent":
        if distinct_operand_units != ["%"] or expected_unit not in ("", "%") \
                or reported_unit != "%":
            result["reasons"].append(
                f"百分比表达式单位不一致：操作数 {distinct_operand_units}、expected {expected_unit or '-'}、reported {reported_unit or '-'}")
            return result
        expected_unit = "%"
    else:
        if any(unit not in _MONEY_TO_WAN for unit in units):
            result["reasons"].append(f"金额表达式出现非金额单位：{distinct_operand_units}")
            return result
        if expected_unit and expected_unit not in _MONEY_TO_WAN:
            result["reasons"].append(f"expected 单位不可换算：{expected_unit}")
            return result
        if kind == "amount":
            # 普通金额比较不允许混合单位：跨单位必须显式 conversion。
            if len(distinct_operand_units) != 1:
                result["reasons"].append(
                    f"混合单位表达式未声明 conversion：{distinct_operand_units}")
                return result
            if expected_unit and expected_unit != distinct_operand_units[0]:
                result["reasons"].append(
                    f"expected 单位 {expected_unit} 与操作数 {distinct_operand_units[0]} 不一致，需声明 conversion")
                return result
            expected_unit = distinct_operand_units[0]
            if reported_unit != expected_unit:
                result["reasons"].append(
                    f"reported 单位 {reported_unit} 与 expected {expected_unit} 不一致，需声明 conversion")
                return result
        else:  # conversion
            if not expected_unit:
                result["reasons"].append("conversion 未声明 expected_unit")
                return result
            if not reported_unit:
                result["reasons"].append("conversion 未声明 reported_unit")
                return result

    normalized = []
    for value, unit in zip(numbers, units):
        factor = _factor(unit, expected_unit)
        if factor is None:
            result["reasons"].append(f"无法把 {unit} 换算到 {expected_unit}")
            return result
        normalized.append(value * factor)
    total = sum(normalized, Decimal("0"))
    result["arithmetic_ok"] = total == expected
    if not result["arithmetic_ok"]:
        result["reasons"].append(f"操作数之和 {total}{expected_unit} ≠ expected {expected}{expected_unit}")
        return result

    if kind == "conversion":
        target_unit = reported_unit
        factor = _factor(expected_unit, target_unit)
        if factor is None:
            result["reasons"].append(f"无法把 {expected_unit} 换算到 {target_unit}")
            return result
        converted = expected * factor
        precision = row.get("target_precision")
        if precision is None:
            precision = _decimals_of(str(row.get("reported") or ""))
        if precision is None:
            precision = _decimals_of(str((row.get("reported_source") or {}).get("quote") or ""))
        if precision is None:
            result["reasons"].append("conversion 无法确定目标精度")
            return result
        quantum = Decimal(1).scaleb(-int(precision))
        rounded = converted.quantize(quantum, rounding=ROUND_HALF_UP)
        difference = abs(rounded - reported)
        result["difference"] = str(difference)
        result["tolerance"] = "0"
        result["envelope"] = ""
        if difference == 0:
            result["material_verdict"] = "PASS"
            result["result"] = "PASS"
            return result
        result["material_verdict"] = "FAIL"
        result["result"] = "FAIL"
        result["reasons"].append(
            f"{converted} 按 {int(precision)} 位 ROUND_HALF_UP 得 {rounded}{target_unit} ≠ reported {reported}{target_unit}")
        return result

    reported_value = reported
    if reported_unit != expected_unit:
        factor = _factor(reported_unit, expected_unit)
        if factor is None:
            result["reasons"].append(f"无法把 reported 从 {reported_unit} 换算到 {expected_unit}")
            return result
        reported_value = reported * factor
    difference = abs(expected - reported_value)
    result["difference"] = str(difference)
    n = len(operands)
    if kind == "percent":
        tolerance = PERCENT_TOLERANCE
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
            "units": verdict.get("units"),
            "expected": row.get("expected"),
            "reported": row.get("reported"),
            "reported_unit": verdict.get("reported_unit"),
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
                    "note": "PASS 只表示单位绑定、算术与材料差都在冻结容差内；PENDING 不计入 PASS。"},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "counts": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
