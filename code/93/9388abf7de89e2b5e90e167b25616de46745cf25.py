import importlib.util
from pathlib import Path
import sys
import os
import pytest

ROOT = Path(__file__).parent
CODE_ROOT = Path(os.environ.get('GBC_REVIEW_CODE_ROOT', str(ROOT / 'code')))
COMPARATOR_ROOT = Path(os.environ.get('GBC_REVIEW_COMPARATOR_ROOT', str(ROOT / 'comparator')))
sys.path.insert(0, str(CODE_ROOT))
from src.schemas.material_detail import CoverageObligationItem
from src.services.obligation_finding_coverage import obligation_covered_by_finding

def module(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

PACKAGE=Path(os.environ.get('GBC_REVIEW_PACKAGE_ROOT', str(ROOT.parent/'unified_closeout_20261103')))
decimal=module('decimal_review',PACKAGE/'tools/decimal_checks_v2.py')
comparator=module('comparator_review',COMPARATOR_ROOT/'scripts/batch2_gold_compare.py')
eval_lib=module('eval_review',COMPARATOR_ROOT/'scripts/gold_baseline_eval_fp_batch1.py')

def covered(operands,location):
    ob=CoverageObligationItem(obligation_id='OBL-TXT-T5',status='insufficient_data',
        checkers=['V33-222'],blocks_gate=True,detail='住房公积金金额未核验',
        legacy_coverage_terms=['住房公积金'],missing_operands=operands)
    finding={'id':'amount1','rule_id':'V33-222','severity':'error','source':'rule',
             'message':'住房公积金金额不一致，差10万元','location':location}
    return obligation_covered_by_finding(ob,{'rule_findings':[finding]}, {'amount1'})

def test_no_operands_no_identity_must_remain_unfinished():
    assert not covered([], {})

def test_same_page_alone_cannot_prove_same_relation():
    assert not covered([{'page':8,'role':'narrative_amount'}],
                       {'page':8,'row':'other-row','table_instance_id':'other-table'})

def test_equal_relation_must_not_override_explicit_year_and_subject_conflict():
    assert not covered([{'check_relation_id':'V33-222:amount:1','year':2024,'org_name':'甲单位'}],
                       {'check_relation_id':'V33-222:amount:1','year':2023,'org_name':'乙单位'})

def test_required_year_subject_cannot_be_missing():
    op={'page':8,'table_instance_id':'t5','row_key':'2210201','column':'决算数','year':2024,'org_name':'甲单位'}
    assert not covered([op],{'page':8,'table_instance_id':'t5','row':'2210201','column':'决算数'})

def test_complete_identity_positive_control():
    op={'page':8,'table_instance_id':'t5','row_key':'2210201','column':'决算数','year':2024,'org_name':'甲单位'}
    assert covered([op],{'page':8,'table_instance_id':'t5','row':'2210201','column':'决算数','year':2024,'org_name':'甲单位'})

def arithmetic(ops,expected,reported):
    values=[{'page':1,'quote':'1.00','unit':'万元'},{'page':1,'quote':'2.00','unit':'万元'},
            {'page':1,'quote':'3.02','unit':'万元'}]
    annotations={'sample':{'checks':[{'check_id':'SUM','items':[{'values':values}]}]}}
    return decimal.evaluate({'sample_id':'sample','check_id':'SUM','kind':'amount',
                             'operands':ops,'expected':expected,'reported':reported},annotations)

def test_amount_policy_and_envelope_must_not_be_added():
    ops=[{'page':1,'quote':'1.00','value':'1.00'},{'page':1,'quote':'2.00','value':'2.00'}]
    # 缺少来源时可保持 PENDING；无论如何不得按叠加容差给 PASS。
    assert arithmetic(ops,'3.00','3.02')['result'] != 'PASS'

def test_numeric_value_must_match_bound_quote():
    r=arithmetic([{'page':1,'quote':'1.00','value':'999.00'}],'999.00','999.00')
    assert r['result']=='PENDING'

def test_reported_value_needs_source():
    r=arithmetic([{'page':1,'quote':'1.00','value':'1.00'}],'1.00','1.00')
    assert r['result']=='PENDING'

def test_generic_quote_cannot_cover_distinct_amount_defect():
    item={'text':'项目支出金额已经核对一致','quotes':['项目支出'],'pages':[99]}
    assert not comparator._anchor_covered(item,'项目支出217,7380.31 万元',eval_lib)

def test_different_amount_cannot_cover_amount_defect_by_name_only():
    item={'text':'住房公积金金额不一致：10.00 与20.00','quotes':[]}
    assert not comparator._anchor_covered(item,'住房公积金（项）100.00万元',eval_lib)
