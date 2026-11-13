import assert from 'node:assert/strict';
import {
  resolveCompleteButtonState, toReviewContextState, isObligationHandled,
  type ReviewLifecycleDataRecord, type ObligationReviewItemRecord,
} from '../app/components/review-workbench/reviewLifecycleAdapters';

const review = {
  slot_id: 's', history: [], current_session: null,
  current_analysis: { analysis_basis_token: 'j:1' },
  completion_gate: { can_complete: false, blockers: [{ code: 'pending_findings', count: 3 }] },
  report_confirmation_gate: { can_complete: true, blockers: [] },
} as unknown as ReviewLifecycleDataRecord;
const parsed = toReviewContextState({ review_context: { available: true, reason: null, slot_id: 's', job_uuid: 'j' }, review });
assert.equal(parsed.kind, 'available');
assert.equal(resolveCompleteButtonState(review, 3).enabled, true);
assert.equal(resolveCompleteButtonState(review, 3).label, '确认整份问题清单');
const blocked = { ...review, report_confirmation_gate: { can_complete: false,
  blockers: [{ code: 'blocking_obligations', count: 1 }] } };
assert.equal(resolveCompleteButtonState(blocked, 3).enabled, false);
const oldBackend = { ...review, report_confirmation_gate: undefined };
assert.equal(resolveCompleteButtonState(oldBackend, 3).enabled, false);
assert.equal(isObligationHandled({ handling_kind: 'system_recheck', decision: 'verified_ok' } as ObligationReviewItemRecord), false);
assert.equal(isObligationHandled({ handling_kind: 'human_confirm', decision: 'verified_ok' } as ObligationReviewItemRecord), true);
console.log('report confirmation contract passed');
