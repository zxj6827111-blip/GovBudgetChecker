"""Reviewer controls; no business writes. Records observed results, not patched behavior."""
import copy
import gzip
import importlib.util
import json
from pathlib import Path

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
CODE = ROOT.parent/'gbc-worktrees/auto-check-remediation-r22'
OUT = Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('reviewed_entry',CODE/'scripts/formal_acceptance_entry.py')
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
cases=[]
def probe(name,b,a,c,expected,**kw):
    r=e.compare_payloads(b,a,c,**kw)
    cases.append({'name':name,'expected':expected,'actual':r})
    print(name, json.dumps({k:r.get(k) for k in ['verdict','zero_regression_claim','evidence_lost']},ensure_ascii=False))

b=e._payload(rule_findings=[{'id':'F-1','rule':'V33-X','message':'已发现的问题','page':8}],
    check_receipts=[{'rule_id':'V33-X','status':'fail','issue_refs':['F-1']}])
c=e._contract()
probe('positive',b,copy.deepcopy(b),c,'compared, full identity, no losses')
probe('empty_expected_contract',{}, {},{'contract_version':e.CONTRACT_VERSION,'expected':{}},'reject incomplete contract, no zero-regression claim')
a=copy.deepcopy(b)
for container in [a['result']['meta'],a['review_report']]: container['check_receipts']=[]
probe('receipts_nonempty_to_empty',b,a,c,'record lost receipt evidence and do not claim zero regression')
a=copy.deepcopy(b)
a['result']['meta']['fact_receipts']=[]
b2=copy.deepcopy(b); b2['result']['meta']['fact_receipts']=[{'rule_id':'V33-X','sub_check_id':'r1','status':'pass','left':10,'right':10}]
probe('fact_receipts_nonempty_to_empty',b2,a,c,'record loss of relation evidence')
a=copy.deepcopy(b)
a['result']['meta']['obligation_coverage']={}
probe('coverage_object_to_empty',b,a,c,'record loss of coverage instances/schema')
a=copy.deepcopy(b)
for ct in [a['result'],a['review_report']]:
 ct.pop('rule_findings',None);ct.pop('issues',None)
b3=copy.deepcopy(a)
probe('all_formal_carriers_absent',b3,a,c,'unknown/unreadable carriers cannot prove zero regression')
a=copy.deepcopy(b);a['result']['issues']={'all':[{'id':'F-2','rule':'V33-Y','message':'additional issue'}]}
probe('all_projection_without_primary_buckets',b,a,c,'register incomplete dict projection, do not silently drop F-2')

raw=json.loads(gzip.decompress((ROOT/'outputs/r22_gold33_20261107/run1/status/GS-2022-01.status.json.gz').read_bytes()))
ident=e.extract_payload_identity(raw)
def expect(i):
 return {'material':{'checksum_sha256':i['material.checksum_sha256']['value']},
 'generation':{'analysis_revision':i['generation.analysis_revision']['value']},
 'code_identity':{'rule_registry_version':i['code.rule_registry_version']['value'],'rule_policy_version':i['code.rule_policy_version']['value']},
 'config_identity':{'use_local_rules':i['config.use_local_rules']['value'],'use_ai_assist':i['config.use_ai_assist']['value'],'mode':i['config.mode']['value']}}
rc={'contract_version':e.CONTRACT_VERSION,'expected':expect(ident)}
a=copy.deepcopy(raw)
for ct in [a['result']['meta'],a['review_report']]:
 ct['check_receipts']=[];ct['fact_receipts']=[];ct['obligation_coverage']={}
probe('real_GS2022_01_all_receipt_evidence_cleared',raw,a,rc,'must not report evidence_lost=[] and zero_regression_claim=true')
a=copy.deepcopy(raw)
a['result']['meta']['structured_ingest']={'parse_run_id':'wrong-parse','document_version_id':999}
probe('real_GS2022_01_undeclared_parse_change',raw,a,rc,'cannot certify parse identity if not required in contract')
probe('real_code_declared_no_provenance',raw,copy.deepcopy(raw),{**rc,'git_commit_declared':'9e397bf20763807eec798b44e4a119355da65d4f'},'declaration alone cannot become verified producer identity')
(OUT/'entry_probe_results.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2))
