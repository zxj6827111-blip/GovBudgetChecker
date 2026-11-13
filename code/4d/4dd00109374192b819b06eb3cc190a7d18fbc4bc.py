"""分析仍在运行时只读采样API健康，验证API/worker隔离的实际效果。"""
import json,time,urllib.request
from pathlib import Path
OUT=Path(__file__).resolve().parent;release=json.loads((OUT/'release_candidate.json').read_text());pre=json.loads((OUT/'deployment_final_preflight.json').read_text());op=urllib.request.build_opener(urllib.request.ProxyHandler({}));samples=[];start=time.monotonic()
while time.monotonic()-start<7200:
 row=dict(elapsed_seconds=round(time.monotonic()-start))
 for name in ['health','ready']:
  before=time.monotonic()
  try:
   with op.open('http://127.0.0.1:18743/'+name,timeout=5) as response:row[name]=dict(status=response.status,seconds=round(time.monotonic()-before,3))
  except Exception as error:row[name]=dict(status=getattr(error,'code','failed'),error_type=type(error).__name__,seconds=round(time.monotonic()-before,3))
 progress=json.loads((OUT/'daily_final_progress.json').read_text());row['finished']=sum(bool(v.get('revision')) and v['revision']!=pre['revisions'].get(j) and v['status'] in {'done','completed','review_required','degraded'} for j,v in progress['rows'].items());row['processing']=progress['status_counts'].get('processing',0);samples.append(row)
 result=dict(candidate=release['candidate'],samples=len(samples),failures=sum(s[n]['status']!=200 for s in samples for n in ['health','ready']),max_seconds=max(s[n]['seconds'] for s in samples for n in ['health','ready']),rows=samples)
 (OUT/'runtime_recovery_health.json').write_text(json.dumps(result,indent=2))
 if row['finished']==111:print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True);break
 time.sleep(10)
else:raise RuntimeError('Health observation timeout; keep evidence')
