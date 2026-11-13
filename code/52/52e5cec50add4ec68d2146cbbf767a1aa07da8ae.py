import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import httpx

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
RT=OUT.parent/'runtime'
WT=ROOT/'artifacts/pdf-ai-local-acceptance-20261026'
env=json.loads((RT/'environment.json').read_text())
os.environ.update(env)
sys.path.insert(0,str(WT))
from scripts.run_isolated_service_validation import database_receipt, structured_ingest_from_payload, structured_ingest_validation

JOBS={'department':('bdba38fab5eab35c2e2c03539ed7036c','5e26afdec7b9'), 'unit':('a296c928fbd69d0b173a5050667e3b6e','8c0ba1fafea0')}

def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str))
def checked(r):
    if not r.is_success:raise RuntimeError(f'{r.request.url.path}: {r.status_code} {r.text[:700]}')
    return r

with httpx.Client(base_url='http://127.0.0.1:3013',timeout=180) as client:
    checked(client.post('/api/auth/login',json=json.loads((RT/'validator.json').read_text())))
    for label,(job,org) in JOBS.items():
        path=OUT/label;path.mkdir(exist_ok=True)
        old=json.loads((RT/'uploads'/job/'status.json').read_text())
        assert old['organization_id']==org
        if '--resume' in sys.argv and old['status'] in {'processing','queued','running','review_required','done'} and '--force-all-ai' not in sys.argv and not (label=='unit' and '--force-unit-ai' in sys.argv):
            print(label,'continue existing reanalysis',flush=True)
            continue
        profile=old.get('ai_profile_id') or json.loads((RT/'data/ai-model-settings.json').read_text())['active_profile_id']
        request={'mode':'dual','use_local_rules':True,'use_ai_assist':True,'ai_profile_id':profile}
        if label=='unit' and '--unit-rules' in sys.argv:
            request.update(mode='legacy',use_ai_assist=False)
        save(path/'reanalyze_request.json',request)
        save(path/'reanalyze_response.json',checked(client.post(f'/api/jobs/{job}/reanalyze',json=request)).json())
        print(label,'reanalysis requested',job,flush=True)
    pending=set(JOBS);last={};deadline=time.monotonic()+1000
    while pending and time.monotonic()<deadline:
        for label in list(pending):
            job,org=JOBS[label]
            state=checked(client.get(f'/api/jobs/{job}/status')).json()
            phase=(state.get('status'),(state.get('stage_progress') or {}).get('phase'))
            if last.get(label)!=phase:print(label,phase,flush=True);last[label]=phase
            if state.get('status') in {'queued','processing','running','pending'}:continue
            save(OUT/label/'status.json',state)
            assert state['status'] not in {'error','failed'},state.get('error')
            assert state['organization_id']==org
            ingest=structured_ingest_from_payload(state)
            db=asyncio.run(database_receipt(env['DATABASE_URL'],job,ingest))
            save(OUT/label/'database.json',db)
            storage=structured_ingest_validation(state,db)
            assert storage['passed'],storage
            for fmt in ('json','csv'):
                report=checked(client.get('/api/reports/download',params={'job_id':job,'format':fmt}))
                (OUT/label/f'report.{fmt}').write_bytes(report.content)
                assert state['analysis_revision'] in report.text
            pdf=next((RT/'uploads'/job).glob('*.pdf'))
            before=next((OUT/'before'/job).glob('*.pdf'))
            assert hashlib.sha256(pdf.read_bytes()).digest()==hashlib.sha256(before.read_bytes()).digest()
            save(OUT/label/'receipt.json',{'job_id':job,'organization_id':org,'organization_name':state['organization_name'],
                'analysis_revision':state['analysis_revision'],'status':state['status'],'storage':storage,
                'structured_ingest_status':ingest['status'],'document_version_id':ingest['document_version_id'],
                'material_slot':ingest.get('material_slot'),'ai_execution':state['result']['meta'].get('ai_execution'),
                'ai_coverage':state['result']['meta'].get('ai_coverage'),'source_unchanged':True})
            print(label,'completed',state['status'],json.dumps(state['result']['meta'].get('ai_coverage'),ensure_ascii=False),flush=True)
            pending.remove(label)
        if pending:time.sleep(2)
    assert not pending,f'Reanalysis deadline reached: {pending}'
print('Both original files reanalyzed and exported with separate organization identities',flush=True)
