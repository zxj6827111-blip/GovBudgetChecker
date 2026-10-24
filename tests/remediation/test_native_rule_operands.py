"""Semantic operand evidence must come from the current original cells."""
from copy import deepcopy
from decimal import Decimal

import pytest

from src.engine.fact_context import FactContext, FactOverlay, apply_fact_overlay
from src.schemas.issues import IssueItem
from src.schemas.parse_artifact import EvidenceRef, FiscalFact, LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable
from src.services.evidence_guard import evaluate_finding_evidence
from src.services.rule_source_binding import bind_rule_sources, verify_bound_rule_locations
from src.utils.issue_bbox import PDFBBoxLocator


T1 = 'FIN_01_income_expenditure_total'
T3 = 'FIN_03_expenditure'
T5 = 'FIN_05_general_public_expenditure'


def source(tmp_path, definitions):
    fitz = pytest.importorskip('fitz')
    document = fitz.open()
    fragments, facts, instances = {}, [], {}
    for _ in range(max(item.get('page', 1) for item in definitions)):
        document.new_page()
    for index, definition in enumerate(definitions):
        page_number = definition.get('page', 1)
        table_code = definition.get('table_code', T3)
        instance_uid = definition.get('instance_uid', table_code)
        fragment_uid = f'fragment-{page_number}-{table_code}-{instance_uid}'
        table = fragments.setdefault(fragment_uid, ParsedTable(
            table_code, 'Original table', page_number, 0, (0, 0, 500, 700),
            0, .99, 'lines', '万元', [], [], fragment_uid))
        row = len(table.rows)
        raw = definition['value']
        page = document[page_number - 1]
        page.insert_text((72, 72 + index * 20), f'ITEM_{index} {raw}')
        rect = list(page.search_for(raw))[-1]
        box = tuple(rect)
        table.rows.append([f'ITEM_{index}', raw])
        cell = ParsedCell(row, 1, raw, raw, float(raw), page_number, box,
                          False, .99, '万元', 'lines')
        table.cells.append(cell)
        evidence = EvidenceRef('pending', page_number,
                               source_cell_id=f'{fragment_uid}:r{row}c1', quote=raw,
                               bbox=box, source_method='native_text', location_level='exact_cell')
        facts.append(FiscalFact(
            fact_uid=f'fact-{index}', indicator=definition.get('indicator', 'total_actual'),
            raw_value=raw, unit='万元', normalized_value=Decimal(raw),
            org_name=definition.get('org_name', '甲局'), fiscal_year=2025, period='本年',
            budget_stage=definition.get('budget_stage', '决算'),
            subject_code=definition.get('subject_code'), subject_name=definition.get('subject_name', definition.get('subject_code') or '合计'),
            table_code=table_code, instance_uid=instance_uid, row_order=row,
            recognition_status='unverified', extra_dims={'evidence_refs': [evidence.to_dict()]}))
        if instance_uid not in instances:
            instances[instance_uid] = LogicalTableInstance(
                instance_uid, table_code, 'Original table', [], page_number, page_number,
                org_name=definition.get('org_name', '甲局'), fiscal_year=2025)
        instance = instances[instance_uid]
        if fragment_uid not in instance.fragment_uids:
            instance.fragment_uids.append(fragment_uid)
        instance.page_end = max(instance.page_end, page_number)
    pdf_path = tmp_path / 'operands.pdf'
    document.save(pdf_path)
    pages = [page.get_text() for page in document]
    document.close()
    import hashlib
    file_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    for fact in facts:
        ref = fact.extra_dims['evidence_refs'][0]
        ref['file_hash'] = file_hash
        ref['evidence_id'] = EvidenceRef.from_dict(ref).evidence_id
        fact.evidence_ids = [ref['evidence_id']]
    artifact = ParseArtifact(
        source_file_hash=file_hash, parse_run_uid='run',
        table_fragments=list(fragments.values()), physical_fragments=list(fragments.values()),
        logical_tables=list(instances.values()))
    context = FactContext(facts, file_hash=file_hash, parse_run_uid='run', org_name='甲局',
                          fiscal_year=2025, report_kind='final', budget_stage='决算')
    return pdf_path, pages, artifact, context


def operand(fact, *, role='child', relation_id='relation-1', use_uid=False):
    ref = {'operand_id': fact.fact_uid, 'relation_id': relation_id, 'role': role,
           'table_code': fact.table_code, 'indicator': fact.indicator,
           'budget_stage': fact.budget_stage, 'value': str(fact.normalized_value), 'value_unit': '万元'}
    if fact.subject_code is not None:
        ref['subject_code'] = fact.subject_code
    else:
        ref['subject_name'] = fact.subject_name
    if use_uid:
        ref['fact_uid'] = fact.fact_uid
    return ref


