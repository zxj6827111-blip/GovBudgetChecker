"""Regression checks for batch reanalysis, queue deadlines and honest AI states."""
import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.schemas.issues import AnalysisConfig, JobContext
from src.services import analyze_dual
from src.services.ai_execution import build_ai_execution, summarize_ai_diagnostics
from src.services.rule_source_binding import (
    bind_ai_literal_sources, bind_rule_sources,
    verify_bound_ai_locations, verify_bound_rule_locations,
)


@pytest.fixture
def analyzer(monkeypatch):
    cls = analyze_dual.DualModeAnalyzer
    monkeypatch.setattr(cls, '_ai_wait_queue', [])
    monkeypatch.setattr(cls, '_ai_queue_guard', asyncio.Lock())
    monkeypatch.setattr(cls, '_ai_runner_lock', asyncio.Lock())
    instance = cls()
    instance.ai_sequential_enabled = True
    return instance


def context(name='test-job'):
    return JobContext(job_id=name, pdf_path='unused.pdf')


def config(**extra):
    return AnalysisConfig(ai_enabled=True, enable_ai_analysis=True, **extra)


@pytest.mark.parametrize('source,verify,key', [
    ('rule', verify_bound_rule_locations, 'checked_rule_findings'),
    ('ai', verify_bound_ai_locations, 'checked_ai_findings'),
])
def test_source_verification_imports_exist_in_helper_scope(tmp_path, source, verify, key):
    import fitz
    path = tmp_path / 'original.pdf'
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), 'Original amount 123.45')
        box = list(page.search_for('123.45')[0])
        pdf.save(path)
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        text = pdf.pages[0].extract_text()
    item = dict(id='finding', source=source, title='Amount', message='Amount',
                page_number=1, bbox=box, evidence=[dict(page=1, text='Original amount 123.45')],
                location={'table_refs': [{'page': 1, 'span': 'Original amount 123.45'}]})
    result = {f'{source}_findings': [item]}
    bind = bind_rule_sources if source == 'rule' else bind_ai_literal_sources
    bind(result, page_texts=[text], source_file_hash=hashlib.sha256(path.read_bytes()).hexdigest())
    assert verify(result, source_pdf_path=str(path)) == {key: 1}
    assert item['verification_status'] == 'verified'


@pytest.mark.asyncio
async def test_queued_job_gets_full_budget_after_waiting(analyzer):
    blocker = 'earlier-job'
    await analyzer._enqueue_ai_ticket(blocker)
    analyzer.ai_service = SimpleNamespace(analyze=AsyncMock(return_value=[]))
    task = asyncio.create_task(analyzer._run_ai_analysis(context(), [{}], config(), timeout_seconds=.03))
    try:
        await asyncio.sleep(.08)
        assert not task.done()
        analyzer.ai_service.analyze.assert_not_called()
        await analyzer._dequeue_ai_ticket(blocker)
        assert await asyncio.wait_for(task, 1) == []
        analyzer.ai_service.analyze.assert_awaited_once()
        assert analyzer._ai_wait_queue == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_timeout_releases_ticket_even_with_fallback(analyzer):
    cancelled = asyncio.Event()

    async def blocked(_context):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    analyzer.ai_service = SimpleNamespace(analyze=blocked)
    with pytest.raises(asyncio.TimeoutError):
        await analyzer._run_ai_analysis(context(), [{}], config(ai_fallback_on_error=True), timeout_seconds=.03)
    assert cancelled.is_set()
    assert analyzer._ai_wait_queue == []
    analyzer.ai_service = SimpleNamespace(analyze=AsyncMock(return_value=[]))
    assert await analyzer._run_ai_analysis(context('next'), [{}], config(), timeout_seconds=.03) == []


@pytest.mark.asyncio
async def test_cancellation_while_queued_releases_ticket(analyzer):
    await analyzer._enqueue_ai_ticket('earlier-job')
    analyzer.ai_service = SimpleNamespace(analyze=AsyncMock(return_value=[]))
    task = asyncio.create_task(analyzer._run_ai_analysis(context(), [{}], config(), timeout_seconds=1))
    await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert analyzer._ai_wait_queue == ['earlier-job']
    analyzer.ai_service.analyze.assert_not_called()


