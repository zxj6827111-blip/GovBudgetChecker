"""重算前保留旧报告及校正原始字节；没有版本标识的历史也必须可回放。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


def _immutable(path,data):
    if path.exists():
        if path.read_bytes()!=data:raise ValueError('historical original bytes differ from frozen archive')
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,name=tempfile.mkstemp(dir=path.parent,prefix='.history-')
    try:
        with os.fdopen(descriptor,'wb')as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:os.link(name,path)
        except FileExistsError:pass
        if path.read_bytes()!=data:raise ValueError('historical original bytes differ from frozen archive')
    finally:Path(name).unlink(missing_ok=True)


def archive_before_reanalysis(job_dir,filenames):
    status_file=job_dir/'status.json'
    if not status_file.is_file():return None
    status_bytes=status_file.read_bytes()
    payload=json.loads(status_bytes)
    if payload.get('status')not in {'done','completed','review_required','degraded','error','cancelled'}:return None
    files={}
    for filename in sorted(set(filenames)|{'status.json','fact_context.json','corrections.jsonl'}):
        if Path(filename).name!=filename:raise ValueError('history backup filename must be local to job')
        path=job_dir/filename
        if path.is_file():files[filename]=path.read_bytes()
    digests={name:hashlib.sha256(data).hexdigest()for name,data in files.items()}
    root=job_dir/'preserved_analyses'
    for previous in root.glob('*/manifest.json'):
        old=json.loads(previous.read_text())
        if old.get('files',{}).get('status.json')==digests.get('status.json')and all(old['files'].get(k)==v for k,v in digests.items()):
            return {'archive_sha256':old['archive_sha256'],'directory':str(previous.parent),
                    'files':len(old['files']),'history_revision_id':old.get('history_revision_id')}
    # 归档身份按这一刻的完整原字节生成，不修改旧报告里的 analysis_revision。
    identity=hashlib.sha256(json.dumps(digests,sort_keys=True).encode()).hexdigest()
    folder=job_dir/'preserved_analyses'/identity
    for name,data in files.items():
        blob=job_dir/'preserved_analyses'/'.blobs'/digests[name]
        _immutable(blob,data)
        folder.mkdir(parents=True,exist_ok=True)
        try:os.link(blob,folder/name)
        except FileExistsError:
            if(folder/name).read_bytes()!=data:raise ValueError('historical linked bytes differ')
    history_revision=None
    if not payload.get('analysis_revision'):
        history_revision='rev-legacy-'+identity[:24]
        alias=job_dir/'analysis_versions'/history_revision
        for name,data in files.items():_immutable(alias/name,data)
    manifest={'job_id':job_dir.name,'source_revision':payload.get('analysis_revision'),'files':digests,
              'archive_sha256':identity,'historical_report_unchanged':True}
    manifest['history_revision_id']=history_revision
    _immutable(folder/'manifest.json',json.dumps(manifest,ensure_ascii=False,sort_keys=True).encode())
    if history_revision:
        _immutable(job_dir/'analysis_versions'/history_revision/'legacy_archive_manifest.json',
                   json.dumps(manifest,ensure_ascii=False,sort_keys=True).encode())
    return {'archive_sha256':identity,'directory':str(folder),'files':len(files),'history_revision_id':history_revision}
