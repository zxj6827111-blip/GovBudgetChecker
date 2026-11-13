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
 manifest=read('release_final13_code_manifest.json')
 assert all(hashlib.sha256((WORK/name).read_bytes()).hexdigest()==sha for name,sha in manifest.items())
deadline=time.monotonic()+2700
while '\nCOMPLETE 345 ' not in (OUT/'release_final13.log').read_text():
 if time.monotonic()>deadline:raise RuntimeError('Final9 originals not complete; do not deploy')
 time.sleep(5)
entries=read('release_final13_summary.json');assert len(entries)==345 and all(e['ok'] and not e['error'] for e in entries)
while not (OUT/'backend_final13_test_receipt.json').exists():
 if time.monotonic()>deadline:raise RuntimeError('Backend exit receipt missing; do not deploy')
 time.sleep(5)
assert read('backend_final13_test_receipt.json')['exit_code']==0 and read('postgres-final13_test_receipt.json')['exit_code']==0
unchanged();run('compare_release_final13.py',output='final13_comparison.log')
c=read('release_final13_comparison.json');assert not any(c[k] for k in ['errors','monetary_cell_changes','unproven_fact_changes','qualification_changes'])
run('verify_report_identity.py','release_final13',output='final13_identity.log')
identity=read('release_final13_identity_verification.json');assert not identity['cross_group_identity_collisions'] and not identity['unresolved_receipt_refs']
run('collect_replay_inventory.py','release_final13',output='release_final13_inventory.json');assert not read('release_final13_errors.json')
run('verify_orphan_release_final13.py',output='final13_orphan_impact.log')
while not (OUT/'final13_source_acceptance.json').exists():
 if time.monotonic()>deadline:raise RuntimeError('Original PDF source acceptance not recorded; no deployment')
 time.sleep(5)
acceptance=read('final13_source_acceptance.json')
assert acceptance['accepted'] and acceptance['impact_sha256']==hashlib.sha256((OUT/'final13_orphan_impact_verified.json').read_bytes()).hexdigest()
run('verify_real_decision_history_final13.py',output='real_decision_history_final13.log');assert read('real_decision_history_final13_receipt.json')['all_notes_and_decisions_visible']
while True:
 log=(OUT/'final9_orchestration.log').read_text()
 if 'PASS verify_served_build.py' in log and read('database_final_verified.json')['verified']==111 and read('runtime_recovery_health.json')['rows'][-1]['finished']==111:break
 if 'Traceback (most recent call last)' in log and 'final_acceptance_hold.json' not in log:raise RuntimeError('Prior final9 verification failed; inspect before final deployment')
 if time.monotonic()>deadline:raise RuntimeError('Prior final9 batch not verified; no shutdown')
 time.sleep(5)

unchanged()
expected={'src/engine/native_hierarchy_checks.py','src/engine/native_issue_dedup.py','tests/remediation/test_orphan_functional_roots.py'}
commit_receipt=OUT/'final13_source_commit.json'
if not commit_receipt.exists():
 assert git('rev-parse','HEAD').startswith('01bfb5c5') and not git('diff','--cached','--name-only')
 assert set(git('diff','--name-only').splitlines())|{'tests/remediation/test_orphan_functional_roots.py'}==expected
 subprocess.run(['git','add','--',*sorted(expected)],cwd=WORK,check=True)
 subprocess.run(['git','commit','-m','修复孤立科目跨层累计并归并原件镜像层级问题'],cwd=WORK,check=True,capture_output=True)
 commit_receipt.write_text(json.dumps(dict(candidate=git('rev-parse','HEAD'),files=sorted(expected)),ensure_ascii=False,indent=2))
assert git('rev-parse','HEAD')==read('final13_source_commit.json')['candidate'];unchanged()
if not (OUT/'deployment_final13_receipt.json').exists():run('deploy_final13.py',output='deployment_final13_run.log')
assert read('deployment_final13_receipt.json')['after']==git('rev-parse','HEAD')
shutil.copy2(OUT/'real_decision_history_final13_receipt.json',OUT/'real_decision_history_final_receipt.json')
accepted=OUT/'daily_final13_accepted.json'
if not accepted.exists():
 marker=OUT/'final13_enqueue_started.json';assert not marker.exists(),'Interrupted enqueue must be inspected, never repeated blindly'
 marker.write_text(json.dumps(dict(candidate=git('rev-parse','HEAD')),indent=2))
 run('enqueue_daily_final.py',output='daily_final13_enqueue.log')
 shutil.copy2(OUT/'daily_final_accepted.json',accepted)
 (OUT/'daily_final_release.json').write_text(json.dumps(dict(candidate=git('rev-parse','HEAD')),indent=2))
assert read('daily_final13_accepted.json')['created_count']==111 and not read('daily_final13_accepted.json')['failed_count']
with (OUT/'runtime_final13_health.log').open('w') as health_log:
 health_process=subprocess.Popen([str(PYTHON),str(OUT/'monitor_recovery_health.py')],cwd=ROOT,stdout=health_log,stderr=subprocess.STDOUT)
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
health_process.wait(timeout=180)
assert health_process.returncode==0 and read('runtime_recovery_health.json')['candidate']==git('rev-parse','HEAD') and read('runtime_recovery_health.json')['rows'][-1]['finished']==111
hold=OUT/'final_acceptance_hold.json';assert hold.exists()
resolution=dict(previous_hold=read('final_acceptance_hold.json'),resolved_by=git('rev-parse','HEAD'),source_impact='final13_orphan_impact_verified.json',current_database='database_final_verified.json',status='source_fix_and_final_batch_verified')
(OUT/'final_acceptance_hold_resolved.json').write_text(json.dumps(resolution,ensure_ascii=False,indent=2))
hold.rename(OUT/'final_acceptance_hold_original.json')
run('write_acceptance_report.py',output='acceptance_report_generation.log')
print('FINAL13_BACKEND_CLOSURE_COMPLETE; final actual UI acceptance remains separate',flush=True)
