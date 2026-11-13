#!/usr/bin/env python3
"""B1 小数复算 v2：把“算出来的差”与“容差内”分开判定。

v1（B1 run 的 build_decimal_checks.py）的问题：
- ``result=PASS`` 只看 ``sum_ok``（操作数之和等于硬编码 expected）和
  ``diff_ok``（硬编码 diff 等于 expected-reported），从不比较容差；
  4116.79 万元、2.89 个百分点的差也会 PASS。
- ``r1_n=0`` 的行直接不计算包络，过容差判断被跳过。
- 百分比行套用金额包络 (n+1)*0.005，百分点容差没有单独口径。
- 数字全部硬编码，没有绑定 annotation 的页码/原文，无法证明来源。

v2 口径：
- 金额：``tolerance = 0.01``（政策容差）；多操作数加总比较再叠加
  ``(n+1)*0.005`` 的显示舍入包络。n 必须等于实际参与加总的操作数个数，
  不能用文档任意叶子总数扩容。
- 百分比：``tolerance = 0.1`` 个百分点，不叠加金额包络。
- 每个操作数必须给出 ``page`` 与 ``quote``，且能在对应 annotation 的
  check 值里逐字找到；找不到即 PENDING（不静默放过）。
- 判定：``PASS``（差≤容差）/ ``FAIL``（差>容差）/ ``PENDING``（来源或
  表达式不可核验）。三者互斥，PENDING 不计入 PASS。

用法：
    python3 tools/decimal_checks_v2.py --annotations <run>/annotations \
        --spec tools/decimal_checks_spec.json --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

AMOUNT_POLICY_TOLERANCE = Decimal("0.01")
PERCENT_TOLERANCE = Decimal("0.1")


def _dec(value) -> Decimal:
    return Decimal(str(value).replace(",", "").replace("，", ""))


def _annotation_values(annotation: dict) -> dict:
    """check_id → 该 check 内所有 values（含页码/引文）。"""
    result = {}
    for check in annotation.get("checks") or []:
        rows = []
        for item in check.get("items") or []:
            for value in item.get("values") or []:
                if isinstance(value, dict):
                    rows.append(value)
        result[check.get("check_id")] = rows
    return result


def _quote_present(row: dict, values: list) -> bool:
    page = row.get("page")
    quote = str(row.get("quote") or "")
    if not isinstance(page, int) or not quote:
        return False
    for value in values:
        if value.get("page") == page and str(value.get("quote") or "") == quote:
            return True
    return False


def evaluate(row: dict, annotations: dict) -> dict:
    sample = str(row.get("sample_id") or "")
    check_id = str(row.get("check_id") or "")
    kind = str(row.get("kind") or "amount")
    operands = row.get("operands") or []
    annotation = annotations.get(sample) or {}
    values = _annotation_values(annotation).get(check_id, [])
    source_ok = all(_quote_present(op, values) for op in operands) if operands else False
    try:
        numbers = [_dec(op.get("value")) for op in operands]
        expected = _dec(row.get("expected"))
        reported = _dec(row.get("reported"))
    except (InvalidOperation, TypeError):
        return {"result": "PENDING", "reason": "操作数/期望值无法解析为 Decimal", "source_ok": source_ok}
    if not operands or not source_ok:
        return {"result": "PENDING", "reason": "操作数未绑定到 annotation 页码+原文", "source_ok": False}
    total = sum(numbers, Decimal("0"))
    if total != expected:
        return {"result": "PENDING", "reason": f"操作数之和 {total} ≠ 记录的 expected {expected}", "source_ok": True}
    difference = abs(expected - reported)
    if kind == "percent":
        tolerance = PERCENT_TOLERANCE
        envelope = None
        over = difference > tolerance
    else:
        if len(operands) > 1:
            envelope = (Decimal(len(operands)) + 1) * Decimal("0.005")
            tolerance = AMOUNT_POLICY_TOLERANCE + envelope
        else:
            envelope = None
            tolerance = AMOUNT_POLICY_TOLERANCE
        over = difference > tolerance
    return {
        "result": "FAIL" if over else "PASS",
        "difference": str(difference),
        "tolerance": str(tolerance),
        "envelope": str(envelope) if envelope is not None else "",
        "n": len(operands),
        "sum_ok": True,
        "source_ok": True,
        "reason": "" if not over else f"差 {difference} 超过容差 {tolerance}",
    }


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
        record = {
            "sample_id": row.get("sample_id"),
            "check_id": row.get("check_id"),
            "item": row.get("item"),
            "kind": row.get("kind") or "amount",
            "operands": " + ".join(f"{op.get('value')}@{op.get('page')}" for op in row.get("operands") or []),
            "expected": row.get("expected"),
            "reported": row.get("reported"),
            "difference": verdict.get("difference", ""),
            "tolerance": verdict.get("tolerance", ""),
            "envelope": verdict.get("envelope", ""),
            "n": verdict.get("n", ""),
            "source_ok": verdict.get("source_ok", False),
            "result": verdict["result"],
            "reason": verdict.get("reason", ""),
        }
        rows.append(record)
    with (args.out / "decimal_checks_v2.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    counts = {}
    for row in rows:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    (args.out / "decimal_checks_v2_summary.json").write_text(
        json.dumps({"rows": len(rows), "counts": counts,
                    "note": "PASS 只表示差在各自容差内；PENDING 不计入 PASS。"},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "counts": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
