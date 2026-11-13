from types import SimpleNamespace
from unittest.mock import patch
import os
import sys
from pathlib import Path

SNAP = Path(os.environ.get('GBC_REVIEW_CODE_ROOT', str(Path(__file__).parent / 'code_f76f741b')))
sys.path.insert(0, str(SNAP))

from src.engine.rules_v33 import Document, R33227_Narrative5_T5_NameConsistency
from src.engine.checklist_identity_rules import CMM018_ScopeTerminology
from src.schemas.material_detail import CoverageObligationItem
from src.services.obligation_finding_coverage import obligation_covered_by_finding
from src.services.review_lifecycle_service import blocking_coverage_obligations


def doc(texts, scope='unit', source='cover'):
    d = Document('isolated.pdf', len(texts), 0, texts, [[] for _ in texts],
                 [None for _ in texts], [[] for _ in texts], report_kind='final')
    d.document_profile = SimpleNamespace(writing_scope={
        'status': 'confirmed', 'value': scope, 'source': source,
    })
    return d


def test_single_opposite_scope_caption_must_be_reported():
    d = doc(['2024年度单位决算', '本表反映部门本年度收支决算情况。'])
    issues = CMM018_ScopeTerminology().apply(d)
    assert issues, 'R21 has no four-occurrence exemption; a single opposite table note is a defect'


def test_four_opposite_scope_captions_positive_control():
    d = doc(['2024年度单位决算'] + ['本表反映部门本年度收支决算情况。'] * 4)
    assert CMM018_ScopeTerminology().apply(d)


def test_authorized_scope_confirmation_has_priority_over_auto_cover():
    d = doc(['2024年度决算'] + ['本表反映部门本年度收支决算情况。'] * 4,
            scope='department', source='human_confirmation')
    assert not CMM018_ScopeTerminology().apply(d), 'Automatic cover scope must not override confirmed human scope'


def test_unit_finding_from_v33222_must_not_close_unfinished_amount_check():
    item = CoverageObligationItem(
        obligation_id='OBL-TXT-T5', group_id='TABLE_TEXT', title='功能分类金额核对',
        status='insufficient_data', reason='insufficient_data',
        detail='说明中的“住房公积金”金额无法绑定', checkers=['V33-222', 'V33-227'],
        blocks_gate=True, legacy_coverage_terms=['住房公积金'])
    row = {'rule_findings': [{
        'id': 'unit-error', 'rule_id': 'V33-222', 'tags': ['V33-222'],
        'severity': 'warn', 'source': 'rule',
        'message': '住房公积金金额单位口径错误：原文444.71元折合0.044471万元，表内444.71万元。',
    }]}
    assert not obligation_covered_by_finding(item, row, visible_issue_ids={'unit-error'})
    assert blocking_coverage_obligations([item], row, visible_issue_ids={'unit-error'}) == [item]


def test_real_name_suffix_difference_is_not_automatically_table_truncation():
    d = doc(['2024年度单位决算', '住房公积金支出（项），支出决算为100.00万元。'])
    entry = {'name': '住房公积金', 'level': '项', 'code_display': '221-02-01', 'row_text': '2210201住房公积金'}
    mention = {'name': '住房公积金支出', 'page': 2, 'level': '项',
               'source_span': d.page_texts[1], 'snippet': d.page_texts[1]}
    with patch('src.engine.rules_v33._ensure_table_anchors'), \
         patch('src.engine.rules_v33._get_first_anchor_page', return_value=1), \
         patch('src.engine.rules_v33._get_table_rows', return_value=[['2210201', '住房公积金', '100.00']]), \
         patch('src.engine.rules_v33._extract_functional_name_index', return_value={'2210201': entry}), \
         patch('src.engine.rules_v33._extract_final_functional_narrative_mentions', return_value={'2210201': [mention]}):
        issues = R33227_Narrative5_T5_NameConsistency().apply(d)
    assert issues, 'A correctly bound suffix difference requires a low finding; truncation needs independent evidence'
