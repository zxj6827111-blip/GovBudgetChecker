"""整份确认的业务边界：确定性问题可一次签认，未知和技术失败不能随之放行。"""
import copy
import json

import pytest
from fastapi import HTTPException

from api import runtime
from src.services import issue_workflow_store as workflow
from src.services import review_lifecycle_service as lifecycle
from src.services.review_problem_set import ReviewProblem, automatic_finding_verified
from tests.test_review_lifecycle_service import _state, _session, _obligation_item, _decision
from tests.test_review_lifecycle_pg import review_db, _make_reviewable_slot, _conn, _sessions, _finding


def verified_issue(key='i'):
    return _finding(key, evidence_status='complete', verification_status='verified',
                    location={'page': 3, 'verification_status': 'verified'})


def test_whole_report_only_resolves_verified_pending_findings():
    problem = ReviewProblem('i', 'finding', '金额不同', 'high', True, automatic_verified=True)
    state = _state(active=_session(), problems=[problem])
    assert not lifecycle.evaluate_completion_gate(state).can_complete
    assert lifecycle.evaluate_completion_gate(state, confirm_report=True).can_complete
    for decision in ['needs_review', 'misspelled']:
        state.decisions = {'i': decision}
        assert not lifecycle.evaluate_completion_gate(state, confirm_report=True).can_complete
    state.decisions = {'i': 'no_issue'}
    assert lifecycle.report_confirmation_ids(state) == []


@pytest.mark.parametrize('status,action', [('execution_error', None), ('not_executed', None),
                                          ('insufficient_data', 'system_recheck'),
                                          ('parse_ambiguity', 'tech_fix')])
def test_system_failures_cannot_be_closed_by_human_or_report_confirmation(status, action):
    item = _obligation_item('tech', status=status)
    item.next_action = action
    state = _state(active=_session(), coverage_items=[item], coverage_blocking_total=1,
                   obligation_decisions={'tech': _decision('verified_ok')})
    assert not lifecycle.evaluate_completion_gate(state, confirm_report=True).can_complete
    assert lifecycle._prepare_obligation_decision(state, 'tech').status_code == 409
    assert lifecycle._obligation_review_block(state).items[0].handling_kind == 'system_recheck'


@pytest.mark.parametrize('patch', [{'source': 'ai'}, {'evidence_status': 'degraded_missing_evidence'},
                                  {'verification_status': None}, {'location': {'page': True}},
                                  {'location': {'page': 3}}, {'_merge_candidate_review': True}])
def test_unverified_or_ai_candidates_are_not_bulk_signed(patch):
    assert not automatic_finding_verified({**verified_issue(), **patch})


@pytest.fixture
def report_job(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'UPLOAD_ROOT', tmp_path)
    folder = tmp_path / 'j'
    folder.mkdir()
    payload = {'job_id': 'j', 'analysis_revision': '1', 'status': 'done',
               'result': {'ai_findings': [], 'rule_findings': [verified_issue()]}}
    runtime.write_json_file(folder / 'status.json', payload)
    return folder, payload


def sign_report(**overrides):
    args = dict(expected_token=workflow.get_job_problem_snapshot('j')['token'],
                expected_revision='1', issue_ids=['i'], actor='reviewer')
    args.update(overrides)
    return workflow.confirm_report_issues_if_snapshot_matches('j', **args)


def test_signature_uses_version_bound_events_and_cannot_carry_into_new_analysis(report_job):
    folder, payload = report_job
    result = sign_report()
    events = result['state']['decision_events']
    assert events[-1]['decision_origin'] == 'whole_report_confirmation'
    assert workflow.get_job_problem_snapshot('j')['decisions'] == {'i': 'confirmed'}
    payload['analysis_revision'] = '2'
    runtime.write_json_file(folder / 'status.json', payload)
    assert workflow.get_job_problem_snapshot('j')['decisions'] == {}
    assert workflow._read_state()['decision_events'] == events


@pytest.mark.parametrize('override', [{'expected_token': 'stale'}, {'expected_revision': 'old'},
                                     {'issue_ids': ['i', 'absent']}])
def test_signature_cas_revision_and_all_item_validation_are_atomic(report_job, override):
    before = copy.deepcopy(workflow._read_state())
    with pytest.raises(HTTPException) as exc:
        sign_report(**override)
    assert exc.value.status_code == 409
    assert workflow._read_state() == before


@pytest.mark.real_database
async def test_whole_report_is_persisted_and_idempotent_on_real_pg(review_db):
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[verified_issue()])
        await lifecycle.start_review(conn, ctx['slot_id'], actor='reviewer')
        completed = await lifecycle.complete_review(conn, ctx['slot_id'], actor='reviewer', confirm_report=True)
        repeated = await lifecycle.complete_review(conn, ctx['slot_id'], actor='other', confirm_report=True)
    assert completed.session.review_result['report_confirmation']['confirmed_issue_ids'] == ['i']
    assert completed.session.review_result['issue_counts']['confirmed'] == 1
    assert repeated.session.review_session_id == completed.session.review_session_id
    assert repeated.session.completed_by == 'reviewer'
    assert workflow.get_job_problem_snapshot(ctx['job_uuid'])['decisions'] == {'i': 'confirmed'}
    assert len([r for r in await _sessions(schema, pool, ctx['slot_id']) if r['status'] == 'completed']) == 1


@pytest.mark.real_database
async def test_pg_failure_keeps_signature_but_never_manufactures_completed_session(review_db, monkeypatch):
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[verified_issue()])
        await lifecycle.start_review(conn, ctx['slot_id'], actor='reviewer')
        async def fail(*args, **kwargs):
            raise RuntimeError('injected completion failure')
        with monkeypatch.context() as patch:
            patch.setattr(lifecycle.review_session_store, 'complete_session', fail)
            with pytest.raises(RuntimeError, match='injected'):
                await lifecycle.complete_review(conn, ctx['slot_id'], actor='reviewer', confirm_report=True)
        assert workflow.get_job_problem_snapshot(ctx['job_uuid'])['decisions'] == {'i': 'confirmed'}
        assert not [r for r in await _sessions(schema, pool, ctx['slot_id']) if r['status'] == 'completed']
        recovered = await lifecycle.complete_review(conn, ctx['slot_id'], actor='reviewer', confirm_report=True)
        assert recovered.session.status == 'completed'
