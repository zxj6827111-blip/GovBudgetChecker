#!/usr/bin/env python
"""R7 独立反例复现器（Codex 第七轮核验包控制的本地等价断言）。

两项 P1 按核验包的**明确预期**复现，不修改预期适配实现：
1. R7-01 声明组边界必须约束实际操作数：完整 53/优50/其余0 与追加
   “其他材料示例 良3个”均 1 问题/fail/等式；部分 53/优50 与追加
   “示例 良10个”均 0 问题/pass/不等式；回执操作数只含本组（优50），
   原文跨度不含材料示例。
2. R7-02 金额列角色由表头栏目确认：mixed/decimal 两份真实 PDF 均
   1 PASS（3 行 6 金额格全核验）；任一金额格变异仍 GAP；同页其他行
   的同值不满足本行（逐行身份约束）。

用法:
    .venv/bin/python scripts/r7_independent_controls_repro.py --out <json>
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

REVIEW_PACKAGE = Path(
    "/Volumes/workSSD/software development/GovBudgetChecker"
    "/outputs/independent_eea723b2_20261111")
FIXTURES = REVIEW_PACKAGE / "fixtures"


def _performance_group() -> dict:
    from src.engine.native_final_disclosure import check_performance_counts
    from src.engine.rule_outcome import RuleOutcomeSignal
    from src.engine.rules_v33 import Rule

    class R(Rule):
        code, severity = "V33-250", "error"

    base = "绩效自评的2025年度项目53个，"
    complete = base + "评级结果如下。绩效评级为“优”的项目50个，其余评级均为0个。"
    partial = base + "其中，绩效评级为“优”的项目50个。"
    example = "相关评级材料如下：其他材料中的示例，绩效评级为“良”的项目{count}个。"
    cases = [
        ("complete", complete, 1, "fail", "=", {"优": 50}, 50),
        ("complete_with_other_material_example",
         complete + example.format(count=3), 1, "fail", "=", {"优": 50}, 50),
        ("partial", partial, 0, "pass", ">=", {"优": 50}, 50),
        ("partial_with_other_material_example",
         partial + example.format(count=10), 0, "pass", ">=", {"优": 50}, 50),
    ]
    outcomes = []
    ok = True
    for name, body, count, status, operator, parts, computed in cases:
        doc = SimpleNamespace(
            page_texts=["甲局\n2025年度部门决算\n十、预算绩效管理情况\n" + body],
            report_kind="final", fiscal_year=2025, organization_name="甲局")
        signal = None
        try:
            issues = check_performance_counts(R(), doc)
        except RuleOutcomeSignal as exc:
            issues = list(getattr(exc, "partial_issues", []) or [])
            signal = str(exc)
        receipts = list(getattr(doc, "fact_receipts", []) or [])
        receipt = receipts[-1] if receipts else {}
        formula = str(receipt.get("formula") or "")
        actual_status = receipt.get("status")
        # 操作数与原文跨度必须同属本组：材料示例 3/10 不得计入。
        spans = [str(source.get("span") or "")
                 for source in receipt.get("disclosure_sources") or []]
        operands_ok = (not any("良" in span or "示例" in span for span in spans))
        if issues:
            location = issues[0].location or {}
            operands_ok = operands_ok and (
                location.get("rating_counts") == parts
                and int(location.get("computed_count") or 0) == computed)
        passed = (signal is None and len(issues) == count
                  and actual_status == status
                  and ("self_evaluation_count " + operator + " sum(") in formula
                  and operands_ok)
        ok = ok and passed
        outcomes.append({
            "case": name, "expected": {"issues": count, "status": status,
                                       "operator": operator, "parts": parts},
            "actual": {"issues": len(issues), "status": actual_status,
                       "formula": formula, "spans": spans[:3],
                       "operands_exclude_material_example": operands_ok},
            "pass": passed,
        })
    return {"ok": ok, "cases": outcomes}


def _mixed_amount_columns() -> dict:
    from src.services.pdf_parser import PDFParser
    from scripts import d1_table_reconciliation as d1

    results = {}
    ok = True
    for name in ("decimal", "mixed"):
        pdf = FIXTURES / f"mixed_amount_source_{name}.pdf"
        if not pdf.exists():
            return {"ok": None, "skipped": f"fixture_{name}_missing"}
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        artifact = PDFParser(None).build_parse_artifact(str(pdf))
        with patch.object(d1.PDFParser, "build_parse_artifact", return_value=artifact):
            normal = d1.reconcile_auto(str(pdf))
        # 任一金额格变异（第一行左金额 +1）必须 GAP，原件不变。
        corrupt = copy.deepcopy(artifact)
        changed = []
        for fragment in list(corrupt.table_fragments) + list(corrupt.physical_fragments):
            for row_index, row in enumerate(fragment.rows):
                if len(row) >= 4:
                    after = _mutate_amount(row[1])
                else:
                    after = None
                if after is not None:
                    before = row[1]
                    row[1] = after
                    changed.append({"fragment": fragment.fragment_uid,
                                    "row": row_index, "before": before,
                                    "after": after})
                    for cell in fragment.cells:
                        if cell.row_idx == row_index and cell.col_idx == 1:
                            cell.raw_text = after
                            cell.numeric_value = Decimal(after)
                    break
            if changed:
                break
        with patch.object(d1.PDFParser, "build_parse_artifact", return_value=corrupt):
            bad = d1.reconcile_auto(str(pdf))
        unchanged = hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
        case_ok = (normal["pass"] == 1 and normal["gap"] == 0
                   and bad["pass"] == 0 and bad["gap"] == 1
                   and unchanged and bool(changed))
        ok = ok and case_ok
        results[name] = {
            "ok": case_ok,
            "normal": {"pass": normal["pass"], "gap": normal["gap"]},
            "mutated": {"pass": bad["pass"], "gap": bad["gap"]},
            "changes": changed[:1],
            "original_unchanged": unchanged,
        }
    return {"ok": ok, "cases": results}


def _mutate_amount(value):
    """保持显示精度的 +1 变异；非数值返回 None。"""
    try:
        text = str(value).replace(",", "")
        decimal = Decimal(text)
    except Exception:  # noqa: BLE001
        return None
    places = len(text.split(".")[1]) if "." in text else 0
    return format(decimal + Decimal(1), f".{places}f")


def _row_identity_constraint() -> dict:
    from scripts.d1_table_reconciliation import _reconcile_row_alignment

    page = ("合计 3000 合计 2800\n"
            "本年收入合计 3000 本年支出合计 2800\n")
    phantom = _reconcile_row_alignment(
        [{"page": 1, "cells": ["本年收入合计", "3000",
                               "本年支出合计", "9999"]}], [page], [1])
    reverse = _reconcile_row_alignment(
        [{"page": 1, "cells": ["合计", "3000", "合计", "2800"]}],
        ["合计 3000 合计 2800\n本年收入合计 3000 本年支出合计 2800\n"], [1])
    return {
        "ok": (bool(phantom["row_mismatches"])
               and any(item["kind"] == "row_missing_in_merged"
                       for item in reverse["row_mismatches"])),
        "value_elsewhere": phantom["row_mismatches"][:2],
        "reverse_coverage": [item for item in reverse["row_mismatches"]
                             if item["kind"] == "row_missing_in_merged"][:2],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    checks = {
        "performance_group_operands": _performance_group(),
        "mixed_amount_columns": _mixed_amount_columns(),
        "row_identity_constraint": _row_identity_constraint(),
    }
    report = {
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
            capture_output=True, text=True).stdout.strip(),
        "checks": checks,
        "ok": all(item.get("ok") is not False for item in checks.values()),
        "note": ("R7 独立反例复现：预期来自 Codex 第七轮核验包；"
                 "ok=None 仅表示本机缺少该输入，不计通过。"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1)[:4000])
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
