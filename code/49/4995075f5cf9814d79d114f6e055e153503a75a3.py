"""已确认业务车道中的原生/视觉字段裁定；不覆盖整表，不按金额配对。"""

from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
import re

from src.engine.fiscal_lexeme import lex_numeric

ADJUDICATION_VERSION = "spatial-fields-v2-native-fragments"


def source_id(table, cell):
    row = cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx
    col = cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx
    return f"{cell.origin_fragment_uid or table.fragment_uid}:r{row}c{col}"


def same_region(a, b):
    if not a or not b:
        return False
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    areas = [(box[2] - box[0]) * (box[3] - box[1]) for box in (a, b)]
    return all(area > 0 and intersection / area >= 0.8 for area in areas)


def compact(text):
    return re.sub(r"\s+", "", str(text or ""))


def adjudicate_fields(table, mappings, artifact, *, organization, year, report_kind):
    from src.services.ocr_fact_promotion import _detect_table_identity
    from src.services.ocr_qualification import bound_table, qualification

    if table.extraction_method == "ocr":
        return table, []
    measure_cols = {m["source_col_idx"]: m for m in mappings}
    proposals, decisions = defaultdict(list), []
    for candidate in artifact.ocr_table_candidates:
        native = [c for c in table.cells if c.page_number == candidate.page_number and c.bbox]
        if (
            not native
            or not candidate.page_bbox
            or not any(
                min(c.bbox[2], candidate.page_bbox[2]) > max(c.bbox[0], candidate.page_bbox[0])
                and min(c.bbox[3], candidate.page_bbox[3]) > max(c.bbox[1], candidate.page_bbox[1])
                for c in native
            )
        ):
            continue
        bound, reasons = bound_table(candidate, artifact)
        if reasons or not bound:
            continue
        code, _, visual_mappings = _detect_table_identity(bound["rows"])
        if code != table.table_code:
            continue
        visual_by_col = {m["source_col_idx"]: m for m in visual_mappings}
        pairs = []
        for visual in bound["cells"]:
            matches = [c for c in native if same_region(c.bbox, visual["page_bbox"])]
            if len(matches) == 1:
                pairs.append((matches[0], visual))
        counts = defaultdict(int)
        for cell, _ in pairs:
            counts[(cell.row_idx, cell.col_idx)] += 1
        pairs = [(c, v) for c, v in pairs if counts[c.row_idx, c.col_idx] == 1]
        by_native = {(c.row_idx, c.col_idx): v for c, v in pairs}
        for cell, visual in pairs:
            mapping = measure_cols.get(cell.col_idx)
            vmapping = visual_by_col.get(visual["col_idx"])
            if (
                cell.is_header
                or not mapping
                or not vmapping
                or mapping["canonical_measure"] != vmapping["canonical_measure"]
            ):
                continue
            # 同额不参与对齐；相同页/格、指标以及各标签的空间对应必须全部成立。
            labels = [
                c
                for c in native
                if c.row_idx == cell.row_idx and c.col_idx in mapping.get("source_label_cols", [])
            ]
            label_conflict = any(
                (v := by_native.get((c.row_idx, c.col_idx))) is None
                or v["row_idx"] != visual["row_idx"]
                or compact(c.raw_text) != compact(v["raw_text"])
                for c in labels
            )
            decision = {
                "physical_source_id": source_id(table, cell),
                "page": cell.page_number,
                "row": cell.row_idx,
                "column": cell.col_idx,
                "bbox": list(cell.bbox),
                "candidate_uid": candidate.candidate_uid,
                "native_text": cell.raw_text,
                "visual_text": visual["raw_text"],
                "status": "visual_candidate_unqualified",
            }
            qualified = qualification(
                candidate,
                artifact,
                organization=organization,
                year=year,
                report_kind=report_kind,
                cell_position=(visual["row_idx"], visual["col_idx"]),
                native_field_alignment=True,
            )
            decision["qualification"] = qualified
            if label_conflict:
                decision["status"] = "row_identity_requires_regional_adjudication"
            elif qualified["status"] == "qualified":
                if compact(cell.raw_text) == compact(visual["raw_text"]):
                    decision["status"] = "spatially_agreed"
                elif (
                    lex_numeric(cell.raw_text).kind == "blank"
                    or re.search(r"\ufffd|\x00|\(cid:\d+\)", cell.raw_text)
                ) and lex_numeric(visual["raw_text"]).kind in {"number", "amount"}:
                    if cell.unit_hint and qualified["unit"] != cell.unit_hint:
                        decision["status"] = "unit_requires_regional_adjudication"
                    else:
                        decision["status"] = "visual_field_selected"
                        proposals[cell.row_idx, cell.col_idx].append((candidate, visual, qualified))
                else:
                    # 两个合法字面冲突需要原图补读；同模型重复一致不是独立投票。
                    decision["status"] = "requires_regional_adjudication"
            decisions.append(decision)
    updates = {}
    for position, options in proposals.items():
        current = [d for d in decisions if (d["row"], d["column"]) == position]
        conflicts = [d for d in current if "requires_regional_adjudication" in d["status"]]
        if conflicts or len({v["raw_text"] for _, v, _ in options}) != 1:
            for d in current:
                if d["status"] == "visual_field_selected":
                    d["status"] = "requires_regional_adjudication"
            continue
        cell = next(c for c in table.cells if (c.row_idx, c.col_idx) == position)
        candidate, visual, qualified = options[0]
        updates[position] = replace(
            cell,
            raw_text=visual["raw_text"],
            normalized_text=compact(visual["raw_text"]),
            numeric_value=None,
            normalized_value_wan=None,
            extraction_method="ocr",
            unit_hint=qualified["unit"],
            confidence=qualified["minimum_score"],
            grid_raw_text=cell.grid_raw_text if cell.grid_raw_text is not None else cell.raw_text,
            native_text_sources=[],
            native_blank_verified=False,
            ocr_provenance={
                "qualification": qualified,
                "source": deepcopy(candidate.provenance),
                "cell": deepcopy(visual),
                "adjudication_version": ADJUDICATION_VERSION,
                "native_candidate": {
                    "raw_text": cell.raw_text,
                    "physical_source_id": source_id(table, cell),
                    "native_text_sources": deepcopy(cell.native_text_sources),
                },
            },
        )
    rows = [list(row) for row in table.rows]
    for (row, col), cell in updates.items():
        rows[row][col] = cell.raw_text
    return replace(
        table, rows=rows, cells=[updates.get((c.row_idx, c.col_idx), c) for c in table.cells]
    ), decisions
