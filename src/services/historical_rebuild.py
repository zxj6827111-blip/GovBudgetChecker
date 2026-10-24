"""候选分析独立留存、全内容对账、原子批次切换及可审计回滚。"""
from __future__ import annotations

import json
import uuid
from copy import deepcopy

from src.services.analysis_fact_snapshot import validate_snapshot, canonical_facts
from src.services.input_processing_identity import content_hash


def decoded(value):
    return json.loads(value) if isinstance(value,str) else value


def compare_snapshots(previous, candidate):
    validate_snapshot(candidate)
    if previous:
        validate_snapshot(previous)
        if previous['source_file_hash']!=candidate['source_file_hash']:
            raise ValueError('candidate original differs from historical original')
    old={f['fact_uid']:f for f in canonical_facts((previous or{}).get('effective_context',{}).get('facts',[]))}
    new={f['fact_uid']:f for f in canonical_facts(candidate['effective_context'].get('facts',[]))}
    changed=[{'fact_uid':uid,'fields':[k for k in new[uid]if new[uid][k]!=old[uid].get(k)]}
             for uid in old.keys()&new.keys()if old[uid]!=new[uid]]
    return {'source_file_hash':candidate['source_file_hash'],'previous_snapshot_hash':(previous or{}).get('snapshot_hash'),
        'candidate_snapshot_hash':candidate['snapshot_hash'],'added_fact_uids':sorted(new.keys()-old.keys()),
        'removed_fact_uids':sorted(old.keys()-new.keys()),'changed_facts':sorted(changed,key=lambda c:c['fact_uid']),
        'previous_count':len(old),'candidate_count':len(new),'independent_accuracy':None}


async def stage_candidate_result(payload):
    """原始/有效事实已读回之后只保存候选报告，不更新任务、当前结果或共享查询。"""
    from src.db.connection import DatabaseConnection
    from src.services.analysis_result_store import compute_analysis_result_fingerprint
    conn=await DatabaseConnection.acquire()
    payload=json.loads(json.dumps(payload,ensure_ascii=False,allow_nan=False))
    try:
        async with conn.transaction():
            row=await conn.fetchrow('SELECT payload FROM analysis_fact_snapshots WHERE job_uuid=$1 AND revision_id=$2',
                payload['job_id'],payload['analysis_revision'])
            if row is None:raise ValueError('candidate fact snapshot missing')
            snapshot=decoded(row['payload']);validate_snapshot(snapshot)
            receipt=(payload.get('structured_ingest')or{}).get('fact_snapshot')or{}
            if receipt.get('storage_consistent')is not True or receipt.get('snapshot_hash')!=snapshot['snapshot_hash']:
                raise ValueError('candidate fact snapshot readback unconfirmed')
            core=compute_analysis_result_fingerprint(payload)
            await conn.execute('INSERT INTO analysis_revision_results(job_uuid,revision_id,core_hash,payload) '
                'VALUES($1,$2,$3,$4::jsonb) ON CONFLICT DO NOTHING',payload['job_id'],payload['analysis_revision'],
                core,json.dumps(payload,ensure_ascii=False))
            stored=await conn.fetchrow('SELECT core_hash,payload FROM analysis_revision_results WHERE job_uuid=$1 AND revision_id=$2',
                payload['job_id'],payload['analysis_revision'])
            if stored['core_hash']!=core or decoded(stored['payload'])!=payload:
                raise ValueError('immutable candidate report readback differs')
        return True
    finally:await DatabaseConnection.release(conn)


async def candidate_record(conn,job,revision):
    row=await conn.fetchrow('SELECT s.*,r.payload AS result,r.core_hash FROM analysis_fact_snapshots s '
        'JOIN analysis_revision_results r USING(job_uuid,revision_id) WHERE s.job_uuid=$1 AND s.revision_id=$2',job,revision)
    if row is None:raise ValueError('candidate snapshot/report missing')
    snapshot=decoded(row['payload']);result=decoded(row['result']);validate_snapshot(snapshot)
    from src.services.analysis_result_store import compute_analysis_result_fingerprint
    if (snapshot['job_uuid'],snapshot['revision_id'])!=(job,revision) or (result.get('job_id'),result.get('analysis_revision'))!=(job,revision):
        raise ValueError('candidate identity mismatch')
    if compute_analysis_result_fingerprint(result)!=row['core_hash']:
        raise ValueError('candidate report hash mismatch')
    if ((result.get('structured_ingest')or{}).get('fact_snapshot')or{}).get('snapshot_hash')!=snapshot['snapshot_hash']:
        raise ValueError('candidate report and fact snapshot differ')
    return snapshot,result,row['parse_run_id']


