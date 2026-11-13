from pathlib import Path
import sys, json, hashlib, types
ROOT=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
SRC=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/independent_ir3_9631ea55_20261106')
sys.path.insert(0,str(ROOT))
from scripts import effect_acceptance_v3 as h
from scripts import effect_fact_sources as previous
source=Path('/private/tmp/gbc-gap01-preflight/effect_fact_sources.py').read_text()
new=types.ModuleType('scripts.effect_fact_sources');exec(compile(source,'gap01-draft','exec'),new.__dict__)
expectations={}
for name in ['effect_boundary_controls','extra_effect','neighbor_effect']:
 for r in json.loads((SRC/f'receipts/{name}.json').read_text()):expectations[r['case']]=r['expected']
results=[]
for name,want in expectations.items():
 p=SRC/'control_cases'/name;ev=h.load_evidence(p/'external-evidence.json',h.sha(p/'external-evidence.json'))
 pair={}
 for label,mod in [('9631ea55',previous),('draft',new)]:
  sys.modules['scripts.effect_fact_sources']=mod
  r=h.compare_collections(p/'before',p/'after',p/'kit',ev)
  pair[label]={'actual':r['overall'],'problems':r['problems']}
 results.append({'case':name,'expected':want,'inputs':{str(q.relative_to(p)):h.sha(q) for q in p.rglob('*') if q.is_file()},'results':pair})
Path('/private/tmp/gbc-gap01-preflight/saved_replay.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps({'cases':len(results),'mismatches':[(r['case'],r['results']['draft']) for r in results if r['expected']!=r['results']['draft']['actual']]},ensure_ascii=False))
