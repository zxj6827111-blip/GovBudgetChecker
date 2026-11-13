import os,json,subprocess,shutil
from pathlib import Path
R=Path(__file__).parent;B=R.parents[1];O=B/'outputs/unified_closeout_round4_review_codex_20261104';D=B/'outputs/unified_closeout_round5_20261104';W=B/'outputs/unified_closeout_round4_20261104_work'
PY=str(B/'.venv/bin/python');ENV=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',DATABASE_URL='')
def run(n,cmd,cwd=R,extra=None):
 with (R/(n+'.log')).open('w') as f:p=subprocess.run(cmd,cwd=cwd,env=dict(ENV,**(extra or {})),stdout=f,stderr=subprocess.STDOUT)
 (R/(n+'_execution.json')).write_text(json.dumps({'cmd':list(map(str,cmd)),'cwd':str(cwd),'exit':p.returncode},indent=2));print(n,p.returncode,flush=True)
# Preserve assertions and old real annotation fixtures; run against new snapshot imports.
run('independent_r4_boundaries',[PY,'-m','pytest','-q','-p','no:cacheprovider',str(R/'test_new_r4_boundaries.py')])
run('prior6',[PY,'-m','pytest','-q','-p','no:cacheprovider',str(R/'test_new_boundary_cases.py')])
run('prior8',[PY,'-m','pytest','-q','-p','no:cacheprovider',str(R/'test_prior8.py')])
run('comparator21',[PY,'-m','pytest','-q','-p','no:cacheprovider','tests/test_anchor_binding.py'],R/'comparator')
run('tools9',[PY,'-m','pytest','-q','-p','no:cacheprovider',str(R/'delivery_tools/test_round5_identity_controls.py')])
run('official',[PY,str(B/'outputs/joint_execution_review_codex_20261103/reproduce_comparator_probes.py'),'--tool',str(R/'comparator/scripts/batch2_gold_compare.py'),'--out',str(R/'official_probes')])
for n,runpath in [('control',W/'gold_r4_1'),('fragment',O/'fragment_pool_mutant'),('amount',W/'amount_mutant_run'),('neighbor',W/'neighbor_amount_run'),('fp',W/'fp_mutant_run')]:
 run(n+'_eval',[PY,str(R/'run_evaluator_snapshot.py'),'--run',str(runpath),'--out',str(R/(n+'_eval'))])
 run(n+'_final',[PY,str(R/'delivery_tools/build_final_ledger.py'),'--ledger',str(R/(n+'_eval/target_ledger.json')),'--out',str(R/(n+'_final'))])
run('real_source_and_roles',[PY,'-m','pytest','-q','-p','no:cacheprovider',str(R/'test_real_source_and_roles.py')])
run('real_source_probe',[PY,str(R/'real_decimal_source_probe.py')])
run('decimal_replay',[PY,str(R/'delivery_tools/decimal_checks_v2.py'),'--annotations',str(D/'B1_review_package/annotations_revised'),'--spec',str(D/'B1_review_package/decimal_spec_v4.json'),'--out',str(R/'decimal_replay')])
run('reg160',[PY,str(R/'comparator/scripts/batch2_regression_compare.py'),'--baseline',str(W/'reg160_r4_1'),'--current',str(W/'reg160_r4_2'),'--contract',str(W/'reg_self_contract_r4.json'),'--stage','S6','--mode','self','--out',str(R/'reg160_replay')])
run('gold33',[PY,str(O/'gold_baseline_run_frozen.py'),'selfcompare','--a',str(W/'gold_r4_1'),'--b',str(W/'gold_r4_2'),'--out',str(R/'gold33_selfcompare.json')])
