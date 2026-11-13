"""只读定位单条复核的实际数据处理耗时，凭据不写入输出。"""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
import asyncpg
from api import runtime
from api.routes.files import _render_preview_png
from src.services import issue_workflow_store as workflow
from src.services import review_lifecycle_service as reviews

async def main():
    job_id='7be587eeff3114c0232c5f6c56033816'
    assert not any(runtime.get_ignored_issues_path(p).is_file() for p in runtime.iter_job_dirs())
    records=[]
    original=runtime.get_job_status_payload
    reads=0
    def counted(*args,**kwargs):
        nonlocal reads
        reads+=1
        return original(*args,**kwargs)
    runtime.get_job_status_payload=counted
    def measure(label,call):
        nonlocal reads
        reads=0
        start=time.perf_counter()
        result=call()
        record={'phase':label,'seconds':time.perf_counter()-start,'full_detail_reads':reads}
        records.append(record)
        print(json.dumps(record),flush=True)
        return result
    measure('detail_cold',lambda:runtime.get_job_status_payload(job_id))
    measure('detail_warm',lambda:runtime.get_job_status_payload(job_id))
    def structured():
        runtime.get_job_status_payload(job_id)
        return runtime.get_job_review_payload(job_id)
    measure('structured_ingest_with_auth',structured)
    state=measure('workflow_all_tasks',lambda:workflow.get_visible_state({'username':'performance-probe','is_admin':True}))
    records[-1]['returned_issues']=len(state['issues'])
    def preview():
        runtime.get_job_status_payload(job_id)
        return _render_preview_png(runtime.find_first_pdf(runtime.UPLOAD_ROOT/job_id),page_number=1,bbox=None,padding=0,scale=1.6)
    image=measure('preview_with_auth',preview)
    records[-1]['bytes']=len(image)
    conn=await asyncpg.connect(os.environ['DATABASE_URL'],timeout=5)
    try:
        async with conn.transaction(readonly=True):
            reads=0
            start=time.perf_counter()
            data=await reviews.load_review_by_job(conn,job_id)
            records.append({'phase':'review_lifecycle','seconds':time.perf_counter()-start,'full_detail_reads':reads})
            print(json.dumps(records[-1]),flush=True)
    finally:
        await conn.close()
    (Path(__file__).parent/'before-timings.json').write_text(json.dumps(records,indent=2))

asyncio.run(main())