def validate_acceptance(item,snapshot,result):
    """验收记录由操作员提供；开发回归不能自动生成真实材料通过记录。"""
    acceptance=item.get('acceptance')or{}
    if (acceptance.get('accepted')is not True or acceptance.get('snapshot_hash')!=snapshot['snapshot_hash']
        or acceptance.get('source_file_hash')!=snapshot['source_file_hash']
        or not str(acceptance.get('reviewer')or'').strip()or not acceptance.get('reviewed_at')
        or acceptance.get('errors')!=[]):
        raise ValueError('explicit version-bound source acceptance required')
    overlay=result.get('fact_overlay')or{}
    if overlay.get('status')in {'failed','partial'}or overlay.get('unresolved'):
        raise ValueError('candidate has unresolved historical corrections')
    if result.get('status')not in {'done','completed','review_required','degraded'}:
        raise ValueError('candidate analysis is incomplete')


async def _event(conn,batch,action,payload):
    event=uuid.uuid4().hex;digest=content_hash(payload)
    await conn.execute('INSERT INTO analysis_rebuild_events(event_id,batch_id,action,payload,payload_hash) '
        'VALUES($1,$2,$3,$4::jsonb,$5)',event,batch,action,json.dumps(payload,ensure_ascii=False),digest)
    stored=await conn.fetchrow('SELECT payload,payload_hash FROM analysis_rebuild_events WHERE event_id=$1',event)
    if stored['payload_hash']!=digest or decoded(stored['payload'])!=payload:
        raise ValueError('rebuild audit readback differs')


async def publish_batch(conn,items,*,batch_id=None):
    from src.services.analysis_fact_snapshot import publish_snapshot
    from src.services.analysis_result_store import _upsert_analysis_job,_upsert_analysis_result,compute_analysis_result_fingerprint
    from src.services.fact_compatibility_projection import archive_legacy_projection
    if not 1<=len(items)<=20 or len({i['job_id']for i in items})!=len(items):
        raise ValueError('batch must contain 1-20 unique jobs')
    batch_id=batch_id or uuid.uuid4().hex;records=[]
    async with conn.transaction():
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM analysis_rebuild_events WHERE batch_id=$1)',batch_id):
            raise ValueError('rebuild batch already exists; inspect its outcome before retrying')
        documents=set()
        for item in sorted(items,key=lambda i:i['job_id']):
            job=item['job_id'];revision=item['revision_id']
            previous=await conn.fetchrow('SELECT * FROM analysis_jobs WHERE job_uuid=$1 FOR UPDATE',job)
            if previous is None or previous['current_fact_revision_id']!=item.get('expected_current_revision'):
                raise ValueError('candidate superseded; current revision changed')
            snapshot,result,run=await candidate_record(conn,job,revision)
            validate_acceptance(item,snapshot,result)
            if result.get('expected_current_fact_revision_id')!=item.get('expected_current_revision'):
                raise ValueError('candidate was computed against another current revision')
            document=await conn.fetchrow('SELECT v.id,v.document_id FROM parse_runs r '
                'JOIN fiscal_document_versions v ON v.id=r.document_version_id WHERE r.id=$1 FOR UPDATE OF v',run)
            if document['document_id']in documents:raise ValueError('one document cannot be published twice in a batch')
            documents.add(document['document_id'])
            old_head=await conn.fetchrow('SELECT * FROM published_fact_projections WHERE document_id=$1',document['document_id'])
            if old_head and old_head['job_uuid']!=job:
                raise ValueError('document is current under another job; rebuild that current job instead')
            legacy_hash=await archive_legacy_projection(conn,document['id'],snapshot['source_file_hash'])
            # PG 自己序列化 NUMERIC/时间；Python 不经过浮点重写历史行。
            before=await conn.fetchval("SELECT jsonb_build_object('job',to_jsonb(j),'result',"
                "(SELECT to_jsonb(r)FROM analysis_results r WHERE r.job_id=j.id))::text FROM analysis_jobs j WHERE job_uuid=$1",job)
            old_snapshot=None
            if previous['current_fact_revision_id']:
                old_snapshot,_,_=await candidate_record(conn,job,previous['current_fact_revision_id'])
            diff=compare_snapshots(old_snapshot,snapshot)
            from src.services import review_context_query,review_slot_sync
            link=await review_context_query.resolve_slot_link_for_job(conn,job)
            previous_slot=(await conn.fetchval('SELECT to_jsonb(s)::text FROM material_slots s WHERE id=$1 FOR UPDATE',
                                              link.slot_id))if link else None
            result=deepcopy(result)
            key=await _upsert_analysis_job(conn,result,result_fingerprint=compute_analysis_result_fingerprint(result))
            await _upsert_analysis_result(conn,key,result)
            await publish_snapshot(conn,result)
            await review_slot_sync.sync_slot_review_state_for_job(conn,job)
            new_head=decoded(await conn.fetchval('SELECT receipt FROM published_fact_projections WHERE document_id=$1',document['document_id']))
            records.append({'job_id':job,'revision_id':revision,'previous_revision':previous['current_fact_revision_id'],
                'snapshot_hash':snapshot['snapshot_hash'],'document_version_id':document['id'],
                'document_id':document['document_id'],'source_file_hash':snapshot['source_file_hash'],
                'legacy_archive_hash':legacy_hash,'previous_projection':dict(old_head)if old_head else None,
                'shared_previous_archive_hash':new_head.get('shared_previous_archive_hash'),
                'previous_material_slot_json':previous_slot,
                'previous_job_and_result_json':before,'comparison':diff,'acceptance':item['acceptance']})
        # receipt may contain PG timestamps from the old head; store its text form separately.
        for record in records:
            if record['previous_projection']:
                record['previous_projection']['receipt']=decoded(record['previous_projection']['receipt'])
                record['previous_projection']={k:(str(v)if k=='updated_at'else v)
                    for k,v in record['previous_projection'].items()}
        await _event(conn,batch_id,'published',{'items':records})
    return {'batch_id':batch_id,'status':'published','items':[{'job_id':r['job_id'],'revision_id':r['revision_id']}for r in records],
            'mirror_sync_pending':True}