def finding(refs, *, rule='V33-120', extra_location=None):
    return {'id': 'rule-test', 'source': 'rule', 'rule_id': rule, 'severity': 'info',
            'title': 'Arithmetic relation', 'message': 'Calculated summary, not original text',
            'formula': 'parent = sum(children)', 'location': {'page': 1, 'operand_refs': refs, **(extra_location or {})},
            'evidence': [{'page': 1, 'text': 'Calculated amount: 100.00'}]}


def bind(item, pages, artifact, context, *, fact_uids=None, version=None):
    return bind_rule_sources(
        {'rule_findings': [item]}, page_texts=pages, source_file_hash=context.file_hash,
        artifact=artifact, fact_context=context,
        consumption=[{'rule_id': item['rule_id'], 'fact_version': version or context.version_token,
                      'fact_uids': fact_uids if fact_uids is not None else [fact.fact_uid for fact in context.facts]}])


def test_income_balance_keeps_zero_operands_and_original_quotes(tmp_path):
    _, pages, artifact, context = source(tmp_path, [
        {'table_code': T1, 'indicator': 'income_actual', 'subject_name': name, 'value': value}
        for name, value in [('总计', '100.00'), ('本年收入合计', '90.01'),
                            ('使用非财政拨款结余', '0.00'), ('年初结转和结余', '10.00')]])
    item = finding([operand(fact) for fact in context.facts], rule='V33-115')
    assert bind(item, pages, artifact, context)['bound_rule_findings'] == 1
    assert len(item['evidence']) == 4
    assert [ref['quote'] for ref in item['evidence']] == ['100.00', '90.01', '0.00', '10.00']
    assert item['formula'] == 'parent = sum(children)'
    assert item['evidence_explanation'][0]['text'].startswith('Calculated amount')
    assert evaluate_finding_evidence(item, page_texts=pages, document_page_count=1,
                                    source_file_hash=context.file_hash)[0]


def test_pooled_table_operands_keep_each_physical_page_and_bbox(tmp_path):
    pdf_path, pages, artifact, context = source(tmp_path, [
        {'table_code': T3, 'subject_name': '合计', 'value': '100.00'},
        {'table_code': T3, 'subject_code': '208', 'value': '100.01', 'page': 2},
        {'table_code': T5, 'subject_name': '合计', 'value': '100.00', 'page': 3},
        {'table_code': T5, 'subject_code': '208', 'value': '100.01', 'page': 4}])
    item = finding([operand(fact, role='parent' if index % 2 == 0 else 'child',
                            relation_id=f'relation-{index // 2}') for index, fact in enumerate(context.facts)])
    assert bind(item, pages, artifact, context)['bound_rule_findings'] == 1
    assert item['location']['pages'] == [1, 2, 3, 4]
    assert [ref['physical_page'] for ref in item['evidence']] == [1, 2, 3, 4]
    assert len({ref['source_cell_id'] for ref in item['evidence']}) == 4
    result = {'rule_findings': [item]}
    assert verify_bound_rule_locations(result, source_pdf_path=str(pdf_path))['checked_rule_findings'] == 1
    assert all(ref['verification_status'] == 'verified' and ref.get('bbox') for ref in item['evidence'])
    assert [ref['page'] for ref in item['location']['table_refs']] == [1, 2, 3, 4]
    assert all(ref['evidence_refs'][0].get('bbox') for ref in item['location']['operand_refs'])
    assert item['fact_version'] == context.version_token
    assert evaluate_finding_evidence(item, page_texts=pages, document_page_count=4,
                                    source_file_hash=context.file_hash)[0]


@pytest.mark.parametrize('problem', ['ambiguous_instance', 'missing_operand', 'old_version', 'old_declared_version', 'wrong_stage',
                                     'numeric_only', 'wrong_hash', 'wrong_cell', 'wrong_bbox', 'blocked_operand'])
