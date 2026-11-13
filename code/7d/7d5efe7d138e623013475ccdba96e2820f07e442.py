from pathlib import Path
import hashlib,json,os,sys,subprocess
ROOT=Path(__file__).resolve().parents[2]; OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
os.environ.update(PYTHON_DOTENV_DISABLED='1',DATABASE_URL='',GOVBUDGET_TEST_DATABASE_URL='',AI_ENABLED='false',AI_ASSIST_ENABLED='false',OCR_ENABLED='false')
from scripts.run_zc07_acceptance import _evaluate_export_agreement
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=OUT/'export_chain.json'; raw=p.read_text(); data=json.loads(raw[raw.index('{'):]); (OUT/'export_chain_stdout.log').write_text(raw); p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
gates={}; integrity=[]
for folder in sorted((OUT/'export_probe').iterdir()):
 if not folder.is_dir():continue
 paths=[str(folder/('report.'+s)) for s in ['json','csv','docx','pdf']]
 gates[folder.name]=_evaluate_export_agreement({'documents':[{'doc_id':folder.name,'export_paths':paths}]},ROOT)
for label,base in [('independent_current',OUT/'live_services'),('claimed_round9',ROOT/'outputs/remediation_20261005_round9/live_services_final')]:
 receipt=json.loads((base/'service_receipt.json').read_text())
 for s in receipt['scenarios']:
  name=s['scenario']; folder=base/name
  status=json.loads((folder/'status.json').read_text()); report=json.loads((folder/'report.json').read_text())
  checks={'source_sha_matches':sha(folder/'source.pdf')==s['source_sha256'], 'db_source_sha_matches':s['database']['job']['file_hash']==s['source_sha256'], 'db_job_identity':s['database']['job']['job_uuid']==s['job_id'], 'export_sha_matches':all(sha(folder/n)==h for n,h in s['exports'].items()), 'terminal_status_matches':status['status']==s['terminal_status']=='review_required', 'report_revision_matches':report['analysis_revision']==s['analysis_revision'], 'worker_claim_present':bool(s['worker_claims']), 'worker_completion_present':bool(s['worker_completion_events']), 'result_saved':s['database']['analysis_results']==1, 'repeated_downloads_identical':all(s['repeated_downloads_identical'].values())}
  integrity.append({'run':label,'scenario':name,'synthetic':s['synthetic'],'job_id':s['job_id'],'checks':checks}); assert all(checks.values()),checks
  gates[label+'_'+name]=_evaluate_export_agreement({'documents':[{'doc_id':name,'export_paths':[str(folder/('report.'+ext)) for ext in ['json','csv','docx','pdf']]}]},ROOT)
 fp={name:sha(ROOT/name)==digest for name,digest in receipt['code_fingerprints'].items()}; assert all(fp.values())
 checks={'run':label,'code_fingerprints_match':fp,'artifact_sha_matches':{name:sha(base/name)==digest for name,digest in receipt['artifact_sha256'].items()}}
 assert all(checks['artifact_sha_matches'].values()); integrity.append(checks)
 if label=='independent_current':
  alive=[]
  for key in ['api_pid','worker_pid']:
   try:os.kill(receipt['processes'][key],0); alive.append(key)
   except ProcessLookupError:pass
  c=subprocess.run(['docker','container','inspect',receipt['postgres']['isolation']],capture_output=True,text=True)
  cleanup={'processes_stopped':not alive,'isolated_container_removed':c.returncode!=0 and 'No such' in c.stderr};assert all(cleanup.values()),cleanup;integrity.append({'cleanup':cleanup})
assert all(g['status']=='computed' and g['export_agreement_rate']==1.0 for g in gates.values()),gates
(OUT/'production_export_gate.json').write_text(json.dumps({'scope':'5 synthetic actual-handler views + 2 fresh service bundles + 2 claimed service bundles; no blind accuracy','bundles':gates},ensure_ascii=False,indent=2)+'\n')
(OUT/'live_service_evidence_integrity.json').write_text(json.dumps(integrity,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'gated_bundles':len(gates),'all_computed_consistent':True,'service_evidence_integrity':True,'cleanup':cleanup}))