@pytest.mark.asyncio
async def test_parent_cancellation_stops_ai_while_awaiting_rules(analyzer, monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def ai(_context):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    analyzer.ai_service = SimpleNamespace(analyze=ai)
    monkeypatch.setattr(analyzer, '_load_rules', AsyncMock(return_value=[{'executor': 'both'}]))
    # An explicit coroutine keeps rules running while the AI branch starts.
    async def rules(*_):
        await asyncio.Event().wait()
    monkeypatch.setattr(analyzer, '_run_engine_analysis', rules)
    task = asyncio.create_task(analyzer._analyze(context(), config()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set(), 'parent cancellation leaked the AI branch'
        assert analyzer._ai_wait_queue == []
    finally:
        # Prevent a deliberately exposed regression from leaking into other tests.
        for child in asyncio.all_tasks():
            if child is not asyncio.current_task() and child.get_coro().__qualname__.endswith('_run_ai_analysis'):
                child.cancel()
                await asyncio.gather(child, return_exceptions=True)


SUCCESS = dict(provider='test', model='test-model', content='[]', finish_reason='stop')


@pytest.mark.parametrize('error_type', ['audit_window_failed', 'audit_window_cancelled', 'analysis_failure'])
def test_window_failure_preserves_actual_reason(error_type):
    execution = build_ai_execution(True, call_ledger=[SUCCESS], window_errors=[{'type': error_type}])
    assert execution['state'] == 'degraded'
    assert execution['error_code'] == error_type


def test_successful_call_cannot_hide_overall_timeout():
    execution = build_ai_execution(True, call_ledger=[SUCCESS], ai_error='AI分析超时(90s)')
    assert execution['state'] == 'degraded'
    assert execution['error_code'] == 'ai_timeout'


def test_final_diagnostics_keep_failure_without_model_content():
    diagnostics = summarize_ai_diagnostics({
        'ai_window_errors': [{'type': 'audit_window_failed', 'message': 'timeout'}],
        'ai_call_ledger': [{**SUCCESS, 'content': 'private model output', 'error': 'timeout'}],
    })
    assert diagnostics['ai_window_errors'][0]['message'] == 'timeout'
    assert diagnostics['ai_call_summary'][0]['error'] == 'timeout'
    assert diagnostics['ai_call_summary'][0]['content_length'] == 20
    assert 'content' not in diagnostics['ai_call_summary'][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,hard_limit,expected', [
    ('page_aware_default', 1, 'review_required'),
    ('explicit', 1, 'error'),
    ('page_aware_default', .04, 'error'),
])
async def test_pipeline_queue_accounting_keeps_explicit_and_hard_limits(tmp_path, monkeypatch, mode, hard_limit, expected):
    import json
    from api import main
    from src.services.analysis_timing import record_ai_queue_wait

    async def body(directory):
        with record_ai_queue_wait():
            await asyncio.sleep(.08)
        await asyncio.sleep(.01)
        main._safe_write(directory, {'status': 'review_required'})

    monkeypatch.setattr(main, '_resolve_pipeline_budget', lambda _: {
        'total_seconds': .03, 'hard_limit_seconds': hard_limit, 'mode': mode,
    })
    monkeypatch.setattr(main, '_run_pipeline_inner', body)
    monkeypatch.setattr(main, 'persist_analysis_job_snapshot', AsyncMock())
    (tmp_path / 'status.json').write_text('{"status":"processing"}')
    await asyncio.wait_for(main._run_pipeline(tmp_path), 1)
    status = json.loads((tmp_path / 'status.json').read_text())
    assert status['status'] == expected
    assert status['pipeline_budget']['ai_queue_wait_seconds'] > 0


@pytest.mark.asyncio
async def test_queue_timing_is_isolated_between_jobs():
    from src.services.analysis_timing import ai_queue_wait_scope, record_ai_queue_wait

    async def job(delay):
        with ai_queue_wait_scope() as timer:
            with record_ai_queue_wait():
                await asyncio.sleep(delay)
            return timer.elapsed

    short, long = await asyncio.gather(job(.01), job(.08))
    assert .005 <= short <= long
    assert long >= .07


@pytest.mark.asyncio
@pytest.mark.parametrize('rules,error', [
    ([{'executor': 'ai'}], 'invalid model configuration'),
    ([], '未加载到大模型检查规则'),
])
async def test_ai_fallback_keeps_configuration_failure_in_result(analyzer, monkeypatch, rules, error):
    monkeypatch.setattr(analyzer, '_load_rules', AsyncMock(return_value=rules))
    analyzer._runtime_ai_error = error
    result = await analyzer._analyze(context(), config(rule_enabled=False, ai_fallback_on_error=True))
    assert result.meta['ai_error'] == error
    assert build_ai_execution(True, ai_error=result.meta['ai_error'])['state'] == 'failed'
    assert analyzer._ai_wait_queue == []


@pytest.mark.asyncio
async def test_body_error_cannot_be_recorded_as_pipeline_success(tmp_path, monkeypatch):
    import json
    from api import main

    async def body(directory):
        main._safe_write(directory, {'status': 'error', 'error': 'test_failure'})

    monkeypatch.setattr(main, '_run_pipeline_inner', body)
    (tmp_path / 'status.json').write_text('{}')
    await main._run_pipeline(tmp_path)
    status = json.loads((tmp_path / 'status.json').read_text())
    assert status['status'] == 'error' and status['error'] == 'test_failure'
    assert status['pipeline_budget']['outcome'] == 'error'


@pytest.mark.asyncio
async def test_invalid_reanalysis_does_not_clear_old_results(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from api import runtime
    monkeypatch.setattr(runtime, 'UPLOAD_ROOT', tmp_path)
    directory = tmp_path / 'old-job'
    directory.mkdir()
    (directory / 'source.pdf').write_bytes(b'%PDF-1.4')
    previous = '{"status":"done","result":{"summary":"preserved"}}'
    (directory / 'status.json').write_text(previous)
    (directory / 'fact_context.json').write_text('{"preserved":true}')
    with pytest.raises(HTTPException) as exc:
        await runtime.reanalyze_job('old-job', {'mode': 'legacy', 'use_local_rules': False, 'use_ai_assist': False})
    assert exc.value.status_code == 422
    assert (directory / 'status.json').read_text() == previous
    assert (directory / 'fact_context.json').read_text() == '{"preserved":true}'
