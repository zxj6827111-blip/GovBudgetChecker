import importlib.util
from pathlib import Path

ROOT=Path(__file__).parent
def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

cmp=load('cmp_r3new',ROOT/'comparator/scripts/batch2_gold_compare.py')
ev=load('eval_r3new',ROOT/'comparator/scripts/gold_baseline_eval_fp_batch1.py')
fp=load('fp_r3new',ROOT/'delivery_tools/evaluate_114_targets.py')
dec=load('decimal_r3new',ROOT/'delivery_tools/decimal_checks_v2.py')

def test_amount_from_neighbor_relation_cannot_cover():
    text='项目支出10.00万元；基本支出100.00万元'
    item={'text':text,'quotes':[text],'pages':[5]}
    assert not cmp._anchor_covered(item,'项目支出100.00万元',ev,page=5)

def test_multipage_primary_cannot_reassign_other_page_quote():
    item={'text':'项目支出10.00万元','quotes':['项目支出100.00万元'],'pages':[5,6],
          'location':{'page':5,'locations':[{'page':6,'text':'项目支出100.00万元'}]}}
    assert not cmp._anchor_covered(item,'项目支出100.00万元',ev,page=5)

def test_fp_same_evidence_amount_format_does_not_change_identity():
    left={'message':'本年支出100.00万元，差10.00万元','pages':[5],'quotes':['项目支出100.00万元']}
    right={'message':'本年支出100.000万元，差10.000万元','pages':[5],'quotes':['项目支出100.00万元']}
    assert fp._same_entity(left,right) is not False

def test_material_values_cannot_all_be_replaced_by_rule_constants():
    c={'source_kind':'rule_constant','rule_basis':'supposed policy','unit':'万元'}
    row={'sample_id':'s','check_id':'SUM','kind':'amount','operands':[dict(c,value='100.00')],
         'expected':'100.00','reported':'100.00','reported_unit':'万元','reported_source':c}
    assert dec.evaluate(row,{})['result']!='PASS'

def test_row_column_conflict_in_source_cannot_pass():
    value={'page':1,'quote':'100.00','unit':'万元','table':'T','row':'住房公积金','column':'决算'}
    ann={'s':{'checks':[{'check_id':'SUM','items':[{'values':[value]}]}]}}
    source=dict(value,row='医疗费',column='预算')
    row={'sample_id':'s','check_id':'SUM','kind':'amount','operands':[dict(source,value='100.00')],
         'expected':'100.00','reported':'100.00','reported_unit':'万元','reported_source':source}
    assert dec.evaluate(row,ann)['result']!='PASS'

def test_correct_relation_quote_positive():
    text='项目支出100.00万元；基本支出10.00万元'
    assert cmp._anchor_covered({'text':text,'quotes':[text],'pages':[5]},'项目支出100.00万元',ev,page=5)
