#!/usr/bin/env python
"""R3 独立反例复现器（Codex 第三轮核验包控制的本地等价断言）。

三项控制按核验包的**明确预期**复现，不修改预期适配实现：
1. R3-01 绩效声明范围：完整构成 53/50 加无关“部分归档”句仍必须 fail；
   部分列举 53/50 必须 pass；fail 回执公式必须是等式。
2. R3-02 导出成员：同一 rule_id 的两个独立关系只导出 A 时，CSV/DOCX/PDF
   必须被拒绝；真实 CSV 删掉一条 issue 行必须被拒绝；删掉一侧金额引文、
   改错页码/版本也必须被拒绝。
3. R3-03 D1：原件与逻辑行一致的拆码行必须通过；逻辑合并损坏、物理
   基本/项目换列两个旧负控必须 GAP。

用法:
    .venv/bin/python scripts/r3_independent_controls_repro.py --out <json>
"""
from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

YICHUAN = (_REPO_ROOT / "uploads/6000a11c0737d5149975516b44fd8682"
           / "上海市普陀区人民政府宜川路街道办事处2025年度部门决算.pdf")
REVIEW_PACKAGE = Path(
    "/Volumes/workSSD/software development/GovBudgetChecker"
    "/outputs/independent_70cd8d37_20261111")


def _performance_scope() -> dict:
    from src.engine.native_final_disclosure import check_performance_counts
    from src.engine.rules_v33 import Rule

    class R(Rule):
        code, severity = "V33-250", "error"

    complete = (
        "绩效自评的2025年度项目53个，全部项目评级结果如下："
        "绩效评级为“优”的项目50个，其余评级均为0个。")
    bodies = {
        "partial": "绩效自评的2025年度项目53个，其中部分项目评级结果如下："
                   "绩效评级为“优”的项目50个。",
        "complete": complete,
        "complete_plus_archive_after": complete + "部分项目评级资料已归档。",
        "complete_plus_archive_before":
            "绩效自评的2025年度项目53个，部分项目评级资料已归档。"
            "全部项目评级结果如下：绩效评级为“优”的项目50个，其余评级均为0个。",
        "plain_partial": "绩效自评的2025年度项目53个，其中，"
                         "绩效评级为“优”的项目50个。",
    }
    expected = {
        "partial": 0, "complete": 1, "complete_plus_archive_after": 1,
        "complete_plus_archive_before": 1, "plain_partial": 0,
    }
    outcomes = []
    ok = True
    for name, body in bodies.items():
        doc = SimpleNamespace(
            page_texts=["甲局\n2025年度部门决算\n十、预算绩效管理情况\n" + body],
            report_kind="final", fiscal_year=2025, organization_name="甲局")
        try:
            issues = check_performance_counts(R(), doc)
        except Exception as exc:  # noqa: BLE001
            issues = list(getattr(exc, "partial_issues", []) or [])
        receipts = list(getattr(doc, "fact_receipts", []) or [])
        formula = receipts[-1].get("formula") if receipts else ""
        status = receipts[-1].get("status") if receipts else ""
        passed = len(issues) == expected[name]
        formula_ok = True
        if name == "complete_plus_archive_after":
            formula_ok = status == "fail" and "=" in str(formula) and ">=" not in str(formula)
        ok = ok and passed and formula_ok
        outcomes.append({
            "case": name, "expected_issues": expected[name],
            "actual_issues": len(issues), "receipt_status": status,
            "formula": formula, "pass": passed and formula_ok,
        })
    return {"ok": ok, "cases": outcomes}


