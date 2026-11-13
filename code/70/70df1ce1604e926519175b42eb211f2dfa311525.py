import asyncio
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

WT = Path('/Users/zhangxingjin/gbc-acceptance-r3')
sys.path.insert(0, str(WT))
load_dotenv(WT / '.env')

from src.engine.ai.audit_prompts import NARRATIVE_CONSISTENCY, TEXT_SEMANTICS
from src.engine.ai.extractor_client import ExtractorClient, ExtractorConfig
from src.schemas.issues import AnalysisConfig, JobContext
from src.services.ai_findings import AIFindingsService
from src.services.ai_execution import build_ai_execution
from src.services.analysis_result_store import persist_analysis_job_snapshot

MAIN = Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT = MAIN / 'scratch/ai_remediation_20261025/acceptance3'
UPLOAD = Path(os.environ['UPLOAD_DIR'])
MARKERS = {
    TEXT_SEMANTICS: '【本次专项：文字与语义】',
    NARRATIVE_CONSISTENCY: '【本次专项：说明合理性与跨段一致性】',
}


class ReplayChat:
    def __init__(self, records):
        self.records = records
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        messages = kwargs.get('messages') or []
        prompt = str(messages[-1].get('content') or '') if messages else ''
        specialty = None
        for name, marker in MARKERS.items():
            if marker in prompt:
                specialty = name
                break
        candidates = [
            record for record in self.records
            if record.get('raw_response')
            and not record.get('error')
            and (specialty is None or record.get('specialty') == specialty)
            and (record.get('window_text') or '') in prompt
        ]
        # Prompt 完全一致时优先；否则按窗口原文+专项唯一匹配。
        exact = [r for r in candidates if r.get('prompt') == prompt]
        chosen = exact[0] if exact else (candidates[0] if len(candidates) == 1 else None)
        if chosen is None:
            raise RuntimeError(
                f'replay response not found: specialty={specialty} prompt_sha={hashlib.sha256(prompt.encode()).hexdigest()[:12]}'
            )
        return {
            'content': chosen['raw_response'],
            'provider_used': 'replay_saved_response',
            'model': 'gpt-6.1-sol',
            'finish_reason': 'stop',
            'tokens': {},
            'replay_source_window': chosen.get('window', {}).get('window_id'),
            'replay_source_attempt': chosen.get('attempt'),
        }


def load_trace(job_id):
    path = UPLOAD / job_id / 'ai_audit_trace.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def match_quote(item):
    return str(item.get('quote') or item.get('original') or '').strip()