async def _restore_legacy(conn,record):
    import hashlib
    text=await conn.fetchval('SELECT payload_text FROM legacy_projection_archives WHERE document_version_id=$1 AND payload_hash=$2',
                            record['document_version_id'],record['legacy_archive_hash'])
    if text is None or hashlib.sha256(text.encode()).hexdigest()!=record['legacy_archive_hash']:
        raise ValueError('rollback legacy archive missing or corrupt')
    # 用 JSON 文本在 PG 内展开，保留金额精度、旧 ID 和旧关联关系。
    version=record['document_version_id']
    await conn.execute('DELETE FROM fact_fiscal_line_items WHERE document_version_id=$1',version)
    await conn.execute('DELETE FROM fiscal_column_mappings WHERE table_instance_id IN '
        '(SELECT id FROM fiscal_table_instances WHERE document_version_id=$1)',version)
    await conn.execute('DELETE FROM fiscal_table_instances WHERE document_version_id=$1',version)
    await conn.execute('DELETE FROM fiscal_table_cells WHERE document_version_id=$1',version)
    shared_text=text
    if record.get('shared_previous_archive_hash'):
        shared_text=await conn.fetchval('SELECT payload_text FROM legacy_projection_archives WHERE document_version_id=$1 AND payload_hash=$2',
                                      version,record['shared_previous_archive_hash'])
        if shared_text is None or hashlib.sha256(shared_text.encode()).hexdigest()!=record['shared_previous_archive_hash']:
            raise ValueError('rollback shared projection archive missing or corrupt')
    report_ids=[uuid.UUID(r['id'])for r in json.loads(shared_text)['reports']]
    current=decoded(await conn.fetchval('SELECT receipt FROM published_fact_projections WHERE document_id=$1',record['document_id']))
    report_ids.append(uuid.UUID(current['shared_report_id']))
    await conn.execute('DELETE FROM org_dept_line_items WHERE report_id=ANY($1::uuid[])',report_ids)
    await conn.execute('DELETE FROM org_dept_table_data WHERE report_id=ANY($1::uuid[])',report_ids)
    await conn.execute('DELETE FROM org_dept_annual_report WHERE id=ANY($1::uuid[])',report_ids)
    for key,table in [('cells','fiscal_table_cells'),('instances','fiscal_table_instances'),
        ('mappings','fiscal_column_mappings'),('facts','fact_fiscal_line_items'),('reports','org_dept_annual_report'),
        ('shared_tables','org_dept_table_data'),('shared_lines','org_dept_line_items')]:
        original=shared_text if key in {'reports','shared_tables','shared_lines'}else text
        await conn.execute(f"INSERT INTO {table} SELECT * FROM jsonb_populate_recordset(NULL::{table},$1::jsonb->$2)",original,key)
    await conn.execute('DELETE FROM published_fact_projections WHERE document_id=$1',record['document_id'])
    old=record['previous_projection']
    if old:
        await conn.execute('INSERT INTO published_fact_projections SELECT * FROM '
            'jsonb_populate_record(NULL::published_fact_projections,$1::jsonb)',json.dumps(old))


