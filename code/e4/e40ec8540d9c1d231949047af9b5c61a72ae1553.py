import os, sys, json, subprocess
from pathlib import Path

R = Path(__file__).parent
BASE = R.parents[1]
PY = str(BASE/'.venv/bin/python')
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', DATABASE_URL='')

def run(name, cmd, cwd=None, env=None):
    with (R/f'{name}.log').open('w') as log:
        p = subprocess.run(cmd,cwd=cwd or R,env=dict(ENV,**(env or {})),stdout=log,stderr=subprocess.STDOUT)
    (R/f'{name}_execution.json').write_text(json.dumps({'cmd':list(map(str,cmd)),'cwd':str(cwd or R),'exit':p.returncode},ensure_ascii=False,indent=2))
    print(name,p.returncode,flush=True)

run('prior6',[PY,'-m','pytest','-q','-p','no:cacheprovider',str(R/'test_new_boundary_cases.py')])
run('own5',[PY,'-m','pytest','-q','-p','no:cacheprovider',str(R/'test_round4_controls.py')])
run('prior8',[PY,'-m','pytest','-q','-p','no:cacheprovider',str(R/'test_prior8.py')])
for name in ('prior10','boundaries47'):
    obj=json.loads((R/f'{name}_command.json').read_text())
    run(name,obj['cmd'],obj['cwd'],obj['env'])
run('comparator15',[PY,'-m','pytest','-q','-p','no:cacheprovider',str(R/'comparator/tests/test_anchor_binding.py')])
run('official',[PY,str(BASE/'outputs/joint_execution_review_codex_20261103/reproduce_comparator_probes.py'),'--tool',str(R/'comparator/scripts/batch2_gold_compare.py'),'--out',str(R/'official_probes')])
run('control_eval',[PY,str(R/'run_evaluator_snapshot.py'),'--run',str(BASE/'outputs/unified_closeout_round4_20261104_work/gold_r4_1'),'--out',str(R/'control_eval')])
run('control_final',[PY,str(R/'delivery_tools/build_final_ledger.py'),'--ledger',str(R/'control_eval/target_ledger.json'),'--out',str(R/'control_final')])
for name,dirname in [('amount','amount_mutant_run'),('fp','fp_mutant_run'),('neighbor','neighbor_amount_run')]:
    run(name+'_eval',[PY,str(R/'run_evaluator_snapshot.py'),'--run',str(BASE/'outputs/unified_closeout_round4_20261104_work'/dirname),'--out',str(R/(name+'_eval'))])
    run(name+'_final',[PY,str(R/'delivery_tools/build_final_ledger.py'),'--ledger',str(R/(name+'_eval/target_ledger.json')),'--out',str(R/(name+'_final'))])
