from types import SimpleNamespace

import pytest

from src.engine.common_rules import CMM005_ComparativeNarrativeLogic
from src.engine.rule_identity import rule_identity_requirements
from src.engine.rule_outcome import RuleDeferred


def test_pending_scope_keeps_literal_template_problem_but_blocks_year_comparison():
    doc = SimpleNamespace(page_texts=['本年度减增加。业务费为0万元，却写比上年增加1万元。'],
                          fact_context=SimpleNamespace(facts=[SimpleNamespace(extra_dims={'org_scope_pending': True})]))
    with pytest.raises(RuleDeferred, match='汇总口径待确认') as caught:
        with rule_identity_requirements(doc, 'CMM-005'):
            pytest.fail('同比比较仍然需要口径，不能进入执行')
    findings = caught.value.partial_issues
    assert any('减增加' in issue.message and issue.location['page'] == 1 for issue in findings)
    assert all(issue.severity != 'error' for issue in findings)
    assert not any('同比口径矛盾' in issue.message for issue in findings)


def test_template_path_is_shared_with_regular_rule_and_does_not_duplicate():
    doc = SimpleNamespace(page_texts=['本年度减增加。'])
    rule = CMM005_ComparativeNarrativeLogic()
    regular = rule.apply(doc)
    independent = rule.scope_independent_issues(doc)
    assert len(regular) == len(independent) == 1
    assert regular[0].message == independent[0].message and regular[0].evidence_text == independent[0].evidence_text
