"""只读性能诊断；不使用账号凭据，不启动/完成复核。"""
import asyncio
import cProfile
import io
import json
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from api import runtime
from src.services.review_worklist_service import load_review_worklist
import asyncpg
import os

async def main():
    receipt = {}
    t = time.perf_counter()
    jobs = [runtime.collect_job_summary(path) for path in runtime.iter_job_dirs()]
    receipt['jobs_cold_seconds'] = time.perf_counter() - t
    t = time.perf_counter()
    jobs = [runtime.collect_job_summary(path) for path in runtime.iter_job_dirs()]
    receipt['jobs_warm_seconds'] = time.perf_counter() - t
    receipt['job_count'] = len(jobs)
    print(json.dumps(receipt), flush=True)
    conn = await asyncpg.connect(os.environ['DATABASE_URL'], timeout=5)
    try:
        async with conn.transaction(readonly=True):
            t = time.perf_counter()
            worklist_profiler = cProfile.Profile()
            if '--profile' in sys.argv:
                worklist_profiler.enable()
            data = await load_review_worklist(conn, jobs=jobs, visible_org_ids=None,
                    job_accessible=lambda job_id: bool(runtime.get_job_access_payload(job_id)), viewer='performance-probe')
            worklist_profiler.disable()
            worklist_stream = io.StringIO()
            if '--profile' in sys.argv:
                pstats.Stats(worklist_profiler,stream=worklist_stream).sort_stats('cumulative').print_stats(25)
                (Path(__file__).parent/'after-worklist-profile.txt').write_text(worklist_stream.getvalue())
            receipt['worklist_without_detail_auth_seconds'] = time.perf_counter()-t
            receipt['counts'] = data.counts
            print(json.dumps(receipt), flush=True)
            profiler=cProfile.Profile()
            t=time.perf_counter()
            if '--profile' in sys.argv:
                profiler.enable()
            runtime.get_job_status_payload(data.items[0].job_id)
            profiler.disable()
            receipt['one_full_detail_auth_seconds']=time.perf_counter()-t
            stream=io.StringIO()
            if '--profile' in sys.argv:
                pstats.Stats(profiler,stream=stream).sort_stats('cumulative').print_stats(20)
                (Path(__file__).parent/'after-detail-profile.txt').write_text(stream.getvalue())
    finally:
        await conn.close()
    (Path(__file__).parent/'after-timings.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt),flush=True)

asyncio.run(main())
