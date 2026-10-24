"""旧版无分析版本标识也要留原字节，原件名不能与派生缓存清理规则冲突。"""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.legacy_history_guard import archive_before_reanalysis


def test_legacy_original_bytes_remain_readable_after_caches_are_removed(tmp_path,monkeypatch):
    from api import runtime
    from api.routes.reports import _load_revision_payload
    job=tmp_path/'job';job.mkdir()
    original=b'{"status":"done","legacy_number":-0.0,"result":{}}'
    (job/'status.json').write_bytes(original)
    (job/'report.pdf').write_bytes(b'original report bytes')
    first=archive_before_reanalysis(job,{'report.pdf'})
    (job/'report.pdf').unlink()
    assert archive_before_reanalysis(job,{'report.pdf'})['archive_sha256']==first['archive_sha256']
    folder=job/'analysis_versions'/first['history_revision_id']
    assert (folder/'status.json').read_bytes()==original
    assert (folder/'report.pdf').read_bytes()==b'original report bytes'
    monkeypatch.setattr(runtime,'UPLOAD_ROOT',tmp_path)
    from api.routes.corrections import _revision_payload
    assert _load_revision_payload('job',first['history_revision_id'])['status']=='done'
    assert _revision_payload('job',first['history_revision_id'])['status']=='done'
    (folder/'status.json').write_text('{"status":"done","tampered":true}')
    from fastapi import HTTPException
    with pytest.raises(HTTPException)as error:_load_revision_payload('job',first['history_revision_id'])
    assert error.value.status_code==409
    with pytest.raises(HTTPException)as error:_revision_payload('job',first['history_revision_id'])
    assert error.value.status_code==409


@pytest.mark.asyncio
async def test_reanalysis_never_deletes_an_original_named_report_pdf(tmp_path,monkeypatch):
    import pymupdf as fitz
    from api import runtime
    job=tmp_path/'job';job.mkdir();source=job/'report.pdf'
    doc=fitz.open();doc.new_page();doc.save(source);doc.close()
    expected=hashlib.sha256(source.read_bytes()).hexdigest()
    (job/'annotated.pdf').write_bytes(b'old annotation')
    (job/'status.json').write_text(json.dumps({'job_id':'job','status':'done','filename':'report.pdf',
        'checksum':expected,'mode':'legacy','use_ai_assist':False,'use_local_rules':True}))
    monkeypatch.setattr(runtime,'UPLOAD_ROOT',tmp_path)
    monkeypatch.setattr(runtime,'_pipeline_runner',object())
    monkeypatch.setattr(runtime,'_job_queue',SimpleNamespace(enqueue=AsyncMock()))
    monkeypatch.setattr(runtime,'persist_analysis_job_snapshot',AsyncMock(return_value=True))
    monkeypatch.setattr(runtime,'_invalidate_reviews_before_analysis_start',AsyncMock())
    await runtime.reanalyze_job('job',{'mode':'legacy','use_ai_assist':False,'use_local_rules':True})
    assert source.is_file()and hashlib.sha256(source.read_bytes()).hexdigest()==expected
    assert not(job/'annotated.pdf').exists()
    manifests=list((job/'preserved_analyses').glob('*/manifest.json'))
    assert len(manifests)==1
    assert (manifests[0].parent/'annotated.pdf').read_bytes()==b'old annotation'
