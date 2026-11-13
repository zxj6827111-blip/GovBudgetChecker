"""Independent acceptance: real startup boundary and correction fallback contract.

Only temporary files, fake queue and fake pipeline; no database or AI traffic.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fitz
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api import runtime, queue_runtime
from api.routes import corrections
from src.services import ai_model_settings as models


def seed(root: Path, job_id: str, *, ai: bool):
    directory = root / job_id
    directory.mkdir()
    with fitz.open() as doc:
        doc.new_page().insert_text((70, 70), 'Independent review source')
        doc.save(directory / 'source.pdf')
    payload = dict(job_id=job_id, status='done', mode='dual', use_local_rules=True,
                   use_ai_assist=ai, filename='source.pdf', report_year=2025,
                   result={'summary': 'preserved'})
    if ai:
        payload.update(ai_profile_id='original-a', ai_configuration={
            'id': 'original-a', 'name': 'Original A', 'model': 'original-model', 'revision': 1})
    (directory / 'status.json').write_text(json.dumps(payload))
    return directory, payload


def configure(*, default_on: bool, include_original: bool = True):
    ids = ['current-b'] + (['original-a'] if include_original else [])
    models.save_settings(models.ModelSettingsUpdate(
        enabled=True, default_use_ai=default_on, active_profile_id='current-b',
        profiles=[models.ModelProfile(id=i, name=i, base_url='https://example.invalid/v1',
                     model='original-model' if i == 'original-a' else 'current-model',
                     api_key='test-only-not-a-live-key') for i in ids]))


@pytest.fixture
def isolated_queue(tmp_path, monkeypatch):
    root = tmp_path / 'independent-uploads'
    root.mkdir()
    monkeypatch.setattr(runtime, 'UPLOAD_ROOT', root)
    monkeypatch.setenv('UPLOAD_DIR', str(root))
    enqueued = []
    async def enqueue(job_id):
        enqueued.append(job_id)
    runner = AsyncMock()
    monkeypatch.setattr(runtime, '_pipeline_runner', runner)
    monkeypatch.setattr(runtime, '_job_queue', SimpleNamespace(enqueue=enqueue))
    monkeypatch.setattr(runtime, 'persist_analysis_job_snapshot', AsyncMock())
    monkeypatch.setattr(runtime, '_invalidate_reviews_before_analysis_start', AsyncMock())
    monkeypatch.setattr(runtime, 'get_settings', lambda: SimpleNamespace(is_dual_mode_enabled=lambda: True))
    return root, enqueued, runner


@pytest.mark.asyncio
@pytest.mark.parametrize('old_ai,current_default', [(False, True), (True, False)])
async def test_real_start_keeps_recorded_choice_and_model(isolated_queue, old_ai, current_default):
    root, enqueued, _ = isolated_queue
    configure(default_on=current_default)
    directory, _ = seed(root, 'review-start', ai=old_ai)
    source_bytes = (directory / 'source.pdf').read_bytes()
    response = await runtime.reanalyze_job('review-start', {'check_method': 'inherit'})
    stored = json.loads((directory / 'status.json').read_text())
    assert response['check_method'] == 'inherit'
    assert stored['status'] == 'queued'
    assert stored['use_ai_assist'] is old_ai
    assert stored['ai_profile_id'] == ('original-a' if old_ai else None)
    assert (directory / 'source.pdf').read_bytes() == source_bytes
    assert enqueued == ['review-start']
    if old_ai:
        snapshot = models.resolve_job_runtime('review-start')
        assert snapshot['id'] == 'original-a'
        assert snapshot['model'] == 'original-model'


@pytest.mark.asyncio
async def test_real_start_deleted_model_preserves_all_artifacts(isolated_queue):
    root, enqueued, _ = isolated_queue
    configure(default_on=True, include_original=False)
    directory, _ = seed(root, 'review-deleted', ai=True)
    (directory / 'ai_config_snapshot.json').write_text('{"old":"snapshot"}')
    (directory / 'check_progress.json').write_text('{"ai":{"state":"failed"}}')
    (directory / 'fact_context.json').write_text('{"preserved":true}')
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    with pytest.raises(HTTPException) as caught:
        await runtime.reanalyze_job('review-deleted', {'check_method': 'inherit'})
    assert caught.value.status_code == 422
    assert before == {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    assert not enqueued


@pytest.mark.asyncio
async def test_correction_inline_must_enforce_deleted_model_preflight(isolated_queue, monkeypatch):
    root, _, runner = isolated_queue
    configure(default_on=True, include_original=False)
    directory, payload = seed(root, 'review-correction', ai=True)
    # Control: the ordinary reanalysis endpoint rejects this exact source/model.
    with pytest.raises(HTTPException) as caught:
        await runtime.reanalyze_job('review-correction', {'check_method': 'inherit'})
    assert caught.value.status_code == 422
    monkeypatch.setattr(corrections, 'require_job_access', lambda *a, **k: (None, '', {'username': 'reviewer'}, payload))
    monkeypatch.setattr(corrections, '_job_dir', lambda _: directory)
    monkeypatch.setattr(corrections, '_current_basis_or_409', lambda *a: 'review-correction:rev-1')
    monkeypatch.setattr(corrections, 'report_evidence_ids', lambda _: {'review-evidence-1'})
    monkeypatch.setattr(corrections, 'append_record_with_basis_cas', lambda *a, **k: {'accepted': True})
    monkeypatch.setattr(queue_runtime, 'queue_enabled', lambda: False)
    monkeypatch.setattr(queue_runtime, 'allow_inline_fallback', lambda: True)
    body = corrections.CorrectionRequest(analysis_basis_token='review-correction:rev-1', reason='independent evidence correction',
        changes=[{'target_type': 'fact', 'target_id': 'fact-1', 'field': 'raw_value', 'value': '123.45', 'evidence_ids': ['review-evidence-1']}])
    response = await corrections.create_job_correction('review-correction', body, Request({'type': 'http'}))
    await asyncio.sleep(0)
    # A deleted model must not be dispatched by a different reanalysis entrance.
    assert runner.await_count == 0, {'actual_response': response, 'pipeline_calls': runner.await_count}
