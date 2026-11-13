"""同一只读数据集对比旧/新读取，保留整份清单哈希，不记录材料明细或凭据。"""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
import asyncpg
from api import runtime
from src.services import issue_workflow_store as workflow
from src.services.review_worklist_service import load_review_worklist

async def main():
    # 有待迁移旧忽略文件时不做只读比较，防止诊断触发迁移写入。
    assert not any(runtime.get_ignored_issues_path(p).is_file() for p in runtime.iter_job_dirs())
    old_path=Path(__file__).parent/'before/src/services/issue_workflow_store.py'
    spec=importlib.util.spec_from_file_location('before_workflow', old_path)
    old=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    current_snapshot=workflow.get_job_problem_snapshot
    jobs=[runtime.collect_job_summary(p) for p in runtime.iter_job_dirs()]
    conn=await asyncpg.connect(os.environ['DATABASE_URL'],timeout=5)
    records=[]
    try:
        async with conn.transaction(readonly=True):
            for label in ['before', 'after_cold_decisions', 'after_warm_1', 'after_warm_2']:
                workflow.get_job_problem_snapshot=old.get_job_problem_snapshot if label=='before' else current_snapshot
                if label=='after_cold_decisions':
                    from src.services.ai_evidence_location import _locate_cached
                    _locate_cached.cache_clear()  # 只清当前诊断进程缓存，线上服务不受影响。
                full_reads=0
                original=runtime.get_job_status_payload
                def counted(*args,**kwargs):
                    nonlocal full_reads
                    full_reads+=1
                    return original(*args,**kwargs)
                runtime.get_job_status_payload=counted
                access=(lambda j: bool(runtime.get_job_status_payload(j))) if label=='before' else (lambda j: bool(runtime.get_job_access_payload(j)))
                start=time.perf_counter()
                try:
                    data=await load_review_worklist(conn,jobs=jobs,visible_org_ids=None,job_accessible=access,viewer='performance-probe')
                finally:
                    runtime.get_job_status_payload=original
                elapsed=time.perf_counter()-start
                encoded=json.dumps(data.model_dump(mode='json'),sort_keys=True,ensure_ascii=False).encode()
                record={'label':label,'seconds':elapsed,'full_detail_reads':full_reads,
                        'response_sha256':hashlib.sha256(encoded).hexdigest(),'counts':data.counts,'materials':len(data.items)}
                records.append(record)
                print(json.dumps(record),flush=True)
    finally:
        workflow.get_job_problem_snapshot=current_snapshot
        await conn.close()
    assert len({r['response_sha256'] for r in records})==1, 'response changed'
    (Path(__file__).parent/'comparison.json').write_text(json.dumps({'mode':'read_only_internal_query','records':records},indent=2))

asyncio.run(main())
