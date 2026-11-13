"""Literal empty-table disclosures do not require manufactured zero facts."""
from __future__ import annotations

import re

from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text


def disclosed_empty_fiscal_table(doc, instance, table) -> bool:
    subject = {
        "FIN_08_gov_fund": "政府性基金预算",
        "FIN_09_state_capital": "国有资本经营预算",
    }.get(table.table_code)
    if subject is None:
        return False
    from src.services.fiscal_column_identity import canonical_column_mappings

    mappings = canonical_column_mappings(table, NINE_TABLE_RULES[table.table_code])
    money = {m["source_col_idx"] for m in mappings}
    if any(cell.numeric_value is not None and cell.numeric_value != 0
           for cell in table.cells if not cell.is_header and (not money or cell.col_idx in money)):
        return False
    context = getattr(doc, "fact_context", None)
    if context and any(f.normalized_value not in (None, 0) for f in context.facts
                       if f.instance_uid == instance.instance_uid and f.table_code == table.table_code):
        return False
    pages = {cell.page_number for cell in table.cells} or {table.page_number}
    texts = getattr(doc, "page_texts", []) or []
    artifact = getattr(doc, "parse_artifact", None)
    for page in pages:
        text = texts[page - 1] if 0 < page <= len(texts) else ""
        if not text:
            text = "".join(block.text for block in getattr(artifact, "text_blocks", [])
                           if block.page_number == page and block.extraction_method == "native_text")
        literal = normalize_text(text)
        if subject not in literal:
            continue
        if "本表无数据" in literal or "本表为空表" in literal:
            return True
        if re.search(r"(?:没有|无|不涉及|未发生)[^。；;]{0,45}" + subject, literal):
            return True
        if re.search(subject + r"[^。；;]{0,45}(?:没有|无收入|无支出|无此项|无相关预算)", literal):
            return True
    return False
