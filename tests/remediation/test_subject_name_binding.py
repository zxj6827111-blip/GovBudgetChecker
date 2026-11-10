"""名称定位不等于名称正确：原件身份、金额实算、歧义与证据负控。"""
from copy import deepcopy
from decimal import Decimal
import hashlib
from pathlib import Path

import pdfplumber
import pytest

from src.engine.native_final_disclosure import _hierarchy_fact
from src.engine.rules_v33 import _tolerant_functional_item_codes, R33222_Narrative5_T5, R33227_Narrative5_T5_NameConsistency
from src.services.fiscal_subject_names import subject_name_candidates, subject_variant_key
from tests.remediation.test_native_parent_name_order import document
from tests.remediation.test_r22_a2_identity_chain import _item_match


@pytest.mark.parametrize('name,code', [('行政运行费', '2120101'), ('事业运行费', '2120150')])
def test_exact_variant_beats_similar_short_subject(name, code):
    entries = {key: dict(level='项', name=value, section_name='某款')
               for key, value in [('2120101', '行政运行'), ('2120150', '事业运行')]}
    assert _tolerant_functional_item_codes(entries, '某款', name) == [code]


def test_literal_candidate_wins_but_equal_strength_ambiguity_is_preserved():
    names = {'1': '行政运行', '2': '行政运行费'}
    assert subject_name_candidates('行政运行费', names) == ['2']
    assert subject_name_candidates('行政运行费用', names) == ['1', '2']
    assert subject_variant_key('其他支出') != subject_variant_key('其他')
    assert subject_variant_key('公务用车购置') != subject_variant_key('公务用车运行维护')


def test_same_parent_variant_beats_literal_name_in_other_parent():
    entries = {'2120101': dict(level='项', name='行政运行', section_name='甲款'),
               '2120201': dict(level='项', name='行政运行费', section_name='乙款')}
    assert _tolerant_functional_item_codes(entries, '甲款', '行政运行费') == ['2120101']


def test_budget_name_locator_uses_same_unique_tiers_without_relaxing_amount_binding():
    from src.engine.budget_rules import _resolve_plain_functional_mention
    entries = {'212': dict(name='城乡社区支出', level='类'),
               '21201': dict(name='城乡社区管理事务', level='款'),
               '2120101': dict(name='行政运行', level='项'),
               '2120150': dict(name='事业运行', level='项')}
    args = entries, '城乡社区支出', '城乡社区管理事务', '行政运行费'
    assert _resolve_plain_functional_mention(*args, name_check=True) == '2120101'
    assert _resolve_plain_functional_mention(*args) is None
    entries['2120102'] = dict(name='行政运行支出', level='项')
    assert _resolve_plain_functional_mention(*args, name_check=True) is None


def test_budget_name_locator_preserves_existing_short_class_and_all_level_differences(monkeypatch):
    from src.engine import budget_rules as target
    entries = {'205': dict(name='教育支出', level='类'),
               '20502': dict(name='普通教育', level='款'),
               '2050202': dict(name='小学教育', level='项')}
    assert target._resolve_plain_functional_mention(entries, '教育', '普通教育', '小学教育', name_check=True) == '2050202'
    monkeypatch.setattr(target, '_candidate_budget_explanation_pages',
                        lambda *_: [(3, '教育（类）普通教育（款）小学教育（项）100.00万元。')])
    mentions = target._extract_budget_functional_narrative_mentions(None, {}, name_index=entries)
    assert {code: values[0]['name'] for code, values in mentions.items()} == {
        '205': '教育', '20502': '普通教育', '2050202': '小学教育'}


@pytest.mark.parametrize('original,narrative', [
    ('行政运行', '行政运行费'), ('行政运行支出', '行政运行决算'),
    ('其他城乡社区公共设施支出', '其它城乡社区公共设施支出'),
])
def test_variant_binds_only_with_original_parent_and_code_chain(original, narrative):
    doc, facts = document(extra=(('2120101', original), ('2120150', '事业运行')))
    before = doc.fact_context.to_dict()
    sources = []
    fact, error = _hierarchy_fact(doc, facts, _item_match('城乡社区支出', '城乡社区管理事务', narrative), aliases=sources)
    assert error is None and fact.subject_code == '2120101'
    assert any(source.get('basis') == 'unique_native_code_parent_and_name_variant' for source in sources)
    assert doc.fact_context.to_dict() == before


@pytest.mark.parametrize('defect', ['unparsed_candidate', 'same_variant', 'wrong_parent', 'foreign_year',
                                   'foreign_run', 'ocr', 'source_label_conflict', 'same_amount_typo',
                                   'low_confidence', 'foreign_fund', 'conflicting_facts'])
