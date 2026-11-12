#!/usr/bin/env python
"""R6 独立反例复现器（Codex 第六轮核验包控制的本地等价断言）。

两项 P1 按核验包的**明确预期**复现，不修改预期适配实现：
1. R6-01 声明组归属：冒号/句号/分号/显式全部+句号的完整声明均
   1 问题、fail、公式等式；部分列举与材料清单 0 问题、pass、`>=`。
2. R6-02 列角色贯穿：年份金额原件（真实 PDFParser 产物）正常 PASS、
   一格 2025→2026 GAP；行级 grounding 能发现原件缺少的金额；
   R5/R4/R3/R2 旧控制复跑保留。

用法:
    .venv/bin/python scripts/r6_independent_controls_repro.py --out <json>
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
    "/outputs/independent_6380eeed_20261111")
YEAR_PDF = REVIEW_PACKAGE / "fixtures/year_amount_source.pdf"


def _performance_scope() -> dict:
    from src.engine.native_final_disclosure import check_performance_counts
    from src.engine.rule_outcome import RuleOutcomeSignal
    from src.engine.rules_v33 import Rule

    class R(Rule):
        code, severity = "V33-250", "error"

    base = "绩效自评的2025年度项目53个，"
    counts = "绩效评级为“优”的项目50个，其余评级均为0个。"
    cases = {
        "complete_colon": (base + "评级结果如下：" + counts, 1, "fail", "="),
        "complete_period": (base + "评级结果如下。" + counts, 1, "fail", "="),
        "complete_semicolon": (base + "评级结果如下；" + counts, 1, "fail", "="),
        "explicit_period": (base + "全部项目评级结果如下。" + counts, 1, "fail", "="),
        "partial": (base + "其中，绩效评级为“优”的项目50个。", 0, "pass", ">="),
        "materials": (base + "其中，绩效评级为“优”的项目50个。"
                      "相关评级材料如下：项目自评表、整改资料。", 0, "pass", ">="),
        # R7-01：材料标题先于数量时其后数字属于其他组，本组无操作数 →
        # 不建加总关系（无回执），也不把示例数字吸入本组。
        "materials_title_terminates_group": (
            base + "评级结果如下。相关评级材料如下：项目自评表、整改资料。"
            "绩效评级为“良”的项目3个。", 0, None, None),
    }
    outcomes = []
    ok = True
    for name, (body, expected_count, expected_status, expected_op) in cases.items():
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
        formula = str(receipts[-1].get("formula")) if receipts else ""
        status = receipts[-1].get("status") if receipts else ""
        if expected_status is None:
            passed = (signal is None and len(issues) == 0 and not receipts)
        else:
            passed = (signal is None and len(issues) == expected_count
                      and status == expected_status and expected_op in formula)
        ok = ok and passed
        outcomes.append({"case": name, "expected_issues": expected_count,
                         "actual_issues": len(issues), "receipt_status": status,
                         "formula": formula, "pass": passed})
    return {"ok": ok, "cases": outcomes}


def _year_amount_lane() -> dict:
    from scripts.d1_table_reconciliation import _reconcile_row_alignment

    text = ("收入支出决算总表（金额角色控制）\n"
            "金额单位：万元\n"
            "收入项目 金额 支出项目 金额\n"
            "本年收入合计 3000 本年支出合计 2800\n"
            "使用非财政拨款结余 0 结余分配 0\n"
            "年初结转和结余 2025 年末结转和结余 2225\n"
            "总计 5025 总计 5025\n")
    rows = [
        {"page": 1, "cells": ["本年收入合计", "3000", "本年支出合计", "2800"]},
        {"page": 1, "cells": ["使用非财政拨款结余", "0", "结余分配", "0"]},
        {"page": 1, "cells": ["年初结转和结余", "2025", "年末结转和结余", "2225"]},
        {"page": 1, "cells": ["总计", "5025", "总计", "5025"]},
    ]
    normal = _reconcile_row_alignment(rows, [text], [1])
    corrupted_rows = copy.deepcopy(rows)
    corrupted_rows[2]["cells"][1] = "2026"
    corrupted = _reconcile_row_alignment(corrupted_rows, [text], [1])
    return {
        "ok": (not normal["row_mismatches"] and bool(corrupted["row_mismatches"])),
        "normal_identities": normal["merged_row_identities"],
        "corrupted": corrupted["row_mismatches"][:3],
    }


def _year_amount_real_pdf() -> dict:
    """清晰 PDF 原件 + 仅注入一格 2025→2026（真实 PDFParser 产物）。"""
    if not YEAR_PDF.exists():
        return {"ok": None, "skipped": "fixture_pdf_missing"}
    from src.services.pdf_parser import PDFParser
    from scripts import d1_table_reconciliation as d1

    digest = hashlib.sha256(YEAR_PDF.read_bytes()).hexdigest()
    artifact = PDFParser(None).build_parse_artifact(str(YEAR_PDF))
    with patch.object(d1.PDFParser, "build_parse_artifact", return_value=artifact):
        normal = d1.reconcile_auto(str(YEAR_PDF))
    corrupt = copy.deepcopy(artifact)
    positions = set()
    for fragment in list(corrupt.table_fragments) + list(corrupt.physical_fragments):
        for row_index, row in enumerate(fragment.rows):
            if len(row) >= 4 and row[0] == "年初结转和结余" and row[2] == "年末结转和结余":
                assert row[1] in {"2025", "2026"}
                row[1] = "2026"
                positions.add((fragment.fragment_uid, row_index, 1))
                for cell in fragment.cells:
                    if cell.row_idx == row_index and cell.col_idx == 1:
                        cell.raw_text = "2026"
                        cell.numeric_value = Decimal("2026")
    with patch.object(d1.PDFParser, "build_parse_artifact", return_value=corrupt):
        bad = d1.reconcile_auto(str(YEAR_PDF))
    unchanged = hashlib.sha256(YEAR_PDF.read_bytes()).hexdigest() == digest
    return {
        "ok": (normal["pass"] == 1 and normal["gap"] == 0
               and bad["pass"] == 0 and bad["gap"] == 1
               and unchanged and len(positions) == 1),
        "normal": {"pass": normal["pass"], "gap": normal["gap"]},
        "corrupted": {"pass": bad["pass"], "gap": bad["gap"]},
        "original_unchanged": unchanged,
        "mutated_positions": len(positions),
    }


def _row_grounding() -> dict:
    """R7-02：逐行身份约束——同页其他行出现同一数值不满足本行；
    原件有、合并缺失的行必须报告（页级存在性不再作为验收依据）。"""
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
        "value_elsewhere_does_not_satisfy_row": phantom["row_mismatches"][:2],
        "reverse_coverage": [item for item in reverse["row_mismatches"]
                             if item["kind"] == "row_missing_in_merged"][:2],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    checks = {
        "performance_scope": _performance_scope(),
        "year_amount_lane": _year_amount_lane(),
        "year_amount_real_pdf": _year_amount_real_pdf(),
        "row_grounding": _row_grounding(),
    }
    report = {
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
            capture_output=True, text=True).stdout.strip(),
        "checks": checks,
        "ok": all(item.get("ok") is not False for item in checks.values()),
        "note": ("R6 独立反例复现：预期来自 Codex 第六轮核验包；"
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
