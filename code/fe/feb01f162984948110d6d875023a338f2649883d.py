"""Independent post-merge checks: mandatory/optional, evidence retention and projections."""
from pathlib import Path
import copy
import importlib.util
import json

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
CODE=ROOT/'artifacts/r22-carrier-schema-fix-20261108'
OUT=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('main_fa_entry', CODE/'scripts/formal_acceptance_entry.py')
e=importlib.util.module_from_spec(s);s.loader.exec_module(e)

def contract():
 return {'contract_version':e.CONTRACT_VERSION,'expected':{
  'material':{'checksum_sha256':'a'*64},
  'generation':{'analysis_revision':'rev-1','parse_run_id':'parse-1','document_version_id':7},
  'code_identity':{'rule_registry_version':'reg-v1','rule_policy_version':'pol-v1'},
  'config_identity':{'use_local_rules':True,'use_ai_assist':False,'mode':'standard'}},
  'optional_fields':[]}
def payload():
 checks=[{'rule_id':'R1','status':'pass','issue_refs':[]}]
 facts=[{'rule_id':'R1','sub_check_id':'r1','status':'pass',
  'formula':'基本支出10≤三公表10万元','operand_fact_ids':['left-fact','right-fact'],
  'operand_sources':{'left':[{'physical_page':3,'bbox':[10,10,20,20]}],
                     'right':[{'physical_page':4,'bbox':[10,10,20,20]}]}}]
 cov={'instances':[{'obligation_id':'OBL-1','status':'completed'}]}
 return {'checksum':'a'*64,'analysis_revision':'rev-1','use_local_rules':True,
  'use_ai_assist':False,'mode':'standard','structured_ingest':{'parse_run_id':'parse-1','document_version_id':7},
  'result':{'rule_findings':[],'meta':{'rule_registry_version':'reg-v1','rule_policy_version':'pol-v1',
    'check_receipts':copy.deepcopy(checks),'fact_receipts':copy.deepcopy(facts),'obligation_coverage':copy.deepcopy(cov)}},
  'review_report':{'issues':[],'check_receipts':copy.deepcopy(checks),'fact_receipts':copy.deepcopy(facts),'obligation_coverage':copy.deepcopy(cov)}}

rows=[]
def record(name,b,a,c,expect):
 result=e.compare_payloads(b,a,c)
 rows.append({'name':name,'expected':expect,'contract':c,'before':b,'after':a,'actual':result})
 print(name,json.dumps({k:result.get(k) for k in ['verdict','reject_reason','zero_regression_claim','evidence_lost','claim_reasons']},ensure_ascii=False))

b=payload();record('positive_intact',b,copy.deepcopy(b),contract(),'compared, true')
c=contract();c['optional_fields']=['material.checksum_sha256'];a=payload();a.pop('checksum')
record('mandatory_checksum_marked_optional',a,copy.deepcopy(a),c,'reject mandatory identity missing despite optional_fields')
c=contract();expected=c.pop('expected');c.update(mode='cross_generation',expected_before=copy.deepcopy(expected),expected_after=copy.deepcopy(expected),optional_fields=['generation.analysis_revision'])
a=payload();a.pop('analysis_revision')
record('cross_generation_missing_revision_marked_optional',a,copy.deepcopy(a),c,'reject missing analysis generation')

b=payload();a=copy.deepcopy(b)
for container in [a['result']['meta'],a['review_report']]:
 f=container['fact_receipts'][0];f['operand_fact_ids']=[];f['operand_sources']={};f['formula']=None
record('same_relation_operand_evidence_cleared',b,a,contract(),'register lost operand evidence; no zero-regression claim')
b=payload();a=copy.deepcopy(b)
for container in [a['result']['meta'],a['review_report']]:container['fact_receipts'][0].pop('operand_sources')
record('same_relation_sources_key_removed',b,a,contract(),'register evidence-field loss; schema additions still permitted')

issue1={'id':'F1','rule':'R1','message':'发现问题一','severity':'error','page':3}
issue2={'id':'F2','rule':'R2','message':'仅all投影中的问题二','severity':'error','page':4}
b=payload();b['result']['issues']={'error':[issue1],'warn':[],'info':[],'all':[issue1]}
a=copy.deepcopy(b);a['result']['issues']['all'].append(issue2)
record('extra_issue_in_all_not_in_buckets',b,a,contract(),'flag inconsistent projection or retain additional F2, cannot be unchanged')
b=payload();b['result']['issues']={'error':[],'warn':[],'info':[],'all':[]}
a=copy.deepcopy(b);a['result']['issues']={'all':[issue2]}
record('damaged_carrier_masked_by_other_empty_carriers',b,a,contract(),'pending/reject unreadable presented carrier; other empty projections do not establish no issues')

(OUT/'remaining_boundary_results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
