"""Bind rule references to actual native spans/cells before evidence verification."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from src.engine.fact_context import _canonical_budget_stage, _owner_rejection_reason, operand_rejection_reason

from src.services.evidence_guard import iter_findings


def _indexed_text(text):
    chars, positions = [], []
    for index, char in enumerate(text):
        for normalized in unicodedata.normalize("NFKC", char):
            if not normalized.isspace():
                chars.append(normalized)
                positions.append(index)
    return "".join(chars), positions


def _native_span(quote, page, pages, file_hash):
    if type(page) is not int or not 1 <= page <= len(pages) or not quote:
        return None
    original = pages[page - 1]
    haystack, offsets = _indexed_text(original)
    needle, _ = _indexed_text(quote)
    if not needle or haystack.count(needle) != 1:
        return None
    start = haystack.index(needle)
    begin, end = offsets[start], offsets[start + len(needle) - 1] + 1
    actual = original[begin:end]
    source_id = f"native:p{page}:s{begin}:e{end}"
    return {"page": page, "physical_page": page, "text": actual,
            "text_snippet": actual, "quote": actual, "file_hash": file_hash,
            "source_block_id": source_id, "source_method": "native_text",
            "source_span": [begin, end], "source_identity_status": "verified",
            "evidence_id": hashlib.sha1("|".join((file_hash, str(page), source_id, actual)).encode()).hexdigest()[:16],
            "verification_status": "unverified", "location_level": "region"}


def _literal_ref_sources(ref, pages, file_hash):
    """Split a unique original claim at physical page boundaries.

    Legacy rules may attach a cross-page sentence to its first or last page.
    Only a literal that actually crosses pages and includes that declared page
    can be repaired; a wrong single-page reference remains unresolved.
    """
    quote, page = ref.get('span'), ref.get('page')
    direct = _native_span(quote, page, pages, file_hash)
    if direct is not None:
        return [direct]
    if not quote or type(page) is not int:
        return []
    merged = '\n'.join(pages)
    haystack, positions = _indexed_text(merged)
    needle, _ = _indexed_text(quote)
    if not needle or haystack.count(needle) != 1:
        return []
    at = haystack.index(needle)
    begin, end = positions[at], positions[at + len(needle) - 1] + 1
    parts, offset = [], 0
    for number, original in enumerate(pages, 1):
        left, right = max(begin, offset), min(end, offset + len(original))
        if left < right and merged[left:right].strip():
            literal = _native_span(merged[left:right], number, pages, file_hash)
            if literal is None:
                return []
            parts.append(literal)
        offset += len(original) + 1
    return parts if len(parts) > 1 and page in {item['physical_page'] for item in parts} else []


def _bbox(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if not all(Decimal(str(item)).is_finite() for item in box):
        return None
    return box if box[2] > box[0] and box[3] > box[1] else None


def _current_cell_catalog(artifact):
    cells = {}
    conflicting = set()
    for table in [*getattr(artifact, 'physical_fragments', []), *getattr(artifact, 'table_fragments', [])]:
        for cell in table.cells:
            uid = cell.origin_fragment_uid or table.fragment_uid
            row = cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx
            col = cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx
            key = f"{uid}:r{row}c{col}"
            previous = cells.get(key)
            if previous is not None and (previous.raw_text, previous.page_number, _bbox(previous.bbox)) != (
                    cell.raw_text, cell.page_number, _bbox(cell.bbox)):
                conflicting.add(key)
            cells[key] = cell
    return cells, conflicting


def _current_cell_evidence(facts, artifact, pages, file_hash, *, cell_catalog=None):
    """Authenticate every original cell, including cells on continuation pages.

    A corrected fact may differ from its printed amount. Its quote continues to
    identify the unchanged PDF cell; the current value is separately recorded.
    Neither the current amount nor a generated formula becomes the quote.
    """
    cells, conflicting = cell_catalog if cell_catalog is not None else _current_cell_catalog(artifact)
    evidence = []
    for fact in facts:
        refs = fact.extra_dims.get("evidence_refs", [])
        if not isinstance(refs, list) or not refs:
            return []
        for ref in refs:
            if not isinstance(ref, dict):
                return []
            cell = cells.get(ref.get("source_cell_id"))
            page, quote = ref.get("physical_page"), ref.get("quote")
            if (cell is None or ref.get("file_hash") != file_hash or not _indexed_text(str(quote or ''))[0]
                    or ref.get("source_cell_id") in conflicting
                    or cell.raw_text != quote or cell.page_number != page
                    or ref.get("source_method") != "native_text" or not _bbox(cell.bbox)
                    or _bbox(ref.get("bbox")) != _bbox(cell.bbox)
                    or type(page) is not int or not 1 <= page <= len(pages)
                    or _indexed_text(quote)[0] not in _indexed_text(pages[page - 1])[0]):
                return []
            expected_id = hashlib.sha1("|".join((file_hash, str(page), ref['source_cell_id'], quote)).encode()).hexdigest()[:16]
            if ref.get('evidence_id') != expected_id:
                return []
            item = dict(ref)
            item.update(page=page, text=quote, text_snippet=quote,
                        bbox=list(_bbox(cell.bbox)), source_identity_status="verified",
                        verification_status="verified", location_level="exact_cell",
                        fact_uid=fact.fact_uid, table_code=getattr(fact, 'table_code', None),
                        instance_uid=getattr(fact, 'instance_uid', None),
                        indicator=getattr(fact, 'indicator', None),
                        subject_code=getattr(fact, 'subject_code', None),
                        subject_name=getattr(fact, 'subject_name', None),
                        current_value=str(fact.normalized_value), current_value_unit="万元")
            if fact.extra_dims.get('correction'):
                item['fact_correction'] = dict(fact.extra_dims['correction'])
            evidence.append(item)
    return evidence


def _table_code(value):
    from src.engine.fact_table_projection import TABLE_CODES

    text = str(value or '')
    return TABLE_CODES.get(text, text if text in TABLE_CODES.values() else None)


def _same_text(left, right):
    return _indexed_text(str(left or ''))[0] == _indexed_text(str(right or ''))[0]


def _expected_amount(ref):
    """Amounts only validate an already selected identity; they never select it."""
    value = ref.get('value', ref.get('expected_value'))
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        factor = {'元': Decimal('.0001'), '万元': Decimal('1'), '亿元': Decimal('10000')}.get(
            ref.get('value_unit', '万元'))
        if factor is None or not amount.is_finite():
            return None
        return amount * factor
    except (InvalidOperation, TypeError, ValueError):
        return None


def _operand_fact(ref, facts, context):
    """Resolve exactly one semantic identity within this rule's current facts.

    Minimal selector: table + indicator/measure + subject_code/subject_name.
    A fact_uid is also a complete identity, but any other declared dimensions
    still have to match. Same-valued candidates never resolve an ambiguity.
    """
    uid = ref.get('fact_uid')
    if ref.get('fact_version') and ref['fact_version'] != context.version_token:
        return None, 'operand_fact_version_not_current'
    table = _table_code(ref.get('table_code') or ref.get('table'))
    indicator = ref.get('indicator') or ref.get('measure')
    has_subject = 'subject_code' in ref or 'subject_name' in ref or 'row_order' in ref
    if not uid and (not table or not indicator or not has_subject):
        return None, 'operand_requires_semantic_identity'
    if (ref.get('table_code') or ref.get('table')) and table is None:
        return None, 'operand_unknown_table'
    candidates = []
    for fact in facts:
        if uid and fact.fact_uid != uid:
            continue
        if table and fact.table_code != table:
            continue
        if indicator and fact.indicator != indicator:
            continue
        if any(key in ref and getattr(fact, key, None) != ref[key]
               for key in ('subject_code', 'instance_uid', 'row_order', 'org_scope', 'period', 'fund_scope')):
            continue
        if 'subject_name' in ref and not _same_text(fact.subject_name, ref['subject_name']):
            continue
        if 'budget_stage' in ref and _canonical_budget_stage(fact.budget_stage) != _canonical_budget_stage(ref['budget_stage']):
            continue
        if 'org_name' in ref and not _same_text(fact.org_name, ref['org_name']):
            continue
        if 'fiscal_year' in ref and fact.fiscal_year != ref['fiscal_year']:
            continue
        if 'source_cell_id' in ref and ref['source_cell_id'] not in {
                item.get('source_cell_id') for item in fact.extra_dims.get('evidence_refs', []) if isinstance(item, dict)}:
            continue
        # Ownership, status, year and stage admission must use current facts,
        # including corrected values, never the pre-overlay artifact values.
        if _owner_rejection_reason(fact, getattr(context, 'org_name', None)):
            continue
        year = getattr(context, 'fiscal_year', None)
        stage = getattr(context, 'budget_stage', None)
        if year and not uid and 'fiscal_year' not in ref and fact.fiscal_year != year:
            continue
        if stage and not uid and 'budget_stage' not in ref and _canonical_budget_stage(fact.budget_stage) != _canonical_budget_stage(stage):
            continue
        candidates.append(fact)
    if len(candidates) != 1:
        return None, 'operand_missing_or_ambiguous'
    fact = candidates[0]
    if operand_rejection_reason(fact, getattr(context, 'org_name', None)):
        return None, 'operand_not_usable'
    if 'value' in ref or 'expected_value' in ref:
        expected = _expected_amount(ref)
        if expected is None or expected != fact.normalized_value:
            return None, 'operand_value_not_current'
    return fact, None


def _bind_operand_refs(location, facts, context, artifact, pages, file_hash):
    refs = location.get('operand_refs')
    if not isinstance(refs, list) or not refs or len(refs) > 2048:
        return [], [], [], ['invalid_operand_refs']
    selected, bound_refs, evidence, errors = [], [], [], []
    cell_catalog = _current_cell_catalog(artifact)
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            errors.append(f'operand#{index}:invalid_operand_ref')
            continue
        fact, error = _operand_fact(ref, facts, context)
        if error:
            errors.append(f'operand#{index}:{error}')
            continue
        source = _current_cell_evidence([fact], artifact, pages, file_hash, cell_catalog=cell_catalog)
        if not source:
            errors.append(f'operand#{index}:missing_current_native_cell_evidence')
            continue
        operand_id = str(ref.get('operand_id') or f'operand-{index}')
        for item in source:
            item.update(operand_id=operand_id, operand_role=ref.get('role'), relation_id=ref.get('relation_id'))
        selected.append(fact)
        evidence.extend(source)
        bound_refs.append({**ref, 'operand_id': operand_id, 'fact_uid': fact.fact_uid,
                           'fact_version': context.version_token,
                           'evidence_ids': [item['evidence_id'] for item in source],
                           'evidence_refs': source})
    # A genuine parent or a single table cannot authenticate a partial proof.
    if errors or len(bound_refs) != len(refs):
        return [], [], [], errors
    return selected, bound_refs, evidence, []


def _vertical_operands(facts, table_code, location):
    """Bind the parent and every immediate child, including zero operands."""
    code = str(location.get('code') or '')
    candidates = [f for f in facts if getattr(f, 'table_code', None) == table_code
                  and getattr(f, 'indicator', None) == 'total_actual'
                  and getattr(f, 'budget_stage', None) == '决算']
    parents = [f for f in candidates if (f.subject_code == code if code != 'TOTAL'
               else f.subject_name == '合计')]
    child_width = {3: 5, 5: 7}.get(len(code))
    children = [f for f in candidates if f.subject_code and (
        len(f.subject_code) == 3 if code == 'TOTAL'
        else child_width and f.subject_code.startswith(code) and len(f.subject_code) == child_width)]
    operands = parents + children
    if (len(parents) != 1 or not children
            or len({f.subject_code for f in children}) != len(children)
            or any(f.recognition_status in {'low_confidence', 'rejected'} for f in operands)):
        return []
    try:
        difference = abs(parents[0].normalized_value - sum(f.normalized_value for f in children))
        if difference != abs(Decimal(str(location['diff']))):
            return []  # Missing children or a changed projection cannot prove this claim.
    except (KeyError, ValueError, ArithmeticError):
        return []
    return operands


def _total_sheet_operands(facts, declared):
    labels = {'总计', '本年支出合计', '结余分配', '年末结转和结余'}
    if not isinstance(declared, dict) or set(declared) != labels:
        return []
    result = []
    for label in sorted(labels):
        matches = [f for f in facts if f.table_code == 'FIN_01_income_expenditure_total'
                   and f.indicator == 'expenditure_actual' and f.budget_stage == '决算'
                   and f.subject_name == label]
        if len(matches) != 1 or matches[0].recognition_status in {'low_confidence', 'rejected'}:
            return []
        try:
            if matches[0].normalized_value != Decimal(str(declared[label])):
                return []
        except (ValueError, ArithmeticError):
            return []
        result.extend(matches)
    return result


def bind_rule_sources(result, *, page_texts, source_file_hash, artifact=None, fact_context=None, consumption=()):
    """Only literal native references may become verified source identities.

    The evidence guard still verifies every quote and current SHA afterwards.
    Generated explanations, absent/ambiguous spans, stale contexts and OCR stay
    unverified. This verifies source identity, never business correctness.
    """
    bound = 0
    if not re.fullmatch(r"[0-9a-f]{64}", source_file_hash or ""):
        return {"bound_rule_findings": 0}
    for finding in iter_findings(result):
        if finding.get("source") not in {"rule", "engine"}:
            continue
        old = finding.get("evidence") or []
        if any(item.get('file_hash') and item['file_hash'] != source_file_hash for item in old):
            continue
        location = finding.get("location") or {}
        has_operands = 'operand_refs' in location
        if not has_operands and old and all(item.get("source_identity_status") == "verified" for item in old):
            continue
        refs = location.get("table_refs") or []
        evidence = []
        bound_refs = []
        literal_refs = []
        if has_operands:
            matching_context = (artifact is not None and fact_context is not None
                                and artifact.source_file_hash == fact_context.file_hash == source_file_hash
                                and artifact.parse_run_uid == fact_context.parse_run_uid)
            errors = ['current_fact_context_unavailable']
            if matching_context and any(token and token != fact_context.version_token for token in (
                    location.get('fact_context_version_token'), finding.get('fact_version'))):
                matching_context = False
                errors = ['declared_fact_version_not_current']
            if matching_context:
                rule = finding.get('rule_id') or finding.get('rule')
                uids = {uid for entry in consumption if entry.get('rule_id') == rule
                        and entry.get('fact_version') == fact_context.version_token for uid in entry.get('fact_uids', [])}
                selected, bound_refs, evidence, errors = _bind_operand_refs(
                    location, [f for f in fact_context.facts if f.fact_uid in uids], fact_context,
                    artifact, page_texts, source_file_hash)
                from src.engine.native_empty_operands import valid_blank_observation
                for observation in location.get('native_blank_sources') or []:
                    if not isinstance(observation, dict) or not valid_blank_observation(artifact, fact_context, observation):
                        errors.append('native_blank_source_not_current_or_unverified')
                # Literal narrative claims are part of the proof too. Retain
                # them alongside the denominator/numerator cell evidence.
                if evidence:
                    for index, ref in enumerate(refs):
                        if not ref.get('span'):
                            continue
                        sources = _literal_ref_sources(ref, page_texts, source_file_hash)
                        if not sources:
                            errors.append(f'table_ref#{index}:literal_span_not_unique_on_claimed_page')
                        else:
                            for literal in sources:
                                literal.update(role=ref.get('role'), table=ref.get('table'), section=ref.get('section'))
                                evidence.append(literal)
                if evidence and not errors:
                    finding['fact_ids'] = list(dict.fromkeys(fact.fact_uid for fact in selected))
                    finding['fact_version'] = fact_context.version_token
            if errors:
                finding['source_binding_status'] = 'unresolved'
                finding['source_binding_errors'] = errors
                finding.setdefault('original_severity', finding.get('severity'))
                finding['severity'] = 'manual_review'
                finding['fact_ids'] = []
                finding['evidence_ids'] = []
                finding['evidence'] = [{**item, 'source_identity_status': 'unverified',
                                        'verification_status': 'unverified'} for item in old]
                # An explicit operand contract is all-or-nothing. Never
                # authenticate a formula using only an unrelated literal span.
                continue
        elif refs and all(ref.get("span") and type(ref.get("page")) is int for ref in refs):
            for ref in refs:
                sources = _literal_ref_sources(ref, page_texts, source_file_hash)
                if not sources:
                    evidence.append(None)
                    continue
                for literal in sources:
                    literal.update(role=ref.get('role'), table=ref.get('table'), section=ref.get('section'))
                    evidence.append(literal)
                    literal_ref = {**ref, **literal, 'span': literal['quote']}
                    # The new identity authenticates native characters, not
                    # the caller's cell ID. Keeping both makes the guard hash
                    # the old cell against the new block's evidence ID.
                    if 'source_cell_id' in literal_ref:
                        literal_ref['declared_source_cell_id'] = literal_ref.pop('source_cell_id')
                    literal_refs.append(literal_ref)
        elif old:
            evidence = [_native_span(item.get('quote') or item.get('text') or item.get('text_snippet'),
                                     item.get('page') or item.get('physical_page') or location.get('page'),
                                     page_texts, source_file_hash) for item in old]
        if not has_operands and (not evidence or any(item is None for item in evidence)):
            evidence = []
            if (artifact is not None and fact_context is not None
                    and artifact.source_file_hash == fact_context.file_hash == source_file_hash
                    and artifact.parse_run_uid == fact_context.parse_run_uid):
                rule = finding.get('rule_id') or finding.get('rule')
                uids = {uid for entry in consumption if entry.get('rule_id') == rule
                        and entry.get('fact_version') == fact_context.version_token for uid in entry.get('fact_uids', [])}
                code = str(location.get('code') or '')
                if not code:
                    match = re.match(r"^(\d{3,7})(?:\s|$)", str(location.get('row') or ''))
                    code = match.group(1) if match else ''
                selected = [fact for fact in fact_context.facts if fact.fact_uid in uids
                            and code and fact.subject_code == code
                            and fact.recognition_status not in {'low_confidence', 'rejected'}]
                from src.engine.fact_table_projection import TABLE_CODES
                table_code = TABLE_CODES.get(location.get('table'))
                if rule == 'V33-101':
                    selected = _total_sheet_operands([f for f in fact_context.facts if f.fact_uid in uids], location.get('identity_operands'))
                elif str(location.get('type') or '').startswith('vertical'):
                    selected = _vertical_operands([f for f in fact_context.facts if f.fact_uid in uids], table_code, location)
                elif location.get('san_gong_item') and selected:
                    peers = [fact for fact in fact_context.facts if fact.fact_uid in uids
                             and fact.table_code == 'FIN_07_three_public'
                             and fact.indicator == location['san_gong_item']
                             and fact.budget_stage == '决算'
                             and fact.recognition_status not in {'low_confidence', 'rejected'}]
                    try:
                        matches = (len(selected) == len(peers) == 1
                                   and selected[0].normalized_value == Decimal(str(location.get('basic_amount')))
                                   and peers[0].normalized_value == Decimal(str(location.get('three_public_amount'))))
                    except (ValueError, ArithmeticError):
                        matches = False
                    if matches:
                        selected.extend(peers)
                    else:
                        selected = []
                elif table_code and rule in {'V33-240', 'V33-241'}:
                    selected = [fact for fact in selected if fact.table_code == table_code]
                else:
                    selected = []  # A parent or partial row alone cannot certify another formula.
                if refs and any(ref.get('table') and TABLE_CODES.get(ref['table']) not in {fact.table_code for fact in selected} for ref in refs):
                    selected = []  # A subset of a multi-table proof is incomplete.
                if selected and len(selected) <= 64:
                    evidence = _current_cell_evidence(selected, artifact, page_texts, source_file_hash)
                    if evidence:
                        finding['fact_ids'] = [fact.fact_uid for fact in selected]
                        finding['fact_version'] = fact_context.version_token
        if not evidence:
            continue
        finding['evidence_explanation'] = old
        finding['evidence'] = evidence
        finding['evidence_ids'] = list(dict.fromkeys(item['evidence_id'] for item in evidence))
        if literal_refs and all(item.get('source_span') for item in evidence) and not bound_refs:
            location['table_refs'] = literal_refs
            location['pages'] = sorted({item['physical_page'] for item in evidence})
            finding['location'] = location
        elif not bound_refs:
            # Page-only placeholders left by normalization must not hide the
            # original spans that have just been authenticated. Both the UI
            # evidence buttons and PDF export consume these same references.
            location['table_refs'] = [
                {**item, 'role': item.get('role') or f'原文位置{index + 1}',
                 'span': item.get('quote')}
                for index, item in enumerate(evidence)
            ]
            location['pages'] = sorted({item['physical_page'] for item in evidence})
            finding['location'] = location
        if bound_refs:
            location['operand_refs'] = bound_refs
            location['pages'] = sorted({item['physical_page'] for item in evidence})
            location['page'] = evidence[0]['physical_page']
            finding['page_number'] = evidence[0]['physical_page']
            # Every operand keeps its own source coordinates. There is no
            # cross-page bounding rectangle and no first-table page fallback.
            location['table_refs'] = [{**item,
                                       'role': item.get('operand_role') or item.get('role'),
                                       'table': item.get('table') or item.get('table_code'),
                                       'row': item.get('subject_name') or item.get('subject_code'),
                                       'field': item.get('indicator')}
                                      for item in evidence]
            location.pop('bbox', None)
            finding.pop('bbox', None)
            finding['location'] = location
            finding['source_binding_status'] = 'bound'
            finding.pop('source_binding_errors', None)
        finding.pop('text_snippet', None)
        finding['source_binding_method'] = ('current_semantic_operands_and_literal_claims' if bound_refs
                                            else 'literal_native_span_or_current_consumed_cell')
        bound += 1
    return {"bound_rule_findings": bound, "scope": "source identity only; evidence guard and human business adjudication remain required"}


def _ai_literal_sources(quote, claimed_page, pages, file_hash, context=None):
    """以原文而非模型页码为准；全篇唯一命中才允许纠正错误页码。"""
    if not isinstance(quote, str) or not quote.strip():
        return []
    needle, _ = _indexed_text(quote)
    direct = _native_span(quote, claimed_page, pages, file_hash)
    if direct is not None:
        return [direct]
    # 纯数字、过短词语不能跨页猜位置。重复引文必须有完整且唯一的上下文。
    if isinstance(context, str) and needle in _indexed_text(context)[0]:
        expanded = _native_span(context, claimed_page, pages, file_hash)
        if expanded is not None:
            return [expanded]
    # 提取器可能截断长引文并补省略号，只绑定仍保留的长原文；
    # 省略部分不恢复、不猜测，原模型引文继续保存在 evidence_explanation。
    if re.search(r"(?:…|\.{3})+$", quote.strip()):
        prefix = re.sub(r"(?:…|\.{3})+$", "", quote.strip()).rstrip()
        if len(_indexed_text(prefix)[0]) >= 40:
            sources = _ai_literal_sources(prefix, claimed_page, pages, file_hash)
            if sources:
                return [{**source, "quote_truncated": True} for source in sources]
    if len(needle) < 8:
        return []
    merged = "\n".join(pages)
    haystack, offsets = _indexed_text(merged)
    if haystack.count(needle) != 1:
        return []
    found = haystack.index(needle)
    begin = offsets[found]
    offset = 0
    for page, text in enumerate(pages, 1):
        if offset <= begin < offset + len(text):
            direct = _native_span(quote, page, pages, file_hash)
            if direct is not None:
                return [direct]
            return _literal_ref_sources({"span": quote, "page": page}, pages, file_hash)
        offset += len(text) + 1
    return []


def _clear_unverified_ai_anchor(finding, reason):
    """原件匹配失败后撤销旧坐标，避免换件或旧缓存的已验证标记继续生效。"""
    old = finding.get("evidence") or []
    finding.setdefault("evidence_explanation", old)
    finding.setdefault("claimed_page_number", finding.get("page_number"))
    finding["evidence"] = []
    for item in old:
        if not isinstance(item, dict):
            continue
        # 引文/原声明页保留作下一次匹配线索；失效的来源与几何身份留在解释快照中。
        retained = {key: value for key, value in item.items() if key not in {
            "bbox", "file_hash", "source_span", "source_block_id", "source_cell_id", "evidence_id"}}
        retained.update(location_level="unresolved", verification_status="unverified")
        finding["evidence"].append(retained)
    finding.update(bbox=None, page_number=None, evidence_ids=[], location_level="unresolved",
                   verification_status="unverified", source_binding_status="unresolved",
                   source_binding_errors=[reason])
    location = dict(finding.get("location") or {})
    location.update(page=None, pages=[], table_refs=[], location_level="unresolved", verification_status="unverified")
    location.pop("bbox", None)
    finding["location"] = location


def bind_ai_literal_sources(result, *, page_texts, source_file_hash):
    """Bind AI quotes to original physical pages, repairing uniquely provable page errors.

    This authenticates the quoted source and enables a precise PDF location; it
    does not validate the model's conclusion. Non-matching or ambiguous quotes
    stay untouched so the evidence guard can keep them in manual review.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", source_file_hash or ""):
        return {"bound_ai_findings": 0, "unresolved_ai_findings": 0}

    bound = 0
    unresolved = 0
    seen = set()
    for finding in iter_findings(result):
        if finding.get("source") != "ai":
            continue
        finding_key = str(finding.get("id") or id(finding))
        if finding_key in seen:
            continue
        seen.add(finding_key)
        old_evidence = finding.get("evidence")
        if not isinstance(old_evidence, list) or not old_evidence:
            _clear_unverified_ai_anchor(finding, "ai_quote_missing")
            unresolved += 1
            continue

        location = finding.get("location") if isinstance(finding.get("location"), dict) else {}
        bound_evidence = []
        for item in old_evidence:
            if not isinstance(item, dict):
                bound_evidence = []
                break
            quote = item.get("quote") or item.get("text") or item.get("text_snippet")
            page = item.get("physical_page") or item.get("page") or finding.get("page_number") or location.get("page")
            try:
                page = int(page)
            except (TypeError, ValueError):
                page = None
            sources = _ai_literal_sources(quote, page, page_texts or [], source_file_hash,
                                          context=item.get("context"))
            if not sources:
                bound_evidence = []
                break
            for source in sources:
                # 模型的 original/context 可能是总结或含省略号，留在解释中；
                # 可核验证据仅承载实际原文，避免一个真实引文被生成文本拖累。
                bound_evidence.append({"type": item.get("type", "text_content"),
                                       "claimed_page": page, **source})

        if not bound_evidence:
            _clear_unverified_ai_anchor(finding, "ai_quote_not_found_or_ambiguous")
            unresolved += 1
            continue

        finding.setdefault("evidence_explanation", old_evidence)
        finding["evidence"] = bound_evidence
        finding["evidence_ids"] = list(dict.fromkeys(item["evidence_id"] for item in bound_evidence))
        pages = sorted({item["physical_page"] for item in bound_evidence})
        location = dict(location)
        location["pages"] = pages
        location["page"] = pages[0]
        finding["claimed_page_number"] = finding.get("claimed_page_number", (finding.get("metrics") or {}).get("ai_reported_page", finding.get("page_number")))
        finding["page_number"] = pages[0]
        # 旧版模型坐标与页码已不再有效，精确坐标只由 PDF 字符核验生成。
        finding["bbox"] = None
        location.pop("bbox", None)
        location["location_level"] = "region"
        location["verification_status"] = "unverified"
        location["table_refs"] = [{**item, "role": "AI 原文引用"} for item in bound_evidence]
        finding["location_level"] = "region"
        finding["verification_status"] = "unverified"
        finding["location"] = location
        finding["source_binding_method"] = "ai_literal_native_span"
        finding["source_binding_status"] = "bound"
        finding.pop("source_binding_errors", None)
        bound += 1

    return {
        "bound_ai_findings": bound,
        "unresolved_ai_findings": unresolved,
        "scope": "unique literal quote source identity only; model conclusions still require review",
    }


