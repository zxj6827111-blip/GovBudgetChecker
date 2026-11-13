from copy import deepcopy
from decimal import Decimal

import pytest

from src.engine.native_empty_operands import blank_term,independent_blank_zero
from src.engine.native_narrative_checks import _Text
from src.engine.native_table_text import compare
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33224_Narrative7_T7
from tests.remediation.test_shiquan_three_public_policy import native_reception

pytestmark = pytest.mark.usefixtures('approved_test_blank_convention')


def test_positive_narrative_against_certified_blank_is_a_material_conflict_not_missing_data():
    doc = native_reception()
    original = deepcopy(doc.fact_context.to_dict())
    term = blank_term(doc,'FIN_07_three_public','reception',code='total')
    assert independent_blank_zero(doc,term)
    text = _Text.from_pages(['公务接待费支出决算1.96万元。'])
    issues = compare(R33224_Narrative7_T7(),doc,text,0,len(text.compact),Decimal('1.96'),term,
                     'reception:positive','公务接待费','三公说明')
    assert len(issues) == 1 and '原文1.96万元，表内0.00万元' in issues[0].message
    assert issues[0].location['native_blank_sources'][0]['source_cell_id']
    assert doc.fact_receipts[-1]['status'] == 'fail'
    assert doc.fact_receipts[-1]['operand_fact_ids'] == []
    assert doc.fact_receipts[-1]['cell_observations'][0]['raw_text'] == ''
    assert doc.fact_context.to_dict() == original


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
