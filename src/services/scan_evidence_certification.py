"""Verify scanned references against independently frozen human attestations.

The recogniser never creates attestations. A deployment pins the external
manifest SHA; raw model scores or OCR self-comparison cannot certify a quote.
"""

from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ocr_service.protocol import valid_bbox


def load_attestations(
    path: str | Path | None = None, expected_sha: str | None = None
) -> dict | None:
    path = path or os.getenv("OCR_EVIDENCE_ATTESTATIONS_PATH")
    expected_sha = expected_sha or os.getenv("OCR_EVIDENCE_ATTESTATIONS_SHA256")
    if not path or not expected_sha:
        return None
    try:
        data = Path(path).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_sha:
            return None
        manifest = json.loads(data)
        if manifest.get("schema_version") != "gbc.scan.attestations.v1" or not manifest.get(
            "frozen_at"
        ):
            return None
        if not manifest.get("reviewed_by") or manifest.get("generated_by") in {
            "ocr",
            "AI",
            "ai",
            "system",
        }:
            return None
        manifest["_manifest_directory"] = str(Path(path).resolve().parent)
        return manifest
    except (OSError, ValueError, TypeError):
        return None


def verify_scan_reference(
    reference: dict[str, Any], pdf_path: str | Path, manifest: dict | None
) -> bool:
    if manifest is None or reference.get("source_method") != "ocr":
        return False
    box = valid_bbox(reference.get("bbox"))
    page_number = reference.get("physical_page")
    if (
        box is None
        or not isinstance(page_number, int)
        or isinstance(page_number, bool)
        or page_number < 1
    ):
        return False
    source = reference.get("source_provenance", {}).get("source", {})
    if not source.get("model_fingerprint") or not source.get("raw_prediction_sha256"):
        return False
    matches = [
        record
        for record in manifest.get("regions", [])
        if isinstance(record, dict)
        and record.get("file_hash") == reference.get("file_hash")
        and record.get("physical_page") == page_number
        and record.get("bbox") == list(box)
        and record.get("quote") == reference.get("quote")
        and record.get("source_cell_id") == reference.get("source_cell_id")
        and record.get("model_fingerprint") == source.get("model_fingerprint")
        and record.get("raw_prediction_sha256") == source.get("raw_prediction_sha256")
        and record.get("reviewed_by")
        and record.get("review_record_sha256")
    ]
    if len(matches) != 1:
        return False
    record = matches[0]
    try:
        import fitz

        review_root = Path(manifest["_manifest_directory"]).resolve()
        review_path = (review_root / record["review_record_path"]).resolve()
        if review_root not in review_path.parents or not review_path.is_file():
            return False
        if hashlib.sha256(review_path.read_bytes()).hexdigest() != record["review_record_sha256"]:
            return False
        path = Path(pdf_path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != reference.get("file_hash"):
            return False
        dpi = int(record.get("render_dpi", 300))
        if not 72 <= dpi <= 600:
            return False
        with fitz.open(path) as document:
            if page_number > document.page_count:
                return False
            page = document[page_number - 1]
            if not page.rect.contains(fitz.Rect(box)):
                return False
            matrix = fitz.Matrix(dpi / 72, dpi / 72)
            whole = page.get_pixmap(matrix=matrix).tobytes("png")
            crop = page.get_pixmap(matrix=matrix, clip=fitz.Rect(box)).tobytes("png")
        return hashlib.sha256(whole).hexdigest() == record.get("render_sha256") == source.get(
            "render_sha256"
        ) and hashlib.sha256(crop).hexdigest() == record.get("crop_sha256")
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        return False
