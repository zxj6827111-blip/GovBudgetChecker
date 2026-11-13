"""固定版本收口编排：任一验证失败即停，不重试提交批次，不放宽验收。"""
import json,subprocess,time,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];PYTHON=ROOT/'artifacts/daily-main/.venv/bin/python'
release=json.loads((OUT/'release_candidate.json').read_text());label=release['final_replay']
def run(script,*args,output=None):
 print('START',script,*args,flush=True)
 with (OUT/(output or script.replace('.py','.log'))).open('w') as log:
  subprocess.run([str(PYTHON),str(OUT/script),*args],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
 print('PASS',script,flush=True)
def read(name):return json.loads((OUT/name).read_text())
deadline=time.monotonic()+1800
while True:
 # 回放运行时 summary 正在整文件写入；只在调度器写完最终完成标记后
 # 读取它，避免把写入中的空文件/半份JSON误判成回归失败。
 replay_log=(OUT/(label+'.log')).read_text()
 if '\nCOMPLETE 345 ' in replay_log:
  entries=read(label+'_summary.json');assert len(entries)==345;break
 if time.monotonic()>deadline:raise RuntimeError('Fixed original replay not finished; no deployment')
 time.sleep(5)
assert all(e['ok'] and not e['error'] for e in entries)
run('compare_release_final7.py',output='final7_comparison.log')
c=read(label+'_comparison.json');assert not any(c[k] for k in ['errors','monetary_cell_changes','unproven_fact_changes','qualification_changes'])
run('verify_report_identity.py',label,output='final7_identity.log')
i=read(label+'_identity_verification.json');assert not i['cross_group_identity_collisions'] and not i['unresolved_receipt_refs']
run('collect_replay_inventory.py',label,output=label+'_inventory.json');assert not read(label+'_errors.json')
run('verify_real_decision_history_final.py',output='real_decision_history_final.log');assert read('real_decision_history_final_receipt.json')['all_notes_and_decisions_visible']
receipt=OUT/'deployment_final_receipt.json'
if not receipt.exists():run('deploy_final_patch.py',output='deployment_final_run.log')
assert read('deployment_final_receipt.json')['after']==release['candidate']
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT/'artifacts/daily-main',text=True).strip()==release['candidate']
if not (OUT/'daily_final_accepted.json').exists():
 run('enqueue_daily_final.py',output='daily_final_enqueue.log')
 (OUT/'daily_final_release.json').write_text(json.dumps(dict(candidate=release['candidate']),indent=2))
else:
 assert read('daily_final_release.json')['candidate']==release['candidate']
accepted=read('daily_final_accepted.json');assert accepted['created_count']==111 and accepted['failed_count']==0
run('watch_daily_final.py',output='daily_final_progress.log')
assert read('daily_final_finished.json')['finished']==111 and not read('daily_final_finished.json')['errors']
deadline=time.monotonic()+180
while True:
 run('verify_database_final.py','--partial',output='database_final_partial.log')
 partial=read('database_final_partial.json')
 if partial['verified']==111 and not partial['pending']:break
 if time.monotonic()>deadline:raise RuntimeError('Current database/file publication did not settle')
 time.sleep(5)
run('verify_database_final.py',output='database_final_verified.log')
run('verify_preservation.py',output='preservation_final.log')
run('database_audit.py','after_final',output='database_after_final_summary.json')
run('verify_database_history.py',output='database_history_final.log')
run('verify_production_history.py',output='production_history_final.log')
run('verify_shared_scope_final.py',output='shared_scope_final.log')
run('verify_served_build.py',output='served_build_final.log')
run('write_acceptance_report.py',output='acceptance_report_generation.log')
print('BACKEND_CLOSURE_COMPLETE; browser verification is a separate observed step',flush=True)
