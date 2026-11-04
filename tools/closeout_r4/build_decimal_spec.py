#!/usr/bin/env python3
"""从 B1 的 v1 decimal_checks.csv + annotation 重建可核验 spec。

每个操作数按“同一 sample/check 内数值相等”的唯一匹配绑定页码与引文；
唯一匹配失败时保留 value、不写 page/quote，由 v2 判 PENDING。
"""
from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _dec(text):
    try:
        return Decimal(str(text).replace(",", "").replace("，", ""))
    except (InvalidOperation, TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    annotations = {}
    for path in sorted(args.annotations.glob("*/annotation.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        annotations[payload["sample_id"]] = payload

    def values_for(sample: str, check_id: str):
        payload = annotations.get(sample) or {}
        rows = []
        for check in payload.get("checks") or []:
            if check.get("check_id") != check_id:
                continue
            for item in check.get("items") or []:
                for value in item.get("values") or []:
                    if isinstance(value, dict):
                        rows.append(value)
        return rows

    spec_rows = []
    with args.csv.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            sample = row["sample_id"]
            check_id = row["check_id"]
            values = values_for(sample, check_id)
            operands = []
            for token in str(row["operands"]).split("+"):
                number = _dec(token)
                matches = [v for v in values if _dec(v.get("quote")) == number]
                unique = matches[0] if len(matches) == 1 else None
                operand = {"value": str(number)}
                if unique is not None:
                    operand["page"] = unique.get("page")
                    operand["quote"] = unique.get("quote")
                    if unique.get("unit"):
                        operand["unit"] = unique["unit"]
                operands.append(operand)
            reported = _dec(row["reported"])
            reported_matches = [v for v in values if _dec(v.get("quote")) == reported]
            reported_source = None
            if len(reported_matches) == 1:
                src = reported_matches[0]
                reported_source = {"page": src.get("page"), "quote": src.get("quote")}
                if src.get("unit"):
                    reported_source["unit"] = src["unit"]
            spec_rows.append({
                "sample_id": sample,
                "check_id": check_id,
                "item": row["item"],
                "kind": "percent" if str(row.get("unit")) == "%" else "amount",
                "operands": operands,
                "n": len(operands),
                "expected": row["expected"],
                "reported": row["reported"],
                "reported_source": reported_source,
                "applicable": not (sample == "B1-2025-U01" and check_id == "GS-N02"
                                   and "财政拨款收入表vs说明" in row["item"]),
                "applicability_note": (
                    "两侧分别是收入表财政拨款收入与说明另一口径合计，不是同一等式；"
                    "按未知表达式 PENDING，不据此判材料 FAIL"
                    if sample == "B1-2025-U01" and check_id == "GS-N02"
                    and "财政拨款收入表vs说明" in row["item"] else ""
                ),
            })
    args.out.write_text(
        json.dumps({"note": "由 v1 decimal_checks.csv + annotation 重建；页码/引文唯一匹配才写入。",
                    "rows": spec_rows}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print(json.dumps({"rows": len(spec_rows),
                      "unbound": sum(1 for row in spec_rows for op in row["operands"] if "page" not in op)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
