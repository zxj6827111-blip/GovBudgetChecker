"""分维度确认的真实事务、幂等与并发验证；只使用显式临时测试库。"""
import asyncio
from uuid import uuid4
import pytest
from fastapi import HTTPException
from tests.test_review_lifecycle_pg import review_db, _conn, _make_reviewable_slot, _coverage_payload
from src.services import review_lifecycle_service as lifecycle


def choice(value,revision=0,ack=True):
    return dict(value=value,expected_revision=revision,note='test-only independent evidence',evidence_reference='test-only page 2',acknowledge_conflicts=ack,cover_independent=True)


async def setup(conn):
    meta={**_coverage_payload(),'document_profile':{'report_kind':{'value':'final'}},
          'document_scope_resolution':{'evidence':[{'scope':'department','page':2,'text':'test-only opposite evidence'}]}}
    material=await _make_reviewable_slot(conn,result_meta=meta,slot_fields={'mapping_key':uuid4().hex})
    await conn.execute('UPDATE analysis_jobs SET file_hash=$2 WHERE job_uuid=$1',material['job_uuid'],'a'*64)
    state=await lifecycle._load_state(conn,material['slot_id'],lock_slot=False)
    request=dict(request_id=str(uuid4()),document_version_id=material['version_id'],file_hash='a'*64,analysis_basis_token=state.basis_token,writing=choice('unit'),amount=None)
    return material,request


@pytest.mark.asyncio
async def test_writing_only_joint_unknown_and_idempotency(review_db):  # noqa: F811 - pytest fixture
    schema,pool=review_db
    async with _conn(schema,pool) as conn:
        material,request=await setup(conn)
        result=await lifecycle.confirm_material_dimensions(conn,material['job_uuid'],actor='test',request=request,actual_file_hash='a'*64)
        assert result['writing']['writing_scope']=='unit'
        assert await conn.fetchval('SELECT count(*) FROM document_scope_decisions')==0
        same=await lifecycle.confirm_material_dimensions(conn,material['job_uuid'],actor='test',request=request,actual_file_hash='a'*64)
        assert same['writing']['revision']==1
        assert await conn.fetchval('SELECT count(*) FROM document_writing_scope_decisions')==1
        with pytest.raises(HTTPException):
            await lifecycle.confirm_material_dimensions(conn,material['job_uuid'],actor='test',request={**request,'writing':choice('department')},actual_file_hash='a'*64)
        material,request=await setup(conn)
        request.update(writing=choice('unknown'),amount=choice('unknown'))
        result=await lifecycle.confirm_material_dimensions(conn,material['job_uuid'],actor='test',request=request,actual_file_hash='a'*64)
        assert result['writing']['writing_scope']=='unknown' and result['amount']['material_scope']=='unknown'


@pytest.mark.asyncio
async def test_joint_conflict_rolls_back_and_concurrent_confirmation(review_db):  # noqa: F811 - pytest fixture
    schema,pool=review_db
    async with _conn(schema,pool) as conn:
        material,request=await setup(conn)
        with pytest.raises(HTTPException):
            await lifecycle.confirm_material_dimensions(conn,material['job_uuid'],actor='test',request={**request,'writing':choice('unit',ack=False),'amount':choice('unknown')},actual_file_hash='a'*64)
        assert await conn.fetchval('SELECT count(*) FROM document_scope_decisions')==0
        assert await conn.fetchval('SELECT count(*) FROM document_writing_scope_decisions')==0
    async def submit():
        async with _conn(schema,pool) as conn:
            try:return await lifecycle.confirm_material_dimensions(conn,material['job_uuid'],actor='test',request={**request,'request_id':str(uuid4())},actual_file_hash='a'*64)
            except HTTPException as exc:return exc.status_code
    results=await asyncio.gather(submit(),submit())
    assert sum(isinstance(r,dict) for r in results)==1 and 409 in results


@pytest.mark.asyncio
async def test_writing_outbox_failure_and_retry_are_durable(review_db,monkeypatch,tmp_path):  # noqa: F811 - pytest fixture
    from unittest.mock import AsyncMock
    import hashlib
    import json
    from api import runtime
    from src.db.connection import DatabaseConnection
    from src.services import document_scope,analysis_result_store
    schema,pool=review_db
    async with _conn(schema,pool) as conn:
        material,request=await setup(conn)
        content=b'test-only original'
        digest=hashlib.sha256(content).hexdigest()
        request['file_hash']=digest
        await conn.execute('UPDATE analysis_jobs SET file_hash=$2 WHERE job_uuid=$1',material['job_uuid'],digest)
        await lifecycle.confirm_material_dimensions(conn,material['job_uuid'],actor='test',request=request,actual_file_hash=digest)
        directory=tmp_path/material['job_uuid']
        directory.mkdir()
        (directory/'source.pdf').write_bytes(content)
        (directory/'status.json').write_text(json.dumps({'status':'done'}))
        monkeypatch.setenv('DATABASE_URL','explicit-isolated-test')
        monkeypatch.setattr(runtime,'UPLOAD_ROOT',tmp_path)
        monkeypatch.setattr(DatabaseConnection,'acquire',AsyncMock(return_value=conn))
        monkeypatch.setattr(DatabaseConnection,'release',AsyncMock())
        monkeypatch.setattr(analysis_result_store,'ensure_analysis_persistence_ready',AsyncMock(return_value=True))
        enqueue=AsyncMock(side_effect=RuntimeError('test-only queue interruption'))
        monkeypatch.setattr(runtime,'reanalyze_job',enqueue)
        assert await document_scope.dispatch_writing_pending()==0
        saved=await document_scope.latest_writing_decision(conn,material['job_uuid'],digest,material['version_id'])
        assert saved['dispatch_status']=='pending' and saved['dispatch_attempts']==1 and saved['dispatch_error']
        await conn.execute('UPDATE document_writing_scope_decisions SET retry_not_before=NULL')
        enqueue.side_effect=None
        enqueue.return_value=None
        assert await document_scope.dispatch_writing_pending()==1
        assert (await document_scope.latest_writing_decision(conn,material['job_uuid'],digest,material['version_id']))['dispatch_status']=='queued'
        assert await document_scope.dispatch_writing_pending()==0
        assert enqueue.await_count==2
