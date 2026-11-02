from types import SimpleNamespace

from src.engine.common_rules import CMM005_ComparativeNarrativeLogic
from src.engine.rule_identity import rule_identity_requirements


def test_pending_scope_checks_same_document_template_and_zero_direction():
    doc = SimpleNamespace(page_texts=['本年度减增加。一般公共预算拨款支出预算0万元，比2023年预算增加1万元。'],
                          fact_context=SimpleNamespace(facts=[SimpleNamespace(extra_dims={'org_scope_pending': True})]))
    with rule_identity_requirements(doc, 'CMM-005'):
        findings = CMM005_ComparativeNarrativeLogic().apply(doc)
    assert any('减增加' in issue.message and issue.location['page'] == 1 for issue in findings)
    assert any('同比口径矛盾' in issue.message for issue in findings)


def test_template_path_is_shared_with_regular_rule_and_does_not_duplicate():
    doc = SimpleNamespace(page_texts=['本年度减增加。'])
    rule = CMM005_ComparativeNarrativeLogic()
    regular = rule.apply(doc)
    independent = rule.scope_independent_issues(doc)
    assert len(regular) == len(independent) == 1
    assert regular[0].message == independent[0].message and regular[0].evidence_text == independent[0].evidence_text
