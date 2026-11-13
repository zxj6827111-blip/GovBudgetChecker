from pathlib import Path
import hashlib,json,os,sys,subprocess
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent;BASE=ROOT/'outputs/remediation_20261006_round11'
sys.path.insert(0,str(ROOT));os.environ.update(PYTHON_DOTENV_DISABLED='1',DATABASE_URL='',GOVBUDGET_TEST_DATABASE_URL='')
from scripts.run_zc07_acceptance import _evaluate_export_agreement
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
snapshot=json.loads((BASE/'source_snapshot.json').read_text());entries={e['path']:e for e in snapshot['files']}; protected=json.loads((OUT/'source_snapshot_before.json').read_text())['files']
source_checks={p:{'matches_handoff':sha(ROOT/p)==entries[p]['sha256'],'snapshot_copy_matches':sha(ROOT/entries[p]['snapshot_path'])==entries[p]['sha256']} for p in protected if p in entries};assert all(all(v.values()) for v in source_checks.values())
manifest=json.loads((BASE/'artifact_manifest.json').read_text());selected=[]
for e in manifest['files']:
 p=e['path']
 if any(s in p for s in ['/live_verified/','/ocr_health_final.json','/ocr_loaded_source_hashes_final.txt','/full_pytest_final.log','/ruff_final.log','/rules_replay.json','/zc07_handoff_preflight.json','/source_snapshot.json']):
  ok=sha(ROOT/p)==e['sha256']; selected.append({'path':p,'matches':ok});assert ok,p
gates={};service_checks=[]
for label,base in [('claimed_round11',BASE/'live_verified'),('independent_round12',OUT/'live_services')]:
 receipt=json.loads((base/'service_receipt.json').read_text())
 assert len(receipt['scenarios'])==5
 for scenario in receipt['scenarios']:
  name=scenario['scenario']; folder=base/name;status=json.loads((folder/'status.json').read_text());report=json.loads((folder/'report.json').read_text())
  checks={'source_sha':sha(folder/'source.pdf')==scenario['source_sha256'],'SQL_source':scenario['database']['job']['file_hash']==scenario['source_sha256'],'SQL_job':scenario['database']['job']['job_uuid']==scenario['job_id'],'current_result_row':scenario['database']['analysis_results']==1,'export_sha':all(sha(folder/n)==h for n,h in scenario['exports'].items()),'same_revision':report['analysis_revision']==scenario['analysis_revision'],'terminal':status['status']==scenario['terminal_status']=='review_required','worker_completion':bool(scenario['worker_completion_events']),'repeated_downloads':all(scenario['repeated_downloads_identical'].values())}
  paths=[str(folder/('report.'+ext)) for ext in ['json','csv','docx','pdf']]; gates[label+'_'+name]=_evaluate_export_agreement({'documents':[{'doc_id':name,'export_paths':paths}]},ROOT)
  if 'correction' in scenario:
   c=scenario['correction'];cf=folder/'corrected';cr=json.loads((cf/'report.json').read_text());ctx=json.loads((cf/'fact_context.json').read_text());target=next(f for f in ctx['facts'] if f['fact_uid']==c['target_id']);checks.update({'correction_SHA':all(sha(cf/n)==h for n,h in c['exports'].items()),'correction_revision':cr['analysis_revision']==c['new_revision']!=c['old_revision'],'persisted_value':float(target['raw_value'])==float(c['corrected_raw_value']),'SQL_revision_incremented':c['database_after']['job']['analysis_revision']>scenario['database']['job']['analysis_revision'],'historical_downloads_identical':all(c['history_identical'].values()),'worker_after':len(c['worker_completion_events_after'])>len(scenario['worker_completion_events'])})
   gates[label+'_'+name+'_corrected']=_evaluate_export_agreement({'documents':[{'doc_id':name+'-corrected','export_paths':[str(cf/('report.'+ext)) for ext in ['json','csv','docx','pdf']]}]},ROOT)
  assert all(checks.values()),(name,checks);service_checks.append({'run':label,'scenario':name,'synthetic':scenario['synthetic'],'derived':scenario.get('derived'),'checks':checks,'service_validation_passed':scenario['service_validation_passed']})
 for p,h in receipt['code_fingerprints'].items():assert sha(ROOT/p)==h,p
 for p,h in receipt['artifact_sha256'].items():assert sha(base/p)==h,p
 if label=='independent_round12':
  stopped={}
  for k in ['api_pid','worker_pid']:
   try:os.kill(receipt['processes'][k],0);stopped[k]=False
   except ProcessLookupError:stopped[k]=True
  r=subprocess.run(['docker','inspect',receipt['postgres']['isolation']],capture_output=True,text=True);stopped['pg_removed']=r.returncode!=0 and 'no such' in r.stderr.lower()
  assert all(stopped.values());service_checks.append({'cleanup':stopped,'overall_status':receipt['status'],'errors':receipt['errors'],'resource_snapshot_recorded':bool(receipt.get('resource_snapshot'))})
assert all(g['status']=='computed' and g['export_agreement_rate']==1.0 for g in gates.values())
dump(OUT/'handoff_integrity.json',{'protected_source_checks':source_checks,'selected_artifact_checks':selected,'source_checked':len(source_checks),'artifacts_checked':len(selected)})
dump(OUT/'live_service_evidence_integrity.json',service_checks)
dump(OUT/'production_export_gate.json',{'scope':'7 original/corrected service views each from claimed and fresh runs; no frozen truth or business scores','bundles':gates})
print(json.dumps({'protected_source_checks':len(source_checks),'selected_artifact_checks':len(selected),'export_bundles':len(gates),'all_exports_consistent':True,'fresh_scenario_checks':5,'fresh_overall_status':receipt['status'],'fresh_cleanup':stopped},ensure_ascii=False))
