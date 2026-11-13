"""逐件核验发布链路；成功后才恢复模型复验。"""
import json,time,sys
from pathlib import Path
import httpx

OUT=Path(__file__).resolve().parent;RT=OUT.parent/'runtime'
label=sys.argv[1]
job={'department':'bdba38fab5eab35c2e2c03539ed7036c','unit':'a296c928fbd69d0b173a5050667e3b6e'}[label]
with httpx.Client(base_url='http://127.0.0.1:3013',timeout=180) as c:
 r=c.post('/api/auth/login',json=json.loads((RT/'validator.json').read_text()));r.raise_for_status()
 request={'mode':'legacy','use_local_rules':True,'use_ai_assist':False}
 r=c.post(f'/api/jobs/{job}/reanalyze',json=request);r.raise_for_status()
 deadline=time.monotonic()+240
 while time.monotonic()<deadline:
  r=c.get(f'/api/jobs/{job}/status');r.raise_for_status();s=r.json()
  if s['status'] not in {'pending','queued','processing','running'}:break
  time.sleep(.5)
 else:raise TimeoutError(label)
 target=OUT/f'{label}_rules_stage';target.mkdir(exist_ok=True)
 (target/'status.json').write_text(json.dumps(s,ensure_ascii=False,indent=2))
 assert s['status'] not in {'error','failed'},s.get('error')
 for fmt in ('json','csv'):
  r=c.get('/api/reports/download',params={'job_id':job,'format':fmt});r.raise_for_status();(target/f'report.{fmt}').write_bytes(r.content)
  assert s['analysis_revision'] in r.text
 print(label,s['status'],s['analysis_revision'],s['structured_ingest']['document_version_id'])
