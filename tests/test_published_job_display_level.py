from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from api.routes import jobs
from src.services import analysis_fact_snapshot, issue_workflow_store, document_scope


@pytest.mark.asyncio
@pytest.mark.parametrize('level', ['department', 'unit'])
async def test_published_job_exposes_linked_level_without_guessing_source_scope(monkeypatch, level):
    monkeypatch.setenv('DATABASE_URL', 'configured')
    published = {'job_id':'job','organization_id':'org','status':'review_required',
                 'document_profile':{'caliber':{'value':None}}}
    before = deepcopy(published)
    monkeypatch.setattr(analysis_fact_snapshot,'read_database_result',AsyncMock(return_value=published))
    monkeypatch.setattr(document_scope,'pending_reanalysis',AsyncMock(return_value=None))
    monkeypatch.setattr(jobs,'user_can_access_job',lambda user,payload:True)
    monkeypatch.setattr(jobs.runtime,'resolve_organization_level',lambda org:level if org=='org' else None)
    monkeypatch.setattr(issue_workflow_store,'project_job_issue_decisions',lambda job,payload:{})
    view = await jobs.published_job_view('job',{'status':'review_required'}, {'is_admin':True})
    assert view['organization_level'] == level
    assert view['document_profile']['caliber']['value'] is None
    assert published == before


@pytest.mark.asyncio
@pytest.mark.parametrize('status,expected', [('review_required','queued'),('error','error'),('cancelled','cancelled')])
async def test_scope_outbox_projects_pending_state_without_overwriting_history_or_failures(monkeypatch,status,expected):
    monkeypatch.setenv('DATABASE_URL','configured')
    published={'job_id':'job','status':'review_required','analysis_revision':'old','result':{'original':True}}
    original=deepcopy(published)
    monkeypatch.setattr(analysis_fact_snapshot,'read_database_result',AsyncMock(return_value=published))
    monkeypatch.setattr(document_scope,'pending_reanalysis',AsyncMock(return_value={'request_id':'request','dispatch_status':'pending'}))
    monkeypatch.setattr(jobs,'user_can_access_job',lambda user,payload:True)
    monkeypatch.setattr(issue_workflow_store,'project_job_issue_decisions',lambda job,payload:{})
    view=await jobs.published_job_view('job',{'status':status},{'is_admin':True})
    assert view['status']==expected
    assert view['scope_reanalysis']['dispatch_status']=='pending'
    assert view['analysis_revision']=='old' and view['result']==published['result']
    assert published==original


@pytest.mark.asyncio
async def test_scope_status_unavailable_cannot_be_presented_as_completed(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setenv('DATABASE_URL','configured')
    monkeypatch.setattr(analysis_fact_snapshot,'read_database_result',AsyncMock(return_value={'job_id':'job'}))
    monkeypatch.setattr(document_scope,'pending_reanalysis',AsyncMock(side_effect=RuntimeError('unavailable')))
    monkeypatch.setattr(jobs,'user_can_access_job',lambda user,payload:True)
    with pytest.raises(HTTPException) as error:
        await jobs.published_job_view('job',{'status':'review_required'},{'is_admin':True})
    assert error.value.status_code==503
