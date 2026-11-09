import copy
from contextlib import nullcontext
import hashlib
import json

import pytest

from src.services import issue_workflow_store as workflow
from src.services.decision_identity import decision_identity


def payload():
    return dict(job_id='job', analysis_revision='new', checksum='a'*64, organization_name='甲单位',
        report_year=2025, report_kind='final', result=dict(meta=dict(rule_policy_version='v3_3'),
        ai_findings=[dict(id='new-ai', source='ai', rule_id='AI-DESC-002', check='增减原因完整性',
            expected='说明支出减少的业务原因',actual='以支出减少重复充当原因',
            title='说明原因不充分', message='新模型措辞', severity='medium', evidence_status='complete',
            evidence_verification=dict(status='verified', source_identity_complete=True),
            evidence=[dict(file_hash='a'*64, physical_page=3, source_block_id='native:p3:s12:e24',
                quote='支出减少10万元，原因是支出减少。', source_identity_status='verified',
                verification_status='verified', location_level='exact_span')])]))


def event(p, status='confirmed'):
    issue=p['result']['ai_findings'][0]
    return dict(job_id='job', issue_id='old-ai', analysis_revision='old', status=status,
        note='已结合原件判断', decision_revision='1', decision_actor='reviewer',
        identity_fingerprint='old-fingerprint', decision_identity=decision_identity(p,issue))


@pytest.mark.parametrize('status', ['confirmed','no_issue'])
def test_verified_same_source_and_check_inherits_despite_id_and_message_changes(status):
    p=payload();e=event(p,status);p['result']['ai_findings'][0]['message']='模型换一种措辞'
    result=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]))
    assert len(result)==1 and result[0]['issue_id']=='new-ai'
    assert result[0]['inherited'] and result[0]['status']==status
    assert result[0]['note']=='已结合原件判断' and result[0]['resolved_from_issue_id']=='old-ai'


@pytest.mark.parametrize('change',['file','quote','source','page','year','organization','check','claim','severity','unverified','duplicate','policy'])
def test_any_changed_or_ambiguous_review_basis_cannot_inherit(change):
    p=payload();e=event(p);issue=p['result']['ai_findings'][0]
    if change=='file':p['checksum']='b'*64
    elif change=='quote':issue['evidence'][0]['quote']='支出减少20万元，原因是支出减少。'
    elif change=='source':issue['evidence'][0]['source_block_id']='native:p3:s25:e50'
    elif change=='page':issue['evidence'][0]['physical_page']=4
    elif change=='year':p['report_year']=2024
    elif change=='organization':p['organization_name']='乙单位'
    elif change=='check':issue['check']='科目用途'
    elif change=='claim':issue['actual']='20'
    elif change=='severity':issue['severity']='high'
    elif change=='unverified':issue['evidence_verification']['status']='failed'
    elif change=='policy':p['result']['meta']['rule_policy_version']='changed'
    else:
        other=copy.deepcopy(issue);other['id']='another-ai';p['result']['ai_findings'].append(other)
    assert workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]))==[]


def test_legacy_event_only_recovers_from_hash_verified_original_revision(tmp_path,monkeypatch):
    p=payload();old=copy.deepcopy(p);old['analysis_revision']='old';old['result']['ai_findings'][0]['id']='old-ai'
    old['result']['ai_findings'][0]['rule_id']='AI-DESC-000';old['result']['ai_findings'][0]['message']='原模型措辞'
    e=event(old);e.pop('decision_identity');e['identity_fingerprint']=workflow.issue_identity_fingerprint(old['result']['ai_findings'][0])
    folder=tmp_path/'job/preserved_analyses/version';folder.mkdir(parents=True);content=json.dumps(old).encode()
    (folder/'status.json').write_bytes(content);(folder/'manifest.json').write_text(json.dumps(dict(files={'status.json':hashlib.sha256(content).hexdigest()})))
    monkeypatch.setattr(workflow.runtime,'UPLOAD_ROOT',tmp_path)
    result=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]));assert result[0]['inherited']
    (folder/'status.json').write_bytes(content+b' ')
    assert workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]))==[]


def test_conflicting_prior_decisions_remain_needs_review():
    p=payload();one=event(p);two=event(p,'no_issue');two['issue_id']='another-old-ai'
    result=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[one,two]))
    assert result[0]['status']=='needs_review' and result[0]['conflict']


def test_newer_decision_supersedes_old_status_on_same_prior_issue():
    p=payload();one=event(p);two=event(p,'no_issue');two['decision_revision']='2'
    result=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[one,two]))
    assert result[0]['status']=='no_issue' and not result[0]['conflict']


@pytest.mark.parametrize('status',['pending','needs_review'])
def test_later_undo_never_resurrects_earlier_confirmed_decision(status):
    p=payload();one=event(p);two=event(p,status);two['decision_revision']='2'
    assert workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[one,two]))==[]


def test_same_quote_and_generic_check_do_not_prove_same_unstructured_ai_claim():
    p=payload();issue=p['result']['ai_findings'][0];issue.pop('expected');issue.pop('actual')
    e=event(p);issue['message']='另一种材料缺陷，与原判断不同'
    assert workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]))==[]


def test_visible_workflow_and_gate_use_same_successor(monkeypatch):
    p=payload();e=event(p);record={**e,'key':'job::old-ai'}
    state=dict(issues={'job::old-ai':record},decision_events=[e],packages=[],updated_at='now')
    monkeypatch.setattr(workflow,'_state_lock',lambda:nullcontext())
    monkeypatch.setattr(workflow,'_read_state',lambda:state)
    monkeypatch.setattr(workflow,'user_can_access_job',lambda *args:True)
    monkeypatch.setattr(workflow.runtime,'get_job_access_payload',lambda job:p)
    monkeypatch.setattr(workflow.runtime,'get_job_status_payload',lambda *args,**kwargs:p)
    result=workflow.get_visible_state({'is_admin':True},job_id='job')
    assert result['issues']['job::new-ai']['inherited']
    assert result['issues']['job::new-ai']['status']=='confirmed'


def test_normalization_retains_source_identity_for_next_process():
    p=payload();e=event(p);state=workflow._normalize_state(dict(issues={'old':e},decision_events=[e]))
    assert state['issues']['job::old-ai']['decision_identity']==e['decision_identity']


def test_changed_basis_keeps_prior_note_visible_without_closing_current_issue(monkeypatch):
    p=payload();e=event(p);p['result']['ai_findings'][0]['check']='另一检查'
    record={**e,'key':'job::old-ai'};state=dict(issues={'job::old-ai':record},decision_events=[e],packages=[])
    monkeypatch.setattr(workflow,'_state_lock',lambda:nullcontext())
    monkeypatch.setattr(workflow,'_read_state',lambda:state)
    monkeypatch.setattr(workflow,'user_can_access_job',lambda *args:True)
    monkeypatch.setattr(workflow.runtime,'get_job_access_payload',lambda job:p)
    monkeypatch.setattr(workflow.runtime,'get_job_status_payload',lambda *args,**kwargs:p)
    result=workflow.get_visible_state({'is_admin':True},job_id='job')
    assert not result['issues']
    assert result['prior_decisions'][0]['note']=='已结合原件判断'
    assert result['prior_decisions'][0]['requires_reconfirmation']
