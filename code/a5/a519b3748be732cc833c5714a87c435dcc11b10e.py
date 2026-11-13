"""原生行列已确认时，用完整 OCR 片段补读单格，无需裁片重复整张表头。"""

from collections import defaultdict
from dataclasses import replace
from types import SimpleNamespace
import re

from ocr_service.protocol import PROTOCOL_VERSION
from src.engine.fiscal_lexeme import lex_numeric, resolve_unit
from src.services.fiscal_business_rows import compact, literal
from src.services.fiscal_field_adjudication import source_id, same_region
from src.services.input_geometry import Affine


def supported_literal(cell, pages):
    indexed = {s["char_index"]: s for s in pages.get(cell.page_number, [])}
    text = ""
    for proof in cell.native_text_sources:
        if proof.get("physical_page") != cell.page_number:
            return False
        indexes = range(proof.get("start_char_index", 0), proof.get("end_char_index", 0))
        selected = [indexed[i] for i in indexes if i in indexed]
        value = "".join(s["text"] for s in selected)
        if not selected or value != proof.get("text"):
            return False
        text += value
    return bool(text and compact(text) == compact(cell.raw_text))


def adjudicate_fragments(table, mappings, artifact, pages, *, organization, year, report_kind):
    from src.services.ocr_qualification import (
        bound_response,
        page_binding_reasons,
        complete_first_pass_cell,
    )

    if (
        table.extraction_method == "ocr"
        or not artifact.ocr_model_fingerprint
        or not any(
            t.get("ocr_response", {}).get("schema_version") == PROTOCOL_VERSION
            for t in artifact.coordinate_transforms.values()
        )
        or not organization
        or not year
        or report_kind not in {"budget", "final"}
    ):
        return table, []
    # 画像要能回到同一原件的真实文字，不能凭文件名替视觉字段提供业务身份。
    page_literals = [compact(literal(samples)) for samples in pages.values()]
    if not any(compact(organization) in text for text in page_literals) or not any(
        f"{year}年" in text for text in page_literals
    ):
        return table, []
    if not any(("决算" if report_kind == "final" else "预算") in text for text in page_literals):
        return table, []
    headers = [c for c in table.cells if c.is_header and c.raw_text.strip()]
    if not headers or not all(supported_literal(c, pages) for c in headers):
        return table, []
    by_col = {m["source_col_idx"]: m for m in mappings}
    fields = [
        c
        for c in table.cells
        if not c.is_header and c.col_idx in by_col and c.extraction_method != "ocr" and c.bbox
    ]
    labels = {(c.row_idx, c.col_idx): c for c in table.cells}
    fragments = defaultdict(list)
    for transform_id, transform in artifact.coordinate_transforms.items():
        source = transform.get("ocr_response", {})
        page = transform.get("physical_page")
        context = transform.get("tile_context", {})
        if (
            source.get("schema_version") != PROTOCOL_VERSION
            or source.get("model_fingerprint") != artifact.ocr_model_fingerprint
        ):
            continue
        stub = SimpleNamespace(
            page_number=page, coordinate_transform_id=transform_id, provenance=source
        )
        if page_binding_reasons(stub, artifact):
            continue
        response = bound_response(source)
        if not response:
            continue
        matrix = Affine(*context["pixel_to_pdf"])
        for index, block in enumerate(response.get("blocks", [])):
            bbox = block.get("bbox")
            if (
                not bbox
                or block.get("score", 0) < 0.98
                or lex_numeric(block.get("text")).kind not in {"number", "amount"}
            ):
                continue
            fragment = {"text": block["text"], "score": block["score"], "render_bbox": bbox}
            if not complete_first_pass_cell({"fragments": [fragment]}, context):
                continue
            box = matrix.box(bbox)
            matches = [
                c
                for c in fields
                if c.page_number == page
                and c.bbox[0] <= box[0]
                and c.bbox[1] <= box[1]
                and c.bbox[2] >= box[2]
                and c.bbox[3] >= box[3]
            ]
            if len(matches) != 1:
                continue
            cell = matches[0]
            if any(
                min(box[2], r[2]) > max(box[0], r[0]) and min(box[3], r[3]) > max(box[1], r[1])
                for r in transform.get("page_recovery", {}).get("unresolved_pdf_regions", [])
            ):
                continue
            label_cells = []
            for col in by_col[cell.col_idx].get("source_label_cols", []):
                label = labels.get((cell.row_idx, col))
                if label is None:
                    # 续页把三段编码合为一格时，缺少的两个位置是明确合并引用，
                    # 不能把金额车道平移，也不能要求原件生成两个空编码字符。
                    spanning = [
                        c
                        for c in table.cells
                        if c.row_idx == cell.row_idx and c.col_idx <= col < c.col_idx + c.col_span
                    ]
                    label = spanning[0] if len(spanning) == 1 else None
                if (
                    label is None
                    and col < len(table.rows[cell.row_idx])
                    and table.rows[cell.row_idx][col] is None
                ):
                    # 旧逻辑视图保留了 None 合并占位，却尚未登记 col_span。
                    # 以原表头的实际横向区间核对来源格覆盖关系，不能按列序号补位。
                    bands = [
                        c.bbox
                        for c in headers
                        if c.bbox and c.col_idx <= col < c.col_idx + c.col_span
                    ]
                    if bands:
                        band = min(bands, key=lambda b: b[2] - b[0])
                        spanning = [
                            c
                            for c in table.cells
                            if c.row_idx == cell.row_idx
                            and c.bbox
                            and c.col_idx < col
                            and c.bbox[0] <= band[0] + 0.5
                            and c.bbox[2] >= band[2] - 0.5
                        ]
                        label = spanning[0] if len(spanning) == 1 else None
                if label not in label_cells:
                    label_cells.append(label)
            if not label_cells or any(
                c is None or c.raw_text.strip() and not supported_literal(c, pages)
                for c in label_cells
            ):
                continue
            unit = resolve_unit(
                cell=lex_numeric(block["text"]).unit, column=cell.unit_hint, table=table.unit_hint
            )
            if not unit.unit or unit.conflicts:
                continue
            fragments[cell.row_idx, cell.col_idx].append(
                {
                    "text": block["text"],
                    "bbox": box,
                    "fragment": fragment,
                    "transform_id": transform_id,
                    "source": source,
                    "block_index": index,
                    "unit": unit.unit,
                    "score": block["score"],
                }
            )
    updates, decisions = {}, []
    for position, options in fragments.items():
        cell = labels[position]
        decision = {
            "physical_source_id": source_id(table, cell),
            "page": cell.page_number,
            "row": cell.row_idx,
            "column": cell.col_idx,
            "bbox": list(cell.bbox),
            "native_text": cell.raw_text,
            "visual_texts": [o["text"] for o in options],
            "candidate_uid": None,
            "status": "spatially_agreed",
            "alignment": "native_lane_and_source_bound_fragment",
        }
        # 只合并同一位置的重复投影；不同位置的同额片段不能拼成一个金额。
        coherent = all(same_region(options[0]["bbox"], o["bbox"]) for o in options)
        if not coherent or len({o["text"] for o in options}) != 1:
            decision["status"] = "requires_regional_adjudication"
        elif compact(cell.raw_text) != compact(options[0]["text"]):
            option = options[0]
            if lex_numeric(cell.raw_text).kind != "blank" and not re.search(
                r"\ufffd|\x00|\(cid:\d+\)", cell.raw_text
            ):
                decision["status"] = "requires_regional_adjudication"
            else:
                qualification = {
                    "status": "qualified",
                    "minimum_score": option["score"],
                    "unit": option["unit"],
                    "organization": organization,
                    "year": year,
                    "report_kind": report_kind,
                    "protocol_version": PROTOCOL_VERSION,
                    "physical_page": cell.page_number,
                    "coordinate_transform_id": option["transform_id"],
                    "evidence_verification": "unverified",
                    "native_overlap_policy": "native_lane_fragment_alignment",
                }
                updates[position] = replace(
                    cell,
                    raw_text=option["text"],
                    normalized_text=option["text"],
                    numeric_value=None,
                    normalized_value_wan=None,
                    extraction_method="ocr",
                    confidence=option["score"],
                    unit_hint=option["unit"],
                    native_blank_verified=False,
                    native_text_sources=[],
                    grid_raw_text=cell.grid_raw_text
                    if cell.grid_raw_text is not None
                    else cell.raw_text,
                    ocr_provenance={
                        "qualification": qualification,
                        "source": option["source"],
                        "cell": {
                            "fragments": [option["fragment"]],
                            "page_bbox": option["bbox"],
                            "raw_text": option["text"],
                        },
                        "source_block_index": option["block_index"],
                        "native_candidate": {
                            "raw_text": cell.raw_text,
                            "physical_source_id": source_id(table, cell),
                            "native_text_sources": cell.native_text_sources,
                        },
                    },
                )
                decision["status"] = "visual_field_selected"
                decision["qualification"] = qualification
        decisions.append(decision)
    rows = [list(row) for row in table.rows]
    for (row, col), cell in updates.items():
        rows[row][col] = cell.raw_text
    return replace(
        table, rows=rows, cells=[updates.get((c.row_idx, c.col_idx), c) for c in table.cells]
    ), decisions
