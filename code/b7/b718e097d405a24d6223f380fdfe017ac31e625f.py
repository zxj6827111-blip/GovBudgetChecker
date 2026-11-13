import os,sys,json
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];MAIN=ROOT/'artifacts/daily-main'
env=json.loads((OUT/'private/effective_environments.json').read_text())['api'];os.environ.update(env);sys.path.insert(0,str(MAIN))
from api import runtime
seen=set();selected=[];skipped=[]
for folder in sorted(runtime.iter_job_dirs(),key=runtime.get_job_quick_timestamp,reverse=True):
 p=folder/'status.json'
 if not p.exists():continue
 s=json.loads(p.read_text());scope=runtime.resolve_job_selection_scope(folder.name,status_payload=s)
 if not scope or not scope.get('department_id'):skipped.append(dict(job=folder.name,reason='unresolved_scope'));continue
 key=str(scope.get('scope_id') or scope['department_id'])
 if key in seen:continue
 seen.add(key);selected.append(dict(job=folder.name,filename=s.get('filename'),year=s.get('report_year'),status=s.get('status'),revision=s.get('analysis_revision'),scope=scope))
(OUT/'daily_latest_preview.json').write_text(json.dumps(dict(selected=selected,unresolved=skipped),ensure_ascii=False,indent=2));print('selected',len(selected),'unresolved',len(skipped))
