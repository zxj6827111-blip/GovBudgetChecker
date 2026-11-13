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
    p=payload()
    e=event(p,status)
    p['result']['ai_findings'][0]['message']='模型换一种措辞'
    result=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]))
    assert len(result)==1 and result[0]['issue_id']=='new-ai'
    assert result[0]['inherited'] and result[0]['status']==status
    assert result[0]['note']=='已结合原件判断' and result[0]['resolved_from_issue_id']=='old-ai'


def native_payload():
    p=payload()
    issue=p['result']['ai_findings'][0]
    issue.update(id='native-current',source='rule',rule_id='V33-005',check_relation_id='native:sum',
                 fact_ids=['same-fact-id'],evidence_ids=['same-evidence-id'])
    p['result']['rule_findings']=p['result'].pop('ai_findings')
    return p,issue


@pytest.mark.parametrize('status',['confirmed','no_issue'])
@pytest.mark.parametrize('change',['none','policy','source','year','owner','unverified'])
def test_native_exact_old_ids_never_bypass_current_source_and_policy_identity(status,change):
    p,issue=native_payload()
    e=dict(job_id='job',issue_id=issue['id'],analysis_revision='old',status=status,
           decision_revision='1',identity_fingerprint=workflow.issue_identity_fingerprint(issue),
           fact_ids=issue['fact_ids'][:],evidence_ids=issue['evidence_ids'][:],
           check_relation_id=issue['check_relation_id'],decision_identity=decision_identity(p,issue))
    if change=='policy':p['result']['meta']['rule_policy_version']='other-policy'
    elif change=='source':
        p['checksum']='b'*64
        issue['evidence'][0]['file_hash']='b'*64
    elif change=='year':p['report_year']=2024
    elif change=='owner':p['organization_name']='乙单位'
    elif change=='unverified':issue['evidence_verification']['status']='failed'
    projected=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]))
    assert bool(projected) == (change=='none')


@pytest.mark.parametrize('change',['none','other_cell','missing_proof','other_convention'])
def test_blank_operand_identity_is_not_replaced_by_the_same_narrative_quote(change):
    p,issue=native_payload()
    observation=dict(file_hash='a'*64,source_cell_id='native:r4c5',table_code='FIN_07_three_public',
        instance_uid='native',indicator='vehicle_purchase_operation',subject_code='total',budget_stage='决算',
        physical_page=2,bbox=[1,2,3,4],presence='blank',calculation=True,convention=dict(id='U-5'))
    proof=dict(file_hash='a'*64,source_cell_id='native:r4c5',field='vehicle_purchase_operation',subject_code='total',
        physical_page=2,bbox=[1,2,3,4],blank_as_zero=True,current_value='0.00',
        verification_status='verified',location_level='exact_cell')
    issue['location']=dict(operand_refs=[],native_blank_sources=[observation],native_blank_calculation_refs=[proof])
    e=dict(job_id='job',issue_id=issue['id'],analysis_revision='old',status='confirmed',decision_revision='1',
        decision_identity=decision_identity(p,issue))
    assert e['decision_identity']
    if change=='other_cell':observation['source_cell_id']=proof['source_cell_id']='native:r4c7'
    elif change=='missing_proof':issue['location']['native_blank_calculation_refs']=[]
    elif change=='other_convention':observation['convention']['id']='other-policy'
    projected=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]))
    assert bool(projected) == (change=='none')


@pytest.mark.parametrize('change',['file','quote','source','page','year','organization','check','claim','severity','unverified','duplicate','policy'])
def test_any_changed_or_ambiguous_review_basis_cannot_inherit(change):
    p=payload()
    e=event(p)
    issue=p['result']['ai_findings'][0]
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
        other=copy.deepcopy(issue)
        other['id']='another-ai'
        p['result']['ai_findings'].append(other)
    assert workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]))==[]


def test_legacy_event_only_recovers_from_hash_verified_original_revision(tmp_path,monkeypatch):
    p=payload()
    old=copy.deepcopy(p)
    old['analysis_revision']='old'
    old['result']['ai_findings'][0]['id']='old-ai'
    old['result']['ai_findings'][0]['rule_id']='AI-DESC-000'
    old['result']['ai_findings'][0]['message']='原模型措辞'
    e=event(old)
    e.pop('decision_identity')
    e['identity_fingerprint']=workflow.issue_identity_fingerprint(old['result']['ai_findings'][0])
    folder=tmp_path/'job/preserved_analyses/version'
    folder.mkdir(parents=True)
    content=json.dumps(old).encode()
    (folder/'status.json').write_bytes(content)
    (folder/'manifest.json').write_text(json.dumps(dict(files={'status.json':hashlib.sha256(content).hexdigest()})))
    monkeypatch.setattr(workflow.runtime,'UPLOAD_ROOT',tmp_path)
    result=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]))
    assert result[0]['inherited']
    (folder/'status.json').write_bytes(content+b' ')
    assert workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]))==[]


