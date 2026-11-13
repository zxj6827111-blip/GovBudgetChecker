"""OCR operand qualification is separate from original-evidence certification."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ocr_service.protocol import PROTOCOL_VERSION, valid_bbox, valid_score


def bound_response(response: dict) -> dict | None:
    """Recompute tokens and cells from the saved raw prediction and transform."""
    import math
    from ocr_service.app import _render_prediction

    try:
        raw = response["raw_prediction"]
        digest = hashlib.sha256(
            json.dumps(
                raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        scale = response.get("prediction_coordinate_scale", [1.0, 1.0])
        if not isinstance(raw, dict) or digest != response.get("raw_prediction_sha256"):
            return None
        if len(scale) != 2 or not all(math.isfinite(value) and value >= 1 for value in scale):
            return None
        return _render_prediction(raw, scale)
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def qualification(
    candidate: Any,
    artifact: Any,
    *,
    organization: str | None,
    year: int | None,
    report_kind: str | None,
) -> dict:
    reasons = []
    provenance = candidate.provenance
    transform = artifact.coordinate_transforms.get(candidate.coordinate_transform_id, {})
    if candidate.mapping_status != "mapped":
        reasons.append("cell_mapping_not_unique")
    if provenance.get("schema_version") != PROTOCOL_VERSION:
        reasons.append("cell_protocol_missing")
    for key in (
        "source_sha256",
        "render_sha256",
        "input_sha256",
        "model_fingerprint",
        "pipeline_fingerprint",
        "raw_prediction_sha256",
    ):
        if not re.fullmatch("[a-f0-9]{64}", str(provenance.get(key) or "")):
            reasons.append("missing_" + key)
    if provenance.get("source_sha256") != artifact.source_file_hash:
        reasons.append("source_hash_mismatch")
    if provenance.get("model_fingerprint") != artifact.ocr_model_fingerprint:
        reasons.append("model_hash_mismatch")
    if provenance.get("render_sha256") != transform.get("render_sha256") or provenance.get(
        "input_sha256"
    ) != transform.get("render_sha256"):
        reasons.append("render_hash_mismatch")
    if not transform.get("bbox_transform_verified"):
        reasons.append("coordinate_transform_unverified")
    raw = provenance.get("raw_prediction")
    try:
        digest = hashlib.sha256(
            json.dumps(
                raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        if digest != provenance.get("raw_prediction_sha256"):
            reasons.append("raw_prediction_hash_mismatch")
    except (ValueError, TypeError):
        reasons.append("raw_prediction_invalid")
    keys = (
        "row_idx",
        "col_idx",
        "row_span",
        "col_span",
        "raw_text",
        "render_bbox",
        "fragments",
        "score",
    )

    def signature(cells):
        return sorted(
            json.dumps({key: cell.get(key) for key in keys}, sort_keys=True) for cell in cells
        )

    bound = bound_response(provenance)
    bound_tables = bound["tables"] if bound else []
    if (
        transform.get("render_size_px")
        and provenance.get("source_size_px") != transform["render_size_px"]
    ):
        reasons.append("prediction_render_size_mismatch")
    dimensions = raw.get("res", raw) if isinstance(raw, dict) else {}
    if bound and dimensions.get("width") and dimensions.get("height"):
        scale = provenance.get("prediction_coordinate_scale", [1.0, 1.0])
        if [
            round(dimensions["width"] * scale[0]),
            round(dimensions["height"] * scale[1]),
        ] != provenance.get("source_size_px"):
            reasons.append("prediction_scale_mismatch")
    if not any(
        signature(table.get("cells", [])) == signature(candidate.cells) for table in bound_tables
    ):
        reasons.append("cells_not_bound_to_raw_prediction")
    blocks = [block for block in artifact.text_blocks if block.extraction_method == "ocr"]
    text = "\n".join(block.text for block in blocks)
    page_blocks = [block for block in blocks if block.page_number == candidate.page_number]
    page_text = "\n".join(block.text for block in page_blocks)
    units = [
        (block, match.group(1))
        for block in page_blocks
        for match in re.finditer(r"单位[:：]\s*(万元|亿元|元)", block.text)
    ]
    if not units or len({unit for block, unit in units}) != 1:
        reasons.append("unit_missing_or_conflicting")
    identity_blocks = [block for block in blocks if organization and organization in block.text]
    year_blocks = [block for block in blocks if year and re.search(rf"{year}\s*年", block.text)]
    if not organization or not identity_blocks:
        reasons.append("original_subject_missing")
    owner_labels = re.findall(
        r"(?:编制部门|编制单位|发布单位|预算主管部门|决算主管部门|预算单位|决算单位)[:：]\s*([^\n:：]+)",
        page_text,
    )
    if owner_labels and {"".join(value.split()) for value in owner_labels} != {
        "".join(str(organization or "").split())
    }:
        reasons.append("table_subject_conflict")
    if not year or not year_blocks:
        reasons.append("original_year_missing")
    page_years = set(re.findall(r"(20\d{2})\s*年", page_text))
    if page_years and page_years != {str(year)}:
        reasons.append("year_scope_conflict")
    stage_word = "决算" if report_kind == "final" else "预算"
    stage_blocks = [block for block in blocks if stage_word in block.text]
    if report_kind not in {"budget", "final"} or stage_word not in text:
        reasons.append("original_stage_missing")
    title = "\n".join(
        block.text for block in page_blocks if "预算表" in block.text or "决算表" in block.text
    )
    if (report_kind == "final" and "预算" in title and "决算" not in title) or (
        report_kind == "budget" and "决算" in title
    ):
        reasons.append("table_stage_conflict")
    if report_kind == "final" and any("预算数" in row for row in candidate.rows):
        reasons.append("budget_actual_columns_require_explicit_binding")
    states = [state for state in artifact.page_states if state.page_number == candidate.page_number]
    if any(state.text_chars for state in states):
        reasons.append("native_OCR_conflict_requires_review")
    critical = identity_blocks + year_blocks + stage_blocks + [block for block, unit in units]
    for block in critical:
        page_transform = artifact.coordinate_transforms.get(
            f"ocr_render_page_{block.page_number}", {}
        )
        saved_response = page_transform.get("ocr_response", {})
        source_blocks = (bound_response(saved_response) or {}).get("blocks", [])
        if (
            saved_response.get("model_fingerprint") != provenance.get("model_fingerprint")
            or saved_response.get("pipeline_fingerprint") != provenance.get("pipeline_fingerprint")
            or saved_response.get("input_sha256")
            != page_transform.get("render_sha256", provenance.get("render_sha256"))
            or not any(
                item["text"] == block.text and item["score"] == block.confidence
                for item in source_blocks
            )
        ):
            reasons.append("identity_not_bound_to_raw_prediction")
    scores = [valid_score(block.confidence) for block in critical]
    seen = set()
    for cell in candidate.cells:
        inline_units = set(re.findall(r"万元|亿元|元", str(cell.get("raw_text") or "")))
        if inline_units and units and inline_units != {units[0][1]}:
            reasons.append("cell_unit_conflict")
        position = (cell.get("row_idx"), cell.get("col_idx"))
        if position in seen or valid_bbox(cell.get("page_bbox")) is None:
            reasons.append("cell_identity_or_bbox_invalid")
        seen.add(position)
        fragments = cell.get("fragments", [])
        if str(cell.get("raw_text") or "").strip():
            if not fragments:
                reasons.append("cell_fragments_missing")
            scores.extend(valid_score(fragment.get("score")) for fragment in fragments)
        if any(char.isdigit() for char in str(cell.get("raw_text") or "")):
            recheck = cell.get("region_recheck", {})
            if recheck.get("status") != "completed" or "".join(
                str(recheck.get("text") or "").split()
            ) != "".join(str(cell["raw_text"]).split()):
                reasons.append("region_recheck_failed_or_conflicting")
            if recheck.get("provenance", {}).get("model_fingerprint") != provenance.get(
                "model_fingerprint"
            ):
                reasons.append("region_model_mismatch")
            if not recheck.get("scores"):
                reasons.append("region_scores_missing")
            response = recheck.get("provenance", {})
            region_blocks = (bound_response(response) or {}).get("blocks", [])
            if response.get("input_sha256") != recheck.get("input_sha256") or not region_blocks:
                reasons.append("region_not_bound_to_raw_prediction")
            else:
                ordered = sorted(
                    region_blocks,
                    key=lambda item: (
                        (item["bbox"][1], item["bbox"][0]) if item.get("bbox") else (0, 0)
                    ),
                )
                if "\n".join(item["text"].strip() for item in ordered) != recheck.get("text") or [
                    item["score"] for item in region_blocks
                ] != recheck.get("scores"):
                    reasons.append("region_not_bound_to_raw_prediction")
            if response.get("pipeline_fingerprint") != provenance.get("pipeline_fingerprint"):
                reasons.append("region_pipeline_mismatch")
            scores.extend(valid_score(score) for score in recheck.get("scores", []))
    if not candidate.cells:
        reasons.append("cells_missing")
    if not scores or any(score is None or score < 0.98 for score in scores):
        reasons.append("critical_recognition_score_below_threshold")
    return dict(
        status="qualified" if not reasons else "rejected",
        reasons=sorted(set(reasons)),
        minimum_score=min((score for score in scores if score is not None), default=0),
        unit=units[0][1] if units and len({unit for block, unit in units}) == 1 else None,
        organization=organization,
        year=year,
        report_kind=report_kind,
        evidence_verification="unverified",
        protocol_version=PROTOCOL_VERSION,
    )
