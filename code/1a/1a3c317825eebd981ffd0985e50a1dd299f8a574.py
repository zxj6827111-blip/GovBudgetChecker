from pathlib import Path
import os,sys,json,subprocess,hashlib,datetime
w=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
out=w/'outputs/ir3_gap02_fix_20261106';py='/Volumes/workSSD/software development/GovBudgetChecker/.venv/bin/python'
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=w,text=True).strip()
env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONPATH']=str(w)+os.pathsep+str(w/'outputs/independent_r3_review_20261106/deps')
# 与上一轮相同的离线测试边界，避免环境意外接入真实 AI/数据库。
for key in list(env):
 if key.startswith('GOVBUDGET_TEST_'):env.pop(key)
jobs=[('original36.log',[py,str(out/'scripts/independent_boundary_controls.py')]),('extra30.log',[py,str(out/'scripts/extra_controls.py')]),('neighbor29.log',[py,str(out/'scripts/neighbor_controls.py')]),
('masking6.log',[py,str(out/'scripts/masking_boundary_controls.py')]),('legal_row1.log',[py,str(out/'scripts/legal_row_label_control.py')]),('exact_pair.log',[py,str(out/'scripts/exact_masking_pair.py')]),
('focused258.log',[py,'-m','pytest','tests/test_effect_acceptance_v3.py','tests/remediation/test_narrative_zero_evidence.py','tests/test_independent_scope_supplement.py','tests/test_verify_pending_review.py','tests/test_unified_receipt_repairs.py','-q','-p','no:cacheprovider']),
('geometry38.log',[py,'-m','pytest','tests/test_effect_fact_source_geometry.py','-q','-p','no:cacheprovider']),
('row_boundary21.log',[py,'-m','pytest','tests/test_effect_row_label_boundary.py','-q','-p','no:cacheprovider']),
('full_tests.log',[py,'-m','pytest','tests','-q','-rs','-p','no:cacheprovider','--junitxml='+str(out/'full_suite.xml')]),
('mypy.log',[py,'-m','mypy','api','src','tests','--cache-dir','/private/tmp/gbc-gap02-mypy']),
('ruff.log',[str(Path(py).parent/'ruff'),'check','--no-cache','scripts/effect_fact_sources.py','tests/test_effect_row_label_boundary.py'])]
commands=[]
for log,cmd in jobs:
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=w,text=True).strip()==head
 assert not subprocess.check_output(['git','diff','HEAD','--name-only'],cwd=w,text=True)
 print('START',log,flush=True)
 with (out/log).open('w') as f:r=subprocess.run(cmd,cwd=w,env=env,stdout=f,stderr=subprocess.STDOUT)
 commands.append({'log':log,'command':cmd,'cwd':str(w),'candidate_sha':head,'exit_code':r.returncode,'completed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'log_sha256':hashlib.sha256((out/log).read_bytes()).hexdigest(),'environment':{'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':env['PYTHONPATH'],'GOVBUDGET_TEST_variables':'unset for offline tests'}})
 (out/'check_commands.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2)+'\n')
 print('DONE',log,r.returncode,(out/log).read_text().splitlines()[-1:],flush=True)
 if r.returncode:sys.exit(r.returncode)
 if log in ['original36.log','extra30.log','neighbor29.log','masking6.log','legal_row1.log']:
  for p in (out/'receipts').glob('*.json'):
   data=json.loads(p.read_text())
   if isinstance(data,list):assert all(x.get('expectation_met') for x in data),(p,data)
