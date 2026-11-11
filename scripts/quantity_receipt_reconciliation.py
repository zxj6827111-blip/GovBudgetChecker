#!/usr/bin/env python
"""数量回执关系到义务/规则/页面的对账（Codex IR-08 返工第 8 项）。

对每份真实决算原件，按产品同一管道重放（原生解析 → 事实上下文 → 规则 →
义务台账），输出并校验三公经费数量回执的对账关系：

  receipt(relation) ──rule_id──> rule_execution_summary.status
        │                              │
        │                              └──> 义务实例状态（insufficient_data /
        │                                   parse_error / execution_error / ...）
        └──disclosure_sources/页码──> 原件页文本命中

校验项（任一失败即整体 FAIL）：
1. 完整覆盖：四项数量关系（购置/出国/接待/车辆保有量）各一条回执，不重不漏；
2. 规则不掩盖：任一数量关系回执为 insufficient_data/parse_error/
   execution_error 时，该规则的聚合状态不得仍是 pass/fail/not_applicable；
3. 义务不被掩盖：规则未完成时，其映射义务必须处于未完成状态（blocking）；
4. 合法适用性：not_applicable 必须带合法依据——金额事实为 0、说明写明
   未发生，或带页码的原文依据（说明零披露/空表说明/U-5 认证空白格），
   且依据文本能在原件该页命中；
5. 未完成必留痕：insufficient_data 必须带 non-empty incomplete_reason；
6. 关系可追溯：每条回执的 rule 必须映射到至少一个义务实例。

输出 JSON（逐条回执、逐项检查结论、汇总）与可选 Markdown 摘要。

用法:
    .venv/bin/python scripts/quantity_receipt_reconciliation.py \
        --manifest data/quantity_reconciliation_cases.json \
        --out outputs/quantity_reconciliation.json [--markdown ...]

manifest 格式:
    [{"name": "宜川", "pdf": "uploads/xxx/x.pdf", "org": "单位全称",
      "year": 2025, "kind": "final"}]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

INDICATORS = ("vehicle_purchase", "overseas", "reception", "vehicle_stock")
UNRESOLVED = {"insufficient_data", "parse_error", "execution_error"}
COMPLETED_STATES = {"completed", "not_applicable"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compact(text: str) -> str:
    return "".join(str(text or "").split())


def run_case(case: dict) -> dict:
    import pdfplumber

    from src.engine.check_obligations import (
        attach_obligation_ids,
        build_obligation_ledger,
        obligation_ids_for_rule,
    )
    from src.engine.fact_context import build_fact_context_from_artifact
    from src.engine.pipeline import build_document, build_issues_payload
    from src.engine.rule_registry import select_rules
    from src.services.pdf_page_extract import (
        extract_tables_from_page,
        extract_visible_text_from_page,
    )
    from src.services.pdf_parser import PDFParser

    pdf_path = (_REPO_ROOT / case["pdf"]).resolve()
    kind = str(case.get("kind") or "final")
    artifact = PDFParser(None).build_parse_artifact(str(pdf_path))
    context = build_fact_context_from_artifact(
        artifact, org_name=case["org"], fiscal_year=int(case["year"]),
        report_kind=kind, doc_type="dept_final",
    )
    with pdfplumber.open(str(pdf_path)) as doc_pdf:
        page_texts = [extract_visible_text_from_page(page) for page in doc_pdf.pages]
        page_tables = [extract_tables_from_page(page) for page in doc_pdf.pages]

    doc = build_document(str(pdf_path), page_texts, page_tables, pdf_path.stat().st_size)
    doc.parse_artifact = artifact
    doc.fact_context = context

    payload = build_issues_payload(
        doc, use_ai_assist=False, report_kind=kind, rules=list(select_rules(kind)))
    summary = payload.get("rule_execution_summary") or {}
    rule_statuses = summary.get("rule_statuses") or {}
    unresolved_rules = {str(item.get("rule_id")): str(item.get("detail") or "")
                        for item in summary.get("unresolved_rules") or []}
    receipts = [item for item in payload.get("fact_receipts") or []
                if isinstance(item, dict)
                and str(item.get("sub_check_id") or "").startswith("quantity:")]

    result = {"summary": "", "ai_findings": [], "rule_findings": [],
              "merged": {}, "meta": payload}
    attach_obligation_ids(result)
    ledger = build_obligation_ledger(
        getattr(doc, "document_profile", None), report_kind=kind,
        rule_execution_summary=summary, ai_execution=None, ai_required=False,
        structured_ingest=None, fact_receipts=payload.get("fact_receipts") or [],
    )
    instances = {str(item.get("obligation_id")): item for item in ledger.get("instances") or []}

    findings = [item for item in payload.get("issues", {}).get("all") or []
                if isinstance(item, dict)]
    checks: list[dict] = []

    def check(name: str, ok: bool, detail) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    rows = []
    seen: dict[str, int] = {}
    for receipt in receipts:
        sub_check = str(receipt.get("sub_check_id") or "")
        indicator = sub_check.split(":", 1)[1] if ":" in sub_check else sub_check
        seen[indicator] = seen.get(indicator, 0) + 1
        rule_id = str(receipt.get("rule_id") or "")
        status = str(receipt.get("status") or "")
        sources = [item for item in receipt.get("disclosure_sources") or []
                   if isinstance(item, dict)]
        source_pages = [item.get("page") for item in sources
                        if isinstance(item.get("page"), int) and item.get("page") > 0]
        source_grounded = []
        for item in sources:
            text = _compact(item.get("text") or "")
            page = item.get("page")
            if not text or not isinstance(page, int) or not (1 <= page <= len(page_texts)):
                continue
            grounded = text[:60] in _compact(page_texts[page - 1])
            source_grounded.append(grounded)
        related_findings = [
            finding for finding in findings
            if str(finding.get("rule_id") or finding.get("rule") or "") == rule_id
            and (str(finding.get("location", {}).get("count_field") or "")
                 in (indicator, "vehicle_stock")
                 or indicator in str(finding.get("message") or ""))
        ]
        finding_pages = [
            int(finding["location"]["page"])
            for finding in related_findings
            if isinstance(finding.get("location"), dict)
            and isinstance(finding["location"].get("page"), int)
        ]
        obligations = [
            {"obligation_id": obligation_id,
             "status": instances.get(obligation_id, {}).get("status"),
             "blocks_gate": instances.get(obligation_id, {}).get("blocks_gate")}
            for obligation_id in obligation_ids_for_rule(rule_id)
        ]
        row = {
            "indicator": indicator,
            "sub_check_id": sub_check,
            "relation_status": status,
            "rule_id": rule_id,
            "rule_status": rule_statuses.get(rule_id),
            "rule_detail": unresolved_rules.get(rule_id),
            "formula": receipt.get("formula"),
            "incomplete_reason": receipt.get("incomplete_reason"),
            "reason_codes": list(receipt.get("reason_codes") or []),
            "disclosure_sources": sources,
            "disclosure_pages": source_pages,
            "disclosure_sources_grounded": source_grounded,
            "finding_ids": [finding.get("id") for finding in related_findings],
            "finding_pages": sorted(set(finding_pages)),
            "obligations": obligations,
            "evidence_page": (source_pages[0] if source_pages
                              else (finding_pages[0] if finding_pages else None)),
        }
        rows.append(row)

        # 关系 → 规则：规则状态不得掩盖未完成关系
        if status in UNRESOLVED:
            check(
                f"{indicator}:rule_status_not_hiding_unresolved",
                str(row["rule_status"]) in UNRESOLVED,
                {"rule_id": rule_id, "relation_status": status,
                 "rule_status": row["rule_status"], "rule_detail": row["rule_detail"]},
            )
            check(
                f"{indicator}:insufficient_has_reason",
                bool(str(row["incomplete_reason"] or "").strip()),
                {"incomplete_reason": row["incomplete_reason"]},
            )
            check(
                f"{indicator}:obligation_reflects_unresolved",
                any(
                    obligation["status"] in UNRESOLVED
                    for obligation in obligations
                ),
                {"obligations": obligations},
            )
        elif status == "not_applicable":
            legal_basis = bool(source_pages) or "金额为0" in str(row["formula"]) \
                or "未发生" in str(row["formula"])
            grounded = (not source_grounded) or all(source_grounded)
            check(
                f"{indicator}:na_has_legal_basis",
                legal_basis and grounded,
                {"formula": row["formula"], "disclosure_pages": source_pages,
                 "sources_grounded": source_grounded},
            )
        elif status == "fail":
            check(
                f"{indicator}:fail_has_finding",
                bool(related_findings),
                {"finding_ids": row["finding_ids"]},
            )
        check(
            f"{indicator}:rule_maps_to_obligation",
            bool(obligations),
            {"rule_id": rule_id, "obligations": obligations},
        )

    for indicator in INDICATORS:
        check(
            f"{indicator}:single_receipt",
            seen.get(indicator, 0) == 1,
            {"count": seen.get(indicator, 0)},
        )
    unknown = sorted(set(seen) - set(INDICATORS))
    check("no_unknown_quantity_relation", not unknown, {"unknown": unknown})

    overall = all(item["ok"] for item in checks)
    return {
        "case": case,
        "source_file_hash": _sha(pdf_path),
        "git_head": _git_head(),
        "coverage": {key: ledger.get(key) for key in (
            "applicable_total", "completed_total", "unresolved_total",
            "blocking_total", "coverage_rate", "blocking_obligation_ids")},
        "rows": rows,
        "checks": checks,
        "ok": overall,
        "failed_checks": [item for item in checks if not item["ok"]],
    }


def _git_head() -> str:
    import subprocess

    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
                          capture_output=True, text=True).stdout.strip()


def _markdown(report: dict) -> str:
    lines = ["# 三公经费数量回执对账", "",
             f"- git HEAD: `{report.get('git_head')}`",
             f"- 总结果: {'PASS' if report.get('ok') else 'FAIL'}", ""]
    for case in report.get("cases") or []:
        lines.append(f"## {case['case']['name']}")
        lines.append("")
        lines.append(f"- 原件哈希: `{case['source_file_hash']}`")
        lines.append(f"- 覆盖: {json.dumps(case['coverage'], ensure_ascii=False)}")
        lines.append("")
        lines.append("| 关系 | 关系状态 | 规则 | 规则状态 | 义务状态 | 证据页 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for row in case["rows"]:
            obligation_status = "；".join(
                f"{item['obligation_id']}={item['status']}"
                for item in row["obligations"]) or "—"
            lines.append(
                f"| {row['sub_check_id']} | {row['relation_status']} | "
                f"{row['rule_id']} | {row['rule_status']} | {obligation_status} | "
                f"{row['evidence_page']} |")
        lines.append("")
        status = "PASS" if case["ok"] else "FAIL"
        lines.append(f"- 结论: **{status}**")
        for failed in case["failed_checks"]:
            lines.append(
                f"  - FAIL `{failed['check']}`: "
                f"{json.dumps(failed['detail'], ensure_ascii=False)[:300]}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, default=None)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    cases = []
    for case in manifest:
        print(f"[case] {case.get('name')} ...", flush=True)
        result = run_case(case)
        print(f"[{'PASS' if result['ok'] else 'FAIL'}] {case.get('name')}",
              flush=True)
        if not result["ok"]:
            for failed in result["failed_checks"]:
                print("  -", failed["check"],
                      json.dumps(failed["detail"], ensure_ascii=False)[:300],
                      flush=True)
        cases.append(result)
    report = {
        "git_head": _git_head(),
        "manifest": str(args.manifest),
        "cases": cases,
        "ok": all(item["ok"] for item in cases),
        "note": (
            "数量回执对账：验证关系→规则→义务→页面可追溯，合法空白/空表说明/"
            "明确无三公支出得到适用性结论，且规则 pass 不掩盖关系 insufficient_data。"
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(_markdown(report), encoding="utf-8")
    print("OVERALL:", "PASS" if report["ok"] else "FAIL", flush=True)
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