def test_name_variant_never_bypasses_identity_or_evidence(defect):
    extra = [('2120101', '行政运行'), ('2120150', '事业运行')]
    if defect == 'same_variant': extra.append(('2120102', '行政运行支出'))
    doc, facts = document(extra=extra)
    parent, name = '城乡社区管理事务', '行政运行费'
    if defect == 'unparsed_candidate': doc.page_texts[0] += '\n2120102 行政运行支出 100.00'
    elif defect == 'wrong_parent': parent = '其他城乡社区支出'
    elif defect == 'foreign_year': facts[-2].fiscal_year -= 1
    elif defect == 'foreign_run': doc.parse_artifact.parse_run_uid = 'old'
    elif defect == 'ocr': doc.parse_artifact.table_fragments[0].cells[-3].ocr_provenance = {'candidate': 'scan'}
    elif defect == 'source_label_conflict': doc.parse_artifact.source_tokens[-3].raw_text = '其他支出'
    elif defect == 'same_amount_typo': name = '行政事业运行'
    elif defect == 'low_confidence':
        facts[-2].confidence = 0.1
        facts[-2].recognition_status = 'low_confidence'
    elif defect == 'foreign_fund': facts[-2].fund_scope = 'government_fund'
    elif defect == 'conflicting_facts':
        duplicate = deepcopy(facts[-2])
        duplicate.fact_uid += ':duplicate'
        duplicate.normalized_value += Decimal('1')
        facts.append(duplicate)
    sources = []
    fact, error = _hierarchy_fact(doc, facts, _item_match('城乡社区支出', parent, name), aliases=sources)
    assert fact is None and error
    assert not any(s.get('basis') == 'unique_native_code_parent_and_name_variant' for s in sources)


@pytest.fixture(scope='module')
def original_prison_document():
    from src.services.pdf_parser import PDFParser
    from src.engine.fact_context import build_fact_context_from_artifact
    from src.engine.rules_v33 import build_document
    path = Path(__file__).parents[1] / 'fixtures/native_subject_names/prison25.pdf'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == '7d605ab111ad043eb88863f3208a845545e0ac7b681f3ad408b2fd7a7eab763b'
    artifact = PDFParser(None).build_parse_artifact(str(path))
    with pdfplumber.open(path) as pdf:
        texts = [page.extract_text() or '' for page in pdf.pages]
    tables = [[] for _ in texts]
    for table in artifact.physical_fragments:
        tables[table.page_number - 1].append(table.rows)
    doc = build_document(str(path), texts, tables, path.stat().st_size)
    doc.parse_artifact = artifact
    doc.fact_context = build_fact_context_from_artifact(artifact, org_name='上海市监狱管理局',
                                                      fiscal_year=2025, report_kind='final')
    return doc


def test_original_pdf_amounts_execute_and_name_differences_remain(original_prison_document):
    doc = deepcopy(original_prison_document)
    before = doc.fact_context.to_dict()
    R33222_Narrative5_T5().apply(doc)
    receipts = [r for r in doc.fact_receipts if any(s.get('basis') == 'unique_native_code_parent_and_name_variant'
                                                  for s in r.get('disclosure_sources', []))]
    assert len(receipts) == 2 and all(r['status'] == 'pass' and r['operand_fact_ids'] for r in receipts)
    issues = R33227_Narrative5_T5_NameConsistency().apply(doc)
    for code, name in [('2040701', '行政运行费'), ('2040750', '事业运行费')]:
        assert any(i.location.get('code') == code and i.location.get('actual_name') == name for i in issues)
    assert doc.fact_context.to_dict() == before


@pytest.mark.parametrize('code', ['2040701', '2040750'])
def test_original_pdf_amount_mutation_still_fails(original_prison_document, code):
    doc = deepcopy(original_prison_document)
    fact = next(f for f in doc.fact_context.facts if f.table_code == 'FIN_05_general_public_expenditure'
                and f.subject_code == code and f.indicator == 'total_actual')
    fact.normalized_value += Decimal('0.02')
    doc.fact_context.version_token = doc.fact_context.compute_version_token()
    issues = R33222_Narrative5_T5().apply(doc)
    assert any(fact.fact_uid in i.fact_ids for i in issues)
    assert any(r['status'] == 'fail' and fact.fact_uid in r['operand_fact_ids'] for r in doc.fact_receipts)


def test_unresolved_name_keeps_gate_and_does_not_claim_original_amount_missing():
    from src.engine.check_obligations import ObligationInstance, _derive_card_summary
    from src.engine.diagnostic_fields import diagnostics_for_texts, render_reason_text
    doc, facts = document(extra=(('2120101', '行政运行'),))
    _, reason = _hierarchy_fact(doc, facts, _item_match('城乡社区支出', '城乡社区管理事务', '行政事业运行'))
    diagnostic, = diagnostics_for_texts(doc, [reason])
    assert diagnostic['reason_codes'] == ['text_binding_failed']
    assert diagnostic['missing_operands'][0]['role'] == '科目名称对应关系'
    text = render_reason_text(reason, diagnostic)
    assert '名称对应位置' in text and '缺数位置' not in text
    instance = ObligationInstance(obligation_id='OBL-TXT-T5', group_id='TABLE_TEXT',
                                  group_title='表文关系', title='名称与金额', status='insufficient_data',
                                  reason='insufficient_data', detail=reason,
                                  missing_operands=tuple(diagnostic['missing_operands']))
    assert instance.is_unresolved
    assert '金额核对尚未完成' in _derive_card_summary(instance)