def test_many_legacy_decisions_read_each_verified_archive_once_per_projection(tmp_path,monkeypatch):
    from pathlib import Path
    p=payload()
    base=copy.deepcopy(p['result']['ai_findings'][0])
    old=copy.deepcopy(p)
    p['result']['ai_findings']=[]
    old['result']['ai_findings']=[]
    old['analysis_revision']='old'
    events=[]
    for number in range(20):
        current=copy.deepcopy(base)
        current['id']=f'new-{number}'
        current['evidence'][0]['source_block_id']=f'native:p3:s{number*30}:e{number*30+24}'
        previous=copy.deepcopy(current)
        previous['id']=f'old-{number}'
        p['result']['ai_findings'].append(current)
        old['result']['ai_findings'].append(previous)
        events.append(dict(job_id='job',issue_id=previous['id'],analysis_revision='old',status='confirmed',
                           decision_revision='1',identity_fingerprint=workflow.issue_identity_fingerprint(previous)))
    folder=tmp_path/'job/preserved_analyses/version'
    folder.mkdir(parents=True)
    snapshot=folder/'status.json'
    content=json.dumps(old).encode()
    snapshot.write_bytes(content)
    (folder/'manifest.json').write_text(json.dumps(dict(files={'status.json':hashlib.sha256(content).hexdigest()})))
    monkeypatch.setattr(workflow.runtime,'UPLOAD_ROOT',tmp_path)
    original=Path.read_bytes
    reads=[]
    def counted(path):
        if path==snapshot:reads.append(path)
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',counted)
    assert len(workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=events)))==20
    assert len(reads)==1
    # 下一次读取必须重验；即使只是加空格、JSON仍等价也不能沿用上次通过结果。
    snapshot.write_bytes(content+b' ')
    assert workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=events))==[]
    assert len(reads)==2


def test_conflicting_prior_decisions_remain_needs_review():
    p=payload()
    one=event(p)
    two=event(p,'no_issue')
    two['issue_id']='another-old-ai'
    result=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[one,two]))
    assert result[0]['status']=='needs_review' and result[0]['conflict']


def test_newer_decision_supersedes_old_status_on_same_prior_issue():
    p=payload()
    one=event(p)
    two=event(p,'no_issue')
    two['decision_revision']='2'
    result=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[one,two]))
    assert result[0]['status']=='no_issue' and not result[0]['conflict']


@pytest.mark.parametrize('status',['pending','needs_review'])
def test_later_undo_never_resurrects_earlier_confirmed_decision(status):
    p=payload()
    one=event(p)
    two=event(p,status)
    two['decision_revision']='2'
    assert workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[one,two]))==[]


@pytest.mark.parametrize('third_id',['successor-b','successor-c'])
def test_undo_of_inherited_successor_never_revives_ancestor_ignore(tmp_path,monkeypatch,third_id):
    first=payload()
    first['analysis_revision']='r1'
    first['result']['ai_findings'][0]['id']='ancestor-a'
    original=workflow._decision_record(first,first['result']['ai_findings'][0],status='no_issue',
        actor='reviewer',note='原件判断为误报',hide_from_results=True)
    state=dict(issues={},decision_events=[],packages=[])
    workflow._record_decision(state,original)
    second=copy.deepcopy(first)
    second['analysis_revision']='r2'
    second['result']['ai_findings'][0]['id']='successor-b'
    inherited=workflow.project_job_issue_decisions('job',second,_state=state)
    assert inherited[0]['status']=='no_issue' and inherited[0]['inherited']

    # 复用用户正常撤销忽略的业务入口；所有持久化都隔离在内存和tmp_path。
    monkeypatch.setattr(workflow,'_state_lock',lambda:nullcontext())
    monkeypatch.setattr(workflow,'_read_state',lambda:state)
    monkeypatch.setattr(workflow,'_write_state',lambda value:None)
    monkeypatch.setattr(workflow,'_record_persistence_state',lambda *args:None)
    monkeypatch.setattr(workflow.runtime,'UPLOAD_ROOT',tmp_path)
    monkeypatch.setattr(workflow.runtime,'get_job_status_payload',lambda *args,**kwargs:second)
    workflow.set_job_ignored_issue_ids('job',set())
    assert state['decision_events'][-1]['issue_id']=='successor-b'
    assert state['decision_events'][-1]['status']=='pending'

    third=copy.deepcopy(second)
    third['analysis_revision']='r3'
    third['result']['ai_findings'][0]['id']=third_id
    projected=workflow.project_job_issue_decisions('job',third,_state=state)
    assert not any(item['status'] in {'confirmed','no_issue','in_package'} for item in projected)
    assert workflow.get_job_ignored_issue_ids('job',payload=third)==set()


