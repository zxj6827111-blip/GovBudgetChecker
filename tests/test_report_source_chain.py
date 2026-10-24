"""生产双模式、跨页层级和说明四证据链的回归。"""
import asyncio
from copy import deepcopy

import pytest

from src.engine.native_issue_dedup import deduplicate_native_issues
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33105_FinGrantTotal_vs_Text, R33120_DetailTableCheck, R33221_Narrative4_T4
from src.schemas.issues import AnalysisConfig, JobContext
from src.services.analyze_dual import DualModeAnalyzer
from src.services.engine_rule_runner import EngineRuleRunner
from src.services.rule_source_binding import _literal_ref_sources, bind_rule_sources, verify_bound_rule_locations
from tests.remediation.test_native_table_checks import document
from tests.remediation.test_real_intake_table_gaps import continuation_doc


def grant_doc(amount='18,108.76'):
    body = ('一、收入支出决算总体情况说明\n'
            '甲局2025年度收入支出总计18,157.33万元。\n'
            '四、财政拨款收入支出决算总体情况说明\n'
            f'甲局2025年度财政拨款收入支出总计{amount}万\n元。\n'
            '五、一般公共预算财政拨款支出决算情况说明\n'
            '一般公共预算支出100万元。')
    return document(body, 'FIN_04_fiscal_grant_total', [
        (None, '总计', 'income_actual', '决算', '18108.76'),
        (None, '总计', 'expenditure_actual', '决算', '18108.76'),
    ])


def test_grant_total_never_uses_same_page_section_one_amount():
    doc = grant_doc()
    assert R33221_Narrative4_T4().apply(doc) == []
    assert doc.fact_receipts[0]['status'] == 'pass'
    assert doc.fact_receipts[0]['operand_fact_ids'] == [doc.fact_context.facts[1].fact_uid]


def test_genuine_grant_mismatch_keeps_statement_and_expense_cell_identity():
    doc = grant_doc('18,000.00')
    first, = R33105_FinGrantTotal_vs_Text().apply(doc)
    second, = R33221_Narrative4_T4().apply(doc)
    assert '财政拨款收入支出总计18,000.00万\n元' in second.location['table_refs'][0]['span']
    assert second.location['operand_refs'][0]['indicator'] == 'expenditure_actual'
    assert second.location['operand_refs'][0]['fact_uid'] == doc.fact_context.facts[1].fact_uid
    assert len(deduplicate_native_issues([first, second], doc)) == 1


def test_missing_section_four_does_not_borrow_section_one():
    doc = grant_doc()
    doc.page_texts[1] = '一、收入支出决算总体情况说明\n收入支出总计18,157.33万元。'
    with pytest.raises(RuleDeferred) as signal:
        R33221_Narrative4_T4().apply(doc)
    assert not signal.value.partial_issues


def test_dual_rule_path_keeps_complete_continuation_and_consumption(monkeypatch, tmp_path):
    original = continuation_doc()
    context = JobContext(job_id='continuation', pdf_path=str(tmp_path / 'original.pdf'),
                         page_texts=['甲单位2025年度部门决算', '续页'],
                         parse_artifact=original.parse_artifact.to_dict(),
                         fact_context=original.fact_context.to_dict(), meta={'report_kind': 'final'})
    analyzer = DualModeAnalyzer()
    monkeypatch.setattr(analyzer.engine_runner, '_select_rule_set', lambda *args: [R33120_DetailTableCheck()])
    monkeypatch.setattr('src.services.analyze_dual.save_snapshot', lambda *args, **kwargs: None)
    response = asyncio.run(analyzer.analyze(context, AnalysisConfig(ai_enabled=False)))
    assert response.meta.get('rule_error') is None
    assert response.rule_findings == []  # The last child is on physical page 2.
    assert response.meta['structured_consumption']
    assert all(item['fact_version'] == original.fact_context.version_token
               for item in response.meta['structured_consumption'])
    assert response.meta['fact_receipts']


def test_explicit_stale_parse_snapshot_cannot_fall_back_to_page_rules():
    original = continuation_doc()
    artifact = deepcopy(original.parse_artifact.to_dict())
    artifact['parse_run_uid'] = 'previous-run'
    context = JobContext(job_id='stale', pdf_path='original.pdf', page_texts=['正文'],
                         parse_artifact=artifact, fact_context=original.fact_context.to_dict())
    with pytest.raises(ValueError, match='版本不一致'):
        asyncio.run(EngineRuleRunner()._prepare_document(context))


