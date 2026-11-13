from pathlib import Path
import sys,json,types,subprocess,hashlib
w=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');sys.path.insert(0,str(w))
from scripts import effect_acceptance_v3 as h
from scripts import effect_fact_sources as old
p=Path('/private/tmp/gbc-gap02-20261106/effect_fact_sources.py');m=types.ModuleType('scripts.effect_fact_sources');exec(compile(p.read_text(),str(p),'exec'),m.__dict__)
sources=[Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/independent_ir3_9631ea55_20261106'),Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/independent_ir3_d71c9ad7_20261106')]
cases=[]
for name in ['effect_boundary_controls','extra_effect','neighbor_effect']:
 for r in h.read(sources[0]/f'receipts/{name}.json'):cases.append((r['case'],sources[0]/'control_cases'/r['case'],r['expected']))
for r in h.read(sources[1]/'receipts/masking_boundary.json'):cases.append((r['case'],sources[1]/'masking_cases'/r['case'],r['expected']))
cases.append(('legitimate_hyphen_row_label_positive',sources[1]/'control_cases/legitimate_hyphen_row_label_positive','PASS'))
cases.append(('exact_same_pdf_row_mask',sources[1]/'masking_cases/exact_same_pdf_row_mask','FAIL'))
rows=[]
for name,folder,want in cases:
 e=folder/'external-evidence.json';ev=h.load_evidence(e,h.sha(e));pair={}
 for label,mod in [('d71_module',old),('draft',m)]:
  sys.modules['scripts.effect_fact_sources']=mod;r=h.compare_collections(folder/'before',folder/'after',folder/'kit',ev)
  pair[label]={'overall':r['overall'],'problems':r['problems']}
 rows.append({'case':name,'folder':str(folder),'expected':want,'versions':pair,'input_sha256':h.sha(e),'pdf_sha256':h.sha(folder/'kit/pdfs/I/original.pdf')})
Path('/private/tmp/gbc-gap02-20261106/preflight_results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'cases':len(rows),'mismatches':[r for r in rows if r['versions']['draft']['overall']!=r['expected']]},ensure_ascii=False))
