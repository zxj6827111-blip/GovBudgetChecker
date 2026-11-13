#!/usr/bin/env python3
"""Preflight: lock identities, verify original hashes, copy historical state read-only,
and classify each PDF from the original (independent of the pipeline under test).

Writes preflight.json into the run directory. Never writes to uploads/ or production data.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]
MAIN = Path("/Volumes/workSSD/software development/GovBudgetChecker")
MANIFEST = MAIN / "outputs/unified_closeout_round6_review_codex_20261104/diagnostic_manifest_10.json"
WORKTREES = {
    "business": Path("/Volumes/workSSD/software development/gbc-worktrees/unified-closeout-20261103"),
    "comparator": Path("/Volumes/workSSD/software development/gbc-worktrees/joint-exp-batch2-tools-20261103"),
    "tools": Path("/Volumes/workSSD/software development/gbc-worktrees/closeout-tools-r4-20261104"),
    "b1": Path("/Volumes/workSSD/software development/gbc-worktrees/gold-b1-20261103"),
}
EXPECTED_HEADS = {
    "business": "4cda0edd361c81530654da8915306a400bd7f100",
    "comparator": "0a1379cd5a4336fda61b2aea217a728c664c0b94",
    "tools": "99b50799c0db9360ee892a2ffe082ff4a7adc2eb",
    "b1": "e0efa58a5c56b77404f92e6c0dabb07dd8d79f3d",
}
FINGERPRINT_FILES = [
    "api/main.py",
    "api/runtime.py",
    "api/worker.py",
    "api/routes/upload.py",
    "api/routes/reports.py",
    "api/routes/jobs.py",
    "config/app.yaml",
    "rules/v3_3.yaml",
    "src/engine/check_obligations.py",
    "src/engine/checklist_catalog.py",
    "src/services/pdf_parser.py",
    "src/services/ocr_client.py",
    "src/services/ocr_pipeline.py",
    "src/services/ocr_tiled_pipeline.py",
    "src/services/native_ocr_fusion.py",
    "src/services/ocr_grid_reconstruction.py",
    "src/services/ocr_fact_promotion.py",
    "src/services/structured_ingest_runner.py",
    "src/services/ai_model_settings.py",
    "src/services/ai_execution.py",
    "src/engine/ai/extractor_client.py",
]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def classify_pdf(path: Path) -> dict:
    import pymupdf  # main-repo venv; independent of candidate parser

    doc = pymupdf.open(path)
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        chars = len("".join(text.split()))
        images = len(page.get_images(full=True))
        pages.append({"page": i + 1, "text_chars": chars, "images": images})
    doc.close()
    no_text = [p["page"] for p in pages if p["text_chars"] < 20]
    total = len(pages)
    if total and len(no_text) == total:
        kind = "scanned"
    elif no_text:
        kind = "mixed"
    else:
        kind = "native_text"
    return {"pdf_pages": total, "no_text_pages": no_text, "kind": kind,
            "text_chars_per_page": [p["text_chars"] for p in pages]}


def cover_text(path: Path, max_pages: int = 3) -> str:
    import pymupdf

    doc = pymupdf.open(path)
    out = []
    for i in range(min(max_pages, doc.page_count)):
        out.append(doc[i].get_text("text") or "")
    doc.close()
    return "\n".join(out)


def main() -> int:
    manifest = json.loads(MANIFEST.read_text())
    result: dict = {
        "kind": "real_painpoint_diag_preflight",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "run_dir": str(RUN),
        "manifest": {"path": str(MANIFEST), "sha256": sha(MANIFEST), "count": manifest["count"]},
        "worktrees": {},
        "identity_ok": True,
        "items": [],
        "environment": {
            "python": sys.version,
            "python_executable": sys.executable,
            "platform": sys.platform,
        },
    }
    for name, path in WORKTREES.items():
        head = git(path, "rev-parse", "HEAD")
        status = git(path, "status", "--porcelain")
        entry = {"path": str(path), "head": head, "expected_head": EXPECTED_HEADS[name],
                 "dirty": [line for line in status.splitlines() if line.strip()]}
        entry["identity_match"] = head == EXPECTED_HEADS[name]
        entry["files"] = {}
        for rel in FINGERPRINT_FILES:
            candidate = path / rel
            entry["files"][rel] = sha(candidate) if candidate.exists() else None
        result["worktrees"][name] = entry
        if not entry["identity_match"]:
            result["identity_ok"] = False

    # OCR service preflight (read-only)
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:9010/health", timeout=5) as resp:
            result["ocr_preflight"] = {"status": resp.status, "body": json.loads(resp.read().decode())}
    except Exception as exc:  # noqa: BLE001
        result["ocr_preflight"] = {"error": f"{type(exc).__name__}: {exc}"}

    for item in manifest["items"]:
        job = item["job_id"]
        pdf = Path(item["pdf_path"])
        status_path = Path(item["historical_status_path"])
        target_dir = RUN / "evidence/historical" / job
        target_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "order": item["order"],
            "job_id": job,
            "filename": item["filename"],
            "year_expected": item["year"],
            "historical_doc_type": item["historical_doc_type"],
            "historical_mode": item["historical_mode"],
            "historical_use_ai_assist": item["historical_use_ai_assist"],
            "historical_ai_state": item["historical_ai_state"],
            "historical_revision": item["historical_revision"],
            "unfinished_total": item["unfinished_total"],
        }
        entry["pdf_exists"] = pdf.exists()
        if pdf.exists():
            actual = sha(pdf)
            entry["pdf_sha256"] = actual
            entry["pdf_sha256_manifest"] = item["sha256"]
            entry["pdf_sha256_match"] = actual == item["sha256"]
            entry["pdf_size"] = pdf.stat().st_size
            entry["pdf_classification"] = classify_pdf(pdf)
            cover = cover_text(pdf)
            entry["cover_text_head"] = cover[:1200]
        if status_path.exists():
            raw = status_path.read_bytes()
            entry["historical_status_sha256"] = hashlib.sha256(raw).hexdigest()
            entry["historical_status_sha256_manifest"] = item["historical_status_sha256"]
            entry["historical_status_match"] = entry["historical_status_sha256"] == item["historical_status_sha256"]
            shutil.copy2(status_path, target_dir / "status.json")
            hist = json.loads(raw)
            issues_obj = (hist.get("result") or {}).get("issues") or {}
            if isinstance(issues_obj, dict):
                issue_list = list(issues_obj.get("all") or [])
                for key in ("error", "warn", "info"):
                    if not issue_list:
                        issue_list = list(issues_obj.get(key) or [])
            else:
                issue_list = list(issues_obj or [])
            hist["_issue_list"] = issue_list
            entry["historical_status_summary"] = {
                "status": hist.get("status"),
                "quality_status": hist.get("quality_status"),
                "analysis_conclusion": hist.get("analysis_conclusion"),
                "mode": hist.get("mode"),
                "dual_mode_enabled": hist.get("dual_mode_enabled"),
                "fiscal_year": hist.get("fiscal_year"),
                "doc_type": hist.get("doc_type"),
                "report_kind": hist.get("report_kind"),
                "organization_name": hist.get("organization_name"),
                "page_coverage": hist.get("page_coverage"),
                "scanned_page_count": hist.get("scanned_page_count"),
                "review_reasons": hist.get("review_reasons"),
                "check_coverage": {
                    k: v for k, v in (hist.get("check_coverage") or {}).items() if k != "instances"
                },
                "check_instances": (hist.get("check_coverage") or {}).get("instances") or [],
                "issues_count": len(hist["_issue_list"]),
                "manual_review_count": len(((hist.get("review_report") or {}).get("manual_review")) or []),
                "issue_ids": [i.get("rule_id") or i.get("id") for i in hist["_issue_list"] if isinstance(i, dict)],
                "issue_severities": [i.get("severity") for i in hist["_issue_list"] if isinstance(i, dict)],
                "ocr_progress": hist.get("ocr_progress"),
                "pipeline_budget": hist.get("pipeline_budget"),
            }
            # preserve full review report too
            (target_dir / "review_report.json").write_text(
                json.dumps(hist.get("review_report") or {}, ensure_ascii=False, indent=1)
            )
            (target_dir / "result_issues.json").write_text(
                json.dumps(hist["_issue_list"], ensure_ascii=False, indent=1)
            )
        result["items"].append(entry)

    (RUN / "preflight.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({
        "identity_ok": result["identity_ok"],
        "ocr_preflight": result["ocr_preflight"].get("body", result["ocr_preflight"]),
        "items": [
            {"order": i["order"], "job": i["job_id"][:8], "pdf_ok": i.get("pdf_sha256_match"),
             "status_ok": i.get("historical_status_match"), "kind": (i.get("pdf_classification") or {}).get("kind"),
             "pages": (i.get("pdf_classification") or {}).get("pdf_pages")}
            for i in result["items"]
        ],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
