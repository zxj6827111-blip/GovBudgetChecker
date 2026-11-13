"""读取/入库回执；不把规则通过率当作 PDF 单元格覆盖率。"""
from __future__ import annotations

from src.services.fiscal_column_identity import canonical_column_mappings
from src.services.fiscal_row_roles import is_index_header, native_bilateral_padding_positions
from src.services.fiscal_table_rules import NINE_TABLE_RULES


def source_cell_id(table, cell):
    return (f"{cell.origin_fragment_uid or table.fragment_uid}:"
            f"r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}"
            f"c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}")


def build_table_import_receipt(artifact, facts, *, org_name=None):
    by_source = {}
    for fact in facts:
        for ref in fact.extra_dims.get("evidence_refs", []):
            if ref.get("source_cell_id"):
                by_source.setdefault((fact.instance_uid, ref["source_cell_id"]), []).append(fact)
    tables = []
    for table in artifact.table_fragments:
        instance = next((i for i in artifact.logical_tables
                         if table.fragment_uid in i.fragment_uids or table.fragment_uid == i.instance_uid), None)
        rule = NINE_TABLE_RULES.get(table.table_code)
        mappings = canonical_column_mappings(table, rule) if rule else []
        money_columns = {m["source_col_idx"] for m in mappings}
        padding = native_bilateral_padding_positions(table)
        bound, unresolved, padding_cells = [], [], []
        for cell in table.cells:
            if (cell.col_idx not in money_columns or cell.is_header or cell.row_idx < table.header_row_count
                    or is_index_header(table.rows[cell.row_idx]) or cell.numeric_value is None):
                continue
            observation = {"source_cell_id": source_cell_id(table, cell), "raw_text": cell.raw_text,
                           "page": cell.page_number, "bbox": list(cell.bbox) if cell.bbox else None,
                           "row": cell.row_idx, "column": cell.col_idx}
            if (cell.row_idx, cell.col_idx) in padding:
                padding_cells.append({**observation, "role": "source_padding",
                                      "basis": "native_bilateral_closing_grid"})
                continue
            matches = by_source.get((instance.instance_uid if instance else None, observation["source_cell_id"]), [])
            if len(matches) == 1 and matches[0].raw_value == cell.raw_text.strip():
                bound.append(observation["source_cell_id"])
            else:
                unresolved.append({**observation, "reason": "no_unique_current_fact", "fact_count": len(matches)})
        positions = {(c.row_idx, c.col_idx) for c in table.cells}
        grid_positions = {(r, c) for r, row in enumerate(table.rows) for c in range(len(row))}
        required_positions = {(r, c) for r, row in enumerate(table.rows)
                              for c, value in enumerate(row) if value is not None}
        tables.append({"fragment_uid": table.fragment_uid, "table_code": table.table_code,
                       "title": table.title, "page": table.page_number, "raw_cell_count": len(table.cells),
                       "matrix_cell_count": len(grid_positions),
                       "merged_placeholder_count": len(grid_positions - required_positions),
                       "matrix_complete": required_positions <= positions <= grid_positions,
                       "amount_column_count": len(money_columns), "fact_bound_count": len(bound),
                       "padding_cells": padding_cells, "unresolved_money_cells": unresolved,
                       "classification_status": "identified" if rule else "retained_unclassified"})
    unresolved_count = sum(len(t["unresolved_money_cells"]) for t in tables)
    return {"basis": "parser_output_and_storage_projection",
            "original_pdf_full_coverage_verified": False,
            "physical_table_count": len(artifact.physical_fragments),
            "physical_cell_count": sum(len(t.cells) for t in artifact.physical_fragments),
            "source_grid_count": len(artifact.source_grids),
            "source_grid_cell_count": sum(len(t.cells) for t in artifact.source_grids),
            "logical_table_count": len(tables), "logical_cell_count": len(artifact.cells()),
            "matrix_complete": all(t["matrix_complete"] for t in tables),
            "fact_bound_count": sum(t["fact_bound_count"] for t in tables),
            "padding_cell_count": sum(len(t["padding_cells"]) for t in tables),
            "unresolved_money_cell_count": unresolved_count,
            "unclassified_table_count": sum(t["classification_status"] == "retained_unclassified" for t in tables),
            "organization_status": "identified" if org_name else "unresolved",
            "tables": tables}
