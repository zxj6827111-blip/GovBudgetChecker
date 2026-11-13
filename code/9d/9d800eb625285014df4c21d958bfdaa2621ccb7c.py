import json
import os
import subprocess
from pathlib import Path

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
BASE=ROOT/'outputs/fp_batch1_20261103'
OUT=BASE/'reviewer_round6_codex_20261103'
BRANCH=Path('/Volumes/workSSD/software development/gbc-worktrees/fp-batch1-20261103')
SCRIPT=OUT/'code/scripts/gold_baseline_eval_fp_batch1.py'
EXCEPTIONS=OUT/'code/scripts/fp_batch1_reference_exceptions.json'
rows=json.loads((BRANCH/'docs/fp_batch1_20261103_round6/probe_index.json').read_text())
extra=[('bbox',BASE/'reviewer_round2_codex_20261103/mutants/bbox/run1'),
       ('severity',BASE/'reviewer_round2_codex_20261103/mutants/severity/run1')]
for label,run in extra:rows.append({'probe':label,'run':str(run),'exit_code':1,'ok':False})
env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHON_DOTENV_DISABLED='1',DATABASE_URL='')
results=[]
for i,row in enumerate(rows):
    label=row['probe'].split('/')[-1]
    run=Path(row['run'])
    if not run.is_absolute():run=BASE/run
    cfg=EXCEPTIONS
    if label in {'multi_map','count_removed_mismatch'}:
        cfg=BRANCH/'docs/fp_batch1_20261103_round6'/f'{label}_exceptions.json'
    target=OUT/'probes'/label
    cmd=[str(ROOT/'.venv/bin/python'),str(SCRIPT),'--run',str(run),
         '--baseline-run',str(BASE/'step0/run1'),
         '--baseline-analysis',str(ROOT/'outputs/gold_baseline_20261102/rev2/analysis'),
         '--targets',str(ROOT/'outputs/gold_baseline_20261102/rev2/fp_batch1_targets.csv'),
         '--out',str(target),'--match-script',str(ROOT/'scripts/gold_baseline_match.py'),
         '--reference-exceptions',str(cfg)]
    proc=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True)
    (OUT/'probes').mkdir(exist_ok=True)
    (OUT/'probes'/f'{label}.log').write_text(proc.stdout+'\n'+proc.stderr)
    result=json.loads((target/'eval_summary.json').read_text())
    record={'probe':label,'command':cmd,'exit_code':proc.returncode,'ok':result['ok'],
            'expected_exit':row['exit_code'],'expected_ok':row['ok'],
            'matched_expectation':proc.returncode==row['exit_code'] and result['ok']==row['ok'],
            'raw_fingerprint_drift':result['raw_fingerprint_drift'],
            'reference_exception_conflicts':result['reference_exception_conflicts'],
            'headline':result['headline']['FPR_headline'] if result.get('headline') else None}
    results.append(record)
    print(label,proc.returncode,result['ok'],'expected',record['matched_expectation'],flush=True)
(OUT/'probe_replay_index.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
raise SystemExit(0 if all(x['matched_expectation'] for x in results) else 1)
