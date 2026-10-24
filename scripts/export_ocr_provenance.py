"""Reconstruct saved OCR request images and verify their recorded SHA-256.

This exports engineering evidence from an actual fact snapshot. It does not
create human truth or certify quotes. Reconstruction is explicitly labelled.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def export(source: Path, snapshot: Path, destination: Path) -> dict:
    from PIL import Image
    from src.services.ocr_pipeline import _render_pages_png

    destination.mkdir(parents=True, exist_ok=True)
    data = json.loads(snapshot.read_text())
    refs = {
        reference["evidence_id"]: reference
        for fact in data.get("facts", [])
        for reference in fact.get("extra_dims", {}).get("evidence_refs", [])
        if reference.get("source_method") == "ocr"
    }
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    pages = sorted({reference["physical_page"] for reference in refs.values()})
    report = {
        "scope": "engineering_provenance_not_human_truth",
        "input_method": "reconstructed_from_original_pdf_and_recorded_crop_then_hash_verified",
        "source_sha256": source_hash,
        "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "coverage": "OCR references in the supplied fiscal fact snapshot",
        "records": [],
        "errors": [],
    }
    rendered = _render_pages_png(str(source), pages) if pages else ([], {})
    images, transforms = rendered if rendered else ([], {})
    images = dict(zip(pages, images, strict=True))

    def save_image(content, expected_hash):
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_hash:
            raise ValueError("reconstructed request image hash mismatch")
        path = destination / f"input-{actual}.png"
        path.write_bytes(content)
        return {"path": path.name, "sha256": actual}

    def save_response(response):
        content = json.dumps(
            response["raw_prediction"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        actual = hashlib.sha256(content).hexdigest()
        if actual != response.get("raw_prediction_sha256"):
            raise ValueError("saved raw response hash mismatch")
        path = destination / f"raw-{actual}.json"
        path.write_bytes(content)
        return {
            "path": path.name,
            "sha256": actual,
            "model_fingerprint": response.get("model_fingerprint"),
            "pipeline_fingerprint": response.get("pipeline_fingerprint"),
            "prediction_coordinate_scale": response.get("prediction_coordinate_scale"),
        }

    for evidence_id, reference in refs.items():
        try:
            provenance = reference["source_provenance"]
            page = reference["physical_page"]
            recorded = provenance["source"]
            if (
                reference.get("file_hash") != source_hash
                or recorded.get("source_sha256") != source_hash
            ):
                raise ValueError("original file hash mismatch")
            image = images[page]
            item = {
                "evidence_id": evidence_id,
                "physical_page": page,
                "source_cell_id": reference.get("source_cell_id"),
                "bbox": reference.get("bbox"),
                "transform": transforms[page],
                "page_input": save_image(image, recorded["input_sha256"]),
                "page_response": save_response(recorded),
                "qualification": provenance.get("qualification"),
            }
            cell = provenance["cell"]
            recheck = cell.get("region_recheck") or {}
            if recheck.get("input_sha256"):
                with Image.open(io.BytesIO(image)) as rendered_image:
                    crop = rendered_image.crop(tuple(cell["render_bbox"]))
                    buffer = io.BytesIO()
                    crop.save(buffer, format="PNG")
                item["region_input"] = save_image(buffer.getvalue(), recheck["input_sha256"])
                if recheck.get("provenance"):
                    item["region_response"] = save_response(recheck["provenance"])
                item["region_status"] = recheck.get("status")
            else:
                item["region_status"] = recheck.get("status", "not_recorded")
            report["records"].append(item)
        except (KeyError, TypeError, ValueError) as exc:
            report["errors"].append({"evidence_id": evidence_id, "reason": str(exc)})
    report["status"] = (
        "failed" if report["errors"] else "verified_request_hashes" if refs else "not_applicable"
    )
    (destination / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = export(args.source, args.snapshot, args.output)
    print(
        json.dumps(
            {
                "status": report["status"],
                "records": len(report["records"]),
                "errors": report["errors"],
            },
            ensure_ascii=False,
        )
    )
    return 2 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
