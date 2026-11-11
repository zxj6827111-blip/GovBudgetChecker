#!/usr/bin/env python
"""D0 真实管道复现器（IR-03 验收器修复版，2026-11-10）。

替换旧版有缺陷的 --task 逻辑（吞 NameError 后页文本清空、校验结果不写
回、unverified 被当成"恢复"）。本脚本按 api/main.py 的同一顺序执行：
  原生解析（ParseArtifact）→ 事实上下文（同一次解析物化）
  → legacy 规则入口（pipeline.build_issues_payload，final 规则集）
  → bind_rule_sources（真实源绑定）
  → verify_bound_rule_locations（PDF 几何核验）
  → apply_evidence_completeness（证据守卫：原文匹配 + 当前原件身份）
回执固定：代码版本/关键文件哈希、原件哈希、解析 run uid、事实数、配置、
执行摘要；findings 的 evidence_verification 为**校验后**写回结果——
verified 才算恢复，unverified/failed/未执行一律不算。

用法:
    .venv/bin/python scripts/d0_realpath_repro.py <pdf> --org <单位名> \
        --year 2025 --kind final --out <json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(pdf: str, org: str, year: int, kind: str, out: str) -> dict:
    import pdfplumber

    from src.engine.fact_context import build_fact_context_from_artifact
    from src.engine.pipeline import build_document, build_issues_payload
    from src.engine.rule_registry import select_rules
    from src.services.evidence_guard import apply_evidence_completeness, iter_findings
    from src.services.pdf_page_extract import (
        extract_tables_from_page,
        extract_visible_text_from_page,
    )
    from src.services.pdf_parser import PDFParser
    from src.services.rule_source_binding import bind_rule_sources, verify_bound_rule_locations

    pdf_path = Path(pdf)
    original_hash = _sha(pdf_path)

    artifact = PDFParser(None).build_parse_artifact(str(pdf_path))
    context = build_fact_context_from_artifact(
        artifact, org_name=org, fiscal_year=year, report_kind=kind, doc_type="dept_final"
    )
    with pdfplumber.open(str(pdf_path)) as doc_pdf:
        page_texts = [extract_visible_text_from_page(page) for page in doc_pdf.pages]
        page_tables = [extract_tables_from_page(page) for page in doc_pdf.pages]
        page_count = len(page_texts)

    doc = build_document(str(pdf_path), page_texts, page_tables, pdf_path.stat().st_size)
    doc.parse_artifact = artifact
    doc.fact_context = context

    started = time.monotonic()
    payload = build_issues_payload(doc, use_ai_assist=False, report_kind=kind,
                                    rules=list(select_rules(kind)))
    elapsed = round(time.monotonic() - started, 1)

    result = {
        "summary": "",
        "ai_findings": [],
        "rule_findings": list((payload.get("issues") or {}).get("all") or []),
        "merged": {},
        "meta": {
            "rule_execution_summary": payload.get("rule_execution_summary"),
            "fact_receipts": payload.get("fact_receipts", []),
            "delivery_suppressions": payload.get("delivery_suppressions", []),
        },
    }

    # api/main.py 同序：源绑定 → PDF 几何 → 证据守卫（校验写回 finding）
    binding = bind_rule_sources(
        result, page_texts=page_texts, source_file_hash=original_hash,
        artifact=artifact, fact_context=context,
        consumption=(list(payload.get("structured_consumption") or [])
                     + list(payload.get("fact_receipts") or [])),
    )
    locations = verify_bound_rule_locations(result, source_pdf_path=str(pdf_path))
    completeness = apply_evidence_completeness(
        result,
        document_page_count=page_count,
        page_texts=page_texts,
        source_file_hash=original_hash,
        source_pdf_path=str(pdf_path),
    )

    verified, failed, unverified = [], [], []
    findings_out = []
    for finding in iter_findings(result):
        status = str((finding.get("evidence_verification") or {}).get("status") or "unverified")
        entry = {
            "id": finding.get("id"),
            "rule_id": finding.get("rule_id") or finding.get("rule"),
            "severity": finding.get("severity"),
            "page": (finding.get("location") or {}).get("page"),
            "message": str(finding.get("message") or "")[:160],
            "evidence_verification": finding.get("evidence_verification"),
            "evidence": [
                {k: e.get(k) for k in ("text", "quote", "page", "physical_page",
                                       "file_hash", "source_cell_id", "verification_status",
                                       "source_identity_status")}
                for e in (finding.get("evidence") or [])
            ],
        }
        findings_out.append(entry)
        if status == "verified":
            verified.append(entry["rule_id"])
        elif status == "failed":
            failed.append(entry)
        else:
            unverified.append(entry["rule_id"])

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
                          capture_output=True, text=True).stdout.strip()
    report = {
        "input": {"pdf": str(pdf_path), "original_sha256": original_hash,
                  "org": org, "year": year, "kind": kind},
        "frozen_versions": {
            "git_head": head,
            "key_source_sha256": {
                str(rel): _sha(_REPO_ROOT / rel) for rel in (
                    "src/engine/rules_v33.py", "src/engine/pipeline.py",
                    "src/engine/issue_evidence.py",
                    "src/services/rule_source_binding.py",
                    "src/services/evidence_guard.py",
                    "src/services/pdf_parser.py",
                )
            },
            "parse_run_uid": artifact.parse_run_uid,
            "fact_count": len(getattr(context, "facts", []) or []),
            "page_count": page_count,
            "config": {"use_ai_assist": False, "ocr": "disabled",
                       "empty_value_conventions": "builtin(U-5)"},
        },
        "elapsed_seconds": elapsed,
        "rule_execution_summary": payload.get("rule_execution_summary"),
        "binding": {"rule_source_binding": binding},
        "locations": {"rule_source_locations": locations},
        "evidence_completeness": completeness,
        "counts": {"findings": len(findings_out), "verified": len(verified),
                   "failed": len(failed), "unverified": len(unverified)},
        "findings": findings_out,
        "note": "verified 才算恢复；unverified/failed/未执行均不算（IR-03）",
    }
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    Path(out).write_text(text, encoding="utf-8")
    print(json.dumps({"counts": report["counts"],
                      "elapsed_seconds": elapsed,
                      "failed": [{"rule_id": f["rule_id"],
                                  "reasons": (f["evidence_verification"] or {}).get("reasons")}
                                 for f in failed],
                      "unverified": sorted(set(unverified))},
                     ensure_ascii=False, indent=1))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--org", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--kind", default="final")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    run(args.pdf, args.org, args.year, args.kind, args.out)


if __name__ == "__main__":
    main()