async def rollback_batch(conn,batch_id):
    async with conn.transaction():
        row=await conn.fetchrow("SELECT payload,payload_hash FROM analysis_rebuild_events WHERE batch_id=$1 AND action='published'",batch_id)
        if row is None:raise ValueError('published rebuild batch not found')
        payload=decoded(row['payload'])
        if content_hash(payload)!=row['payload_hash']:raise ValueError('batch receipt hash mismatch')
        if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM analysis_rebuild_events WHERE batch_id=$1 AND action='rolled_back')",batch_id):
            raise ValueError('batch has already been rolled back')
        for record in sorted(payload['items'],key=lambda r:r['job_id']):
            current=await conn.fetchval('SELECT current_fact_revision_id FROM analysis_jobs WHERE job_uuid=$1 FOR UPDATE',record['job_id'])
            if current!=record['revision_id']:raise ValueError('newer analysis exists; refusing stale rollback')
            head=await conn.fetchrow('SELECT job_uuid,revision_id FROM published_fact_projections WHERE document_id=$1 FOR UPDATE',record['document_id'])
            if head is None or (head['job_uuid'],head['revision_id'])!=(record['job_id'],record['revision_id']):
                raise ValueError('document projection has moved; refusing stale rollback')
        for record in payload['items']:
            await _restore_legacy(conn,record)
            job_id=await conn.fetchval('SELECT id FROM analysis_jobs WHERE job_uuid=$1',record['job_id'])
            before=record['previous_job_and_result_json']
            await conn.execute('DELETE FROM analysis_results WHERE job_id=$1',job_id)
            await conn.execute("INSERT INTO analysis_results SELECT * FROM jsonb_populate_record(NULL::analysis_results,$1::jsonb->'result') "
                "WHERE $1::jsonb->'result' IS NOT NULL AND $1::jsonb->'result'!='null'::jsonb",before)
            columns=await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
                "AND table_name='analysis_jobs' AND column_name!='id' ORDER BY ordinal_position")
            names=','.join('"'+r['column_name']+'"'for r in columns)
            await conn.execute(f'UPDATE analysis_jobs SET ({names})=(SELECT {names} FROM '
                "jsonb_populate_record(NULL::analysis_jobs,$1::jsonb->'job')) WHERE id=$2",before,job_id)
            if record.get('previous_material_slot_json'):
                slot=record['previous_material_slot_json']
                slot_columns=await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
                    "AND table_name='material_slots' AND column_name!='id' ORDER BY ordinal_position")
                slot_names=','.join('"'+r['column_name']+'"'for r in slot_columns)
                await conn.execute(f'UPDATE material_slots SET ({slot_names})=(SELECT {slot_names} FROM '
                    'jsonb_populate_record(NULL::material_slots,$1::jsonb)) WHERE id=(SELECT id FROM '
                    'jsonb_populate_record(NULL::material_slots,$1::jsonb))',slot)
            if record['previous_revision']:
                await conn.execute('UPDATE analysis_fact_snapshots SET mirror_pending=TRUE WHERE job_uuid=$1 AND revision_id=$2',
                                   record['job_id'],record['previous_revision'])
        await _event(conn,batch_id,'rolled_back',{'items':[{'job_id':r['job_id'],'revision_id':r['previous_revision']}
                                                        for r in payload['items']]})
    return {'batch_id':batch_id,'status':'rolled_back','mirror_sync_pending':True}


async def synchronize_legacy_rollback(conn,job_dir,batch_id):
    """旧结果还没有事实快照时，从已留存的原结果恢复文件镜像。"""
    from api import runtime
    async with conn.transaction():
        current=await conn.fetchval('SELECT current_fact_revision_id FROM analysis_jobs WHERE job_uuid=$1 FOR UPDATE',job_dir.name)
        if current is not None:raise ValueError('legacy rollback mirror superseded by published snapshot')
        event=await conn.fetchrow("SELECT payload,payload_hash FROM analysis_rebuild_events WHERE batch_id=$1 AND action='published'",batch_id)
        if event is None:raise ValueError('legacy rollback receipt missing')
        if not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM analysis_rebuild_events WHERE batch_id=$1 AND action='rolled_back')",batch_id):
            raise ValueError('legacy mirror cannot be restored before database rollback')
        data=decoded(event['payload'])
        if content_hash(data)!=event['payload_hash']:raise ValueError('legacy rollback receipt corrupt')
        records=[r for r in data['items']if r['job_id']==job_dir.name and r['previous_revision']is None]
        if len(records)!=1:raise ValueError('legacy rollback identity ambiguous')
        before=decoded(records[0]['previous_job_and_result_json'])
        original=(before.get('result')or{}).get('raw_response')
        if isinstance(original,str):original=json.loads(original)
        if not isinstance(original,dict):raise ValueError('legacy report mirror requires preserved original report')
        runtime.write_json_file(job_dir/'status.json',original)
        runtime.write_structured_ingest_payload(job_dir,original.get('structured_ingest')or{})
        pointer=job_dir/'analysis_versions'/'current.json'
        # 只清当前镜像指针，历史版本目录和失败候选全部保留。
        if pointer.exists():pointer.unlink()
        if runtime.read_json_file(job_dir/'status.json',default={})!=original:
            raise ValueError('legacy status mirror readback differs')
        return {'status':'legacy_restored','job_id':job_dir.name}