async def replay_one(sid):
    status = json.loads((OUT / f'{sid}_status.json').read_text(encoding='utf-8'))
    job_id = status['job_id']
    result = status.get('result') or {}
    meta = result.get('meta') or {}
    page_texts = ((meta.get('structure_view') or {}).get('page_texts') or [])
    if not page_texts:
        raise RuntimeError(f'{sid}: no page_texts')
    fact_context = json.loads((UPLOAD / job_id / 'fact_context.json').read_text(encoding='utf-8'))
    trace = load_trace(job_id)
    original_ai = list(result.get('ai_findings') or [])
    replay_job = f'replay_{sid}_{int(time.time())}'
    job_dir = UPLOAD / replay_job
    job_dir.mkdir(parents=True, exist_ok=False)
    pdf_name = status.get('filename') or 'source.pdf'
    source_pdf = UPLOAD / job_id / pdf_name
    (job_dir / pdf_name).write_bytes(source_pdf.read_bytes())

    replay_client = ExtractorClient(ExtractorConfig(
        enabled=True, direct_only=True, audit_provider='replay',
        main_provider='replay', audit_model='gpt-6.1-sol', main_model='gpt-6.1-sol',
        timeout=180, max_tokens=4000, specialty_retries=0,
    ))
    replay_chat = ReplayChat(trace)
    replay_client._direct_ai_client = replay_chat
    service = AIFindingsService(AnalysisConfig(ai_enabled=True))
    service.ai_client = replay_client
    context = JobContext(
        job_id=replay_job,
        pdf_path=str(job_dir / pdf_name),
        page_texts=page_texts,
        page_tables=[],
        fact_context=fact_context,
        meta={'check_receipts': meta.get('check_receipts') or []},
    )
    replay_issues = await service.analyze(context)
    ledger = service.ai_client.pop_call_ledger()
    execution = build_ai_execution(
        True,
        call_ledger=ledger,
        window_errors=service.ai_errors,
        coverage=service.ai_coverage,
    )

    original_by_key = {}
    for item in original_ai:
        key = (match_quote(item), str(item.get('check') or ''), item.get('page_number'))
        original_by_key.setdefault(key, item)
    matched = 0
    unmatched = []
    for issue in replay_issues:
        key = (match_quote(issue.model_dump()), str(issue.check or ''), issue.page_number)
        original = original_by_key.get(key)
        if original is None:
            # 退化为 quote+check（页码可能由窗口映射略有差异）
            for (quote, check, _page), candidate in original_by_key.items():
                if quote == key[0] and check == key[1]:
                    original = candidate
                    break
        if original is None:
            unmatched.append({'quote': key[0][:120], 'check': key[1], 'page': key[2]})
            continue
        issue.id = original.get('id') or issue.id
        matched += 1

    replay_dicts = [issue.model_dump() for issue in replay_issues]
    replay_by_id = {item['id']: item for item in replay_dicts}

    # 复用原始规则结果/合并关系；只替换 AI 结果，且把 review_report 内对应候选
    # 的 finding 替换为回放结果，避免页面仍读旧 AI 对象。
    replay_result = copy.deepcopy(result)
    replay_result['ai_findings'] = replay_dicts
    replay_report = copy.deepcopy(status.get('review_report') or {})
    for key in ('issues', 'tips', 'manual_review'):
        bucket = replay_report.get(key)
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if not isinstance(item, dict):
                continue
            finding = item.get('finding')
            if isinstance(finding, dict) and finding.get('id') in replay_by_id:
                item['finding'] = copy.deepcopy(replay_by_id[finding['id']])
            if item.get('id') in replay_by_id:
                item.update({k: v for k, v in replay_by_id[item['id']].items() if k not in {'id'}})
    replay_meta = replay_result.setdefault('meta', {})
    replay_meta['ai_findings'] = replay_dicts
    replay_meta['ai_coverage'] = service.ai_coverage
    replay_meta['ai_audit_diagnostics'] = service.audit_diagnostics
    replay_meta['ai_window_errors'] = service.ai_errors
    replay_meta['ai_execution'] = execution
    replay_meta['conclusion_scope'] = meta.get('conclusion_scope') or status.get('conclusion_scope')
    replay_meta['replay'] = {
        'source_job_id': job_id,
        'source_trace_sha256': hashlib.sha256(
            (UPLOAD / job_id / 'ai_audit_trace.jsonl').read_bytes()
        ).hexdigest(),
        'replay_job_id': replay_job,
        'new_model_calls': 0,
        'source_trace_records': len(trace),
        'used_records': replay_chat.calls,
        'matched_findings': matched,
        'unmatched_findings': unmatched,
    }
    quality_gate = copy.deepcopy(replay_meta.get('quality_gate') or {})
    quality_gate['ai_execution'] = execution
    replay_meta['quality_gate'] = quality_gate

    payload = copy.deepcopy(status)
    payload.update({
        'job_id': replay_job,
        'saved_path': f'{replay_job}/{pdf_name}',
        'result': replay_result,
        'review_report': replay_report,
        'analysis_revision': 1,
        'status': status.get('status') or 'review_required',
        'quality_status': status.get('quality_status') or 'review_required',
        'analysis_conclusion': status.get('analysis_conclusion') or 'incomplete',
        'conclusion_scope': meta.get('conclusion_scope') or status.get('conclusion_scope'),
        'human_decisions': [],
        'replay': replay_meta['replay'],
    })
    payload['result']['meta']['replay'] = replay_meta['replay']
    # 回放只复用已有事实快照，不新建假快照；清掉发布指针，避免把回放伪装成
    # 一次新的可信事实分析。状态文件与 analysis_results 仍落库，API/CSV 可读。
    if isinstance(payload.get('structured_ingest'), dict):
        payload['structured_ingest'] = copy.deepcopy(payload['structured_ingest'])
        payload['structured_ingest']['fact_snapshot'] = None
    payload.pop('expected_current_fact_revision_id', None)
    payload['result']['meta']['fact_snapshot'] = None
    (job_dir / 'status.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    persisted = await persist_analysis_job_snapshot(payload, include_results=True)
    return {
        'sample': sid,
        'source_job_id': job_id,
        'replay_job_id': replay_job,
        'original_ai_count': len(original_ai),
        'replay_ai_count': len(replay_issues),
        'matched_findings': matched,
        'unmatched_findings': unmatched,
        'replay_calls': replay_chat.calls,
        'coverage': {k: service.ai_coverage.get(k) for k in (
            'status', 'planned_calls', 'succeeded_calls', 'failed_calls', 'retried_calls', 'attempts_total')},
        'execution_state': execution.get('state'),
        'persisted': bool(persisted),
        'page_texts': len(page_texts),
        'trace_records': len(trace),
        'source_trace_sha256': replay_meta['replay']['source_trace_sha256'],
    }


async def main():
    results = []
    for sid in ('S1', 'S3'):
        results.append(await replay_one(sid))
    (OUT / 'replay_summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
