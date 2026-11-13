import copy,gzip,json,subprocess,os
from pathlib import Path
R=Path(__file__).parent;B=R.parents[1];W=B/'outputs/unified_closeout_round4_20261104_work'
sid='GS-2024-06';src=W/'gold_r4_1';dst=R/'fragment_pool_mutant';dst.mkdir(exist_ok=True)
for p in src.iterdir():
 if p.name=='status':
  (dst/p.name).mkdir(exist_ok=True)
  for f in p.iterdir():
   if f.name!=sid+'.status.json.gz' and not (dst/p.name/f.name).exists():(dst/p.name/f.name).symlink_to(f.resolve())
 elif not (dst/p.name).exists():(dst/p.name).symlink_to(p.resolve(),target_is_directory=p.is_dir())
data=json.loads(gzip.decompress((src/'status'/f'{sid}.status.json.gz').read_bytes()));changes=[]
def rewrite(x):
 if isinstance(x,str):return x.replace('1,659.14','9.99').replace('1659.14','9.99')
 if isinstance(x,list):return [rewrite(v) for v in x]
 if isinstance(x,dict):return {k:rewrite(v) for k,v in x.items()}
 return x
def visit(x,path=''):
 if isinstance(x,dict):
  if x.get('id')=='CMM-011-19':
   before=copy.deepcopy(x);changed=rewrite(x);x.clear();x.update(changed)
   x.setdefault('location',{})['locations']=[
    {'page':27,'text':'自然资源海洋气象等支出','source_block_id':'native:p27:s528:e540','source_span':[528,540],'bbox':[269,548,420,563],'row':'自然资源海洋气象等支出'},
    {'page':27,'text':'1,659.14','source_block_id':'native:p27:s900:e908','source_span':[900,908],'bbox':[200,700,260,715],'row':'其他项目支出'}]
   changes.append({'path':path,'before':before,'after':copy.deepcopy(x)});return
  for k,v in x.items():visit(v,path+'/'+k)
 elif isinstance(x,list):
  for i,v in enumerate(x):visit(v,path+'/'+str(i))
visit(data)
(dst/'status'/f'{sid}.status.json.gz').write_bytes(gzip.compress(json.dumps(data,ensure_ascii=False).encode()))
(R/'fragment_pool_changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2))
PY=str(B/'.venv/bin/python');env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',DATABASE_URL='')
for name,cmd in [('eval',[PY,str(R/'run_evaluator_snapshot.py'),'--run',str(dst),'--out',str(R/'fragment_pool_eval')]),('final',[PY,str(R/'delivery_tools/build_final_ledger.py'),'--ledger',str(R/'fragment_pool_eval/target_ledger.json'),'--out',str(R/'fragment_pool_final')])]:
 with (R/('fragment_pool_'+name+'.log')).open('w') as f:p=subprocess.run(cmd,cwd=R,env=env,stdout=f,stderr=subprocess.STDOUT)
 print(name,p.returncode,flush=True)
