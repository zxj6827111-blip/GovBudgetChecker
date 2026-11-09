"""实际双模式链：引擎适配、同源合并后，缺表原文仍须通过证据门才成为正式问题。"""
import hashlib

import pytest

from src.engine.native_issue_dedup import deduplicate_native_issues
from src.engine.rules_v33 import Issue, build_document
from src.schemas.review_report import build_review_report
from src.services.engine_rule_runner import EngineRuleRunner
from src.services.evidence_guard import apply_evidence_completeness
from src.services.rule_source_binding import bind_rule_sources


PAGES = ['甲局2025年度决算', '目录\n三、支出决算表', '收入支出决算总表\n本年支出合计10.00']


def report_for(primary_refs):
    doc = build_document('unit.pdf', PAGES, [[], [], []], 0)
    primary = Issue(rule='V33-002', severity='error', message='缺失表：支出决算表',
                    location={'table': '支出决算表', 'page': 2, 'table_refs': primary_refs})
    dependent = Issue(rule='V33-201', severity='error', message='T3(支出决算表)缺失，无法与T1支出合计(10.00)进行勾稽',
                      location={'table': '收入支出决算总表 / 支出决算表', 'page': 3, 'table_refs': [
                          {'role': 'T1', 'table': '收入支出决算总表', 'page': 3, 'span': '本年支出合计10.00'},
                          {'role': 'T3', 'table': '支出决算表', 'row': '合计'}]})
    runner = EngineRuleRunner()
    # 经过真实 IssueItem 适配后，单条原文引用还没有证据文本，必须由后续绑定认证。
    findings = [runner._issue_to_finding(issue, document=doc) for issue in [primary, dependent]]
    merged = deduplicate_native_issues(findings, doc)
    assert len(merged) == 1
    # 第二次合并也不能把附注引用重新混入主条，兼容多层适配。
    merged = deduplicate_native_issues(merged, doc)
    result = {'rule_findings': [item.model_dump(mode='json') for item in merged], 'meta': {}}
    bind_rule_sources(result, page_texts=PAGES, source_file_hash=hashlib.sha256(b'pdf').hexdigest())
    apply_evidence_completeness(result, document_page_count=3, page_texts=PAGES,
                               source_file_hash=hashlib.sha256(b'pdf').hexdigest())
    report = build_review_report({'job_id': 'unit', 'status': 'review_required', 'result': result})
    return result['rule_findings'][0], report


def test_dual_missing_table_stays_formal_after_merging_dependent_check():
    finding, report = report_for([{'page': 2, 'span': '三、支出决算表', 'role': '目录所列表名', 'table': '支出决算表'}])
    assert [item['rule_id'] for item in report['issues']] == ['V33-002']
    assert finding['evidence_verification']['source_identity_complete']
    assert finding['evidence'][0]['quote'] == '三、支出决算表'
    assert [item['rule_id'] for item in finding['location']['related_findings']] == ['V33-002', 'V33-201']


@pytest.mark.parametrize('refs', [[], [{'page': 2, 'span': '原件不存在的缺表证据'}]])
def test_missing_primary_proof_cannot_borrow_valid_dependent_quote(refs):
    finding, report = report_for(refs)
    assert report['issues'] == []
    assert not finding['evidence_verification']['source_identity_complete']
    assert any(item.get('record_type') == 'finding_review_candidate' for item in report['manual_review'])
