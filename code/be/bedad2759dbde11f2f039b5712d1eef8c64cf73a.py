import json, time
from pathlib import Path
import httpx
BASE="http://127.0.0.1:18744"; OUT=Path('scratch/ai_remediation_20261025/acceptance2')
SAMPLES={
 'S1':(Path('uploads/933e79a25e74b58365580b45f5df61ad/上海市文化和旅游局执法总队2022年度部门决算.pdf'),'2022'),
 'S2':(Path('uploads/2dc9c68127b0808d7661de536e2e6616/上海虹桥国际中央商务区管理委员会.pdf'),'2024'),
 'S3':(Path('uploads/5c9899d5f9d57bff5f49a35b82514203/上海市文化和旅游局2022年度部门决算.pdf'),'2022'),
}
client=httpx.Client(timeout=120)
token=client.post(BASE+'/api/auth/login',json={'username':'admin','password':'admin123'}).json()['token']; headers={'X-Session-Token':token}
for sid,(path,year) in SAMPLES.items():
    with path.open('rb') as f:
        r=client.post(BASE+'/api/documents/upload',headers=headers,files={'file':(path.name,f,'application/pdf')},data={'fiscal_year':year,'doc_type':'dept_final'})
    print(sid,'upload',r.status_code,r.text[:200]); r.raise_for_status(); up=r.json(); job=up['job_id']
    (OUT/f'{sid}_job.json').write_text(json.dumps({'job_id':job,'upload':up},ensure_ascii=False,indent=2),encoding='utf-8')
    r=client.post(BASE+f'/api/analyze/{job}',headers=headers,json={'mode':'dual','use_ai_assist':True,'use_local_rules':True})
    print(sid,'analyze',r.status_code,r.text[:200])
    deadline=time.time()+1500
    while time.time()<deadline:
        time.sleep(10)
        s=client.get(BASE+f'/api/jobs/{job}/status',headers=headers).json(); state=s.get('status')
        print(sid,'state',state,s.get('progress'),s.get('stage'))
        if state in {'done','review_required','degraded','error'}:
            (OUT/f'{sid}_status.json').write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')
            meta=(s.get('result') or {}).get('meta') or {}; ex=meta.get('ai_execution') or {}; cov=meta.get('ai_coverage') or {}
            print(sid,'terminal',state,'ai',ex.get('state'),'coverage',cov.get('status'),'calls',cov.get('completed_calls'),'/',cov.get('planned_calls'),'ai_count',len((s.get('result') or {}).get('ai_findings') or []),'rule_count',len((s.get('result') or {}).get('rule_findings') or []))
            break
    else: print(sid,'TIMEOUT')
