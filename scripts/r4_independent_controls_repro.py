#!/usr/bin/env python
"""R4 独立反例复现器（Codex 第四轮核验包控制的本地等价断言）。

三项控制按核验包的**明确预期**复现，不修改预期适配实现：
1. R4-01 范围限定：归档句插在完整声明与评级数字之间（逗号/括号）、或
   完整声明与数字被句号分隔时，53 全部/优50/其余0 仍必须 fail 且公式
   为等式；合法部分列举保持 pass。
2. R4-02 D1 整数金额："合计 100"→"合计 200"、"201 教育 100"→"200"
   必须 GAP；清晰 PDF 原件 + 错误有效片段 200 必须 GAP、正常 100 PASS。
3. R4-03 导出字段：页码 2→22、22→222、版本加 -old、截断均按字段值
   等值比较拒绝；真实导出正常正例继续通过。

用法:
    .venv/bin/python scripts/r4_independent_controls_repro.py --out <json>
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

REVIEW_PACKAGE = Path(
    "/Volumes/workSSD/software development/GovBudgetChecker"
    "/outputs/independent_ec6bd84b_20261111")
INTEGER_PDF = REVIEW_PACKAGE / "fixtures/integer_table_embedded_original.pdf"


def _performance_scope() -> dict:
    from src.engine.native_final_disclosure import check_performance_counts
    from src.engine.rules_v33 import Rule
    from src.engine.rule_outcome import RuleOutcomeSignal

    class R(Rule):
        code, severity = "V33-250", "error"

    base = ("绩效自评的2025年度项目53个，全部项目评级结果如下："
            "绩效评级为“优”的项目50个，其余评级均为0个。")
    cases = {
        "base": base,
        "archive_after": base + "部分项目评级资料已归档。",
        "archive_between_comma":
            "绩效自评的2025年度项目53个，全部项目评级结果如下，"
            "部分项目评级资料已归档，绩效评级为“优”的项目50个，其余评级均为0个。",
        "archive_between_parentheses":
            "绩效自评的2025年度项目53个，全部项目评级结果如下"
            "（部分项目评级资料已归档）：绩效评级为“优”的项目50个，其余评级均为0个。",
        "complete_declaration_sentence":
            "绩效自评的2025年度项目53个，全部项目评级结果如下。"
            "绩效评级为“优”的项目50个，其余评级均为0个。",
        "partial": "绩效自评的2025年度项目53个，其中部分项目评级结果如下："
                   "绩效评级为“优”的项目50个。",
    }
    expected = {name: (0 if name == "partial" else 1) for name in cases}
    outcomes = []
    ok = True
    for name, body in cases.items():
        doc = SimpleNamespace(
            page_texts=["甲局\n2025年度部门决算\n十、预算绩效管理情况\n" + body],
            report_kind="final", fiscal_year=2025, organization_name="甲局")
        try:
            issues = check_performance_counts(R(), doc)
        except RuleOutcomeSignal as exc:
            issues = list(getattr(exc, "partial_issues", []) or [])
        receipts = list(getattr(doc, "fact_receipts", []) or [])
        formula = str(receipts[-1].get("formula")) if receipts else ""
        status = receipts[-1].get("status") if receipts else ""
        passed = len(issues) == expected[name]
        formula_ok = True
        if expected[name] == 1:
            formula_ok = status == "fail" and "=" in formula and ">=" not in formula
        ok = ok and passed and formula_ok
        outcomes.append({"case": name, "expected_issues": expected[name],
                         "actual_issues": len(issues), "receipt_status": status,
                         "formula": formula, "pass": passed and formula_ok})
    return {"ok": ok, "cases": outcomes}


def _d1_integer_controls() -> dict:
    from scripts.d1_table_reconciliation import _reconcile_row_alignment

    controls = {}
    ok = True
    for name, original, merged_cells in [
        ("decimal", "合计 100.00", ["合计", "200.00"]),
        ("integer", "合计 100", ["合计", "200"]),
        ("coded_integer", "201 一般公共服务支出 100",
         ["201", "一般公共服务支出", "200"]),
    ]:
        result = _reconcile_row_alignment(
            [{"page": 1, "cells": merged_cells}], [original], [1])
        rejected = bool(result["row_mismatches"])
        ok = ok and rejected
        controls[name] = {"original": original, "merged": merged_cells,
                          "rejected": rejected,
                          "identities": result["merged_row_identities"],
                          "mismatches": result["row_mismatches"][:2]}
    # 一致的正例必须通过（100 对 100）。
    normal = _reconcile_row_alignment(
        [{"page": 1, "cells": ["合计", "100"]}], ["合计 100"], [1])
    ok = ok and not normal["row_mismatches"]
    controls["normal_integer"] = {
        "ok": not normal["row_mismatches"],
        "identities": normal["merged_row_identities"]}
    return {"ok": ok, "controls": controls}


def _d1_real_pdf_control() -> dict:
    """清晰 PDF 原件 + 仅注入错误有效片段 200（解析器返回被打桩）。"""
    if not INTEGER_PDF.exists():
        return {"ok": None, "skipped": "fixture_pdf_missing"}
    from src.services.pdf_parser import PDFParser
    from scripts import d1_table_reconciliation as d1

    results = {}
    for name, value in (("normal", "100"), ("corrupted", "200")):
        fragment = SimpleNamespace(fragment_uid="p1", page_number=1,
                                   rows=[["合计", value]],
                                   source_fragment_uids=[])
        instance = SimpleNamespace(table_code="FIN_03_expenditure",
                                   title="支出决算表", page_start=1, page_end=1,
                                   fragment_uids=["p1"])
        artifact = SimpleNamespace(table_fragments=[fragment],
                                   physical_fragments=[fragment],
                                   logical_tables=[instance])
        with patch.object(d1.PDFParser, "build_parse_artifact",
                          return_value=artifact):
            results[name] = d1.reconcile_auto(str(INTEGER_PDF))
    ok = (results["normal"]["gap"] == 0 and results["corrupted"]["gap"] == 1)
    return {
        "ok": ok,
        "normal": {"pass": results["normal"]["pass"],
                   "gap": results["normal"]["gap"]},
        "corrupted": {"pass": results["corrupted"]["pass"],
                      "gap": results["corrupted"]["gap"]},
        "corrupted_detail": [
            item for item in results["corrupted"]["results"] if item["verdict"] != "PASS"][:1],
    }


def _export_field_exactness() -> dict:
    from scripts.d4_real_link_acceptance import (
        _verify_text_issue_block, verify_export_formats)

    revision = "rev-20261111T010000-12345678"
    member = {"id": "A", "page": 2,
              "evidence": [{"page": 2, "quote": "13.24"}],
              "label": "原文摘录"}

    def block(page_value: str, revision_value: str) -> str:
        return (f"ISSUE_ID|A|\n页码：{page_value}\n分析版本：{revision_value}\n"
                f"原文摘录（第2页）：13.24")

    normal = _verify_text_issue_block(block("2", revision), member, revision)
    wrong_page = _verify_text_issue_block(block("22", revision), member, revision)
    wrong_revision = _verify_text_issue_block(
        block("2", revision + "-old"), member, revision)
    truncated = _verify_text_issue_block(
        block("2", revision[:-2]), member, revision)
    # 预期值出现在说明里不能补偿字段错值。
    stray = _verify_text_issue_block(
        block("22", revision).replace("13.24", "页码2 " + revision + " 13.24"),
        member, revision)
    controls = {
        "normal": normal["ok"] is True,
        "wrong_page_2_to_22": "page_not_bound" in wrong_page["failures"],
        "wrong_revision_old": "revision_not_bound" in wrong_revision["failures"],
        "wrong_revision_truncated":
            "revision_not_bound" in truncated["failures"],
        "stray_reference_no_compensation": (
            "page_not_bound" in stray["failures"]
            or "revision_not_bound" in stray["failures"]),
    }
    # 真实导出（核验包 actual_export）正例继续通过、字段变异被拒。
    package = REVIEW_PACKAGE / "exports"
    real = {"ok": None, "skipped": "review_package_exports_missing"}
    if (package / "actual_export.json").exists():
        payload = json.loads((package / "actual_export.json").read_text("utf-8"))
        issues = list((payload.get("review_report") or {}).get("issues") or [])
        real_revision = str(payload.get("analysis_revision") or "")
        contents = {
            "csv": (package / "actual_export.csv").read_bytes(),
            "docx": (package / "actual_export.docx").read_bytes(),
            "pdf": (package / "actual_export.pdf").read_bytes(),
        }
        normal_real = verify_export_formats(contents, issues, real_revision)

        def mutate_docx(field: str, new_value: str) -> bytes:
            with zipfile.ZipFile(io.BytesIO(contents["docx"])) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            tree = ET.fromstring(files["word/document.xml"])
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            changes = 0
            for table in tree.findall(".//w:tbl", ns):
                if "ISSUE_ID|V33-CROSS-SAN-GONG-ECON-22|" not in "".join(
                        table.itertext()):
                    continue
                for row in table.findall("w:tr", ns):
                    cells = row.findall("w:tc", ns)
                    if len(cells) == 2 and "".join(cells[0].itertext()) == field:
                        texts = cells[1].findall(".//w:t", ns)
                        assert len(texts) == 1
                        texts[0].text = new_value
                        changes += 1
            assert changes == 1, (field, changes)
            files["word/document.xml"] = ET.tostring(
                tree, encoding="utf-8", xml_declaration=True)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            return buffer.getvalue()

        wrong_page_real = dict(contents)
        wrong_page_real["docx"] = mutate_docx("证据页码", "222")
        wrong_revision_real = dict(contents)
        wrong_revision_real["docx"] = mutate_docx("分析版本", real_revision + "-old")
        real = {
            "ok": (normal_real["ok"] is True
                   and verify_export_formats(wrong_page_real, issues,
                                             real_revision)["ok"] is False
                   and verify_export_formats(wrong_revision_real, issues,
                                             real_revision)["ok"] is False),
            "normal": {"ok": normal_real["ok"],
                       "member_counts": {
                           suffix: normal_real["formats"][suffix]["member_count"]
                           for suffix in ("csv", "docx", "pdf")}},
        }
    all_ok = all(controls.values()) and real.get("ok") is not False
    return {"ok": all_ok, "block_controls": controls, "real_export": real}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    checks = {
        "performance_scope": _performance_scope(),
        "d1_integer_controls": _d1_integer_controls(),
        "d1_real_pdf_control": _d1_real_pdf_control(),
        "export_field_exactness": _export_field_exactness(),
    }
    report = {
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
            capture_output=True, text=True).stdout.strip(),
        "checks": checks,
        "ok": all(item.get("ok") is not False for item in checks.values()),
        "note": ("R4 独立反例复现：预期来自 Codex 第四轮核验包；"
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
