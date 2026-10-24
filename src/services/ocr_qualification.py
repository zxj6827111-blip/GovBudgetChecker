"""OCR operand qualification is separate from original-evidence certification."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from ocr_service.protocol import PROTOCOL_VERSION, valid_bbox, valid_score

OPERAND_BINDING_VERSION = "raw-cells-pdf-page-v2"


def bound_ocr_block(block,artifact):
    """身份文字也必须回到同一输入、原始预测和几何变换，不能只看高分。"""
    try:
        tid=block.block_uid.split(':block-',1)[0]
        transform=artifact.coordinate_transforms[tid];source=transform['ocr_response']
        from src.schemas.parse_artifact import OcrTableCandidate
        from src.services.input_geometry import Affine
        from src.services.ocr_grid_reconstruction import detector_input_reasons
        candidate=OcrTableCandidate('identity',block.page_number,[],coordinate_transform_id=tid,
            provenance={**source,'source_sha256':artifact.source_file_hash,'render_sha256':transform['render_sha256']})
        if (source.get('schema_version')!=PROTOCOL_VERSION or detector_input_reasons(source)
            or page_binding_reasons(candidate,artifact)or source.get('model_fingerprint')!=artifact.ocr_model_fingerprint):
            return False
        matrix=Affine(*transform['pixel_to_pdf_matrix'])
        return any(b['text']==block.text and b.get('score')==block.confidence and b.get('bbox')
                   and matrix.box(b['bbox'])==list(block.bbox or[])for b in (bound_response(source)or{}).get('blocks',[]))
    except (KeyError,TypeError,ValueError):return False


def page_binding_reasons(candidate: Any, artifact: Any) -> list[str]:
    """Bind a candidate to the independently saved physical page request.

    A candidate is a mutable view. Its response hashes alone cannot establish
    which page supplied the image; the renderer and request journal must agree.
    Older cached artifacts without this journal require a new parse.
    """
    from src.services.ocr_grid_reconstruction import GRID_PROTOCOL, bound_grid_table
    if candidate.provenance.get('schema_version') == GRID_PROTOCOL:
        return bound_grid_table(candidate, artifact)[1]
    reasons = []
    transform = artifact.coordinate_transforms.get(candidate.coordinate_transform_id, {})
    context = transform.get("tile_context") or {}
    if candidate.provenance.get("schema_version") == PROTOCOL_VERSION:
        from src.services.ocr_tiles import digest

        unsigned = {key: value for key, value in context.items() if key != "tile_id"}
        saved = transform.get("ocr_response") or {}
        if not context or digest(unsigned) != context.get("tile_id"):
            reasons.append("tile_identity_mismatch")
        if (
            context.get("physical_page") != candidate.page_number
            or context.get("source_sha256") != artifact.source_file_hash
        ):
            reasons.append("tile_original_source_mismatch")
        if (
            saved.get("tile_context") != context
            or candidate.provenance.get("tile_context") != context
            or context.get("input_sha256") != transform.get("render_sha256")
            or context.get("size_px") != transform.get("render_size_px")
        ):
            reasons.append("tile_response_context_mismatch")
        if transform.get("transform_version") == "pdf-tile-v3":
            if context.get("pixel_to_pdf") != transform.get("pixel_to_pdf_matrix") or context.get(
                "pdf_to_pixel"
            ) != transform.get("pdf_to_pixel_matrix"):
                reasons.append("tile_transform_mismatch")
    page = candidate.page_number
    expected_id = (
        "ocr_tile_" + str(context.get("tile_id"))
        if transform.get("transform_version") == "pdf-tile-v3"
        else f"ocr_render_page_{page}"
    )
    if (
        not isinstance(page, int)
        or isinstance(page, bool)
        or page < 1
        or (artifact.page_count and page > artifact.page_count)
        or candidate.coordinate_transform_id != expected_id
    ):
        reasons.append("candidate_page_not_bound_to_transform")
    transform = artifact.coordinate_transforms.get(candidate.coordinate_transform_id, {})
    if transform.get("physical_page") != page:
        reasons.append("candidate_page_not_bound_to_transform")
    if transform.get("source_sha256") != artifact.source_file_hash:
        reasons.append("transform_source_hash_mismatch")
    request = transform.get("page_request")
    saved = transform.get("ocr_response")
    if not isinstance(request, dict) or not isinstance(saved, dict):
        return reasons + ["page_request_identity_missing"]
    expected_request = {
        "physical_page": page,
        "source_sha256": artifact.source_file_hash,
        "render_sha256": transform.get("render_sha256"),
        "input_sha256": transform.get("render_sha256"),
        **{
            key: saved.get(key)
            for key in (
                "schema_version",
                "raw_prediction_sha256",
                "model_fingerprint",
                "pipeline_fingerprint",
            )
        },
    }
    if any(request.get(key) != value or value is None for key, value in expected_request.items()):
        reasons.append("page_request_not_bound_to_response")
    if (
        saved.get("input_sha256") != transform.get("render_sha256")
        or bound_response(saved) is None
        or any(
            candidate.provenance.get(key) != saved.get(key)
            for key in (
                "schema_version",
                "input_sha256",
                "raw_prediction_sha256",
                "model_fingerprint",
                "pipeline_fingerprint",
                "source_size_px",
                "prediction_coordinate_scale",
            )
        )
    ):
        reasons.append("candidate_response_not_bound_to_page_request")
    return reasons


def bound_table(candidate: Any, artifact: Any) -> tuple[dict | None, list[str]]:
    """Bind the row view and PDF coordinates to the reconstructed raw cells."""
    from src.services.ocr_grid_reconstruction import GRID_PROTOCOL, bound_grid_table
    if candidate.provenance.get('schema_version') == GRID_PROTOCOL:
        return bound_grid_table(candidate, artifact)
    from src.services.ocr_pipeline import _pdf_bbox_from_render_bbox

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

    transform = artifact.coordinate_transforms.get(candidate.coordinate_transform_id, {})
    # Prefer the saved response even for rejected candidates: diagnostics retain
    # the actual source amount, rather than a modified candidate response.
    response = bound_response(transform.get("ocr_response", candidate.provenance))
    matches = [
        table
        for table in (response or {}).get("tables", [])
        if table.get("mapping_status") == "mapped"
        and signature(table.get("cells", [])) == signature(candidate.cells)
    ]
    if len(matches) != 1:
        return None, ["cells_not_bound_to_raw_prediction"]
    table = deepcopy(matches[0])
    reasons = page_binding_reasons(candidate, artifact)
    if candidate.rows != table["rows"]:
        reasons.append("rows_not_bound_to_raw_prediction")
    table["physical_page"] = transform.get("physical_page")
    original = {(cell.get("row_idx"), cell.get("col_idx")): cell for cell in candidate.cells}
    for cell in table["cells"]:
        saved = original[(cell["row_idx"], cell["col_idx"])]
        expected_box = _pdf_bbox_from_render_bbox(cell["render_bbox"], transform)
        if expected_box is None or valid_bbox(saved.get("page_bbox")) != expected_box:
            reasons.append("cell_page_bbox_not_bound_to_render")
        cell["page_bbox"] = expected_box
        cell["region_recheck"] = deepcopy(saved.get("region_recheck", {}))
    return table, reasons


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
        if len(scale) != 2 or not all(math.isfinite(value) and value == 1 for value in scale):
            return None
        return _render_prediction(raw, scale)
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def qualified_ocr_cover_owner(artifact: Any) -> str | None:
    """Read a split unit name immediately after a high-score cover heading."""
    cover = [
        block
        for block in artifact.text_blocks
        if block.extraction_method == "ocr"
        and block.page_number == 1
        and block.confidence is not None
        and block.confidence >= 0.98
    ]
    for index, block in enumerate(cover):
        if not re.search(r"20\d{2}年.*单位(?:预算|决算)", "".join(block.text.split())):
            continue
        pieces = []
        for following in cover[index + 1 : index + 4]:
            text = "".join(following.text.split())
            if not re.fullmatch(r"[\u4e00-\u9fff]{2,80}", text):
                break
            pieces.append(text)
            owner = "".join(pieces)
            if re.search(r"(?:中心|医院|学校|管理所|办事处)$", owner):
                return owner
    return None


def complete_first_pass_cell(cell, context):
    """v3 可直接使用完整的首轮格；同模型重复读不是独立投票要求。"""
    if not context or not cell.get("fragments"):
        return False
    width, height = context["size_px"]
    left, top, right, bottom = context["crop_box_px"]
    parent_width, parent_height = context.get("parent_size_px", context["size_px"])
    margins = [
        8 if left > 0 else 2,
        8 if top > 0 else 2,
        8 if right < parent_width else 2,
        8 if bottom < parent_height else 2,
    ]
    for fragment in cell["fragments"]:
        box = valid_bbox(fragment.get("bbox") or fragment.get("render_bbox"))
        if box is None or (
            box[0] < margins[0]
            or box[1] < margins[1]
            or box[2] > width - margins[2]
            or box[3] > height - margins[3]
        ):
            return False
    return True


def qualification(
    candidate: Any,
    artifact: Any,
    *,
    organization: str | None,
    year: int | None,
    report_kind: str | None,
    cell_position: tuple[int, int] | None = None,
    native_field_alignment: bool = False,
) -> dict:
    if native_field_alignment and cell_position is None:
        raise ValueError("native field alignment requires an explicit cell position")
    from src.services.ocr_grid_reconstruction import GRID_PROTOCOL, qualify_grid
    if candidate.provenance.get('schema_version') == GRID_PROTOCOL:
        return qualify_grid(candidate, artifact, organization=organization, year=year,
            report_kind=report_kind, cell_position=cell_position, native_field_alignment=native_field_alignment)
    reasons = page_binding_reasons(candidate, artifact)
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
    size = provenance.get('source_size_px', [])
    padding = [0,0,(-size[0])%32,(-size[1])%32] if len(size)==2 else []
    expected_input = [size[0]+padding[2],size[1]+padding[3]] if padding else []
    if (provenance.get('detector_input_verified') is not True
            or provenance.get('model_input_size_px') != expected_input
            or provenance.get('inference_padding_px') != padding
            or provenance.get('input_resize_applied') is not False
            or not provenance.get('detector_tensor_sizes_px')
            or provenance['detector_tensor_sizes_px'][0] != expected_input):
        reasons.append('actual_detector_input_unverified')
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
    bound = bound_response(provenance)
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
        ] != provenance.get("model_input_size_px"):
            reasons.append("prediction_scale_mismatch")
    _, binding_reasons = bound_table(candidate, artifact)
    reasons.extend(binding_reasons)
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
    if cell_position is not None:
        # A split cover name remains bound to each original high-score block.
        if organization:
            compact_owner = "".join(organization.split())
            witnesses = [[block] for block in identity_blocks]
            for start, block in enumerate(blocks):
                group = []
                for end in range(start, min(start + 3, len(blocks))):
                    if blocks[end].page_number != block.page_number:
                        break
                    group.append(blocks[end])
                    if compact_owner in "".join("".join(item.text.split()) for item in group):
                        witnesses.append(list(group))
                        break
            if witnesses:
                identity_blocks = max(
                    witnesses, key=lambda group: min(block.confidence or 0 for block in group)
                )
        # Select the strongest direct identity witness, rather than every
        # incidental use of a year or the word budget in the whole document.
        if year_blocks:
            year_blocks = [max(year_blocks, key=lambda block: block.confidence or 0)]
        if stage_blocks:
            stage_blocks = [max(stage_blocks, key=lambda block: block.confidence or 0)]
        if identity_blocks:
            reasons = [reason for reason in reasons if reason != "original_subject_missing"]
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
    from src.services.table_reading_assessment import native_region_conflicts

    if not native_field_alignment and native_region_conflicts(artifact, candidate):
        reasons.append("native_OCR_conflict_requires_review")
    critical = identity_blocks + year_blocks + stage_blocks + [block for block, unit in units]
    for block in critical:
        block_transform_id = (
            block.block_uid.split(":block-", 1)[0]
            if block.block_uid.startswith("ocr_tile_")
            else f"ocr_render_page_{block.page_number}"
        )
        page_transform = artifact.coordinate_transforms.get(block_transform_id, {})
        saved_response = page_transform.get("ocr_response", {})
        source_blocks = (bound_response(saved_response) or {}).get("blocks", [])
        if (
            page_transform.get("physical_page") != block.page_number
            or page_transform.get("source_sha256") != artifact.source_file_hash
            or page_transform.get("page_request", {}).get("physical_page") != block.page_number
            or page_transform.get("page_request", {}).get("raw_prediction_sha256")
            != saved_response.get("raw_prediction_sha256")
            or saved_response.get("model_fingerprint") != provenance.get("model_fingerprint")
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
    cells_to_check = candidate.cells
    if cell_position is not None:
        from src.services.fiscal_row_roles import is_index_header

        row_idx, col_idx = cell_position
        first_data = next(
            (
                index
                for index, row in enumerate(candidate.rows)
                if not is_index_header(row)
                and any(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", str(value).strip()) for value in row)
            ),
            1,
        )
        cells_to_check = [
            cell
            for cell in candidate.cells
            if (cell.get("row_idx"), cell.get("col_idx")) == cell_position
            or (
                cell.get("row_idx", 0) < first_data
                and not is_index_header(candidate.rows[cell["row_idx"]])
            )
            or (
                cell.get("row_idx") == row_idx
                and not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", str(cell.get("raw_text") or "").strip())
            )
        ]
        # 编码列必须由表头确认；不能把前方恰为三位数的金额当作编码。
        code_columns = {
            cell.get("col_idx")
            for cell in candidate.cells
            if cell.get("row_idx", 0) < first_data
            and (
                "编码" in str(cell.get("raw_text") or "")
                or str(cell.get("raw_text") or "").strip() in {"类", "款", "项"}
            )
        }
        cells_to_check += [
            cell
            for cell in candidate.cells
            if cell.get("row_idx") == row_idx
            and cell.get("col_idx", 0) < col_idx
            and cell.get("col_idx") in code_columns
            and re.fullmatch(r"\d{3,7}", str(cell.get("raw_text") or ""))
            and cell not in cells_to_check
        ]
    for cell in cells_to_check:
        box = valid_bbox(cell.get("page_bbox"))
        if box and any(
            min(box[2], region[2]) > max(box[0], region[0])
            and min(box[3], region[3]) > max(box[1], region[1])
            for region in transform.get("page_recovery", {}).get("unresolved_pdf_regions", [])
        ):
            reasons.append("cell_region_recovery_incomplete")
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
            if provenance.get("schema_version") == PROTOCOL_VERSION and not recheck:
                if not complete_first_pass_cell(cell, provenance.get("tile_context")):
                    reasons.append("cell_boundary_requires_regional_recovery")
                continue
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
            request_matches = [
                request
                for request in transform.get("region_requests", [])
                if all(
                    request.get(key) == cell.get(key)
                    for key in ("row_idx", "col_idx", "row_span", "col_span", "render_bbox")
                )
                and request.get("input_sha256") == recheck.get("input_sha256")
                and request.get("raw_prediction_sha256") == response.get("raw_prediction_sha256")
            ]
            if len(request_matches) != 1:
                reasons.append("region_request_not_bound_to_cell")
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
        operand_binding_version=OPERAND_BINDING_VERSION,
        physical_page=transform.get("physical_page"),
        native_overlap_policy="field_alignment" if native_field_alignment else "table_isolation",
        coordinate_transform_id=candidate.coordinate_transform_id,
    )