def test_incomplete_or_wrong_identity_is_never_partially_bound(tmp_path, problem):
    _, pages, artifact, context = source(tmp_path, [
        {'subject_name': '合计', 'value': '100.00'}, {'subject_code': '208', 'value': '100.01'}])
    item = finding([operand(fact) for fact in context.facts])
    kwargs = {}
    if problem == 'ambiguous_instance':
        duplicate = deepcopy(context.facts[0])
        duplicate.fact_uid, duplicate.instance_uid = 'duplicate', 'other-instance'
        context.facts.append(duplicate)
    elif problem == 'missing_operand':
        kwargs['fact_uids'] = ['fact-0']
    elif problem == 'old_version':
        kwargs['version'] = 'old-version'
    elif problem == 'old_declared_version':
        item['location']['fact_context_version_token'] = 'old-version'
    elif problem == 'wrong_stage':
        item['location']['operand_refs'][0]['budget_stage'] = '年初预算'
    elif problem == 'numeric_only':
        item['location']['operand_refs'][0] = {'value': '100.00'}
    elif problem == 'wrong_hash':
        context.facts[0].extra_dims['evidence_refs'][0]['file_hash'] = 'b' * 64
    elif problem == 'wrong_cell':
        context.facts[0].extra_dims['evidence_refs'][0]['source_cell_id'] = 'unknown-cell'
    elif problem == 'wrong_bbox':
        context.facts[0].extra_dims['evidence_refs'][0]['bbox'] = [0, 0, 10, 10]
    elif problem == 'blocked_operand':
        context.facts[0].recognition_status = 'ambiguous'
    assert bind(item, pages, artifact, context, **kwargs)['bound_rule_findings'] == 0
    assert item['source_binding_status'] == 'unresolved'
    assert item['severity'] == 'manual_review'
    assert not item['fact_ids']
    assert not evaluate_finding_evidence(item, page_texts=pages, document_page_count=1,
                                         source_file_hash=context.file_hash)[0]


def test_equal_amounts_from_another_owner_do_not_supply_an_operand(tmp_path):
    _, pages, artifact, context = source(tmp_path, [
        {'subject_name': '合计', 'value': '100.00', 'org_name': '甲局'},
        {'subject_name': '合计', 'value': '100.00', 'org_name': '乙局', 'instance_uid': 'other'}])
    item = finding([operand(context.facts[0])])
    assert bind(item, pages, artifact, context)['bound_rule_findings'] == 1
    assert item['fact_ids'] == ['fact-0']
    other = finding([operand(context.facts[1], use_uid=True)])
    assert bind(other, pages, artifact, context)['bound_rule_findings'] == 0


def test_correction_changes_current_operand_without_rewriting_printed_quote(tmp_path):
    _, pages, artifact, original = source(tmp_path, [{'subject_name': '合计', 'value': '100.00'}])
    context = apply_fact_overlay(original, [FactOverlay('fact-0', normalized_value=Decimal('105.00'), reason='人工确认')])
    item = finding([operand(context.facts[0], use_uid=True)])
    assert bind(item, pages, artifact, context)['bound_rule_findings'] == 1
    assert item['evidence'][0]['quote'] == '100.00'
    assert item['evidence'][0]['current_value'] == '105.00'
    assert item['evidence'][0]['fact_correction']['reason'] == '人工确认'
    stale = finding([operand(original.facts[0], use_uid=True)])
    assert bind(stale, pages, artifact, context)['bound_rule_findings'] == 0
    assert stale['source_binding_errors'] == ['operand#0:operand_value_not_current']


def test_literal_narrative_span_is_retained_alongside_current_operand(tmp_path):
    pdf_path, pages, artifact, context = source(tmp_path, [{'subject_name': '合计', 'value': '100.00'}])
    claim = 'ITEM_0 100.00'
    item = finding([operand(context.facts[0], use_uid=True)], extra_location={
        'table_refs': [{'page': 1, 'span': claim, 'role': 'narrative'}]})
    assert bind(item, pages, artifact, context)['bound_rule_findings'] == 1
    assert [ref['quote'] for ref in item['evidence']] == ['100.00', claim]
    verify_bound_rule_locations({'rule_findings': [item]}, source_pdf_path=str(pdf_path))
    assert item['location']['table_refs'][1]['quote'] == claim
    assert item['evidence'][1]['source_block_id'].startswith('native:p1:')
    assert evaluate_finding_evidence(item, page_texts=pages, document_page_count=1,
                                    source_file_hash=context.file_hash)[0]
    false_claim = finding([operand(context.facts[0])], extra_location={
        'table_refs': [{'page': 1, 'span': 'Calculated summary 105.00'}]})
    assert bind(false_claim, pages, artifact, context)['bound_rule_findings'] == 0


def test_selected_bbox_uses_its_own_quote_instead_of_first_evidence(tmp_path):
    pdf_path, pages, artifact, context = source(tmp_path, [
        {'subject_name': '合计', 'value': '100.00'}, {'subject_code': '208', 'value': '90.00', 'page': 2}])
    item = finding([operand(fact) for fact in context.facts])
    bind(item, pages, artifact, context)
    item['evidence'][0].pop('bbox')
    item['evidence'][0]['location_level'] = 'region'
    issue = IssueItem(**{key: value for key, value in item.items()
                         if key in {'id', 'source', 'rule_id', 'severity', 'title', 'message', 'evidence', 'location'}})
    locator = PDFBBoxLocator(str(pdf_path))
    try:
        located = locator.locate(issue)
    finally:
        locator.close()
    assert located.page_number == 2
    assert located.bbox == item['evidence'][1]['bbox']
    assert located.verification_status == 'verified'
