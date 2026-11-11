#!/usr/bin/env python
"""R2 独立反例复现器（Codex 第二轮核验包控制的本地等价断言）。

把核验包中的独立控制（D1 错列/合并视图、D4 同页标题顶替金额、绩效部分
列举、抑制依据不完整、签认发布交错、学校跨栏编码）分别按**明确预期**
复现，输出 JSON 回执。预期来自核验包，不为当前实现放宽。

用法:
    .venv/bin/python scripts/r2_independent_controls_repro.py --out <json>
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

YICHUAN = (_REPO_ROOT / "uploads/6000a11c0737d5149975516b44fd8682"
           / "上海市普陀区人民政府宜川路街道办事处2025年度部门决算.pdf")
SCHOOL = (_REPO_ROOT / "uploads" / "075e31ba6f282134daad0b9ae225d0a5"
          / "上海科技管理学校2025年度决算.pdf")


def _d1_columns() -> dict:
    from scripts.d1_table_reconciliation import _reconcile_row_alignment

    page = "支出决算表\n合计 100.00 70.00 30.00\n201 一般公共服务支出 100.00 70.00 30.00"
    normal = [{"page": 1, "cells": ["合计", "100.00", "70.00", "30.00"]},
              {"page": 1, "cells": ["201", "一般公共服务支出", "100.00", "70.00", "30.00"]}]
    wrong = copy.deepcopy(normal)
    for row in wrong:
        row["cells"][-2:] = row["cells"][-2:][::-1]
    normal_result = _reconcile_row_alignment(normal, [page], [1])
    wrong_result = _reconcile_row_alignment(wrong, [page], [1])
    return {
        "ok": normal_result["row_mismatches"] == [] and bool(wrong_result["row_mismatches"]),
        "normal_mismatches": len(normal_result["row_mismatches"]),
        "swapped_mismatches": len(wrong_result["row_mismatches"]),
    }


def _d4_same_page_operands() -> dict:
    from scripts.d4_real_link_acceptance import verify_all_evidence

    finding = {
        "id": "I", "source": "rule", "rule_id": "R", "severity": "high",
        "location": {"page": 1, "table_refs": [
            {"page": 1, "span": "本年支出100万元", "role": "合计"},
            {"page": 1, "span": "基本70万元 项目40万元", "role": "分项"},
        ]},
        "evidence": [{"evidence_id": "e1", "page": 1, "quote": "支出决算表"}],
        "evidence_verification": {"status": "verified"},
    }

    class Client:
        def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, json=lambda: {
                "physical_page": 1, "quote": "支出决算表",
                "file_hash": "a" * 64})

    result = verify_all_evidence(
        Client(), "control", {"rule_findings": [finding]},
        ["支出决算表\n本年支出100万元\n基本70万元 项目40万元"], "a" * 64, 1)
    return {
        "ok": result["ok"] is False,
        "failure_count": result["failure_count"],
        "reasons": [item["reason"] for item in result["failures"]],
    }


def _performance_variants() -> dict:
    from src.engine.native_final_disclosure import check_performance_counts
    from src.engine.rules_v33 import Rule

    class R(Rule):
        code, severity = "V33-250", "error"

    bodies = [
        "绩效自评的2025年度项目53个，其中，绩效评级为“优”的项目50个。",
        "绩效自评的2025年度项目53个，全部项目评级结果如下：绩效评级为“优”的项目50个，其余评级均为0个。",
        "绩效自评的2025年度项目53个，其中部分项目评级结果如下：绩效评级为“优”的项目50个。",
    ]
    outcomes = []
    for body in bodies:
        doc = SimpleNamespace(
            page_texts=["甲局\n2025年度部门决算\n十、预算绩效管理情况\n" + body],
            report_kind="final", fiscal_year=2025, organization_name="甲局")
        try:
            issues = check_performance_counts(R(), doc)
            error = None
        except Exception as exc:  # noqa: BLE001
            issues = list(getattr(exc, "partial_issues", []) or [])
            error = str(exc)
        outcomes.append({
            "original": body,
            "issue_count": len(issues),
            "receipt_status": (getattr(doc, "fact_receipts", []) or [{}])[-1].get("status"),
            "error": error,
        })
    return {
        "ok": [item["issue_count"] for item in outcomes] == [0, 1, 0],
        "variants": outcomes,
    }


def _suppression_contract() -> dict:
    from src.engine.rules_v33 import apply_delivery_suppressions

    class Issue:
        rule = "INDEPENDENT-SUPPRESSION"
        message = "仍然失败的金额不一致"
        location = {"page": 1, "pos": 0,
                    "delivery_suppressed": {"policy_id": "unregistered-policy"}}

    doc = SimpleNamespace()
    kept = apply_delivery_suppressions(doc, [Issue()])
    return {
        "ok": len(kept) == 1 and not getattr(doc, "delivery_suppression_ledger", []),
        "kept": len(kept),
        "ledger": getattr(doc, "delivery_suppression_ledger", []),
    }


def _confirm_revision_race() -> dict:
    import secrets

    os.environ["TESTING"] = "true"
    os.environ["GOVBUDGET_CONFIRM_REPORT_ENABLED"] = "1"
    # api.runtime 导入即构建安全配置；隔离控制用一次性密钥，不读生产 .env
    os.environ.setdefault("GOVBUDGET_AUTH_ENABLED", "true")
    os.environ.setdefault("GOVBUDGET_API_KEY", secrets.token_hex(24))
    os.environ.setdefault("USER_SESSION_SECRET", secrets.token_hex(24))
    from api import runtime
    from src.services import report_confirmation_gate as gate
    with tempfile.TemporaryDirectory(prefix="r2-confirm-race-") as temporary:
        root = Path(temporary)
        job = "control"
        (root / job).mkdir()
        status_path = root / job / "status.json"

        def status(revision):
            return {
                "analysis_revision": revision,
                "result": {"rule_findings": [{
                    "id": "finding-" + revision, "rule_id": "R", "severity": "high",
                    "message": "m", "location": {"page": 1},
                    "evidence_verification": {"status": "verified"}}]},
                "review_reasons": [],
            }

        status_path.write_text(json.dumps(status("R1")), encoding="utf-8")
        original_append = gate.append_confirm_record

        def append_after_publication(job_id, record):
            # 模拟发布方绕过文件锁直接写 status.json（Codex 独立交错）
            status_path.write_text(json.dumps(status("R2")), encoding="utf-8")
            return original_append(job_id, record)

        gate.append_confirm_record = append_after_publication
        try:
            import api.runtime as runtime_module
            original_root = runtime_module.UPLOAD_ROOT
            runtime_module.UPLOAD_ROOT = root
            try:
                result = gate.confirm_report_atomic(
                    job, expected_analysis_revision="R1",
                    idempotency_key="race-key-independent",
                    confirm_report_enabled=True, actor="independent")
                records = gate.read_confirm_records(job)
            finally:
                runtime_module.UPLOAD_ROOT = original_root
        finally:
            gate.append_confirm_record = original_append
        return {
            "ok": (result["http"] == 409
                   and result.get("detail", {}).get("current") == "R2"
                   and records == []),
            "http": result["http"],
            "detail": result.get("detail"),
            "records": records,
        }


def _school_cross_identity() -> dict:
    if not SCHOOL.exists():
        return {"ok": None, "skipped": "school_pdf_missing"}
    import pdfplumber

    from src.engine.fact_context import build_fact_context_from_artifact
    from src.engine.pipeline import build_issues_payload
    from src.engine.rule_registry import select_rules
    from src.engine.rules_v33 import (
        _FIN_06_TABLE_ALIASES,
        _cross_table_by_alias,
        _econ_table_uses_2025_codes,
        _ensure_parsed_tables,
    )
    from src.services.pdf_page_extract import (
        extract_tables_from_page,
        extract_visible_text_from_page,
    )
    from src.services.pdf_parser import PDFParser

    artifact = PDFParser(None).build_parse_artifact(str(SCHOOL))
    context = build_fact_context_from_artifact(
        artifact, org_name="上海科技管理学校", fiscal_year=2025,
        report_kind="final", doc_type="dept_final")
    with pdfplumber.open(str(SCHOOL)) as doc_pdf:
        page_texts = [extract_visible_text_from_page(page) for page in doc_pdf.pages]
        page_tables = [extract_tables_from_page(page) for page in doc_pdf.pages]
    from src.engine.pipeline import build_document

    doc = build_document(str(SCHOOL), page_texts, page_tables, SCHOOL.stat().st_size)
    doc.parse_artifact = artifact
    doc.fact_context = context
    _ensure_parsed_tables(doc)
    table6 = _cross_table_by_alias(doc, _FIN_06_TABLE_ALIASES)
    version_2025 = _econ_table_uses_2025_codes(table6)
    payload = build_issues_payload(
        doc, use_ai_assist=False, report_kind="final",
        rules=list(select_rules("final")))
    summary = payload.get("rule_execution_summary") or {}
    receipts = {item.get("sub_check_id"): item
                for item in payload.get("fact_receipts") or []
                if item.get("rule_id") == "V33-CROSS-SAN-GONG-ECON"}
    relation = receipts.get("V33-CROSS-SAN-GONG-ECON:cross:san_gong:vehicle_operation") or {}
    return {
        "ok": (version_2025 is True
               and summary.get("rule_statuses", {}).get("V33-CROSS-SAN-GONG-ECON") == "pass"
               and relation.get("status") == "pass"
               and "4.15" in str(relation.get("formula"))),
        "version_2025": version_2025,
        "rule_status": summary.get("rule_statuses", {}).get("V33-CROSS-SAN-GONG-ECON"),
        "vehicle_operation": {"status": relation.get("status"),
                              "formula": relation.get("formula")},
    }


def _operand_evidence() -> dict:
    if not YICHUAN.exists():
        return {"ok": None, "skipped": "yichuan_pdf_missing"}
    import importlib.util

    import pdfplumber

    from src.engine.fact_context import build_fact_context_from_artifact
    from src.engine.pipeline import build_document, build_issues_payload
    from src.engine.rule_registry import select_rules
    from src.services.evidence_guard import apply_evidence_completeness
    from src.services.pdf_page_extract import (
        extract_tables_from_page,
        extract_visible_text_from_page,
    )
    from src.services.pdf_parser import PDFParser
    from src.services.rule_source_binding import (
        _current_cell_catalog,
        bind_rule_sources,
        verify_bound_rule_locations,
    )

    raw = hashlib.sha256(YICHUAN.read_bytes()).hexdigest()
    artifact = PDFParser(None).build_parse_artifact(str(YICHUAN))
    context = build_fact_context_from_artifact(
        artifact, org_name="上海市普陀区人民政府宜川路街道办事处",
        fiscal_year=2025, report_kind="final", doc_type="dept_final")
    with pdfplumber.open(str(YICHUAN)) as doc_pdf:
        page_texts = [extract_visible_text_from_page(page) for page in doc_pdf.pages]
        page_tables = [extract_tables_from_page(page) for page in doc_pdf.pages]
    doc = build_document(str(YICHUAN), page_texts, page_tables, YICHUAN.stat().st_size)
    doc.parse_artifact = artifact
    doc.fact_context = context
    payload = build_issues_payload(
        doc, use_ai_assist=False, report_kind="final",
        rules=list(select_rules("final")))
    result = {"summary": "", "ai_findings": [],
              "rule_findings": payload["issues"]["all"], "merged": {}, "meta": {}}
    bind_rule_sources(
        result, page_texts=page_texts, source_file_hash=raw, artifact=artifact,
        fact_context=context,
        consumption=list(payload.get("structured_consumption") or [])
        + list(payload.get("fact_receipts") or []))
    verify_bound_rule_locations(result, source_pdf_path=str(YICHUAN))
    apply_evidence_completeness(
        result, document_page_count=len(page_texts), page_texts=page_texts,
        source_file_hash=raw, source_pdf_path=str(YICHUAN))
    spec = importlib.util.spec_from_file_location(
        "d4", _REPO_ROOT / "scripts" / "d4_real_link_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cells, _conflicts = _current_cell_catalog(artifact)
    findings = [item for item in result["rule_findings"]
                if item.get("rule_id") == "V33-CROSS-SAN-GONG-ECON"]
    check = module.verify_relation_operands(findings, cells, page_texts, len(page_texts))
    control = module.operand_adversarial_control(findings, cells, page_texts, len(page_texts))
    return {
        "ok": check["ok"] and control["ok"],
        "relations_total": check["relations_total"],
        "relations_ok": check["relations_ok"],
        "control_ok": control["ok"],
        "failures": check["failures"][:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    checks = {
        "d1_column_swap": _d1_columns(),
        "d4_same_page_missing_operands": _d4_same_page_operands(),
        "performance_variants": _performance_variants(),
        "suppression_policy_only": _suppression_contract(),
        "confirm_revision_race": _confirm_revision_race(),
        "school_cross_identity": _school_cross_identity(),
        "yichuan_operand_evidence": _operand_evidence(),
    }
    report = {
        "git_head": __import__("subprocess").run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
            capture_output=True, text=True).stdout.strip(),
        "checks": checks,
        "ok": all(item.get("ok") is not False for item in checks.values()),
        "note": ("R2 独立反例复现：预期来自 Codex 第二轮核验包；"
                 "ok=None 仅表示本机缺少该原件，不计通过。"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1)[:4000])
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