@pytest.mark.parametrize('status',['pending','needs_review'])
@pytest.mark.parametrize('clock',['newer','same_second','missing'])
def test_unsequenced_cross_id_undo_does_not_lose_to_larger_ancestor_revision(status,clock):
    p=payload()
    p['analysis_revision']='r3'
    original=event(p,'no_issue')
    original.update(analysis_revision='r1',decision_revision='5',
        updated_at='2026-11-09T00:00:00Z',hide_from_results=True)
    undo=event(p,status)
    undo.update(issue_id='new-ai',analysis_revision='r2',decision_revision='1',
        updated_at='2026-11-09T00:00:01Z' if clock=='newer' else original['updated_at'])
    if clock=='missing':undo.pop('updated_at')
    result=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[original,undo]))
    assert not any(item['status'] in {'confirmed','no_issue','in_package'} for item in result)
    assert not any(item['hide_from_results'] for item in result)


def test_same_second_new_user_decisions_order_across_ids_and_preserve_history(monkeypatch):
    monkeypatch.setattr(workflow,'_now',lambda:'2026-11-09T00:00:00Z')
    state=dict(issues={},decision_events=[],packages=[])
    generations=[]
    for revision,issue_id,status in [('r1','a','no_issue'),('r2','b','pending'),('r3','c','confirmed')]:
        p=payload()
        p['analysis_revision']=revision
        p['result']['ai_findings'][0]['id']=issue_id
        record=workflow._decision_record(p,p['result']['ai_findings'][0],status=status,
            actor='reviewer',note=revision,hide_from_results=status=='no_issue')
        workflow._record_decision(state,record)
        generations.append(p)
    assert [e['event_sequence'] for e in state['decision_events']]==[1,2,3]
    current=copy.deepcopy(generations[-1])
    current['analysis_revision']='r4'
    result=workflow.project_job_issue_decisions('job',current,_state=state)
    assert result[0]['status']=='confirmed' and result[0]['note']=='r3'
    # 后代撤销/确认不能改写原分析的显式决定。
    historical=workflow.project_job_issue_decisions('job',generations[0],_state=state)
    assert historical[0]['status']=='no_issue' and not historical[0]['inherited']


@pytest.mark.parametrize('current_id',['old-ai','another-old-ai','new-ai'])
def test_inherited_canonical_id_never_hides_same_generation_member_conflict(current_id):
    p=payload()
    p['result']['ai_findings'][0]['id']=current_id
    one=event(p,'no_issue')
    one['hide_from_results']=True
    two=event(p,'confirmed')
    two['issue_id']='another-old-ai'
    result=workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[one,two]))
    assert result[0]['status']=='needs_review' and result[0]['conflict']
    assert not result[0]['hide_from_results']


@pytest.mark.parametrize('change',['source','year','policy','claim'])
def test_newer_decision_for_other_source_basis_cannot_supersede_current_basis(change):
    current=payload()
    first=event(current,'confirmed')
    first['event_sequence']=1
    other=copy.deepcopy(current)
    issue=other['result']['ai_findings'][0]
    if change=='source':
        other['checksum']='b'*64
        issue['evidence'][0]['file_hash']='b'*64
    elif change=='year':other['report_year']=2024
    elif change=='policy':other['result']['meta']['rule_policy_version']='different'
    else:issue['actual']='另一个判断对象'
    undo=event(other,'pending')
    undo.update(issue_id='other',event_sequence=2)
    result=workflow.project_job_issue_decisions('job',current,_state=dict(decision_events=[first,undo]))
    assert result[0]['status']=='confirmed' and not result[0]['conflict']


def test_appending_user_order_never_rewrites_or_reorders_legacy_events():
    p=payload()
    old=event(p,'no_issue')
    old['event_id']='old-immutable'
    old['updated_at']='2026-11-08T00:00:00Z'
    state=dict(issues={},decision_events=[copy.deepcopy(old)],packages=[])
    record=workflow._decision_record(p,p['result']['ai_findings'][0],status='pending',actor='reviewer',note=None)
    workflow._record_decision(state,record)
    assert state['decision_events'][0]==old and 'event_sequence' not in old
    assert state['decision_events'][1]['event_sequence']==1
    normalized=workflow._normalize_state(state)
    assert normalized['decision_events'][1]['event_sequence']==1
    assert 'event_sequence' not in normalized['decision_events'][0]
    migrated=workflow._decision_record(p,p['result']['ai_findings'][0],status='no_issue',actor=None,note=None,
        origin='legacy_migration')
    workflow._record_decision(state,migrated)
    assert 'event_sequence' not in state['decision_events'][-1]


