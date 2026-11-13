import importlib.util
from pathlib import Path
import sys
ROOT=Path(__file__).parent
sys.path.insert(0,str(Path('/Volumes/workSSD/software development/gbc-worktrees/unified-closeout-20261103')))
from src.schemas.material_detail import CoverageObligationItem
from src.services.obligation_finding_coverage import obligation_covered_by_finding

def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
cmp=load('cmp_r2',Path('/Volumes/workSSD/software development/gbc-worktrees/joint-exp-batch2-tools-20261103/scripts/batch2_gold_compare.py'))
ev=load('ev_r2',Path('/Volumes/workSSD/software development/gbc-worktrees/joint-exp-batch2-tools-20261103/scripts/gold_baseline_eval_fp_batch1.py'))
dec=load('dec_r2',Path('/Volumes/workSSD/software development/gbc-worktrees/closeout-tools-r4-20261104/tools/closeout_r4/decimal_checks_v2.py'))
ledger=load('ledger_r2',Path('/Volumes/workSSD/software development/gbc-worktrees/closeout-tools-r4-20261104/tools/closeout_r4/build_final_ledger.py'))

def item(text,pages,refs=None):
 return {'text':text,'quotes':[text],'pages':pages,'location':{'locations':refs or []}}

def test_same_page_wrong_amount_must_not_cover():
 assert not cmp._anchor_covered(item('住房公积金10.00万元',[5]),'住房公积金100.00万元',ev,page=5,amount_required=True)

def test_missing_page_must_not_cover_located_anchor():
 assert not cmp._anchor_covered(item('项目支出100.00万元',[]),'项目支出100.00万元',ev,page=5)

def test_other_page_quote_cannot_cover_target_page():
 x=item('项目支出10.00万元',[5,6],[{'page':6,'text':'项目支出100.00万元'}])
 assert not cmp._anchor_covered(x,'项目支出100.00万元',ev,page=5)

def test_yuan_vs_wanyuan_not_same_unit():
 assert not cmp._anchor_covered(item('其他项目支出100.00万元',[5]),'项目支出100.00元',ev,page=5)

def test_exact_same_page_quote_positive():
 assert cmp._anchor_covered(item('项目支出100.00万元',[5]),'项目支出100.00万元',ev,page=5)

def arithmetic(ops,reported,source,expected=None):
 vals=[{'page':1,'quote':'100.00元','unit':'元'}, {'page':1,'quote':'100.00万元','unit':'万元'},
       {'page':1,'quote':'10000.00元','unit':'元'},{'page':1,'quote':'1.00万元','unit':'万元'}]
 ann={'s':{'checks':[{'check_id':'C','items':[{'values':vals}]}]}}
 return dec.evaluate({'sample_id':'s','check_id':'C','kind':'amount','operands':ops,
  'expected':expected or reported,'reported':reported,'reported_source':source,'reported_unit':source['unit']},ann)

def test_incompatible_units_cannot_pass():
 op={'page':1,'quote':'100.00元','unit':'元','value':'100.00'}
 reported={'page':1,'quote':'100.00万元','unit':'万元'}
 assert arithmetic([op],'100.00',reported)['result']!='PASS'

def test_same_unit_source_positive():
 op={'page':1,'quote':'100.00元','unit':'元','value':'100.00'}
 assert arithmetic([op],'100.00',{'page':1,'quote':'100.00元','unit':'元'})['result']=='PASS'

def test_fp_same_identity_rewording_not_fixed():
 row={'type':'FP','current_state':'still_present','baseline_amounts':['100.00','90.00'],
  'best_current_match':{'id':'same-entity','tier':'F','similarity':0.89,'amounts':['100.00','90.00']}}
 assert ledger.decide(row)[0]!='fixed'
