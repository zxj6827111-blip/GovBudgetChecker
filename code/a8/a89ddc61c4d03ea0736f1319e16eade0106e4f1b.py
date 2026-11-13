"""回放真实 v3 OCR 响应，验证原生漏格恢复；故障注入不计入独立验收。"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from src.schemas.parse_artifact import ParseArtifact
from src.services.fiscal_input_builder import FiscalInputBuilder
from src.services.input_geometry import Affine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("frozen_artifact", type=Path)
    parser.add_argument("frozen_context", type=Path)
    parser.add_argument("saved_ocr_receipt", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False, parents=True)
    saved = json.loads(args.saved_ocr_receipt.read_text())
    source_path = Path(saved["source"])
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == saved["source_sha256"]
    artifact = ParseArtifact.from_dict(json.loads(args.frozen_artifact.read_text()))
    assert artifact.source_file_hash == saved["source_sha256"]
    frozen = json.loads(args.frozen_context.read_text())
    profile = {k: frozen.get(k) for k in ("org_name", "fiscal_year", "report_kind")}
    builder = FiscalInputBuilder()
    before = builder.facts(artifact, **profile)
    original_grids = deepcopy([t.to_dict() for t in artifact.source_grids])
    context = saved["tile_context"]
    matrix = Affine(*context["pixel_to_pdf"])
    target = matrix.box(saved["aligned_candidates"][0]["bbox"])
    x, y = (target[0] + target[2]) / 2, (target[1] + target[3]) / 2
    matches = [
        (t, c)
        for t in artifact.table_fragments
        for c in t.cells
        if c.page_number == saved["page"]
        and c.bbox
        and not c.is_header
        and c.bbox[0] < x < c.bbox[2]
        and c.bbox[1] < y < c.bbox[3]
    ]
    assert len(matches) == 1, "visual source position must identify one native field"
    table, cell = matches[0]
    row = cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx
    col = cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx
    physical_id = f"{cell.origin_fragment_uid or table.fragment_uid}:r{row}c{col}"
    original = next(
        f
        for f in before.facts
        if any(e["source_cell_id"] == physical_id for e in f.extra_dims["evidence_refs"])
    )
    cell.raw_text = cell.normalized_text = "�"
    cell.numeric_value = cell.normalized_value_wan = None
    cell.native_text_sources = []
    table.rows[cell.row_idx][cell.col_idx] = "�"
    source = saved["result"]["provenance"]
    artifact.ocr_model_fingerprint = source["model_fingerprint"]
    transform_id = "ocr_tile_" + context["tile_id"]
    artifact.coordinate_transforms[transform_id] = {
        "transform_version": "pdf-tile-v3",
        "physical_page": saved["page"],
        "source_sha256": artifact.source_file_hash,
        "tile_context": context,
        "ocr_response": source,
        "render_sha256": context["input_sha256"],
        "render_size_px": context["size_px"],
        "bbox_transform_verified": True,
        "pixel_to_pdf_matrix": context["pixel_to_pdf"],
        "pdf_to_pixel_matrix": context["pdf_to_pixel"],
        "page_request": {
            "physical_page": saved["page"],
            "source_sha256": artifact.source_file_hash,
            "render_sha256": context["input_sha256"],
            "input_sha256": context["input_sha256"],
            **{
                k: source[k]
                for k in (
                    "schema_version",
                    "raw_prediction_sha256",
                    "model_fingerprint",
                    "pipeline_fingerprint",
                )
            },
        },
    }
    started = time.monotonic()
    after = builder.facts(artifact, **profile)
    recovered = next(
        f
        for f in after.facts
        if any(e["source_cell_id"] == physical_id for e in f.extra_dims["evidence_refs"])
    )
    receipt = {
        "scope": "saved_real_response_replay_with_native_fault_injection_not_new_inference_or_blind_acceptance",
        "source": str(source_path),
        "source_sha256": artifact.source_file_hash,
        "response_sha256": source["raw_prediction_sha256"],
        "protocol": source["schema_version"],
        "physical_source_id": physical_id,
        "original": original.to_dict(),
        "injected_native_literal": "�",
        "actual": recovered.to_dict(),
        "same_business_identity": original.identity_key() == recovered.identity_key(),
        "same_original_literal": original.raw_value == recovered.raw_value,
        "selected_visual": recovered.extra_dims["evidence_refs"][0]["source_method"] == "ocr",
        "all_business_identities_unchanged": {f.fact_uid: f.identity_key() for f in before.facts}
        == {f.fact_uid: f.identity_key() for f in after.facts},
        "source_grids_unchanged": original_grids == [t.to_dict() for t in artifact.source_grids],
        "original_file_unchanged": hashlib.sha256(source_path.read_bytes()).hexdigest()
        == artifact.source_file_hash,
        "before_fact_count": len(before.facts),
        "after_fact_count": len(after.facts),
        "versions": artifact.input_versions,
        "seconds": time.monotonic() - started,
        "decisions": [
            d
            for d in artifact.reconstruction_diagnostics
            if d.get("physical_source_id") == physical_id
        ],
    }
    (args.output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, default=str)
    )
    keys = (
        "same_business_identity",
        "same_original_literal",
        "selected_visual",
        "all_business_identities_unchanged",
        "source_grids_unchanged",
        "original_file_unchanged",
    )
    print(json.dumps({k: receipt[k] for k in keys}, ensure_ascii=False))
    assert all(receipt[k] for k in keys)


if __name__ == "__main__":
    main()
