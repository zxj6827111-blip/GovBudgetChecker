import copy,gzip,json,re,subprocess,os
from pathlib import Path

R=Path(__file__).parent;BASE=R.parents[1];W=BASE/'outputs/unified_closeout_round3_20261104_work'
PY=str(BASE/'.venv/bin/python');ENV=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',DATABASE_URL='')

def make_run(name,sample):
    src=W/'gold_r3_1';dst=R/name;dst.mkdir()
    for p in src.iterdir():
        if p.name=='status':
            (dst/'status').mkdir()
            for f in p.iterdir():
                if f.name!=sample+'.status.json.gz':(dst/'status'/f.name).symlink_to(f.resolve())
        else:(dst/p.name).symlink_to(p.resolve(),target_is_directory=p.is_dir())
    data=json.loads(gzip.decompress((src/'status'/f'{sample}.status.json.gz').read_bytes()))
    return dst,data

def rewrite(x,fn):
    if isinstance(x,str):return fn(x)
    if isinstance(x,list):return [rewrite(v,fn) for v in x]
    if isinstance(x,dict):return {k:rewrite(v,fn) for k,v in x.items()}
    return x

name='neighbor_amount_mutant';sid='GS-2024-06';dst,data=make_run(name,sid);changes=[]
def visit(x,path=''):
    if isinstance(x,dict):
        if x.get('id')=='CMM-011-19':
            before=copy.deepcopy(x)
            def f(s):
                if '1,659.14' in s or '1659.14' in s:
                    return s.replace('1,659.14','9.99').replace('1659.14','9.99')+'；其他项目支出1,659.14万元。'
                return s
            changed=rewrite(x,f);x.clear();x.update(changed);changes.append({'path':path,'before':before,'after':copy.deepcopy(x)})
            return
        for k,v in x.items():visit(v,path+'/'+k)
    elif isinstance(x,list):
        for i,v in enumerate(x):visit(v,path+'/'+str(i))
visit(data)
(dst/'status'/f'{sid}.status.json.gz').write_bytes(gzip.compress(json.dumps(data,ensure_ascii=False).encode()))
(R/'neighbor_amount_changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2))

name='formatted_fp_mutant';sid='GS-2022-03';dst,data=make_run(name,sid)
old=json.loads(gzip.decompress((W/'fp_mutant_run/status'/f'{sid}.status.json.gz').read_bytes()))
item=copy.deepcopy(next(x for x in old['review_report']['issues'] if x['id']=='V33-117-999'))
before=copy.deepcopy(item)
item['message']=re.sub(r'(?<!\d)(\d[\d,]*\.\d+)',lambda m:m.group(1)+'0',item['message'])
data['review_report']['issues'].append(item)
(dst/'status'/f'{sid}.status.json.gz').write_bytes(gzip.compress(json.dumps(data,ensure_ascii=False).encode()))
(R/'formatted_fp_changes.json').write_text(json.dumps({'before':before,'after':item},ensure_ascii=False,indent=2))

for name in ('neighbor_amount_mutant','formatted_fp_mutant'):
    for stage,cmd in [
      ('eval',[PY,str(R/'run_evaluator_snapshot.py'),'--run',str(R/name),'--out',str(R/(name+'_eval'))]),
      ('final',[PY,str(R/'delivery_tools/build_final_ledger.py'),'--ledger',str(R/(name+'_eval/target_ledger.json')),'--out',str(R/(name+'_final'))])]:
        with (R/f'{name}_{stage}.log').open('w') as log:
            p=subprocess.run(cmd,cwd=R,env=ENV,stdout=log,stderr=subprocess.STDOUT)
        print(name,stage,p.returncode,flush=True)
        (R/f'{name}_{stage}_command.json').write_text(json.dumps({'cmd':cmd,'exit':p.returncode},ensure_ascii=False,indent=2))
