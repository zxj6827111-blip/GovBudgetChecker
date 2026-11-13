import json, os, time
from pathlib import Path
import fitz, httpx
from dotenv import load_dotenv
load_dotenv('/Users/zhangxingjin/gbc-acceptance-r3/.env')
BASE='http://127.0.0.1:18745'
OUT=Path('/Volumes/workSSD/software development/GovBudgetChecker/scratch/ai_remediation_20261025/acceptance3')
pdf=Path('/Users/zhangxingjin/gbc-acceptance-r3/acceptance_runtime/recheck_fixture.pdf')
doc=fitz.open(); page=doc.new_page()
lines=[
 '2025年度部门预算公开说明',
 '单位名称：测试单位',
 '一、主要职能',
 '本单位负责测试业务。',
 '二、机构设置',
 '本单位内设一个科室。',
 '三、预算编制说明',
 '2025年收入预算为100万元，支出预算为90万元。',
 '四、其他相关情况说明',
 '本单位无三公经费预算。',
]
y=72
for line in lines:
    page.insert_text((72,y),line,fontsize=12); y+=24
doc.save(pdf); doc.close()
c=httpx.Client(timeout=120,headers={'X-API-Key':os.getenv('GOVBUDGET_API_KEY','')})
t=c.post(BASE+'/api/auth/login',json={'username':'admin','password':'admin123'}).json()['token']; c.headers['X-Session-Token']=t
with pdf.open('rb') as h:
    up=c.post(BASE+'/api/documents/upload',files={'file':(pdf.name,h,'application/pdf')},data={'fiscal_year':'2025','doc_type':'dept_budget'})
print('upload',up.status_code,up.text[:200]); up.raise_for_status(); job=up.json()['job_id']
r=c.post(BASE+f'/api/analyze/{job}',json={'mode':'dual','use_ai_assist':False,'use_local_rules':True}); print('analyze',r.status_code,r.text[:200]); r.raise_for_status()
for _ in range(60):
    time.sleep(3); s=c.get(BASE+f'/api/jobs/{job}/status').json(); print('state',s.get('status'),s.get('progress'),s.get('stage'))
    if s.get('status') in {'done','review_required','degraded','error','failed'}: break
(OUT/'RECHECK_fixture_job.json').write_text(json.dumps({'job_id':job,'upload':up.json()},ensure_ascii=False,indent=2))
(OUT/'RECHECK_fixture_status_before.json').write_text(json.dumps(s,ensure_ascii=False,indent=2))
res=s.get('result') or {}
print('findings',len(res.get('rule_findings') or []),len(res.get('ai_findings') or []))
for it in (res.get('rule_findings') or [])[:10]: print(' ',it.get('id'),it.get('rule_id'),it.get('title'))
