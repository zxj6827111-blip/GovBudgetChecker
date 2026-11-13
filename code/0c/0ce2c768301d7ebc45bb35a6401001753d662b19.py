"""最后候选：等固定验证与旧批次结束，提交、切换、正式111重跑并验收。"""
import hashlib,json,shutil,subprocess,time
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];WORK=ROOT/'artifacts/comprehensive-review-closure-20261109';PYTHON=ROOT/'artifacts/daily-main/.venv/bin/python'
read=lambda name:json.loads((OUT/name).read_text())
def run(script,*args,output=None):
 print('START',script,*args,flush=True)
 with (OUT/(output or script.replace('.py','.log'))).open('w') as log:subprocess.run([str(PYTHON),str(OUT/script),*args],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
 print('PASS',script,flush=True)
def git(*args):return subprocess.check_output(['git',*args],cwd=WORK,text=True).strip()
def unchanged():
 manifest=read('release_final9_code_manifest.json')
 assert all(hashlib.sha256((WORK/name).read_bytes()).hexdigest()==sha for name,sha in manifest.items())
deadline=time.monotonic()+2700
while '\nCOMPLETE 345 ' not in (OUT/'release_final9.log').read_text():
 if time.monotonic()>deadline:raise RuntimeError('Final9 originals not complete; do not deploy')
 time.sleep(5)
entries=read('release_final9_summary.json');assert len(entries)==345 and all(e['ok'] and not e['error'] for e in entries)
while not (OUT/'backend_final9_test_receipt.json').exists():
 if time.monotonic()>deadline:raise RuntimeError('Backend exit receipt missing; do not deploy')
 time.sleep(5)
assert read('backend_final9_test_receipt.json')['exit_code']==0 and read('postgres-final9_test_receipt.json')['exit_code']==0
unchanged();run('compare_release_final9.py',output='final9_comparison.log')
c=read('release_final9_comparison.json');assert not any(c[k] for k in ['errors','monetary_cell_changes','unproven_fact_changes','qualification_changes'])
run('verify_report_identity.py','release_final9',output='final9_identity.log')
identity=read('release_final9_identity_verification.json');assert not identity['cross_group_identity_collisions'] and not identity['unresolved_receipt_refs']
run('collect_replay_inventory.py','release_final9',output='release_final9_inventory.json');assert not read('release_final9_errors.json')
run('verify_real_decision_history_final9.py',output='real_decision_history_final9.log');assert read('real_decision_history_final9_receipt.json')['all_notes_and_decisions_visible']
while True:
 log=(OUT/'recovery_verification_orchestration.log').read_text()
 if 'Traceback (most recent call last)' in log:raise RuntimeError('Intermediate production verification failed; inspect before final deployment')
 if 'BACKEND_RECOVERY_CLOSURE_COMPLETE' in log and read('runtime_recovery_health.json')['rows'][-1]['finished']==111:break
 if time.monotonic()>deadline:raise RuntimeError('Intermediate production batch not verified; no shutdown')
 time.sleep(5)
unchanged()
expected={'src/services/pdf_parser.py','src/services/issue_workflow_store.py','tests/remediation/test_decision_source_identity.py','tests/remediation/test_native_continuation_data_role.py'}
commit_receipt=OUT/'final9_source_commit.json'
if not commit_receipt.exists():
 assert git('rev-parse','HEAD').startswith('d9cd1839') and not git('diff','--cached','--name-only')
 assert set(git('diff','--name-only').splitlines())|{'tests/remediation/test_native_continuation_data_role.py'}==expected
 subprocess.run(['git','add','--',*sorted(expected)],cwd=WORK,check=True)
 subprocess.run(['git','commit','-m','修复原生续页行角色与历史意见读取重复解析'],cwd=WORK,check=True,capture_output=True)
 commit_receipt.write_text(json.dumps(dict(candidate=git('rev-parse','HEAD'),files=sorted(expected)),ensure_ascii=False,indent=2))
assert git('rev-parse','HEAD')==read('final9_source_commit.json')['candidate'];unchanged()
if not (OUT/'deployment_final9_receipt.json').exists():run('deploy_final9.py',output='deployment_final9_run.log')
assert read('deployment_final9_receipt.json')['after']==git('rev-parse','HEAD')
shutil.copy2(OUT/'real_decision_history_final9_receipt.json',OUT/'real_decision_history_final_receipt.json')
accepted=OUT/'daily_final9_accepted.json'
if not accepted.exists():
 marker=OUT/'final9_enqueue_started.json';assert not marker.exists(),'Interrupted enqueue must be inspected, never repeated blindly'
 marker.write_text(json.dumps(dict(candidate=git('rev-parse','HEAD')),indent=2))
 run('enqueue_daily_final.py',output='daily_final9_enqueue.log')
 shutil.copy2(OUT/'daily_final_accepted.json',accepted)
 (OUT/'daily_final_release.json').write_text(json.dumps(dict(candidate=git('rev-parse','HEAD')),indent=2))
assert read('daily_final9_accepted.json')['created_count']==111 and not read('daily_final9_accepted.json')['failed_count']
run('watch_daily_final.py',output='daily_final_progress.log')
assert read('daily_final_finished.json')['finished']==111 and not read('daily_final_finished.json')['errors']
end=time.monotonic()+180
while True:
 run('verify_database_final.py','--partial',output='database_final_partial.log')
 partial=read('database_final_partial.json')
 if partial['verified']==111 and not partial['pending']:break
 if time.monotonic()>end:raise RuntimeError('Final9 database publication did not settle')
 time.sleep(5)
run('verify_database_final.py',output='database_final_verified.log')
run('verify_preservation.py',output='preservation_final.log')
run('database_audit.py','after_final',output='database_after_final_summary.json')
run('verify_database_history.py',output='database_history_final.log')
run('verify_production_history.py',output='production_history_final.log')
run('verify_shared_scope_final.py',output='shared_scope_final.log')
run('verify_served_build.py',output='served_build_final.log')
run('write_acceptance_report.py',output='acceptance_report_generation.log')
print('FINAL9_BACKEND_CLOSURE_COMPLETE; final actual UI acceptance remains separate',flush=True)
