from pathlib import Path
import json,os,subprocess,sys
b=Path(__file__).resolve().parent
root=b.parents[1]
env=dict(os.environ,AUDIT_LOG_PATH=str(b/'test_audit.jsonl'),PYTHONUNBUFFERED='1')
steps=[]
for name,args in [('public_parse',['run_parser.py']),('bbox_comparison',['compare_source.py']),('native_identity',['compare_native_identity.py','public','--verify-pdf-characters']),('public_rules',['run_rules.py'])]:
 row={'step':name,'status':'running'};steps.append(row);(b/'execution.json').write_text(json.dumps(steps,indent=2));print('START',name,flush=True)
 with (b/f'{name}.log').open('w') as log:r=subprocess.run([sys.executable,str(b/args[0]),*args[1:]],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
 row.update(status='passed' if r.returncode==0 else 'failed',exit_code=r.returncode);(b/'execution.json').write_text(json.dumps(steps,indent=2));print('END',name,r.returncode,flush=True)
 if r.returncode:raise SystemExit(r.returncode)
