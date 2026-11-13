from decimal import Decimal
import pytest
from src.services import issue_workflow_store as workflow
from src.services.review_problem_set import collect_review_problems
from src.services.review_lifecycle_service import _problem_blockers
from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.rules_v33 import build_document
from src.engine.rule_outcome import RuleOutcomeSignal


def test_conflicting_member_decisions_must_remain_unresolved():
    issue = {'id': 'pg-canonical', 'rule_id': 'V33-222', 'severity': 'high',
             'title': '单位错误', 'message': '同源单位问题', 'evidence_status': 'complete',
             'location': {'member_issue_ids': ['member-a', 'member-b']}}
    payload = {'analysis_revision': 'rev-current', 'result': {'rule_findings': [issue]}}
    events = [{'job_id': 'job', 'issue_id': 'member-a', 'analysis_revision': 'rev-current',
               'status': 'confirmed', 'decision_revision': '1'},
              {'job_id': 'job', 'issue_id': 'member-b', 'analysis_revision': 'rev-current',
               'status': 'no_issue', 'decision_revision': '1'}]
    projected = workflow.project_job_issue_decisions('job', payload, _state={'decision_events': events})
    assert projected[0]['status'] == 'needs_review', projected
    problems = collect_review_problems(job_uuid='job', rule_findings=[issue])
    assert _problem_blockers(problems, {x['issue_id']: x['status'] for x in projected})


HEAD = ('（三）一般公共预算财政拨款支出决算具体情况\n'
        '一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：\n')
TAIL = '六、一般公共预算财政拨款基本支出决算情况说明'


def make_doc(body):
    doc = build_document('/tmp/acceptance.pdf', [HEAD + body + TAIL], [[]], 1000)
    doc.report_kind = 'final'
    return doc


def test_missing_unit_must_defer_even_with_only_one_included_item():
    doc = make_doc(
        '1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n'
        '2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算40.00万元，支出决算为40。\n')
    with pytest.raises(RuleOutcomeSignal) as signal:
        R33251_FunctionalDetailCompleteness().apply(doc)
    assert signal.value.status == 'insufficient_data'


def test_conditional_increase_excludes_already_counted_yuan_amount():
    doc = make_doc(
        '1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n'
        '2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算20.00万元，支出决算为179.82元。\n'
        '3、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。年初预算20.00万元，支出决算为10.00万元。\n')
    issues = R33251_FunctionalDetailCompleteness().apply(doc)
    final_issue = next(x for x in issues if x.location.get('unit_normalized_items'))
    expected = Decimal('179.82') - Decimal('179.82') / Decimal('10000')
    assert f'合计将增加{expected}万元' in final_issue.message, final_issue.message
