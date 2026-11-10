"""业务边界验收：不能用未检查、局部列举或漏单位制造人工复算。"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.fact_context import FactContext
from src.engine.native_final_disclosure import check_function_disclosures, check_performance_counts
from src.engine.narrative_amount_units import same_item_final_unit
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import Rule
from src.schemas.parse_artifact import FiscalFact


class Probe(Rule):
    code, severity = 'V33-250', 'warn'


def performance(parts, total=53):
    body = (f'绩效自评的2025年度项目{total}个，平均得分95.55分（其中，'
            + '；'.join(f'绩效评级为“{name}”的项目{count}个' for name, count in parts) + '）。')
    return SimpleNamespace(page_texts=['甲局2025年度部门决算',
        '十、预算绩效管理情况\n' + body], report_kind='final', fiscal_year=2025,
        organization_name='甲局')


@pytest.mark.parametrize('parts,failed', [
    ([('优', 50)], False), ([('优', 54)], True),
    ([('优', 50), ('良', 0), ('合格', 0), ('不合格', 0)], True),
    ([('优', 50), ('良', 2), ('合格', 1), ('不合格', 0)], False),
])
def test_partial_composition_and_complete_composition_have_different_relations(parts, failed):
    doc = performance(parts)
    issues = check_performance_counts(Probe(), doc)
    assert bool(issues) is failed
    receipt = doc.fact_receipts[-1]
    assert receipt['status'] == ('fail' if failed else 'pass')
    assert receipt['disclosure_sources'][0]['page'] == 2
    assert receipt['disclosure_sources'][0]['comparison_basis'] == (
        'complete_sum' if len(parts) == 4 else 'disclosed_lower_bound')


def test_ambiguous_rating_keeps_original_position_and_specific_reason():
    doc = performance([('优', 50), ('优', 1)])
    with pytest.raises(RuleDeferred):
        check_performance_counts(Probe(), doc)
    receipt = doc.fact_receipts[-1]
    assert receipt['reason_codes'] == ['text_binding_failed']
    assert receipt['disclosure_sources'][0]['page'] == 2
    assert 'rule_no_reason' not in receipt['reason_codes']


def detail_document(body):
    return build_document('2025部门决算.pdf', ['甲局2025年度部门决算',
        '五、一般公共预算财政拨款支出决算情况说明\n一般公共预算财政拨款支出4.19万元。\n'
        '（三）一般公共预算财政拨款支出决算具体情况\n'
        '年初预算0.00万元，支出决算4.20万元。\n' + body], [], 0)


ITEM = '1、一般公共服务支出（类）市场监督管理事务（款）其他市场监督管理事务（项）'


def test_missing_literal_unit_is_reported_while_same_item_supported_sum_is_checked():
    doc = detail_document(ITEM + '4.19万元。主要用于电梯建设。年初预算0.00万元，支出决算为4.19。')
    issues = R33251_FunctionalDetailCompleteness().apply(doc)
    issue, = issues
    assert issue.location['defect_kind'] == 'amount_unit_omitted'
    assert issue.location['page'] == 2
    assert issue.location.get('raw_unit') is None
    assert issue.location['unit_basis_sources'][0]['page'] == 2
    final = next(r for r in doc.fact_receipts if r['sub_check_id'] == 'V33-251:final:item_sum')
    assert final['status'] == 'pass'
    assert final['disclosure_sources']
    assert not final['reason_codes']


@pytest.mark.parametrize('body', [
    ITEM + '4.19。年初预算4.19万元，支出决算4.19。',
    ITEM + '4.19万元。上年支出4.19万元，本年支出决算4.19。',
    ITEM + '4.19万元。支出决算4.19元，另记支出决算4.19。',
])
def test_unit_is_not_borrowed_from_budget_prior_year_or_conflicting_final(body):
    assert same_item_final_unit(body, 0, len(body), '4.19', body.rfind('支出决算')) is None


def test_table_text_can_compare_a_unit_supported_by_the_same_item():
    doc = detail_document(ITEM + '4.19万元。主要用于电梯建设。年初预算0.00万元，支出决算为4.19。')
    facts = []
    for code, name in [('total', '合计'), ('201', '一般公共服务支出'),
            ('20138', '市场监督管理事务'), ('2013899', '其他市场监督管理事务')]:
        facts.append(FiscalFact(fact_uid='f:' + code, indicator='total_actual', raw_value='4.19',
            unit='万元', normalized_value=Decimal('4.19'), org_name='甲局', fiscal_year=2025,
            period='本年', budget_stage='决算', subject_code=code, subject_name=name,
            table_code='FIN_05_general_public_expenditure', instance_uid='table',
            fund_scope='general_public_budget'))
    doc.fact_context = FactContext(facts, 'a' * 64, 'run', org_name='甲局', fiscal_year=2025)
    doc.parse_artifact = SimpleNamespace(source_file_hash='a' * 64, parse_run_uid='run')
    assert check_function_disclosures(Probe(), doc) == []
    source = doc.fact_receipts[-1]['disclosure_sources']
    assert any(r.get('raw_unit_missing') for r in source)


def obligation(ledger, key):
    return next(r for r in ledger['instances'] if r['obligation_id'] == key)


@pytest.mark.parametrize('kind,code', [('budget', 'BUD-115'), ('final', 'V33-SG-COMPLETION')])
def test_unknown_amount_does_not_complete_quantity_checks(kind, code):
    from src.engine.three_public_counts import count_issues
    from src.engine.check_obligations import build_obligation_ledger
    rule = Probe()
    rule.code = code
    scope = '公务用车购置费1万元。因公出国费1万元。公务接待费1万元。公务用车保有量0辆。'
    doc = SimpleNamespace(page_texts=['甲局2025年度部门决算', '三公经费说明\n' + scope])
    with pytest.raises(RuleDeferred):
        count_issues(rule, doc, report_kind=kind, scope=scope, section_title='三公经费说明',
                     locate_page=lambda *_: 2, items=('vehicle_purchase',) if kind == 'budget' else None)
    ledger = build_obligation_ledger(None, report_kind=kind,
        rule_execution_summary={'rule_statuses': {code: 'insufficient_data'}}, fact_receipts=doc.fact_receipts)
    assert obligation(ledger, 'OBL-SG-COUNTS')['status'] == 'insufficient_data'
    assert any(r['status'] == 'insufficient_data' and r['obligation_id'] == 'OBL-SG-COUNTS'
               for r in doc.fact_receipts)


def test_missing_quantity_receipt_cannot_be_hidden_by_a_rule_pass():
    from src.engine.check_obligations import build_obligation_ledger
    from src.engine.fact_context import make_fact_receipt
    fields = ['purchase_count', 'overseas_groups', 'overseas_people',
              'reception_batches', 'reception_people', 'vehicle_stock']
    receipts = [make_fact_receipt('V33-SG-COMPLETION', 'three_public:count:' + name,
        status='pass', obligation_id='OBL-SG-COUNTS') for name in fields]
    summary = {'rule_statuses': {'V33-SG-COMPLETION': 'pass'}}
    complete = build_obligation_ledger(None, report_kind='final', rule_execution_summary=summary,
                                      fact_receipts=receipts)
    assert obligation(complete, 'OBL-SG-COUNTS')['status'] == 'completed'
    incomplete = build_obligation_ledger(None, report_kind='final', rule_execution_summary=summary,
                                        fact_receipts=receipts[:-1])
    assert obligation(incomplete, 'OBL-SG-COUNTS')['status'] == 'insufficient_data'


def amount_share_receipts():
    from src.engine.fact_context import make_fact_receipt
    data = []
    for code in ['CMM-001', 'V33-224']:
        child = 'three_public:detail:reception:10'
        data.extend([make_fact_receipt(code, child, status='pass', obligation_id='OBL-SG-TABLE-TEXT'),
            make_fact_receipt(code, 'three_public:amount_scope', status='pass',
                obligation_id='OBL-SG-TABLE-TEXT', required_sub_check_ids=[child])])
    child = 'three_public:share:reception:20'
    data.extend([make_fact_receipt('V33-224', child, status='insufficient_data',
        obligation_id='OBL-SG-SHARE', incomplete_reason='占比缺少可绑定事实'),
        make_fact_receipt('V33-224', 'three_public:percentage_scope', status='insufficient_data',
            obligation_id='OBL-SG-SHARE', required_sub_check_ids=[child])])
    return data


def test_share_gap_does_not_poison_completed_amounts_and_missing_children_still_block():
    from src.engine.check_obligations import build_obligation_ledger, build_check_receipts
    summary = {'rule_statuses': {'V33-108': 'pass', 'CMM-001': 'pass', 'V33-224': 'insufficient_data'}}
    data = amount_share_receipts()
    cover = build_obligation_ledger(None, report_kind='final', rule_execution_summary=summary, fact_receipts=data)
    assert obligation(cover, 'OBL-SG-TABLE-TEXT')['status'] == 'completed'
    assert obligation(cover, 'OBL-SG-SHARE')['status'] == 'insufficient_data'
    projected = build_check_receipts({}, report_kind='final', rule_execution_summary=summary,
                                    obligation_coverage=cover, fact_receipts=data)
    assert not any(r['obligation_id'] == 'OBL-SG-TABLE-TEXT' and ':share:' in r['sub_check_id']
                   for r in projected)
    data.pop(0)
    cover = build_obligation_ledger(None, report_kind='final', rule_execution_summary=summary, fact_receipts=data)
    assert obligation(cover, 'OBL-SG-TABLE-TEXT')['status'] == 'insufficient_data'


def test_alias_decisions_preserve_history_and_conflicts_do_not_complete():
    from src.engine.relation_coverage import canonical_session_decisions
    old = {'obligation_id': 'OBL-TXT-T7', 'decision': 'verified_ok', 'review_session_id': 'current'}
    mapped = canonical_session_decisions([old])
    assert mapped['OBL-SG-TABLE-TEXT']['decision'] == 'verified_ok'
    assert old['obligation_id'] == 'OBL-TXT-T7'
    conflict = canonical_session_decisions([old, {**old, 'obligation_id': 'OBL-SG-TABLE-TEXT',
                                                'decision': 'verified_issue'}])
    assert conflict['OBL-SG-TABLE-TEXT']['decision'] == 'pending'
    assert len(conflict['OBL-SG-TABLE-TEXT']['original_decisions']) == 2


def test_unrelated_horizontal_gap_does_not_hide_a_vertical_real_error():
    from src.engine.native_table_arithmetic import check_expenditure_arithmetic
    facts = []
    for code, total in [('total', '100'), ('201', '100'), ('20101', '90'), ('202', '0')]:
        for indicator, value in [('total_actual', total), ('basic_actual', total),
                ('project_actual', '0'), ('to_superior_actual', '0'),
                ('operational_actual', '0'), ('subsidiary_actual', '0')]:
            if code == '202' and indicator == 'subsidiary_actual':
                continue
            facts.append(FiscalFact(fact_uid=f'{code}:{indicator}', indicator=indicator,
                raw_value=value, normalized_value=Decimal(value), unit='万元',
                subject_code=code, subject_name=code, fiscal_year=2025, org_name='甲局',
                period='本年', budget_stage='决算', table_code='FIN_03_expenditure',
                instance_uid='table'))
    doc = SimpleNamespace(fact_context=FactContext(facts, 'a' * 64, 'run', org_name='甲局', fiscal_year=2025),
        parse_artifact=SimpleNamespace(source_file_hash='a' * 64, parse_run_uid='run'))
    rule = Probe()
    rule.code = 'V33-241'
    with pytest.raises(RuleDeferred) as signal:
        check_expenditure_arithmetic(rule, doc)
    assert signal.value.partial_issues
    assert any(r['sub_check_id'] == 'expenditure:vertical:total_actual:201' and r['status'] == 'fail'
               for r in doc.fact_receipts)
    assert any('202' in r['sub_check_id'] and r['status'] == 'insufficient_data' for r in doc.fact_receipts)


def test_retired_shell_cannot_run_or_contribute_a_fake_pass():
    from src.engine.rules_v33 import R33121_ThreePublicCheck
    from src.engine.rule_outcome import RuleNotApplicable, RuleOutcomeSignal
    from src.engine.rule_registry import all_rule_ids
    from src.engine.check_obligations import get_obligation, obligation_ids_for_rule
    assert 'V33-121' not in all_rule_ids()
    assert get_obligation('OBL-SG-TOTAL').checkers_by_kind['final'] == ('V33-244',)
    assert 'OBL-SG-TOTAL' in obligation_ids_for_rule('V33-121')  # 历史查询仍能归类
    assert 'OBL-INTERNAL-TABLE-TOTAL' in obligation_ids_for_rule('V33-120')
    with pytest.raises(RuleOutcomeSignal) as exc:
        R33121_ThreePublicCheck().apply(SimpleNamespace())
    assert exc.value.status == 'not_applicable'


def test_empty_monetary_scope_does_not_invent_a_missing_percentage_check():
    from tests.remediation.test_putuo_code_repair import empty_three_public
    from src.engine.native_table_text import check_three_public_details
    from src.engine.relation_coverage import scoped_rule_status
    doc = empty_three_public()
    rule = Probe()
    rule.code = 'V33-224'
    assert check_three_public_details(rule, doc, check_percentages=True) == []
    assert scoped_rule_status('OBL-SG-TABLE-TEXT', rule.code, doc.fact_receipts, 'final') == 'not_applicable'
    assert scoped_rule_status('OBL-SG-SHARE', rule.code, doc.fact_receipts, 'final') == 'not_applicable'


def test_declaration_yaml_preserves_unique_budget_configuration():
    from pathlib import Path
    import yaml
    class UniqueLoader(yaml.SafeLoader):
        pass
    def mapping(loader, node, deep=False):
        keys = [loader.construct_object(key, deep=deep) for key, _ in node.value]
        assert len(keys) == len(set(keys)), keys
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)
    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    data = yaml.load((Path(__file__).resolve().parents[2] / 'rules/v3_3.yaml').read_text(), Loader=UniqueLoader)
    assert data['budget']['scope']['applies_to'] == ['预算公开']
