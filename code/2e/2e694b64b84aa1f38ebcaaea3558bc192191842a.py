"""统一逻辑格所有权与原生/视觉候选对齐；物理网格始终只读。"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from statistics import median
from src.engine.fiscal_lexeme import lex_numeric
from src.services.fiscal_business_rows import rebuild_business_rows, valid_samples

RECONSTRUCTION_VERSION = "fiscal-reconstruction-v5-source-image-grid"
TEMPLATE_VERSION = "fiscal-lanes-v2-paired-subjects"


def overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


class UnifiedFiscalReconstructor:
    def rebuild(self, artifact, **profile):
        from src.engine.fact_context import _column_mappings_for_instance, _fragment_for_instance
        from src.services.fiscal_field_adjudication import adjudicate_fields
        from src.services.document_profile_resolver import _kind_from_doc_type
        from src.services.fiscal_fragment_adjudication import adjudicate_fragments
        from src.services.ocr_grid_reconstruction import rebuild_image_tables, PREFIX

        image_candidates, image_diagnostics = rebuild_image_tables(artifact, **profile)
        image_by_source = {c["physical_source_id"]: c for c in image_candidates}
        image_instances = {
            i.instance_uid for i in artifact.logical_tables if i.instance_uid.startswith(PREFIX)
        }
        candidates = [c for c in image_candidates if c["instance_uid"] not in image_instances]
        resolved = []
        ownership = {}
        conflicts = []
        tables = {}
        diagnostics = image_diagnostics
        pages = valid_samples(artifact)
        for instance in artifact.logical_tables:
            table = _fragment_for_instance(artifact, instance)
            if table is None:
                continue
            mappings = _column_mappings_for_instance(instance, table)
            table, row_diagnostics = rebuild_business_rows(deepcopy(table), mappings, pages)
            field_decisions, fragment_decisions = [], []
            if not instance.identity_conflict and not instance.instance_uid.startswith(PREFIX):
                table, field_decisions = adjudicate_fields(
                    table,
                    mappings,
                    artifact,
                    organization=instance.org_name or profile.get("org_name"),
                    year=instance.fiscal_year or profile.get("fiscal_year"),
                    report_kind=_kind_from_doc_type(
                        profile.get("report_kind") or profile.get("doc_type")
                    ),
                )
                table, fragment_decisions = adjudicate_fragments(
                    table,
                    mappings,
                    artifact,
                    pages,
                    organization=instance.org_name or profile.get("org_name"),
                    year=instance.fiscal_year or profile.get("fiscal_year"),
                    report_kind=_kind_from_doc_type(
                        profile.get("report_kind") or profile.get("doc_type")
                    ),
                )
            field_decisions.extend(fragment_decisions)
            diagnostics.extend(
                {"instance_uid": instance.instance_uid, **d} for d in row_diagnostics
            )
            diagnostics.extend(
                {"instance_uid": instance.instance_uid, **d} for d in field_decisions
            )
            tables[instance.instance_uid] = table
            lanes = []
            for mapping in mappings:
                col = int(mapping["source_col_idx"])
                boxes = [c.bbox for c in table.cells if c.col_idx == col and c.bbox]
                if boxes:
                    lanes.append(
                        {
                            "indicator": mapping["canonical_measure"],
                            "source_col_idx": col,
                            "x0": median(b[0] for b in boxes),
                            "x1": median(b[2] for b in boxes),
                            "header": mapping.get("source_col_name"),
                            "label_columns": mapping.get("source_label_cols", []),
                        }
                    )
            # 金额车道固定在原页横向区间，编码列合并不会平移金额列。
            by_row = defaultdict(list)
            for cell in table.cells:
                by_row[(cell.page_number, cell.row_idx)].append(cell)
            for (page, row), cells in by_row.items():
                money = [
                    c
                    for c in cells
                    if not c.is_header
                    and c.bbox
                    and any(lane["source_col_idx"] == c.col_idx for lane in lanes)
                ]
                baseline = median(c.bbox[3] for c in money) if money else None
                for cell in cells:
                    source_id = f"{cell.origin_fragment_uid or table.fragment_uid}:r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}"
                    lane = next(
                        (lane for lane in lanes if cell.col_idx == lane["source_col_idx"]), None
                    )
                    record = {
                        "logical_cell_id": f"{instance.instance_uid}:p{page}:r{row}:c{cell.col_idx}",
                        "instance_uid": instance.instance_uid,
                        "page": page,
                        "row": row,
                        "row_bottom_anchor": baseline,
                        "anchor_kind": "amount_cell_bottom_median",
                        "column": deepcopy(lane),
                        "raw_text": cell.raw_text,
                        "value_kind": lex_numeric(cell.raw_text).kind,
                        "physical_source_id": source_id,
                        "bbox": list(cell.bbox) if cell.bbox else None,
                        "native_sources": deepcopy(cell.native_text_sources),
                        "role": "header" if cell.is_header else "body",
                        "status": "resolved",
                        "visual_candidates": [],
                    }
                    if source_id in image_by_source:
                        record.update(deepcopy(image_by_source[source_id]))
                    # OCR 是真实片段粒度；不将一个词框拆成虚构逐字框。
                    if cell.extraction_method == "ocr":
                        record["ocr_provenance"] = deepcopy(cell.ocr_provenance)
                        provenance = cell.ocr_provenance
                        for fragment in provenance.get("cell", {}).get("fragments", []):
                            # OCR 词框作为整体拥有，绝不把片段框均分成虚构字符框。
                            source = provenance.get("source", {})
                            key = (
                                "ocr_fragment",
                                page,
                                source.get("input_sha256"),
                                source.get("raw_prediction_sha256"),
                                tuple(fragment.get("render_bbox") or ()),
                                fragment.get("text"),
                            )
                            owner = ownership.setdefault(key, record["logical_cell_id"])
                            if owner != record["logical_cell_id"]:
                                record["status"] = "ownership_conflict"
                                conflicts.append(
                                    {
                                        "fragment": list(key),
                                        "owners": [owner, record["logical_cell_id"]],
                                    }
                                )
                        for group in provenance.get("fragment_groups", []):
                            fragment = group["selected"]
                            source = fragment["source"]
                            key = (
                                "ocr_fragment",
                                page,
                                source["input_sha256"],
                                source["raw_prediction_sha256"],
                                tuple(fragment["render_bbox"]),
                                fragment["text"],
                            )
                            if cell.is_header:
                                continue
                            owner = ownership.setdefault(key, record["logical_cell_id"])
                            if owner != record["logical_cell_id"]:
                                record["status"] = "ownership_conflict"
                                conflicts.append(
                                    {
                                        "fragment": list(key),
                                        "owners": [owner, record["logical_cell_id"]],
                                    }
                                )
                    # 清空的尾数格保存的是“已归属另一格”的证明，不是第二个正文所有者。
                    # 合并表头同样通过引用共享；只有非空正文才拥有字符。
                    record["source_relation"] = (
                        "shared_header_reference"
                        if cell.is_header
                        else "released_source_reference"
                        if not cell.raw_text.strip()
                        else "owned_body"
                    )
                    for source in cell.native_text_sources:
                        if record["source_relation"] != "owned_body":
                            continue
                        indexes = (
                            range(source["start_char_index"], source["end_char_index"])
                            if "start_char_index" in source and "end_char_index" in source
                            else []
                        )
                        for index in indexes:
                            key = (source.get("physical_page", page), index)
                            owner = ownership.setdefault(key, record["logical_cell_id"])
                            if owner != record["logical_cell_id"]:
                                record["status"] = "ownership_conflict"
                                conflicts.append(
                                    {
                                        "character": list(key),
                                        "owners": [owner, record["logical_cell_id"]],
                                    }
                                )
                    decisions = [d for d in field_decisions if d["physical_source_id"] == source_id]
                    record["visual_candidates"] = deepcopy(decisions)
                    disagreements = [
                        d for d in decisions if "requires_regional_adjudication" in d["status"]
                    ]
                    if disagreements:
                        record["status"] = disagreements[0]["status"]
                    candidates.append(record)
                    if record["status"] == "resolved":
                        resolved.append(deepcopy(record))
        conflicted_owners = {owner for conflict in conflicts for owner in conflict["owners"]}
        for record in candidates:
            if record["logical_cell_id"] in conflicted_owners:
                record["status"] = "ownership_conflict"
        resolved = [deepcopy(record) for record in candidates if record["status"] == "resolved"]
        return {
            "candidates": candidates,
            "resolved": resolved,
            "conflicts": conflicts,
            "version": RECONSTRUCTION_VERSION,
            "templates": TEMPLATE_VERSION,
            "tables": tables,
            "diagnostics": diagnostics,
        }