def test_literal_cross_page_claim_is_split_without_moving_single_page_evidence():
    pages = ['公务用车支出8.58万', '元，占43.55%。']
    refs = _literal_ref_sources({'page': 2, 'span': '公务用车支出8.58万元，占43.55%'}, pages, 'a'*64)
    assert [ref['physical_page'] for ref in refs] == [1, 2]
    assert refs[0]['quote'] == pages[0]
    assert refs[1]['quote'] == '元，占43.55%'
    assert _literal_ref_sources({'page': 2, 'span': pages[0]}, pages, 'a'*64) == []


def test_bound_original_span_replaces_old_page_only_evidence_button():
    finding = {'id': 'literal', 'source': 'rule', 'rule_id': 'CMM-007',
               'evidence': [{'page': 1, 'text': '本期金额100万元。'}],
               'location': {'page': 1, 'table_refs': [{'page': 1, 'location_level': 'region'}]}}
    bind_rule_sources({'rule_findings': [finding]}, page_texts=['本期金额100万元。'],
                      source_file_hash='a'*64)
    ref, = finding['location']['table_refs']
    assert ref['source_identity_status'] == 'verified'
    assert ref['quote'] == '本期金额100万元。'
    assert ref['source_block_id'] == finding['evidence'][0]['source_block_id']


def test_literal_name_button_uses_new_block_identity_instead_of_old_cell_id(tmp_path):
    import hashlib
    import fitz
    import pdfplumber

    path = tmp_path / 'name.pdf'
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), 'Grain reserve')
        pdf.save(path)
    with pdfplumber.open(path) as pdf:
        text = pdf.pages[0].extract_text()
    finding = {'id': 'name', 'source': 'rule', 'rule_id': 'V33-227',
               'evidence': [{'page': 1, 'text': 'generated comparison'}],
               'location': {'table_refs': [{'page': 1, 'span': 'Grain reserve',
                                           'role': '原表科目名称', 'source_cell_id': 'old:r0c1',
                                           'bbox': [1, 1, 2, 2]}]}}
    result = {'rule_findings': [finding]}
    bind_rule_sources(result, page_texts=[text], source_file_hash=hashlib.sha256(path.read_bytes()).hexdigest())
    verify_bound_rule_locations(result, source_pdf_path=str(path))
    ref, = finding['location']['table_refs']
    assert 'source_cell_id' not in ref
    assert ref['declared_source_cell_id'] == 'old:r0c1'
    assert ref['source_identity_status'] == ref['verification_status'] == 'verified'
    assert ref['bbox'] == finding['evidence'][0]['bbox']
    assert ref['bbox'] != [1, 1, 2, 2]


def test_public_expense_rounding_keeps_amount_cells_despite_neighbor_text_artifact():
    from src.engine.native_table_arithmetic import relation
    from src.engine.rules_v33 import R33117_BasicExpenseClassification

    doc = document('公用经费合计 23,378.84\n302 商品和服务支出 22,465.55\n310 出资本性支出 913.30',
                   'FIN_06_basic_expenditure', [
                       ('total', '公用经费合计', 'total_actual', '决算', '23378.84'),
                       ('302', '商品和服务支出', 'total_actual', '决算', '22465.55'),
                       ('310', '出资本性支出', 'total_actual', '决算', '913.30')])
    for index, fact in enumerate(doc.fact_context.facts):
        fact.extra_dims['evidence_refs'] = [{'physical_page': 2, 'quote': fact.raw_value,
                                           'source_cell_id': f'table:r{index}c2', 'bbox': [10, 20, 30, 40]}]
    issues, missing = relation(R33117_BasicExpenseClassification(), doc, 'public-summary',
                               '公用经费合计', doc.fact_context.facts[0], doc.fact_context.facts[1:], hints=True)
    assert missing == []
    issue, = issues
    assert issue.severity == 'info'
    assert len(issue.location['operand_refs']) == 3
    assert issue.fact_ids == [fact.fact_uid for fact in doc.fact_context.facts]
    assert 'table_refs' not in issue.location
