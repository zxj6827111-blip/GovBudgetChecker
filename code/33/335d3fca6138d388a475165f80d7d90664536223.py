import json,time,collections,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];before=json.loads((OUT/'deployment_final_preflight.json').read_text());jobs=[r['job'] for r in json.loads((OUT/'daily_latest_preview.json').read_text())['selected']];cache={};signatures={};started=time.time();seen=None;initial_signatures={j:((ROOT/'uploads'/j/'status.json').stat().st_mtime_ns,(ROOT/'uploads'/j/'status.json').stat().st_size) for j in jobs}
while time.time()-started<7200:
 for job in jobs:
  p=ROOT/'uploads'/job/'status.json';signature=(p.stat().st_mtime_ns,p.stat().st_size)
  if signatures.get(job)!=signature:
   s=json.loads(p.read_text());meta=(s.get('result') or {}).get('meta') or {};summary=meta.get('rule_execution_summary') or {}
   cache[job]=dict(status=s.get('status'),revision=s.get('analysis_revision'),stage=s.get('stage'),use_ai_assist=s.get('use_ai_assist'),execution_errors=summary.get('execution_error',0),parse_errors=summary.get('parse_error',0))
   signatures[job]=signature
 finished=[j for j,s in cache.items() if (s['revision'] and s['revision']!=before['revisions'][j] and s['status'] in ['done','completed','review_required','degraded']) or (s['status'] in ['error','failed'])]
 errors=[dict(job=j,**cache[j]) for j in finished if cache[j]['status'] in ['error','failed'] or cache[j]['execution_errors'] or cache[j]['parse_errors']]
 result=dict(expected=len(jobs),finished=len(finished),status_counts=dict(collections.Counter(s['status'] for s in cache.values())),errors=errors,elapsed_seconds=round(time.time()-started),rows=cache)
 (OUT/'daily_final_progress.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 key=(result['finished'],json.dumps(result['status_counts'],sort_keys=True),len(errors))
 if key!=seen:print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False),flush=True);seen=key
 if len(finished)==len(jobs):
  (OUT/'daily_final_finished.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(1 if errors else 0)
 time.sleep(5)
raise SystemExit('Daily jobs still running: preserve state; do not resubmit')
