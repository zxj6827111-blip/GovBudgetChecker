import hashlib,json,gzip,subprocess
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];MAIN=ROOT/'artifacts/daily-main'
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
before=json.loads((OUT/'preservation_before.json').read_text());pre=json.loads((OUT/'deployment_preflight.json').read_text());accepted=json.loads((OUT/'daily_reanalysis_accepted.json').read_text());expected={r['job_id'] for r in accepted['created']};assert accepted['failed_count']==0
assert all(h(ROOT/name)==sha for name,sha in before['root_modified_files'].items())
assert h(ROOT/'.env')==before['root_env_sha256']
assert all(h(ROOT/name)==sha for name,sha in before['pdfs'].items())
assert h(ROOT/'data/ai-model-settings.json')==pre['ai_settings_sha256']
current_changed={job for job,sha in pre['statuses'].items() if h(ROOT/'uploads'/job/'status.json')!=sha}
assert current_changed==expected,(sorted(current_changed-expected),sorted(expected-current_changed))
original_workflow=json.loads(gzip.decompress((OUT/'private/workflow_before.json.gz').read_bytes()));current=json.loads((ROOT/'uploads/.issue_workflow.json').read_text())
by_event={e['event_id']:e for e in current.get('decision_events',[])}
for event in original_workflow['decision_events']:
 now=by_event.get(event['event_id']);assert now and all(now.get(k)==v for k,v in event.items()),event['event_id']
for key,record in original_workflow['issues'].items():
 now=current['issues'].get(key);assert now,key
 for field in ['status','note','analysis_revision','decision_revision','identity_fingerprint','fact_ids','evidence_ids','check_relation_id']:
  assert now.get(field)==record.get(field),(key,field)
archive_files=0
for job in expected:
 manifests=[p for p in (ROOT/'uploads'/job/'preserved_analyses').glob('*/manifest.json') if json.loads(p.read_text()).get('files',{}).get('status.json')==pre['statuses'][job]]
 assert manifests,job
 for name,sha in json.loads(manifests[0].read_text())['files'].items():assert h(manifests[0].parent/name)==sha,(job,name);archive_files+=1
release=json.loads((OUT/'release_candidate.json').read_text());assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=MAIN,text=True).strip()==release['candidate']
assert not subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=MAIN,text=True).strip()
code=json.loads((OUT/'implementation_manifest.json').read_text());assert all(h(MAIN/name)==sha for name,sha in code.items())
receipt=dict(release=release['candidate'],root_dirty_files_preserved=len(before['root_modified_files']),original_and_historical_pdfs_preserved=len(before['pdfs']),historical_statuses_preserved=len(pre['statuses'])-len(expected),reanalysis_statuses=len(expected),prior_reports_archived_byte_exact=True,archive_files_verified=archive_files,workflow_records_preserved=len(original_workflow['issues']),workflow_events_preserved=len(original_workflow['decision_events']),root_configuration_preserved=True,ai_settings_preserved=True,runtime_checkout_clean=True)
(OUT/'preservation_final.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2));print(json.dumps(receipt,ensure_ascii=False))
