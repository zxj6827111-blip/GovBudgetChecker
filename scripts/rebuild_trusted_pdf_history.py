#!/usr/bin/env python3
"""本地历史重建工具：清点、候选计算、验收后发布和回滚；默认不切换当前结果。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb')as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


async def connection():
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env',override=False)
    conn=await asyncpg.connect(os.environ['DATABASE_URL'])
    if await conn.fetchval("SELECT to_regclass('analysis_rebuild_events')")is None:
        await conn.close();raise RuntimeError('apply reviewed database migrations before rebuilding history')
    return conn


async def inventory(args):
    from api import runtime
    conn=await connection();items=[]
    try:
        for job in args.jobs:
            if not job.replace('-','').replace('_','').isalnum():raise ValueError('invalid job ID')
            directory=args.uploads.resolve()/job
            original=runtime.find_first_pdf(directory)
            row=await conn.fetchrow('SELECT job_uuid,analysis_revision,current_fact_revision_id,metadata '
                'FROM analysis_jobs WHERE job_uuid=$1',job)
            if row is None:raise ValueError('history job does not exist: '+job)
            items.append({'job_id':job,'source_sha256':sha(original),'original_filename':original.name,
                'current_revision':row['current_fact_revision_id'],'analysis_generation':row['analysis_revision'],
                'files':{str(p.relative_to(directory)):sha(p)for p in directory.rglob('*')if p.is_file()}})
        result={'version':'gbc.history-inventory.v1','items':items,'workflow_sha256':
            sha(args.uploads/'.issue_workflow.json')if(args.uploads/'.issue_workflow.json').is_file()else None}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x')as f:json.dump(result,f,ensure_ascii=False,indent=2)
        print(json.dumps({'status':'inventoried','jobs':len(items),'inventory':str(args.output.resolve())}))
    finally:await conn.close()


def build(args):
    entries=json.loads(args.inventory.read_text())['items'];args.output.mkdir(parents=True,exist_ok=True)
    receipts=[]
    for item in entries:
        job=item['job_id'];source=args.uploads.resolve()/job
        root=args.output.resolve()/('candidate-'+uuid.uuid4().hex)
        if root==source or source in root.parents:raise ValueError('candidate directory must be outside original job')
        target=root/job
        for relative,expected in item['files'].items():
            path=source/relative
            if not path.is_file()or sha(path)!=expected:raise ValueError('history inventory changed: '+relative)
        shutil.copytree(source,target,ignore=shutil.ignore_patterns('analysis_versions','fact_snapshots','cache_conflicts','preserved_analyses','__pycache__'))
        for path in target.rglob('*'):
            if path.is_file()and sha(path)!=item['files'].get(str(path.relative_to(target))):
                raise ValueError('source changed while copying candidate workspace')
        workflow=args.uploads/'.issue_workflow.json'
        if workflow.is_file():shutil.copyfile(workflow,root/'.issue_workflow.json')
        status_path=target/'status.json';status=json.loads(status_path.read_text())
        status.update(status='queued',use_ai_assist=False,mode='legacy',use_local_rules=True)
        status_path.write_text(json.dumps(status,ensure_ascii=False))
        env={**os.environ,'UPLOAD_DIR':str(root),'TRUSTED_REBUILD_EXPECTED_CURRENT':json.dumps(item['current_revision']),
             'TRUSTED_REBUILD_SOURCE_SHA':item['source_sha256']}
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'_candidate-worker','--job',job],
                              cwd=ROOT,env=env,stdout=(target/'candidate.log').open('w'),stderr=subprocess.STDOUT)
        payload=json.loads(status_path.read_text())
        ok=result.returncode==0 and payload.get('analysis_revision')and payload.get('status')not in {'error','queued','processing','running'}
        receipt={'job_id':job,'revision_id':payload.get('analysis_revision'),'expected_current_revision':item['current_revision'],
                 'candidate_directory':str(target),'status':'candidate_ready'if ok else'candidate_incomplete',
                 'acceptance':{'accepted':False,'source_file_hash':item['source_sha256'],
                               'snapshot_hash':((payload.get('structured_ingest')or{}).get('fact_snapshot')or{}).get('snapshot_hash'),
                               'reviewer':None,'reviewed_at':None,'errors':None}}
        receipts.append(receipt)
        (args.output/'candidate_manifest.json').write_text(json.dumps({'items':receipts},ensure_ascii=False,indent=2))
        if not ok:raise RuntimeError('candidate paused or failed; inspect '+str(target/'candidate.log'))
    print(json.dumps({'status':'candidates_ready','jobs':len(receipts),'published':False}))


def resume(args):
    manifest=json.loads(args.manifest.read_text())
    for item in manifest['items']:
        if item['status']=='candidate_ready':continue
        target=Path(item['candidate_directory']).resolve()
        if target.name!=item['job_id']or target.parent.parent!=args.manifest.resolve().parent:
            raise ValueError('candidate resume directory differs from its manifest')
        env={**os.environ,'UPLOAD_DIR':str(target.parent),
             'TRUSTED_REBUILD_EXPECTED_CURRENT':json.dumps(item['expected_current_revision']),
             'TRUSTED_REBUILD_SOURCE_SHA':item['acceptance']['source_file_hash']}
        with (target/'candidate.log').open('a')as log:
            result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'_candidate-worker','--job',item['job_id']],
                env=env,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        payload=json.loads((target/'status.json').read_text())
        ok=result.returncode==0 and payload.get('analysis_revision')and payload.get('status')in {'done','completed','review_required','degraded'}
        item.update(revision_id=payload.get('analysis_revision'),status='candidate_ready'if ok else'candidate_incomplete')
        item['acceptance'].update(accepted=False,reviewer=None,reviewed_at=None,errors=None,
            snapshot_hash=((payload.get('structured_ingest')or{}).get('fact_snapshot')or{}).get('snapshot_hash'))
        args.manifest.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        if not ok:raise RuntimeError('candidate remains incomplete; checkpoint preserved for the next resume')
    print(json.dumps({'status':'candidates_ready','published':False}))


async def worker(args):
    conn=await connection()
    try:
        current=await conn.fetchval('SELECT current_fact_revision_id FROM analysis_jobs WHERE job_uuid=$1',args.job)
        if current!=json.loads(os.environ['TRUSTED_REBUILD_EXPECTED_CURRENT']):raise ValueError('inventory is stale')
    finally:await conn.close()
    from api import runtime
    import fcntl
    lease=(runtime.UPLOAD_ROOT/args.job/'.candidate-worker.lock').open('a+b')
    fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    source=runtime.find_first_pdf(runtime.UPLOAD_ROOT/args.job)
    if sha(source)!=os.environ['TRUSTED_REBUILD_SOURCE_SHA']:raise ValueError('original file changed since inventory')
    from api.main import _run_pipeline_body
    from src.services.structured_ingest_runner import close_structured_ingest_resources
    try:await _run_pipeline_body(runtime.UPLOAD_ROOT/args.job,candidate_only=True)
    finally:
        await close_structured_ingest_resources()
        fcntl.flock(lease,fcntl.LOCK_UN);lease.close()


async def switch(args):
    from src.services.historical_rebuild import publish_batch,rollback_batch
    conn=await connection()
    try:
        if args.command=='publish':
            items=json.loads(args.manifest.read_text())['items']
            if len(items)>20:raise ValueError('publish one reviewed batch of at most 20 jobs')
            from api import runtime
            for item in items:
                original=runtime.find_first_pdf(args.uploads.resolve()/item['job_id'])
                if sha(original)!=(item.get('acceptance')or{}).get('source_file_hash'):
                    raise ValueError('original file changed before publication')
                from src.services.legacy_history_guard import archive_before_reanalysis
                archive_before_reanalysis(args.uploads.resolve()/item['job_id'],runtime.REANALYZE_EPHEMERAL_FILES)
            result=await publish_batch(conn,items,batch_id=args.batch_id)
        elif args.command=='rollback':result=await rollback_batch(conn,args.batch_id)
        else:result={'batch_id':args.batch_id,'status':'mirror_sync'}
        # 发布事务已提交；镜像失败只保留待重试，不撤销数据库权威结果。
        from src.services.analysis_fact_snapshot import synchronize_published_mirror
        from src.db.connection import DatabaseConnection
        await DatabaseConnection.initialize(os.environ['DATABASE_URL'])
        try:
            jobs=items if args.command=='publish'else [dict(r)for r in await conn.fetch(
                "SELECT value->>'job_id' AS job_id FROM analysis_rebuild_events,"
                "jsonb_array_elements(payload->'items') WHERE batch_id=$1 AND action='published'",args.batch_id)]
            mirrors=[]
            for item in jobs:
                try:
                    root=args.uploads.resolve()/item['job_id']
                    mirror=await synchronize_published_mirror(item['job_id'],root)
                    if args.command in {'rollback','sync'}and mirror['status']=='legacy_without_snapshot':
                        from src.services.historical_rebuild import synchronize_legacy_rollback
                        mirror=await synchronize_legacy_rollback(conn,root,args.batch_id)
                    mirrors.append(mirror)
                except Exception as error:mirrors.append({'job_id':item['job_id'],'status':'pending_retry','error':str(error)})
            result['mirrors']=mirrors
        finally:await DatabaseConnection.close()
        print(json.dumps(result,ensure_ascii=False,default=str))
    finally:await conn.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('inventory');p.add_argument('--uploads',type=Path,required=True);p.add_argument('--jobs',nargs='+',required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('build');p.add_argument('--uploads',type=Path,required=True);p.add_argument('--inventory',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('resume');p.add_argument('--manifest',type=Path,required=True)
    p=sub.add_parser('_candidate-worker');p.add_argument('--job',required=True)
    p=sub.add_parser('publish');p.add_argument('--uploads',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--batch-id')
    p=sub.add_parser('rollback');p.add_argument('--uploads',type=Path,required=True);p.add_argument('--batch-id',required=True)
    p=sub.add_parser('sync');p.add_argument('--uploads',type=Path,required=True);p.add_argument('--batch-id',required=True)
    args=parser.parse_args()
    if args.command=='build':build(args)
    elif args.command=='resume':resume(args)
    else:asyncio.run({'inventory':inventory,'_candidate-worker':worker,'publish':switch,'rollback':switch,'sync':switch}[args.command](args))


if __name__=='__main__':main()
