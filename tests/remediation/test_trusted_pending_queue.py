"""软预算未完成时仍然续跑；不能写成完成，也不重复同时领取。"""
import asyncio
import time
import pytest
from api import runtime
from api.job_queue import DurableJobQueue


@pytest.mark.asyncio
async def test_pending_ocr_checkpoint_requeues_after_delay_and_finishes_once(tmp_path,monkeypatch):
    monkeypatch.setattr(runtime,'UPLOAD_ROOT',tmp_path)
    job=tmp_path/'source-job'
    job.mkdir()
    runtime.write_json_file(job/'status.json',{'status':'queued'})
    calls=[]
    async def runner(directory):
        calls.append(directory.name)
        if len(calls)==1:
            runtime.merge_job_status(directory,{'status':'queued','retry_not_before':time.time()+.15,
                'ocr_pending':{'status':'pending','completed_tiles':1,'remaining_tiles':1}})
        else:
            saved=runtime.read_json_file(directory/'status.json',default={})
            assert saved['ocr_pending']['completed_tiles']==1
            runtime.merge_job_status(directory,{'status':'done'})
    queue=DurableJobQueue(runner,max_workers=1,poll_interval_seconds=.05)
    await queue.start()
    try:
        deadline=time.monotonic()+3
        while runtime.read_json_file(job/'status.json',default={}).get('status')!='done':
            if time.monotonic()>deadline:raise AssertionError('pending checkpoint was not resumed')
            await asyncio.sleep(.02)
        assert calls==['source-job','source-job']
    finally:
        await queue.stop()