def verify_bound_rule_locations(result, *, source_pdf_path):
    """Run the PDF content/geometry gate after replacing generated evidence.

    Copy only locator-owned fields back so relation contracts, current fact
    versions and correction audit records survive the IssueItem boundary.
    """
    return _verify_bound_locations(
        result,
        source_pdf_path=source_pdf_path,
        allowed_sources={"rule", "engine"},
        result_key="checked_rule_findings",
    )


def verify_bound_ai_locations(result, *, source_pdf_path):
    """Authenticate exact literal source locations for AI findings."""
    receipt = _verify_bound_locations(
        result,
        source_pdf_path=source_pdf_path,
        allowed_sources={"ai"},
        result_key="checked_ai_findings",
    )
    from src.services.ai_page_display import synchronize_ai_page_display
    for finding in iter_findings(result):
        synchronize_ai_page_display(finding)
    return receipt


def _verify_bound_locations(result, *, source_pdf_path, allowed_sources, result_key):
    # These names are used in this helper's scope. Imports in the public wrapper
    # functions do not populate this function's local or module namespace.
    from src.schemas.issues import IssueItem
    from src.utils.issue_bbox import PDFBBoxLocator

    checked = 0
    locator = PDFBBoxLocator(source_pdf_path)
    try:
        for finding in iter_findings(result):
            if (finding.get('source') not in allowed_sources
                    or not finding.get('source_binding_method')
                    or finding.get('source_binding_status') == 'unresolved'):
                continue
            item = IssueItem(
                id=str(finding.get('id') or ''), source='rule', severity='info',
                title=str(finding.get('title') or ''), message=str(finding.get('message') or ''),
                evidence=finding.get('evidence') or [], location=finding.get('location') or {},
                page_number=locator._to_positive_int(finding.get('page_number')),
                bbox=locator._normalize_bbox(finding.get('bbox')),
                location_level=str(finding.get('location_level') or 'unresolved'),
                verification_status=str(finding.get('verification_status') or 'unverified'),
            )
            located = locator.locate(item)
            for key in ('evidence', 'location', 'page_number', 'bbox', 'location_level', 'verification_status'):
                finding[key] = getattr(located, key)
            checked += 1
    finally:
        locator.close()
    return {result_key: checked}
