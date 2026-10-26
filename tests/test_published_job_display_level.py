from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from api.routes import jobs
from src.services import analysis_fact_snapshot, issue_workflow_store


@pytest.mark.asyncio
@pytest.mark.parametrize('level', ['department', 'unit'])
async def test_published_job_exposes_linked_level_without_guessing_source_scope(monkeypatch, level):
    monkeypatch.setenv('DATABASE_URL', 'configured')
    published = {'job_id':'job','organization_id':'org','status':'review_required',
                 'document_profile':{'caliber':{'value':None}}}
    before = deepcopy(published)
    monkeypatch.setattr(analysis_fact_snapshot,'read_database_result',AsyncMock(return_value=published))
    monkeypatch.setattr(jobs,'user_can_access_job',lambda user,payload:True)
    monkeypatch.setattr(jobs.runtime,'resolve_organization_level',lambda org:level if org=='org' else None)
    monkeypatch.setattr(issue_workflow_store,'project_job_issue_decisions',lambda job,payload:{})
    view = await jobs.published_job_view('job',{'status':'review_required'}, {'is_admin':True})
    assert view['organization_level'] == level
    assert view['document_profile']['caliber']['value'] is None
    assert published == before
