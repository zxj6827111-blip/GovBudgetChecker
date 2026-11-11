#!/usr/bin/env python
"""R5 独立反例复现器（Codex 第五轮核验包控制的本地等价断言）。

两项 P1 按核验包的**明确预期**复现，不修改预期适配实现：
1. R5-01 范围：资料标题（“部分项目评级资料如下：…”/“相关评级材料
   如下：…”）不新增评级数量关系——合法部分列举必须保持 0 问题、
   pass、公式 >=；明确完整 53/优50/其余0 仍 fail、公式 =。
2. R5-02 D1：无编码双栏表 6 个金额格全部进入核验（互换两格 GAP）；
   同值 301/302 在合计行金额位是金额；年份形态金额 2025→2026 必须
   GAP；清晰 PDF（若本机存在第五轮 fixture）真实解析注入必须 GAP。

用法:
    .venv/bin/python scripts/r5_independent_controls_repro.py --out <json>
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
    "/outputs/independent_8e948c7a_20261111")
MULTILANE_PDF = REVIEW_PACKAGE / "fixtures/no_code_multilane_source.pdf"


def _performance_scope() -> dict:
    from src.engine.native_final_disclosure import check_performance_counts
    from src.engine.rule_outcome import RuleOutcomeSignal
    from src.engine.rules_v33 import Rule

    class R(Rule):
        code, severity = "V33-250", "error"

    partial = "绩效自评的2025年度项目53个，其中，绩效评级为“优”的项目50个。"
    cases = {
        "partial": (partial, 0, "pass", ">="),
        "materials_a": (partial + "部分项目评级资料如下：项目自评表、评审意见。",
                        0, "pass", ">="),
        "materials_b": (partial + "相关评级材料如下：项目自评表、整改资料。",
                        0, "pass", ">="),
        "materials_before_claims": (
            "绩效自评的2025年度项目53个，相关评级材料如下：自评表。"
            "其中，绩效评级为“优”的项目50个。", 0, "pass", ">="),
        "bare_with_counts": (
            "绩效自评的2025年度项目53个，评级结果如下："
            "绩效评级为“优”的项目50个，其余评级均为0个。", 1, "fail", "="),
        "complete": (
            "绩效自评的2025年度项目53个，全部项目评级结果如下："
            "绩效评级为“优”的项目50个，其余评级均为0个。", 1, "fail", "="),
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
        passed = (signal is None and len(issues) == expected_count
                  and status == expected_status and expected_op in formula)
        ok = ok and passed
        outcomes.append({"case": name, "expected_issues": expected_count,
                         "actual_issues": len(issues), "receipt_status": status,
                         "formula": formula, "pass": passed})
    return {"ok": ok, "cases": outcomes}


def _d1_multilane() -> dict:
    from scripts.d1_table_reconciliation import _reconcile_row_alignment

    text = ("收入支出决算总表\n"
            "收入项目 金额 支出项目 金额\n"
            "本年收入A 100 本年支出A 100\n"
            "本年收入B 200 本年支出B 200\n"
            "收入总计 300 支出总计 300\n")
    rows = [
        {"page": 1, "cells": ["本年收入A", "100", "本年支出A", "100"]},
        {"page": 1, "cells": ["本年收入B", "200", "本年支出B", "200"]},
        {"page": 1, "cells": ["收入总计", "300", "支出总计", "300"]},
    ]
    normal = _reconcile_row_alignment(rows, [text], [1])
    swapped = copy.deepcopy(rows)
    swapped[0]["cells"][3], swapped[1]["cells"][3] = "200", "100"
    corrupted = _reconcile_row_alignment(swapped, [text], [1])
    return {
        "ok": (not normal["row_mismatches"]
               and len(normal["merged_row_identities"]) == 3
               and bool(corrupted["row_mismatches"])),
        "normal_identities": normal["merged_row_identities"],
        "corrupted_mismatches": corrupted["row_mismatches"][:2],
    }


def _d1_same_value_and_year() -> dict:
    from scripts.d1_table_reconciliation import _reconcile_row_alignment

    same_page = ("支出决算表\n"
                 "301 工资福利支出 100\n"
                 "302 商品和服务支出 200\n"
                 "合计 301\n")
    same_normal = _reconcile_row_alignment(
        [{"page": 1, "cells": ["301", "工资福利支出", "100"]},
         {"page": 1, "cells": ["302", "商品和服务支出", "200"]},
         {"page": 1, "cells": ["合计", "", "301"]}], [same_page], [1])
    same_corrupted = _reconcile_row_alignment(
        [{"page": 1, "cells": ["301", "工资福利支出", "100"]},
         {"page": 1, "cells": ["302", "商品和服务支出", "200"]},
         {"page": 1, "cells": ["合计", "", "302"]}], [same_page], [1])
    year_page = ("收入支出决算总表\n某局 2025 年度决算表\n合计 2025\n")
    year_normal = _reconcile_row_alignment(
        [{"page": 1, "cells": ["合计", "2025"]}], [year_page], [1])
    year_corrupted = _reconcile_row_alignment(
        [{"page": 1, "cells": ["合计", "2026"]}], [year_page], [1])
    return {
        "ok": (not same_normal["row_mismatches"] and bool(same_corrupted["row_mismatches"])
               and not year_normal["row_mismatches"]
               and bool(year_corrupted["row_mismatches"])),
        "same_value_normal": not same_normal["row_mismatches"],
        "same_value_corrupted": bool(same_corrupted["row_mismatches"]),
        "year_amount_normal": not year_normal["row_mismatches"],
        "year_amount_corrupted": bool(year_corrupted["row_mismatches"]),
    }


def _d1_real_multilane_pdf() -> dict:
    """第五轮清晰 PDF 原件 + 仅注入错误有效片段（真实 PDFParser 产物）。"""
    if not MULTILANE_PDF.exists():
        return {"ok": None, "skipped": "fixture_pdf_missing"}
    from src.services.pdf_parser import PDFParser
    from scripts import d1_table_reconciliation as d1

    digest = hashlib.sha256(MULTILANE_PDF.read_bytes()).hexdigest()
    artifact = PDFParser(None).build_parse_artifact(str(MULTILANE_PDF))
    with patch.object(d1.PDFParser, "build_parse_artifact", return_value=artifact):
        normal = d1.reconcile_auto(str(MULTILANE_PDF))
    bad = copy.deepcopy(artifact)
    positions = set()
    for fragment in list(bad.table_fragments) + list(bad.physical_fragments):
        for row_index, row in enumerate(fragment.rows):
            if len(row) < 4 or row[2] not in ("本年支出A", "本年支出B"):
                continue
            value = "200" if row[2] == "本年支出A" else "100"
            row[3] = value
            positions.add((fragment.fragment_uid, row_index, 3))
            for cell in fragment.cells:
                if cell.row_idx == row_index and cell.col_idx == 3:
                    cell.raw_text = value
                    cell.numeric_value = Decimal(value)
    with patch.object(d1.PDFParser, "build_parse_artifact", return_value=bad):
        corrupted = d1.reconcile_auto(str(MULTILANE_PDF))
    unchanged = hashlib.sha256(MULTILANE_PDF.read_bytes()).hexdigest() == digest
    return {
        "ok": (normal["pass"] == 1 and normal["gap"] == 0
               and corrupted["pass"] == 0 and corrupted["gap"] == 1
               and unchanged and len(positions) == 2),
        "normal": {"pass": normal["pass"], "gap": normal["gap"]},
        "corrupted": {"pass": corrupted["pass"], "gap": corrupted["gap"]},
        "original_unchanged": unchanged,
        "mutated_positions": len(positions),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    checks = {
        "performance_scope": _performance_scope(),
        "d1_multilane": _d1_multilane(),
        "d1_same_value_and_year": _d1_same_value_and_year(),
        "d1_real_multilane_pdf": _d1_real_multilane_pdf(),
    }
    report = {
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
            capture_output=True, text=True).stdout.strip(),
        "checks": checks,
        "ok": all(item.get("ok") is not False for item in checks.values()),
        "note": ("R5 独立反例复现：预期来自 Codex 第五轮核验包；"
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