def test_reused_successor_id_for_another_claim_cannot_discard_old_claim_undo(monkeypatch):
    monkeypatch.setattr(workflow,'_now',lambda:'2026-11-09T00:00:00Z')
    state=dict(issues={},decision_events=[],packages=[])
    first=payload()
    first['analysis_revision']='r1'
    first['result']['ai_findings'][0]['id']='a'
    workflow._record_decision(state,workflow._decision_record(first,first['result']['ai_findings'][0],
        status='no_issue',actor='reviewer',note='X误报',hide_from_results=True))
    second=copy.deepcopy(first)
    second['analysis_revision']='r2'
    second['result']['ai_findings'][0]['id']='b'
    workflow._record_decision(state,workflow._decision_record(second,second['result']['ai_findings'][0],
        status='pending',actor='reviewer',note='撤销X'))
    current=copy.deepcopy(second)
    current['analysis_revision']='r3'
    current['result']['ai_findings'][0]['id']='c'
    other=copy.deepcopy(current['result']['ai_findings'][0])
    other.update(id='b',actual='另一个判断对象Y')
    current['result']['ai_findings'].append(other)
    workflow._record_decision(state,workflow._decision_record(current,other,
        status='confirmed',actor='reviewer',note='确认Y'))
    result=workflow.project_job_issue_decisions('job',current,_state=state)
    assert not any(item['issue_id']=='c' and item['status'] in {'confirmed','no_issue','in_package'} for item in result)
    assert next(item for item in result if item['issue_id']=='b')['status']=='confirmed'


def test_late_legacy_import_cannot_replace_same_identity_user_undo(monkeypatch):
    monkeypatch.setattr(workflow,'_now',lambda:'2026-11-09T00:00:00Z')
    state=dict(issues={},decision_events=[],packages=[])
    p=payload()
    p['analysis_revision']='r1'
    issue=p['result']['ai_findings'][0]
    workflow._record_decision(state,workflow._decision_record(p,issue,status='pending',actor='reviewer',note='撤销'))
    workflow._record_decision(state,workflow._decision_record(p,issue,status='no_issue',actor=None,note='旧导入',
        hide_from_results=True,origin='legacy_migration'))
    p['analysis_revision']='r2'
    assert workflow.project_job_issue_decisions('job',p,_state=state)==[]


def test_same_quote_and_generic_check_do_not_prove_same_unstructured_ai_claim():
    p=payload()
    issue=p['result']['ai_findings'][0]
    issue.pop('expected')
    issue.pop('actual')
    e=event(p)
    issue['message']='另一种材料缺陷，与原判断不同'
    assert workflow.project_job_issue_decisions('job',p,_state=dict(decision_events=[e]))==[]


def test_visible_workflow_and_gate_use_same_successor(monkeypatch):
    p=payload()
    e=event(p)
    record={**e,'key':'job::old-ai'}
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
    p=payload()
    e=event(p)
    state=workflow._normalize_state(dict(issues={'old':e},decision_events=[e]))
    assert state['issues']['job::old-ai']['decision_identity']==e['decision_identity']


def test_changed_basis_keeps_prior_note_visible_without_closing_current_issue(monkeypatch):
    p=payload()
    e=event(p)
    p['result']['ai_findings'][0]['check']='另一检查'
    record={**e,'key':'job::old-ai'}
    state=dict(issues={'job::old-ai':record},decision_events=[e],packages=[])
    monkeypatch.setattr(workflow,'_state_lock',lambda:nullcontext())
    monkeypatch.setattr(workflow,'_read_state',lambda:state)
    monkeypatch.setattr(workflow,'user_can_access_job',lambda *args:True)
    monkeypatch.setattr(workflow.runtime,'get_job_access_payload',lambda job:p)
    monkeypatch.setattr(workflow.runtime,'get_job_status_payload',lambda *args,**kwargs:p)
    result=workflow.get_visible_state({'is_admin':True},job_id='job')
    assert not result['issues']
    assert result['prior_decisions'][0]['note']=='已结合原件判断'
    assert result['prior_decisions'][0]['requires_reconfirmation']
