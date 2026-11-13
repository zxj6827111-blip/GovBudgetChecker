import json
from pathlib import Path

import pytest

from test_obligation_review_pg import (
    review_db, _conn, _make_reviewable_slot,
    test_stale_analysis_basis_is_rejected_even_with_active_session as test_explicit_basis_control,
)
from src.schemas.review_lifecycle import ObligationDecisionRequest
from src.services import review_lifecycle_service as service

pytestmark = pytest.mark.real_database
OUT = Path(__file__).resolve().parents[2]


async def test_old_page_request_without_basis_cannot_write_into_new_analysis(review_db):
    schema,pool=review_db
    async with _conn(schema,pool) as conn:
        ctx=await _make_reviewable_slot(conn,blocking_ids=['OBL-A'])
        first=await service.start_review(conn,ctx['slot_id'],actor='reviewer-a')
        before=await service.load_review_by_slot(conn,ctx['slot_id'])
        # Actual ReviewWorkbenchPage payload shape; first decision has no expected_revision.
        payload=ObligationDecisionRequest(decision='verified_ok',note='旧页面已读材料',job_uuid=ctx['job_uuid'])
        await conn.execute('UPDATE analysis_jobs SET analysis_revision=2 WHERE id=$1',ctx['job_id'])
        second=await service.start_review(conn,ctx['slot_id'],actor='reviewer-a')
        after=await service.load_review_by_slot(conn,ctx['slot_id'])
        old_basis=before.current_analysis.analysis_basis_token
        new_basis=after.current_analysis.analysis_basis_token
        assert old_basis != new_basis
        observation={'old_basis':old_basis,'new_basis':new_basis,
                     'old_session':first.session.review_session_id,
                     'new_session':second.session.review_session_id,
                     'request':payload.model_dump(exclude_none=True)}
        try:
            result=await service.decide_obligation(conn,ctx['slot_id'],obligation_id='OBL-A',
                actor='reviewer-a',**payload.model_dump())
        except service.ReviewLifecycleError as exc:
            observation.update(rejected=True,status=exc.status_code,error=exc.error)
        else:
            observation.update(rejected=False,decision=result.decision.model_dump(mode='json'),
                               written_session=result.session.review_session_id)
        (OUT/'pg_stale_page_result.json').write_text(json.dumps(observation,ensure_ascii=False,indent=2))
        assert observation['rejected'], 'Old UI request was accepted into the new analysis session'
