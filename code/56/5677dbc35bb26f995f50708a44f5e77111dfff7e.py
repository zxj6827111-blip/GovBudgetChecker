"""金额读取、列身份、事实覆盖和规则准入分别记账，未知项不能从分母消失。"""
from __future__ import annotations

import re

from src.services.fiscal_column_identity import canonical_column_mappings, code_dimension_columns, header_paths
from src.services.fiscal_row_roles import is_index_header, native_bilateral_padding_positions
from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text


def source_cell_id(table, cell):
    return (f"{cell.origin_fragment_uid or table.fragment_uid}:"
            f"r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}"
            f"c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}")


def _serial_lane_proofs(artifact):
    """序号续页须继承原页字面表头、相同列边界和连续编号，排除依据留存。"""
    proofs = {}
    previous = None
    for table in sorted(artifact.physical_fragments, key=lambda t: (t.page_number, t.table_index)):
        if table.extraction_method not in {"lines", "lines_strict"}:
            continue
        header = next((c for c in table.cells if normalize_text(c.raw_text) in {"序号", "序"}), None)
        if header and any(normalize_text(c.raw_text) == "单位名称" for c in table.cells if c.row_idx == header.row_idx):
            lane = header.col_idx
            basis = f"literal_serial_header:{table.fragment_uid}:r{header.row_idx}c{lane}"
        elif previous and table.page_number == previous[0].page_number + 1 and all(
                len(row) == len(previous[0].rows[0]) for row in table.rows):
            lane, basis = previous[1], previous[2]
        else:
            previous = None
            continue
        numeric = [c for c in table.cells if c.col_idx == lane and c.numeric_value is not None]
        if not numeric or any(not re.fullmatch(r"\d+", c.raw_text.strip()) or not c.bbox for c in numeric):
            previous = None
            continue
        values = [int(c.raw_text.strip()) for c in numeric]
        if values != list(range(values[0], values[0] + len(values))):
            previous = None
            continue
        if not header:
            last = previous[3]
            old_boxes = [c.bbox for c in previous[0].cells if c.col_idx == lane and c.bbox]
            if values[0] != last + 1 or not old_boxes or any(
                    abs(c.bbox[i] - old_boxes[-1][i]) > .5 for c in numeric for i in (0, 2)):
                previous = None
                continue
        for cell in numeric:
            proofs[source_cell_id(table, cell)] = basis
        previous = (table, lane, basis, values[-1])
    return proofs


def _numeric_exclusion(table, cell, mappings, serial_proofs=None):
    """只排除能指明原列/原行依据的数字；小数金额不能因落入标签列被吞掉。"""
    row = table.rows[cell.row_idx]
    if is_index_header(row):
        return "literal_index_header"
    if serial_proofs and source_cell_id(table, cell) in serial_proofs:
        return serial_proofs[source_cell_id(table, cell)]
    literal = cell.raw_text.strip()
    if not re.fullmatch(r"\d+", literal):
        return None
    paths = header_paths(table)
    path = paths[cell.col_idx] if cell.col_idx < len(paths) else []
    if (cell.col_idx in code_dimension_columns(table, paths)
            or any(part in {"科目编码", "功能分类科目编码", "经济分类科目编码", "类", "款", "项", "序号", "行次", "栏次", "年度", "年份"} for part in path)):
        return "literal_dimension_header:" + "/".join(path)
    label_cols = {col for mapping in mappings for col in mapping.get("source_label_cols", [])}
    if cell.col_idx in label_cols:
        return "mapped_subject_dimension"
    # 无表头续页的完整编码须有紧邻名称和右侧小数金额车道，不能仅按数值长度过滤。
    if (table.table_code.startswith("UNKNOWN_") and cell.col_idx == 0
            and re.fullmatch(r"\d{3}(?:\d{2}){0,2}", literal) and len(row) >= 3
            and re.search(r"[\u4e00-\u9fff]", str(row[1] or ""))
            and any(re.fullmatch(r"-?[\d,]+\.\d+", str(value or "").strip()) for value in row[2:])):
        return "native_code_name_amount_lanes"
    return None


