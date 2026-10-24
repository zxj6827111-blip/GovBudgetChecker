"""不同资金的已确认分项可组成财政拨款合计，其他身份维度仍须相同。"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext
from src.engine.native_table_arithmetic import check_fiscal_grant_arithmetic, check_fund_zero_disclosures
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33119_FiscalTotalCheck, R33122_EmptyTableCheck
from src.schemas.parse_artifact import FiscalFact


def document():
    terms = [('expenditure_actual', 'fiscal_appropriation', '10'),
             ('general_public_budget', 'general_public_budget', '10'),
             ('government_fund_budget', 'government_fund', '0'),
             ('state_capital_budget', 'state_owned', '0')]
    facts = [FiscalFact(fact_uid=f'f-{i}', indicator=role, raw_value=value, unit='万元',
                        normalized_value=Decimal(value), org_name='甲中心', fiscal_year=2025,
                        period='本年', fund_scope=fund, budget_stage='决算', instance_uid='table',
                        table_code='FIN_04_fiscal_grant_total', row_order=1, subject_code='201',
                        subject_name='一般公共服务支出', recognition_status='unverified',
                        extra_dims={'org_scope_pending': True, 'evidence_refs': []})
             for i, (role, fund, value) in enumerate(terms)]
    return SimpleNamespace(fact_context=FactContext(facts=facts, org_name='甲中心', fiscal_year=2025,
                                                    file_hash='a' * 64, parse_run_uid='run'),
                           parse_artifact=SimpleNamespace(source_file_hash='a' * 64, parse_run_uid='run'))


def test_explicit_zero_fund_parts_execute_without_faking_a_single_fund_scope():
    doc = document()
    assert check_fiscal_grant_arithmetic(R33119_FiscalTotalCheck(), doc) == []
    assert doc.fact_receipts[0]['status'] == 'pass'


@pytest.mark.parametrize('problem', ['fund_scope', 'instance_uid', 'fiscal_year', 'missing'])
def test_fund_formula_keeps_other_identity_and_operand_gates(problem):
    doc = document()
    if problem == 'missing':
        doc.fact_context.facts.pop()
    else:
        setattr(doc.fact_context.facts[-1], problem, {'fund_scope': 'all_funds', 'instance_uid': 'foreign',
                                                     'fiscal_year': 2024}[problem])
    with pytest.raises(RuleDeferred):
        check_fiscal_grant_arithmetic(R33119_FiscalTotalCheck(), doc)


def fund_document(monkeypatch):
    from copy import deepcopy
    from src.engine import fact_table_projection
    # 此测试只验证公式选取；原格投影和缺列阻断由集成/取数测试覆盖。
    monkeypatch.setattr(fact_table_projection, 'fact_bound_table_rows', lambda *args, **kwargs: [['已验证原格']])
    doc = document()
    for role, fund, code in [('government_fund_budget', 'government_fund', 'FIN_08_gov_fund'),
                             ('state_capital_budget', 'state_owned', 'FIN_09_state_capital')]:
        source = next(f for f in doc.fact_context.facts if f.indicator == role)
        target = deepcopy(source)
        target.fact_uid, target.indicator, target.table_code, target.instance_uid = code, 'total_actual', code, code
        target.fund_scope = fund
        doc.fact_context.facts.append(target)
    return doc


def test_empty_fund_formula_uses_named_facts_and_preserves_known_nonzero(monkeypatch):
    doc = fund_document(monkeypatch)
    assert check_fund_zero_disclosures(R33122_EmptyTableCheck(), doc) == []
    target = next(f for f in doc.fact_context.facts if f.table_code == 'FIN_08_gov_fund')
    target.normalized_value = Decimal('100')
    findings = check_fund_zero_disclosures(R33122_EmptyTableCheck(), doc)
    assert len(findings) == 1 and findings[0].severity == 'error'


def test_unmapped_fund_column_never_means_zero(monkeypatch):
    doc = fund_document(monkeypatch)
    doc.fact_context.facts = [f for f in doc.fact_context.facts if f.indicator != 'government_fund_budget']
    with pytest.raises(RuleDeferred, match='缺少明确可用数值'):
        check_fund_zero_disclosures(R33122_EmptyTableCheck(), doc)
