"""Locate both printed names in a functional-classification comparison."""
from __future__ import annotations

import re
import unicodedata

from .fact_context import _fragment_for_instance


def _compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))


def _interleaved_wrapped_code_name_span(page_text: str, code: str, name: str):
    """表内名称格多行折行、编码/数值夹在中间行时，返回唯一跨行原文片段。"""
    lines = [line.strip() for line in str(name or '').splitlines() if line.strip()]
    code_str = str(code or '').strip()
    if len(lines) < 2 or not code_str:
        return None
    pattern = re.compile(
        rf'(?:^|(?<=\s)){re.escape(lines[0])}\s*\n\s*{re.escape(code_str)}(?!\d)[^\n]*\n\s*{re.escape(lines[-1])}'
    )
    matches = list(pattern.finditer(str(page_text or '')))
    if len(matches) != 1:
        return None
    candidate = matches[0].group(0)
    compact_candidate = _compact(candidate)
    if not all(_compact(part) in compact_candidate for part in lines):
        return None
    return candidate


def _qualified_name_span(page_text, code, name):
    """重复名称只有原文中唯一相邻的科目编码+名称才可消歧。"""
    chars, positions = [], []
    for index, char in enumerate(page_text):
        for normalized in unicodedata.normalize('NFKC', char):
            if not normalized.isspace():
                chars.append(normalized)
                positions.append(index)
    text = ''.join(chars)
    needle = str(code) + _compact(name)
    if text.count(needle) != 1:
        return _interleaved_wrapped_code_name_span(page_text, code, name)
    start = text.index(needle)
    raw_start = positions[start]
    if raw_start and page_text[raw_start - 1].isdigit():
        return None
    return page_text[raw_start:positions[start + len(needle) - 1] + 1]


def functional_name_refs(doc, code, expected_name, mention):
    artifact, context = getattr(doc, 'parse_artifact', None), getattr(doc, 'fact_context', None)
    if artifact is None or context is None or (artifact.source_file_hash != context.file_hash
            or artifact.parse_run_uid != context.parse_run_uid):
        return []
    facts = [f for f in context.facts if f.table_code == 'FIN_05_general_public_expenditure'
             and f.subject_code == code and f.indicator == 'total_actual'
             and f.fiscal_year == context.fiscal_year and f.org_name == context.org_name]
    if len(facts) != 1:
        return []
    fact = facts[0]
    instances = [i for i in artifact.logical_tables if i.instance_uid == fact.instance_uid]
    if len(instances) != 1:
        return []
    table = _fragment_for_instance(artifact, instances[0])
    if table is None:
        return []
    cells = [c for c in table.cells if c.row_idx == fact.row_order and c.bbox
             and _compact(c.raw_text) == _compact(expected_name)]
    if len(cells) != 1:
        return []
    cell = cells[0]
    source_id = f'{cell.origin_fragment_uid or table.fragment_uid}:r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}'
    name_ref = {'page': cell.page_number, 'span': cell.raw_text, 'role': '原表科目名称',
                'bbox': list(cell.bbox), 'source_cell_id': source_id}
    if _compact(doc.page_texts[cell.page_number - 1]).count(_compact(cell.raw_text)) != 1:
        span = _qualified_name_span(doc.page_texts[cell.page_number - 1], code, cell.raw_text)
        if not span:
            return []
        # This literal spans the code and name cells. Do not borrow the name
        # cell's smaller rectangle or claim it is a single-cell quote.
        name_ref = {'page': cell.page_number, 'span': span, 'role': '原表科目编码及名称',
                    'name_source_cell_id': source_id}

    cross_page_spans = mention.get('cross_page_spans') or []
    if cross_page_spans:
        narrative_refs = []
        seen_page_spans = set()
        for piece in cross_page_spans:
            try:
                piece_page = int(piece.get('page'))
            except (TypeError, ValueError):
                return []
            if not 1 <= piece_page <= len(doc.page_texts):
                return []
            page_compact = _compact(doc.page_texts[piece_page - 1])
            chosen_span = None
            for candidate_span in (piece.get('span'), piece.get('context')):
                compact_piece = _compact(candidate_span)
                if compact_piece and page_compact.count(compact_piece) == 1:
                    chosen_span = candidate_span
                    break
            if not chosen_span:
                return []
            key = (piece_page, _compact(chosen_span))
            if key not in seen_page_spans:
                seen_page_spans.add(key)
                narrative_refs.append({'page': piece_page, 'span': chosen_span, 'role': '功能分类说明原文'})
        return [*narrative_refs, name_ref]

    try:
        page = int(mention.get('page'))
    except (TypeError, ValueError):
        return []
    quote = mention.get('source_span')
    if not quote or not 1 <= page <= len(doc.page_texts):
        return []
    if _compact(doc.page_texts[page - 1]).count(_compact(quote)) != 1:
        return []
    narrative_refs = []
    seen_spans = set()
    for raw_quote in [quote, *(mention.get('extra_spans') or [])]:
        compact_quote = _compact(raw_quote)
        if not compact_quote or compact_quote in seen_spans:
            continue
        if _compact(doc.page_texts[page - 1]).count(compact_quote) == 1:
            seen_spans.add(compact_quote)
            narrative_refs.append({'page': page, 'span': raw_quote, 'role': '功能分类说明原文'})
    return [*narrative_refs, name_ref]
