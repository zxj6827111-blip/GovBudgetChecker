"""处理队列和详情共用真实页数、问题/提示计数。"""
import json

import pytest

from api import runtime
from src.schemas.review_report import review_report_counts


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'UPLOAD_ROOT', tmp_path)
    monkeypatch.setattr(runtime, 'ORG_AVAILABLE', False)
    runtime._JOB_SUMMARY_CACHE.clear()
    directory = tmp_path / 'queue-summary'
    directory.mkdir()
    return directory


def write_status(directory, **fields):
    payload = {'job_id': directory.name, 'status': 'review_required', 'progress': 100, **fields}
    (directory / 'status.json').write_text(json.dumps(payload, ensure_ascii=False))
    return payload


def test_summary_and_detail_use_original_pdf_total_even_when_scanned_count_is_zero(job):
    import fitz

    with fitz.open() as pdf:
        for _ in range(3):
            pdf.new_page()
        pdf.save(job / 'source.pdf')
    write_status(job, scanned_page_count=0, page_count=9, result={'meta': {'pages': 9}})
    original_status = (job / 'status.json').read_bytes()
    original_pdf = (job / 'source.pdf').read_bytes()
    assert runtime.collect_job_summary(job)['page_count'] == 3
    assert runtime.get_job_status_payload(job.name)['page_count'] == 3
    assert (job / 'status.json').read_bytes() == original_status
    assert (job / 'source.pdf').read_bytes() == original_pdf


@pytest.mark.parametrize('payload, expected', [
    ({'result': {'meta': {'pages': 45}}, 'scanned_page_count': 0}, 45),
    ({'result': {'meta': {'page_extraction': {'page_count': 30}}}}, 30),
    ({'page_count': 32}, 32),
    ({'page_count': '32'}, 32),
    ({'scanned_page_count': 10, 'page_coverage': 0.9}, None),
    ({'page_count': 0}, None),
    ({'page_count': True}, None),
    ({'page_count': 1.5}, None),
])
def test_page_count_fallback_never_borrows_scanned_pages(payload, expected):
    assert runtime.get_job_document_page_count(payload) == expected


def test_queue_keeps_zero_material_issues_and_exposes_all_detail_tips(job):
    findings = [{'id': f'tip-{index}', 'source': 'rule', 'rule_id': 'V33-120',
                 'severity': 'info', 'message': '相差0.01万元，可能为显示取整误差。'} for index in range(5)]
    write_status(job, scanned_page_count=0, review_reasons=[{'code': 'review', 'message': '检查尚未完成'}],
                 result={'rule_findings': findings, 'meta': {'pages': 45}})
    summary = runtime.collect_job_summary(job)
    detail = runtime.get_job_status_payload(job.name)
    counts = review_report_counts(detail['review_report'])
    assert summary['page_count'] == detail['page_count'] == 45
    assert summary['review_issue_total'] == counts['review_issue_total'] == 0
    assert summary['review_candidate_total'] == counts['review_candidate_total'] == 0
    assert summary['tip_total'] == counts['tip_total'] == 5
    assert summary['review_notice_total'] == counts['review_notice_total'] == 1


def test_unknown_document_pages_stay_unknown_in_summary(job):
    write_status(job, scanned_page_count=0, page_coverage=0.9)
    assert runtime.collect_job_summary(job)['page_count'] is None


@pytest.mark.parametrize('status, expected', [
    ('done', 1700000000.0), ('review_required', 1700000000.0),
    ('processing', None), ('queued', None), ('failed', None),
])
def test_summary_analysis_finish_timestamp_does_not_borrow_old_running_results(job, status, expected):
    write_status(job, status=status, updated_ts=1800000000,
                 result={'meta': {'finished_at': 1700000000}})
    summary = runtime.collect_job_summary(job)
    assert summary['analysis_finished_ts'] == expected


def test_summary_without_analysis_finish_time_does_not_use_updated_time(job):
    write_status(job, status='done', updated_ts=1800000000)
    assert runtime.collect_job_summary(job)['analysis_finished_ts'] is None
