"""完整原件隔离重放：原件只读，生产配置/数据库/任务不写入。"""
import os,json,subprocess,shutil,sys,time,gzip,argparse,hashlib,concurrent.futures,threading
from pathlib import Path
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'outputs/comprehensive_review_closure_20261109';PYTHON=ROOT/'artifacts/daily-main/.venv/bin/python'
a=argparse.ArgumentParser();a.add_argument('--code-root',type=Path,required=True);a.add_argument('--label',required=True);a.add_argument('--jobs',nargs='*');a.add_argument('--workers',type=int,default=2);a.add_argument('--resume',action='store_true');args=a.parse_args();args.code_root=args.code_root.resolve()
profile=json.loads((ROOT/'outputs/claude_closeout_20261108/gold33_final2/meta.json').read_text())['profile'];profile.update(GBC_TEMPLATE_SCOPE='shanghai',GBC_BUDGET_TEMPLATE_SCOPE='shanghai',GOVBUDGET_REPORTING_TASKS_ENABLED='false')
entries=[];lock=threading.Lock();index={};excluded=[]
for p in (OUT/'frozen').glob('*/status.json.gz'):
 s=json.loads(gzip.decompress(p.read_bytes()));job=p.parent.name
 if args.jobs and job not in args.jobs:continue
 folder=ROOT/'uploads'/job;candidates=[x for x in folder.glob('*.pdf') if x.name==s.get('filename')]
 if len(candidates)!=1:
  candidates=[x for x in folder.glob('*.pdf') if hashlib.sha256(x.read_bytes()).hexdigest()==s.get('checksum')]
 if len(candidates)!=1:
  excluded.append(dict(job=job,reason='canonical_pdf_missing_or_ambiguous',filename=s.get('filename')));continue
 index[job]=(s,candidates[0])
priority=['b5ed45cf74b47da8ac7f26f7eb89ae5b','23734f26696d2271a1e7246d80329a51','a2dfd43f42d932ae2f424635d199ebdc','adc362450611fda386326f0401070d08','d0aa8eb9dba80b04acd6bd712a544a8f','e03f4b4c2e8bff80339122aa0979b56e','e70c220f61dcc7f836b8add753c1109f','fa759cab8d4ca7bfa6ee41feda258951']
ordered=sorted(index,key=lambda j:(priority.index(j) if j in priority else (8 if any(x in index[j][0].get('filename','') for x in ['曹杨','建设和管理委员会']) else 9),j))
(OUT/(args.label+'_source_manifest.json')).write_text(json.dumps({j:dict(filename=s.get('filename'),pdf=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for j,(s,p) in index.items()},ensure_ascii=False,indent=2));(OUT/(args.label+'_excluded.json')).write_text(json.dumps(excluded,ensure_ascii=False,indent=2))
if args.resume:
 previous=OUT/(args.label+'_summary.json')
 if previous.exists():
  prior=json.loads(previous.read_text());entries.extend(e for e in prior if e.get('ok') and e['job'] in index)
  completed={e['job'] for e in entries};ordered=[j for j in ordered if j not in completed]
  for job in ordered:
   base=OUT/args.label/job
   if base.exists():
    interrupted=OUT/(args.label+'_interrupted')/job;interrupted.parent.mkdir(exist_ok=True);assert not interrupted.exists();base.rename(interrupted)
 print('RESUME',len(entries),'complete;',len(ordered),'remaining',flush=True)
def run(job):
 s,pdf=index[job];base=OUT/args.label/job;target=base/'uploads'/job;target.mkdir(parents=True,exist_ok=True)
 org=base/'org';org.mkdir(exist_ok=True)
 for n in ['organizations.json','job_org_links.json']:
  f=ROOT/'data'/n
  if f.exists():shutil.copyfile(f,org/n)
 shutil.copyfile(pdf,target/pdf.name)
 meta_p=OUT/'frozen'/job/'job_meta.json.gz';meta=json.loads(gzip.decompress(meta_p.read_bytes())) if meta_p.exists() else {}
 initial={**s,**meta,'job_id':job,'checksum':hashlib.sha256(pdf.read_bytes()).hexdigest(),'status':'queued','use_local_rules':True,'use_ai_assist':False,'mode':'legacy'}
 for n in ['result','review_report','structured_ingest','fact_overlay','check_coverage','expected_current_fact_revision_id','analysis_revision','human_decisions']:initial.pop(n,None)
 initial['saved_path']=str(target/pdf.name)
 (target/'status.json').write_text(json.dumps(initial,ensure_ascii=False));(target/'job_meta.json').write_text(json.dumps(meta,ensure_ascii=False))
 env={k:os.environ[k] for k in ['PATH','HOME','LANG','TMPDIR'] if k in os.environ};env.update(profile);env.update(ORG_DATA_DIR=str(org),UPLOAD_DIR=str(base/'uploads'),AUDIT_LOG_PATH=str(base/'audit.jsonl'),AI_MODEL_SETTINGS_PATH=str(base/'absent_ai.json'),DISCLOSURE_CALENDAR_PATH=str(base/'absent_calendar.json'))
 task={'kind':'pipeline','job_dir':str(target),'dotenv_names':[],'pinned_names':sorted(profile)};(base/'task.json').write_text(json.dumps(task));result=base/'worker_result.json';t=time.time();record={}
 with (base/'worker.log').open('w') as log:
  try:
   subprocess.run([str(PYTHON),'-s',str(ROOT/'scripts/gold_baseline_run.py'),'_worker','--code-root',str(args.code_root),'--task',str(base/'task.json'),'--result',str(result)],cwd=args.code_root,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=300)
   record=json.loads(result.read_text()) if result.exists() else {'ok':False,'error':'no_result'}
  except subprocess.TimeoutExpired:record={'ok':False,'error':'timeout'}
 status=json.loads((target/'status.json').read_text());todo=record.get('todo') or {};entry=dict(job=job,filename=s.get('filename'),ok=record.get('ok'),status=status.get('status'),seconds=round(time.time()-t,2),error=record.get('error'),todo=todo)
 with lock:
  entries.append(entry);(OUT/(args.label+'_summary.json')).write_text(json.dumps(entries,ensure_ascii=False,indent=2));print(len(entries),'/',len(index),job,entry['status'],entry['seconds'],'blocking',todo.get('blocking_ids'),'error',entry['error'],flush=True)
 return entry
with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:list(pool.map(run,ordered))
print('COMPLETE',len(entries),'excluded',len(excluded),'worker_failures',sum(not e['ok'] for e in entries),flush=True)
