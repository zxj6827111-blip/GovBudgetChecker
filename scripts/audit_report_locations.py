"""只读审计全部已上传报告的 PDF 页码、引用与坐标，保存核对凭据。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def normalize(text):
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def audit(output: Path):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import fitz
    from api.runtime import UPLOAD_ROOT, get_job_status_payload
    from src.services.evidence_guard import iter_findings
    from src.services.pdf_selection import select_canonical_pdf

    reports = []
    output.mkdir(parents=True, exist_ok=True)
    for status in sorted(UPLOAD_ROOT.glob("*/status.json")):
        pdfs = sorted(status.parent.glob("*.pdf"))
        if not pdfs:
            continue
        pdf_path = select_canonical_pdf(status.parent)
        payload = get_job_status_payload(status.parent.name)
        (output / f"{status.parent.name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        result = payload.get("result") or {}
        entries = {}
        with fitz.open(pdf_path) as pdf:
            for finding in iter_findings(result):
                if finding.get("id") in entries:
                    continue
                evidence = [x for x in finding.get("evidence") or [] if isinstance(x, dict)]
                entry = {"id": finding.get("id"), "source": finding.get("source"), "rule_id": finding.get("rule_id"),
                         "claimed_page": finding.get("claimed_page_number"), "primary_page": finding.get("page_number"),
                         "evidence_pages": sorted({x.get("physical_page") or x.get("page") for x in evidence if x.get("physical_page") or x.get("page")}),
                         "unresolved": not any(x.get("verification_status") == "verified" and x.get("bbox") for x in evidence),
                         "geometry_errors": [], "clip_quote_warnings": []}
                for index, ref in enumerate(evidence):
                    if ref.get("verification_status") != "verified" or not ref.get("bbox"):
                        continue
                    page = ref.get("physical_page") or ref.get("page")
                    if not isinstance(page, int) or not 1 <= page <= len(pdf):
                        entry["geometry_errors"].append(f"evidence[{index}]:page_out_of_range")
                        continue
                    bbox = ref["bbox"]
                    rect = pdf[page - 1].rect
                    if not (len(bbox) == 4 and 0 <= bbox[0] < bbox[2] <= rect.width + 1 and 0 <= bbox[1] < bbox[3] <= rect.height + 1):
                        entry["geometry_errors"].append(f"evidence[{index}]:bbox_outside_page")
                        continue
                    if ref.get("file_hash") != sha:
                        entry["geometry_errors"].append(f"evidence[{index}]:wrong_original_hash")
                    quote = normalize(ref.get("quote") or ref.get("text"))
                    crop_text = normalize(pdf[page - 1].get_text(clip=fitz.Rect(bbox)))
                    # fitz 与字符定位器可能阅读顺序不同，单独列复核，不宣称坐标错误。
                    if quote and quote not in crop_text:
                        entry["clip_quote_warnings"].append(f"evidence[{index}]:different_reading_order_or_clip")
                primary = finding.get("page_number") or (finding.get("location") or {}).get("page")
                first_page = (evidence[0].get("physical_page") or evidence[0].get("page")) if evidence else None
                entry["previous_ui_cross_page_mix"] = bool(finding.get("bbox") and first_page and primary and first_page != primary)
                entries[finding.get("id")] = entry
            reports.append({"job_id": status.parent.name, "filename": pdf_path.name, "pages": len(pdf),
                            "page_sizes": [[page.rect.width, page.rect.height] for page in pdf],
                            "source_hash": sha, "findings": list(entries.values()),
                            "common_occurrences": len(result.get("source_overlap_relations") or []),
                            "ai_recovery": (result.get("meta") or {}).get("ai_location_recovery")})
        print(f"Checked {pdf_path.name}: {len(entries)} findings", flush=True)
    findings = [x for report in reports for x in report["findings"]]
    receipt = {"reports": reports, "summary": {
        "reports": len(reports), "findings": len(findings),
        "ai_findings": sum(x["source"] == "ai" for x in findings),
        "previous_ui_cross_page_mix": sum(x["previous_ui_cross_page_mix"] for x in findings),
        "geometry_errors": sum(len(x["geometry_errors"]) for x in findings),
        "clip_quote_warnings": sum(len(x["clip_quote_warnings"]) for x in findings),
        "unresolved": sum(x["unresolved"] for x in findings),
        "common_occurrences": sum(x["common_occurrences"] for x in reports),
        "snapshots_modified": False, "model_calls": 0}}
    target = output / "location-audit.json"
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt["summary"], ensure_ascii=False), flush=True)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "location-round2")
    audit(parser.parse_args().output)