def _minimal_export_control() -> dict:
    from scripts.d4_real_link_acceptance import verify_export_formats

    revision = "rev-independent-R3"
    issues = [
        {"id": "relation-A", "rule_id": "CROSS-TEST", "location": {"page": 22},
         "evidence": [{"page": 22, "quote": "13.24"},
                      {"page": 24, "quote": "0.00"}]},
        {"id": "relation-B", "rule_id": "CROSS-TEST", "location": {"page": 24},
         "evidence": [{"page": 22, "quote": "81.11"},
                      {"page": 24, "quote": "19.56"}]},
    ]
    text = ("analysis_revision: " + revision + "\n"
            "relation-A CROSS-TEST page 22; 13.24 / 0.00\n")
    docx_buffer = io.BytesIO()
    with zipfile.ZipFile(docx_buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"><Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>')
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
            '.org/package/2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats'
            '.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            + text + '</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
    import pymupdf as fitz

    def _text_pdf(lines: list[str]) -> bytes:
        document = fitz.open()
        page = document.new_page()
        y = 50
        for line in lines:
            page.insert_text((50, y), line)
            y += 18
        data = document.tobytes()
        document.close()
        return data

    # 负控 1：三种格式都只写 relation-A（漏掉同 rule_id 的 relation-B）。
    pdf_bytes = _text_pdf([revision, "relation-A CROSS-TEST page 22; 13.24 / 0.00"])
    contents = {"csv": text.encode(), "docx": docx_buffer.getvalue(),
                "pdf": pdf_bytes}
    result = verify_export_formats(contents, issues, revision)
    # 负控 2：PDF 有完整成员块，但 relation-B 少一侧金额 19.56。
    partial_pdf = _text_pdf([
        revision,
        "ISSUE_ID|relation-A|", "证据页码22", "页码：22",
        "原文摘录（第22页）：13.24", "原文摘录（第24页）：0.00",
        "ISSUE_ID|relation-B|", "证据页码24", "页码：24",
        "原文摘录（第22页）：81.11",
    ])
    partial = verify_export_formats(
        {"csv": text.encode(), "docx": docx_buffer.getvalue(), "pdf": partial_pdf},
        issues, revision)
    partial_pdf_failures = [
        item for item in partial["formats"]["pdf"]["failed_members"]
        if item["issue_id"] == "relation-B"
        and "evidence_not_bound" in item["failures"]
    ]
    # 负控 3：PDF 正确成员块但页码写错。
    wrong_page_pdf = _text_pdf([
        revision,
        "ISSUE_ID|relation-A|", "证据页码22", "页码：22",
        "原文摘录（第22页）：13.24", "原文摘录（第24页）：0.00",
        "ISSUE_ID|relation-B|", "证据页码23", "页码：23",
        "原文摘录（第22页）：81.11", "原文摘录（第24页）：19.56",
    ])
    wrong_page = verify_export_formats(
        {"csv": text.encode(), "docx": docx_buffer.getvalue(), "pdf": wrong_page_pdf},
        issues, revision)
    wrong_page_failures = [
        item for item in wrong_page["formats"]["pdf"]["failed_members"]
        if item["issue_id"] == "relation-B"
        and "page_not_bound" in item["failures"]
    ]
    return {
        "ok": (result["ok"] is False and bool(partial_pdf_failures)
               and bool(wrong_page_failures)),
        "expected": "reject: relation-B/page24/81.11/19.56 omitted in all three",
        "actual": {
            "ok": result["ok"],
            "csv_missing": result["formats"]["csv"]["missing_members"],
            "docx_missing": result["formats"]["docx"]["missing_members"],
            "pdf_missing": result["formats"]["pdf"]["missing_members"],
            "pdf_one_side_missing": partial_pdf_failures,
            "pdf_wrong_page": wrong_page_failures,
        },
    }


def _actual_csv_missing_issue() -> dict:
    """用核验包中的真实导出（旧版无 evidence_quotes）复现删行负控。"""
    from scripts.d4_real_link_acceptance import verify_export_formats

    package = REVIEW_PACKAGE / "exports"
    if not (package / "actual_export.json").exists():
        return {"ok": None, "skipped": "review_package_exports_missing"}
    payload = json.loads((package / "actual_export.json").read_text("utf-8"))
    issues = list((payload.get("review_report") or {}).get("issues") or [])
    revision = str(payload.get("analysis_revision") or "")
    contents = {
        "csv": (package / "actual_export.csv").read_bytes(),
        "docx": (package / "actual_export.docx").read_bytes(),
        "pdf": (package / "actual_export.pdf").read_bytes(),
    }
    reader = csv.DictReader(io.StringIO(contents["csv"].decode("utf-8-sig")))
    fields = reader.fieldnames
    rows = list(reader)
    target = "V33-CROSS-SAN-GONG-ECON-22"
    kept = [row for row in rows
            if not (row["record_type"] == "issue" and row["issue_id"] == target)]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(kept)
    mutated = dict(contents)
    mutated["csv"] = output.getvalue().encode("utf-8-sig")
    result = verify_export_formats(mutated, issues, revision)
    csv_result = result["formats"]["csv"]
    return {
        "ok": (result["ok"] is False and target in csv_result["missing_members"]),
        "removed": target,
        "original_issue_rows": sum(1 for row in rows if row["record_type"] == "issue"),
        "mutated_issue_rows": len(kept),
        "missing_members": csv_result["missing_members"],
        "member_count": csv_result.get("member_count"),
    }


def _d1_controls() -> dict:
    from scripts.d1_table_reconciliation import _reconcile_row_alignment

    split_page = (
        "301 01 基本工资 100.00 302 01 办公费 10.00\n"
        "301 02 津贴补贴 200.00 302 02 印刷费 20.00")
    split_rows = [
        {"page": 1, "cells": ["301", "01", "基本工资", "100.00",
                              "302", "01", "办公费", "10.00"]},
        {"page": 1, "cells": ["301", "02", "津贴补贴", "200.00",
                              "302", "02", "印刷费", "20.00"]},
    ]
    normal = _reconcile_row_alignment(split_rows, [split_page], [1])
    column_page = ("支出决算表\n合计 100.00 70.00 30.00\n"
                   "201 一般公共服务支出 100.00 70.00 30.00")
    normal_rows = [
        {"page": 1, "cells": ["合计", "100.00", "70.00", "30.00"]},
        {"page": 1, "cells": ["201", "一般公共服务支出", "100.00", "70.00", "30.00"]},
    ]
    swapped_rows = copy.deepcopy(normal_rows)
    for row in swapped_rows:
        row["cells"][-2:] = row["cells"][-2:][::-1]
    column_swapped = _reconcile_row_alignment(swapped_rows, [column_page], [1])
    return {
        "ok": (normal["row_mismatches"] == []
               and not column_swapped["row_mismatches"] == []),
        "split_code_normal_mismatches": len(normal["row_mismatches"]),
        "split_code_identities": normal["merged_row_identities"],
        "column_swap_mismatches": len(column_swapped["row_mismatches"]),
    }


def _d1_real_injections() -> dict:
    """Codex 原始注入脚本：逻辑合并金额互换、物理基本/项目换列。"""
    if not YICHUAN.exists():
        return {"ok": None, "skipped": "yichuan_pdf_missing"}
    from src.services.pdf_parser import PDFParser
    from scripts import d1_table_reconciliation as d1

    artifact = PDFParser(None).build_parse_artifact(str(YICHUAN))
    with patch.object(d1.PDFParser, "build_parse_artifact", return_value=artifact):
        normal = d1.reconcile_auto(str(YICHUAN))
    logical = copy.deepcopy(artifact)
    table = next(item for item in logical.table_fragments
                 if item.table_code == "FIN_02_income")
    table.rows = copy.deepcopy(table.rows)
    table.cells = copy.deepcopy(table.cells)
    before = [table.rows[2][2], table.rows[3][2]]
    table.rows[2][2], table.rows[3][2] = table.rows[3][2], table.rows[2][2]
    for cell in table.cells:
        if cell.row_idx in (2, 3) and cell.col_idx == 2:
            cell.raw_text = table.rows[cell.row_idx][2]
            cell.numeric_value = Decimal(cell.raw_text)
    physical_before = json.dumps(
        [fragment.to_dict() for fragment in logical.physical_fragments],
        sort_keys=True, default=str)
    with patch.object(d1.PDFParser, "build_parse_artifact", return_value=logical):
        logical_swap = d1.reconcile_auto(str(YICHUAN))
    physical_unchanged = physical_before == json.dumps(
        [fragment.to_dict() for fragment in logical.physical_fragments],
        sort_keys=True, default=str)
    physical = copy.deepcopy(artifact)
    changes = []
    for fragment in physical.physical_fragments:
        for index, row in enumerate(fragment.rows):
            norms = ["".join(str(value or "").split()).replace(",", "") for value in row]
            if "14107.46" in norms and "6215.76" in norms:
                left, right = norms.index("14107.46"), norms.index("6215.76")
                row[left], row[right] = row[right], row[left]
                for cell in fragment.cells:
                    if cell.row_idx == index and cell.col_idx in (left, right):
                        cell.raw_text = row[cell.col_idx]
                        cell.numeric_value = Decimal(cell.raw_text.replace(",", ""))
                changes.append({"page": fragment.page_number,
                                "columns": [left, right]})
    with patch.object(d1.PDFParser, "build_parse_artifact", return_value=physical):
        physical_swap = d1.reconcile_auto(str(YICHUAN))
    return {
        "ok": (normal["pass"] == 9 and normal["gap"] == 0
               and logical_swap["pass"] == 8 and logical_swap["gap"] == 1
               and physical_swap["pass"] == 8 and physical_swap["gap"] == 1
               and physical_unchanged and bool(changes)),
        "normal": {"pass": normal["pass"], "gap": normal["gap"]},
        "logical_total_class_swap": {"pass": logical_swap["pass"],
                                     "gap": logical_swap["gap"]},
        "physical_basic_project_swap": {"pass": physical_swap["pass"],
                                        "gap": physical_swap["gap"]},
        "physical_fragments_unchanged": physical_unchanged,
        "physical_changes": changes,
        "logical_change": {"before": before,
                           "after": [table.rows[2][2], table.rows[3][2]]},
    }


def _real_export_chain() -> dict:
    """真实导出链路：正常成员全通过，删行/删侧证据/错页/错版本被拒。"""
    os.environ["TESTING"] = "true"
    os.environ["GOVBUDGET_AUTH_ENABLED"] = "false"
    from fastapi.testclient import TestClient

    from api import runtime
    from api.main import app
    from scripts.d4_real_link_acceptance import verify_export_formats
    import pymupdf as fitz

    # 隔离控制不接触开发库：api.main 导入时 load_dotenv 可能注入 DATABASE_URL。
    os.environ.pop("DATABASE_URL", None)

    issues = [
        {"id": "relation-A", "source": "rule", "rule_id": "CROSS-TEST",
         "severity": "error", "title": "跨表关系", "message": "A 跨表关系",
         "location": {"page": 22}, "evidence_status": "complete",
         "evidence_verification": {"status": "verified",
                                   "source_identity_complete": True},
         "evidence": [{"page": 22, "quote": "13.24"},
                      {"page": 24, "quote": "0.00"}]},
        {"id": "relation-B", "source": "rule", "rule_id": "CROSS-TEST",
         "severity": "error", "title": "跨表关系", "message": "B 跨表关系",
         "location": {"page": 22}, "evidence_status": "complete",
         "evidence_verification": {"status": "verified",
                                   "source_identity_complete": True},
         "evidence": [{"page": 22, "quote": "81.11"},
                      {"page": 24, "quote": "19.56"}]},
    ]
    revision = "rev-r3-real-export"
    with tempfile.TemporaryDirectory(prefix="r3-export-") as temporary:
        root = Path(temporary)
        original_root = runtime.UPLOAD_ROOT
        runtime.UPLOAD_ROOT = root
        try:
            job = "control"
            job_dir = root / job
            job_dir.mkdir()
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "R3 EXPORT SOURCE")
            document.save(job_dir / "source.pdf")
            document.close()
            payload = {
                "job_id": job, "status": "done",
                "saved_path": f"{job}/source.pdf",
                "analysis_revision": revision,
                "review_report": {"issues": issues, "tips": [],
                                  "manual_review": [], "human_decisions": []},
                "result": {"rule_findings": issues, "ai_findings": [], "merged": {}},
            }
            (job_dir / "status.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            client = TestClient(app)
            contents = {}
            for suffix in ("csv", "docx", "pdf"):
                response = client.get(
                    f"/api/reports/download?job_id={job}&format={suffix}")
                response.raise_for_status()
                contents[suffix] = response.content
            normal = verify_export_formats(contents, issues, revision)
            # 删掉 CSV 的 relation-B 行
            reader = csv.DictReader(io.StringIO(contents["csv"].decode("utf-8-sig")))
            fields = reader.fieldnames
            rows = [row for row in reader
                    if not (row["record_type"] == "issue"
                            and row["issue_id"] == "relation-B")]
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            mutated_csv = dict(contents)
            mutated_csv["csv"] = output.getvalue().encode("utf-8-sig")
            csv_missing = verify_export_formats(mutated_csv, issues, revision)
            # DOCX 删掉一侧金额证据
            with zipfile.ZipFile(io.BytesIO(contents["docx"])) as archive:
                xml = archive.read("word/document.xml").decode("utf-8")
                other = {name: archive.read(name) for name in archive.namelist()
                         if name != "word/document.xml"}
            mutated_xml = xml.replace(
                '<w:t xml:space="preserve">19.56</w:t>',
                '<w:t xml:space="preserve">19.99</w:t>')
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", mutated_xml)
                for name, data in other.items():
                    archive.writestr(name, data)
            mutated_docx = dict(contents)
            mutated_docx["docx"] = buffer.getvalue()
            docx_missing = verify_export_formats(mutated_docx, issues, revision)
            # 错页
            wrong_page_xml = re.sub(
                r'(<w:t xml:space="preserve">证据页码</w:t></w:r></w:p></w:tc>'
                r'<w:tc><w:p><w:r><w:t xml:space="preserve">)22(</w:t>)',
                r"\g<1>23\g<2>", xml, count=1)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", wrong_page_xml)
                for name, data in other.items():
                    archive.writestr(name, data)
            wrong_page = dict(contents)
            wrong_page["docx"] = buffer.getvalue()
            page_result = verify_export_formats(wrong_page, issues, revision)
            # 错版本
            wrong_revision_xml = xml.replace(revision, "rev-other")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", wrong_revision_xml)
                for name, data in other.items():
                    archive.writestr(name, data)
            wrong_revision = dict(contents)
            wrong_revision["docx"] = buffer.getvalue()
            revision_result = verify_export_formats(wrong_revision, issues, revision)
        finally:
            runtime.UPLOAD_ROOT = original_root
    return {
        "ok": (normal["ok"] is True
               and csv_missing["ok"] is False
               and docx_missing["ok"] is False
               and page_result["ok"] is False
               and revision_result["ok"] is False),
        "normal": {"ok": normal["ok"],
                   "member_counts": {suffix: normal["formats"][suffix]["member_count"]
                                     for suffix in ("csv", "docx", "pdf")}},
        "csv_missing_member": csv_missing["formats"]["csv"]["missing_members"],
        "docx_missing_evidence": [
            item for item in docx_missing["formats"]["docx"]["failed_members"]],
        "wrong_page": [item for item in page_result["formats"]["docx"]["failed_members"]],
        "wrong_revision": [
            item for item in revision_result["formats"]["docx"]["failed_members"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    checks = {
        "performance_scope": _performance_scope(),
        "minimal_export_membership": _minimal_export_control(),
        "actual_csv_missing_issue": _actual_csv_missing_issue(),
        "d1_split_code_and_column_swap": _d1_controls(),
        "d1_real_injections": _d1_real_injections(),
        "real_export_chain": _real_export_chain(),
    }
    report = {
        "git_head": __import__("subprocess").run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
            capture_output=True, text=True).stdout.strip(),
        "checks": checks,
        "ok": all(item.get("ok") is not False for item in checks.values()),
        "note": ("R3 独立反例复现：预期来自 Codex 第三轮核验包；"
                 "ok=None 仅表示本机缺少该原件，不计通过。"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1)[:5000])
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
