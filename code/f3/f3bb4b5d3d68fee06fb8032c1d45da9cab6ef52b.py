import importlib.util
from pathlib import Path
ROOT=Path(__file__).parent

def load(name,p):
 s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
cmp=load('cmp_r4neg',ROOT/'comparator/scripts/batch2_gold_compare.py')
ev=load('ev_r4neg',ROOT/'comparator/scripts/gold_baseline_eval_fp_batch1.py')
dec=load('dec_r4neg',ROOT/'delivery_tools/decimal_checks_v2.py')

def test_different_row_fragments_cannot_form_amount_occurrence():
 item={'text':'项目支出10.00万元；基本支出100.00万元','pages':[5],
       'location':{'locations':[
         {'page':5,'text':'项目支出','table_id':'T','row':'项目支出','bbox':[10,10,100,20]},
         {'page':5,'text':'100.00万元','table_id':'T','row':'基本支出','bbox':[200,40,300,50]}]}}
 assert not cmp._anchor_covered(item,'项目支出100.00万元',ev,page=5)

def test_conflicting_explicit_decimal_row_column_cannot_pass():
 value={'page':1,'quote':'100.00','unit':'万元','table':'T','row':'住房公积金','column':'决算'}
 ann={'s':{'basic':{'tables':[{'name':'T','page':1}],'pages':1},
           'checks':[{'check_id':'C','items':[{'values':[value]}]}]}}
 source=dict(value,row='医疗费',column='预算')
 row={'sample_id':'s','check_id':'C','kind':'amount', 'operands':[dict(source,value='100.00')],
      'expected':'100.00','reported':'100.00','reported_unit':'万元','reported_source':source}
 assert dec.evaluate(row,ann)['result']!='PASS'

def test_matching_explicit_decimal_identity_positive():
 value={'page':1,'quote':'100.00','unit':'万元','table':'T','row':'住房公积金','column':'决算'}
 ann={'s':{'basic':{'tables':[{'name':'T','page':1}],'pages':1},
           'checks':[{'check_id':'C','items':[{'values':[value]}]}]}}
 row={'sample_id':'s','check_id':'C','kind':'amount', 'operands':[dict(value,value='100.00')],
      'expected':'100.00','reported':'100.00','reported_unit':'万元','reported_source':value}
 assert dec.evaluate(row,ann)['result']=='PASS'