def _inherited_dimension_basis(table, cell, mappings, width_safe):
    """列宽已安全对齐的续页可继承字面编码表头，不能用漂移视图吞掉整数金额。"""
    if not width_safe or cell.origin_col_idx not in {None, cell.col_idx}:
        return None
    if not re.fullmatch(r"\d+", cell.raw_text.strip()):
        return None
    basis = _numeric_exclusion(table, cell, mappings)
    if not basis or not basis.startswith("literal_dimension_header:") or not cell.bbox:
        return None
    headers = [c for c in table.cells if c.row_idx < table.header_row_count
               and c.col_idx == cell.col_idx and c.bbox and c.raw_text.strip()]
    if any(h.bbox[0] - .5 <= cell.bbox[0] and cell.bbox[2] <= h.bbox[2] + .5 for h in headers):
        return f"inherited_physical_dimension:{table.fragment_uid}:c{cell.col_idx}:{basis}"
    return None


def build_table_import_receipt(artifact, facts, *, org_name=None):
    from src.engine.fact_context import _column_mappings_for_instance, operand_rejection_reason
    from src.services.table_reading_assessment import assess_table_reading

    by_source = {}
    for fact in facts:
        for ref in fact.extra_dims.get("evidence_refs", []):
            if ref.get("source_cell_id"):
                by_source.setdefault((fact.instance_uid, ref["source_cell_id"]), []).append(fact)
    tables = []
    observed_ids = set()
    serial_proofs = _serial_lane_proofs(artifact)
    physical = {}
    for fragment in artifact.physical_fragments:
        rule = NINE_TABLE_RULES.get(fragment.table_code)
        original_mappings = canonical_column_mappings(fragment, rule) if rule else []
        for cell in fragment.cells:
            physical[source_cell_id(fragment, cell)] = (fragment, cell, original_mappings)
    for table in artifact.table_fragments:
        if table.extraction_method == "page_text_fallback":
            continue  # 正文提示没有物理金额格，不能重复计入金额分母。
        instance = next((i for i in artifact.logical_tables
                         if table.fragment_uid in i.fragment_uids or table.fragment_uid == i.instance_uid), None)
        rule = NINE_TABLE_RULES.get(table.table_code)
        mappings = (_column_mappings_for_instance(instance, table) if instance and rule
                    else canonical_column_mappings(table, rule) if rule else [])
        mapping_by_col = {m["source_col_idx"]: m for m in mappings}
        width_identity_safe = len({len(row) for row in table.rows if any(str(v or "").strip() for v in row)}) <= 1
        padding = native_bilateral_padding_positions(table)
        bound, unresolved, padding_cells, excluded, observations = [], [], [], [], []
        present = columns_bound = admitted = conflicts = blocked = 0
        for cell in table.cells:
            if cell.numeric_value is None:
                continue
            cid = source_cell_id(table, cell)
            observed_ids.add(cid)
            observation = {"source_cell_id": cid, "raw_text": cell.raw_text,
                           "page": cell.page_number, "bbox": list(cell.bbox) if cell.bbox else None,
                           "row": cell.row_idx, "column": cell.col_idx,
                           "physical_row": cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx,
                           "physical_column": cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}
            # 排除判据必须回到物理列；不安全合并视图可能已把整数金额挪进“款/项”。
            source_table, source_cell, source_mappings = physical.get(cid, (table, cell, mappings))
            basis = _numeric_exclusion(source_table, source_cell, source_mappings, serial_proofs)
            if not basis and source_table.fragment_uid != table.fragment_uid:
                basis = _inherited_dimension_basis(table, cell, mappings, width_identity_safe)
            if not basis and table.extraction_method == "text" and cell.bbox:
                # 文字策略可把闭合网格连同附近正文再次投影；仅豁免同页同位置的唯一原格。
                duplicates = [(uid, original) for uid, (fragment, original, _) in physical.items()
                              if fragment.extraction_method in {"lines", "lines_strict"}
                              and original.page_number == cell.page_number and original.bbox
                              and normalize_text(original.raw_text) == normalize_text(cell.raw_text)
                              and original.bbox[0] <= (cell.bbox[0] + cell.bbox[2]) / 2 <= original.bbox[2]
                              and original.bbox[1] <= (cell.bbox[1] + cell.bbox[3]) / 2 <= original.bbox[3]]
                if len(duplicates) == 1:
                    basis = f"duplicate_text_projection_of_physical_cell:{duplicates[0][0]}"
            if basis:
                excluded.append({**observation, "basis": basis})
                continue
            if (cell.row_idx, cell.col_idx) in padding:
                padding_cells.append({**observation, "role": "source_padding",
                                      "basis": "native_bilateral_closing_grid"})
                continue
            matches = by_source.get((instance.instance_uid if instance else None, cid), [])
            mapping = mapping_by_col.get(cell.col_idx)
            exact = len(matches) == 1 and matches[0].raw_value == cell.raw_text.strip()
            source_consistent = (source_cell.raw_text == cell.raw_text
                                 and source_cell.page_number == cell.page_number
                                 and source_cell.bbox == cell.bbox)
            present += bool(matches)
            column_safe = bool(mapping) and width_identity_safe and all(
                f.extra_dims.get("column_identity_status", "bound") == "bound"
                and f.indicator == mapping["canonical_measure"] for f in matches)
            columns_bound += column_safe
            rejection = operand_rejection_reason(matches[0], org_name) if exact else None
            conflict = not source_consistent or bool(matches) and (not exact or bool(mapping) and any(
                f.indicator != mapping["canonical_measure"] for f in matches))
            conflicts += conflict
            blocked += bool(exact and rejection)
            reason = None
            if conflict:
                reason = "fact_binding_conflict"
            elif not mapping:
                reason = "amount_column_unmapped"
            elif not column_safe:
                reason = "column_identity_unresolved"
            elif not exact:
                label_cols = mapping.get("source_label_cols", [])
                labels = [c for c in table.cells if c.row_idx == cell.row_idx
                          and c.col_idx in label_cols and c.raw_text.strip()]
                reason = "source_label_missing" if not labels else "fact_unbound"
            else:
                bound.append(cid)
                if rejection:
                    reason = "rule_admission_blocked"
                else:
                    admitted += 1
            observations.append({**observation, "expected_indicator": mapping["canonical_measure"] if mapping else None,
                                 "observation_kind": ("unclassified_numeric_projection" if not rule and table.extraction_method == "text"
                                                      else "fiscal_amount_candidate"),
                                 "fact_count": len(matches), "column_identity_bound": column_safe,
                                 "rule_rejection": rejection, "reason": reason})
            if reason:
                unresolved.append(observations[-1])
        positions = {(c.row_idx, c.col_idx) for c in table.cells}
        grid_positions = {(r, c) for r, row in enumerate(table.rows) for c in range(len(row))}
        required_positions = {(r, c) for r, row in enumerate(table.rows)
                              for c, value in enumerate(row) if value is not None}
        tables.append({"fragment_uid": table.fragment_uid, "table_code": table.table_code,
                       "title": table.title, "page": table.page_number, "raw_cell_count": len(table.cells),
                       "matrix_cell_count": len(grid_positions),
                       "merged_placeholder_count": len(grid_positions - required_positions),
                       "matrix_complete": required_positions <= positions <= grid_positions,
                       "amount_column_count": len(mapping_by_col), "amount_cell_count": len(observations),
                       "fact_present_count": present, "fact_bound_count": len(bound),
                       "column_identity_bound_count": columns_bound, "fact_conflict_count": conflicts,
                       "rule_admitted_count": admitted, "rule_blocked_count": blocked,
                       "padding_cells": padding_cells, "excluded_numeric_cells": excluded,
                       "amount_cells": observations, "unresolved_money_cells": unresolved,
                       "classification_status": "identified" if rule else "retained_unclassified"})
    # 逻辑视图丢失的原物理金额也必须进回执。金额按来源身份检查，不能按同额反找。
    orphaned = []
    for table in artifact.physical_fragments:
        if table.extraction_method == "page_text_fallback":
            continue
        rule = NINE_TABLE_RULES.get(table.table_code)
        mappings = canonical_column_mappings(table, rule) if rule else []
        for cell in table.cells:
            cid = source_cell_id(table, cell)
            if cell.numeric_value is None or cid in observed_ids or _numeric_exclusion(table, cell, mappings, serial_proofs):
                continue
            orphaned.append({"source_cell_id": cid, "raw_text": cell.raw_text,
                             "page": cell.page_number, "bbox": list(cell.bbox) if cell.bbox else None,
                             "row": cell.row_idx, "column": cell.col_idx, "table_code": table.table_code,
                             "reason": "physical_amount_missing_from_logical_view", "fact_count": 0})
    unresolved = [dict(table_code=t["table_code"], **cell)
                  for t in tables for cell in t["unresolved_money_cells"]] + orphaned
    reading_quality = assess_table_reading(artifact)
    reading_quality["unbound_money_cells"] = unresolved
    coverage = {
        "basis": "all_retained_numeric_cells_with_explicit_exclusions",
        "original_pdf_full_coverage_verified": False,
        "amount_cell_count": sum(t["amount_cell_count"] for t in tables) + len(orphaned),
        "identified_fiscal_amount_cell_count": sum(t["amount_cell_count"] for t in tables if t["classification_status"] == "identified"),
        "unclassified_numeric_projection_count": sum(c["observation_kind"] == "unclassified_numeric_projection"
                                                    for t in tables for c in t["amount_cells"]),
        "column_identity_bound_count": sum(t["column_identity_bound_count"] for t in tables),
        "unmapped_amount_cell_count": sum(c["reason"] == "amount_column_unmapped" for c in unresolved),
        "fact_present_count": sum(t["fact_present_count"] for t in tables),
        "fact_bound_count": sum(t["fact_bound_count"] for t in tables),
        "fact_conflict_count": sum(t["fact_conflict_count"] for t in tables),
        "rule_admitted_count": sum(t["rule_admitted_count"] for t in tables),
        "rule_blocked_count": sum(t["rule_blocked_count"] for t in tables),
        "pending_review_count": len(unresolved),
    }
    coverage["fact_missing_count"] = coverage["amount_cell_count"] - coverage["fact_present_count"]
    coverage["column_identity_unresolved_count"] = coverage["amount_cell_count"] - coverage["column_identity_bound_count"]
    reading_quality["fiscal_coverage"] = coverage
    if unresolved:
        reading_quality["status"] = "partial"
    return {"original_pdf_full_coverage_verified": False,
            "physical_table_count": len(artifact.physical_fragments),
            "physical_cell_count": sum(len(t.cells) for t in artifact.physical_fragments),
            "source_grid_count": len(artifact.source_grids),
            "source_grid_cell_count": sum(len(t.cells) for t in artifact.source_grids),
            "logical_table_count": len(tables), "logical_cell_count": len(artifact.cells()),
            "matrix_complete": all(t["matrix_complete"] for t in tables),
            **coverage,
            "basis": "parser_output_and_storage_projection",
            "fiscal_coverage": coverage,
            "padding_cell_count": sum(len(t["padding_cells"]) for t in tables),
            "excluded_numeric_cell_count": sum(len(t["excluded_numeric_cells"]) for t in tables),
            "unresolved_money_cell_count": len(unresolved),
            "reading_quality": reading_quality,
            "orphaned_physical_amounts": orphaned,
            "unclassified_table_count": sum(t["classification_status"] == "retained_unclassified" for t in tables),
            "organization_status": "identified" if org_name else "unresolved",
            "tables": tables}
