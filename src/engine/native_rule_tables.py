"""Adapt the current merged artifact to the older named-column rule interface."""
from __future__ import annotations

from src.engine.fact_context import _fragment_for_instance
from src.engine.fact_table_projection import TABLE_CODES, ProjectionUnavailable, fact_bound_table_rows
from src.engine.structured_rules import ParsedTable, materialize_table


def build_native_rule_tables(doc):
    artifact = doc.parse_artifact
    titles = {code: title for title, code in TABLE_CODES.items() if not title.startswith("BUD_")}
    result = {}
    for instance in artifact.logical_tables:
        table = _fragment_for_instance(artifact, instance)
        if table is None or table.extraction_method == "page_text_fallback" or table.table_code not in titles:
            continue
        pages = sorted({c.page_number for c in table.cells}) or [table.page_number]
        try:
            rows = fact_bound_table_rows(doc, titles[table.table_code])
            parsed = materialize_table(rows or [], title=table.title, table_code=table.table_code,
                                       pages=pages, header_rows=table.header_row_count)
            source = {(c.row_idx, c.col_idx): c for c in table.cells}
            for row_idx, row in enumerate(parsed.rows):
                for col_idx, cell in enumerate(row.cells):
                    original = source.get((row_idx, col_idx))
                    if original is not None:
                        cell.page = original.page_number
                        cell.bbox = original.bbox
                        cell.confidence = original.confidence
        except ProjectionUnavailable as error:
            # Preserve the instance and its actual failure; never rebuild old
            # page rows to bypass a rejected current fact.
            parsed = ParsedTable(table_code=table.table_code, title=table.title,
                                 page_span=(min(pages), max(pages)), parse_errors=[str(error)])
        parsed.anchor_table_name = titles[table.table_code]
        parsed.anchor_confirmed = True
        parsed.bbox = table.bbox
        result[instance.instance_uid] = parsed
    return result
