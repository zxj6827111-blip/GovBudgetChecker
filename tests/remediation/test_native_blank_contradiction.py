from copy import deepcopy
from decimal import Decimal

import pytest

from src.engine.native_empty_operands import blank_term,independent_blank_zero
from src.engine.native_narrative_checks import _Text
from src.engine.native_table_text import compare
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33224_Narrative7_T7
from src.engine.native_issue_dedup import deduplicate_native_issues
from src.engine.pipeline import _issue_to_dict
from src.services.rule_source_binding import bind_rule_sources
from tests.remediation.test_shiquan_three_public_policy import native_reception

pytestmark = pytest.mark.usefixtures('approved_test_blank_convention')


@pytest.mark.parametrize('field',['file_hash','parse_run_uid'])
def test_missing_version_on_both_sides_is_not_a_current_source_identity(field):
    doc=native_reception()
    setattr(doc.fact_context,field,'')
    setattr(doc.parse_artifact,'source_file_hash' if field=='file_hash' else field,'')
    assert blank_term(doc,'FIN_07_three_public','reception',code='total') is None


def test_positive_narrative_against_certified_blank_is_a_material_conflict_not_missing_data():
    doc = native_reception()
    original = deepcopy(doc.fact_context.to_dict())
    doc.page_texts = ['公务接待费支出决算1.96万元。']
    term = blank_term(doc,'FIN_07_three_public','reception',code='total')
    assert independent_blank_zero(doc,term)
    text = _Text.from_pages(doc.page_texts)
    issues = compare(R33224_Narrative7_T7(),doc,text,0,len(text.compact),Decimal('1.96'),term,
                     'reception:positive','公务接待费','三公说明')
    assert len(issues) == 1 and '原文1.96万元，表内0.00万元' in issues[0].message
    assert issues[0].location['native_blank_sources'][0]['source_cell_id']
    assert doc.fact_receipts[-1]['status'] == 'fail'
    assert doc.fact_receipts[-1]['operand_fact_ids'] == []
    assert doc.fact_receipts[-1]['cell_observations'][0]['raw_text'] == ''
    assert doc.fact_context.to_dict() == original
    finding = _issue_to_dict(issues[0],1,doc)
    bind_rule_sources({'issues':{'all':[finding]}},page_texts=doc.page_texts,
                      source_file_hash=doc.fact_context.file_hash,artifact=doc.parse_artifact,
                      fact_context=doc.fact_context)
    assert not finding.get('source_binding_errors')
    assert finding['source_binding_status'] == 'bound'
    duplicate = deepcopy(issues[0])
    duplicate.rule = 'V33-108'
    assert len(deduplicate_native_issues([issues[0],duplicate],doc)) == 1
    duplicate.location['native_blank_sources'][0]['source_cell_id'] += '-unverified'
    assert len(deduplicate_native_issues([issues[0],duplicate],doc)) == 2


@pytest.mark.parametrize('fault',['uncertified','other_source','other_year','narrative_only'])
def test_uncertain_blank_is_never_promoted_to_a_zero_amount(fault):
    doc = native_reception()
    term = blank_term(doc,'FIN_07_three_public','reception',code='total')
    if fault == 'uncertified':
        next(c for c in doc.parse_artifact.table_fragments[0].cells if (c.row_idx,c.col_idx)==(4,11)).native_blank_verified=False
    elif fault == 'other_source':
        doc.parse_artifact.source_file_hash='b'*64
    elif fault == 'other_year':
        doc.fact_context.fiscal_year=2025
    else:
        term.extra_dims['native_blank_observation']['convention']='narrative_explicit_zero'
    assert not independent_blank_zero(doc,term)
    text = _Text.from_pages(['公务接待费支出决算1.96万元。'])
    with pytest.raises(RuleDeferred):
        compare(R33224_Narrative7_T7(),doc,text,0,len(text.compact),Decimal('1.96'),term,
                'reception:positive','公务接待费','三公说明')
    assert doc.fact_receipts[-1]['status'] == 'insufficient_data'


def test_same_blank_field_with_same_claim_in_two_places_is_one_item_with_two_originals():
    doc = native_reception()
    doc.page_texts=['公务接待费支出决算1.96万元。公务接待费支出1.96万元。']
    text = _Text.from_pages(doc.page_texts)
    term = blank_term(doc,'FIN_07_three_public','reception',code='total')
    split = text.compact.index('。') + 1
    first = compare(R33224_Narrative7_T7(),doc,text,0,split,Decimal('1.96'),term,'claim:one','公务接待费','三公说明')[0]
    second = compare(R33224_Narrative7_T7(),doc,text,split,len(text.compact),Decimal('1.96'),term,'claim:two','公务接待费','三公说明')[0]
    grouped = deduplicate_native_issues([first,second],doc)
    assert len(grouped) == 1 and len(grouped[0].location['table_refs']) == 2
    assert len(grouped[0].location['check_relation_ids']) == 2
    finding = _issue_to_dict(grouped[0],1,doc)
    bind_rule_sources({'rule_findings':[finding]},page_texts=doc.page_texts,
                      source_file_hash=doc.fact_context.file_hash,artifact=doc.parse_artifact,
                      fact_context=doc.fact_context)
    assert finding['source_binding_status'] == 'bound' and len(finding['evidence']) == 2
    assert len(finding['location']['native_blank_calculation_refs']) == 2
