import copy
import json
import pytest
from scripts import verify_pending_review as h


def test_scope_review_is_external_exact_object_only(tmp_path):
    row = {'sample_id': 'S', 'sha256': 'a'*64, 'obligations': {},
           'receipts': [{'obligation_id': 'O', 'rule_id': 'R', 'sub_check_id': 'x', 'status': 'not_applicable', 'incomplete_reason': 'source'}]}
    decision = {'sample_id': 'S', 'pdf_sha256': 'a'*64, 'key': ['O','R','x'],
                'scope_review': 'APPROVED_OBJECT_SCOPE_ONLY', 'diagnostic_scope_decision': 'explanation_only',
                'reason': 'independent source review', 'sources': [{'page': 1}]}
    review = {'version': 'independent-diagnostic-scope-review-20261106-v1', 'decisions': [decision]}
    p = tmp_path/'scope.json'
    p.write_text(json.dumps(review))
    original = {'entries': []}
    applied = h.apply_scope_review(original, {'S': row}, p, h.sha256_file(p))
    assert original == {'entries': []}
    assert applied['entries'][0]['case_id'] == h.receipt_scope_id(row, row['receipts'][0])
    for field, bad in [('sample_id','other'),('pdf_sha256','b'*64),('key',['O','R','other'])]:
        altered = copy.deepcopy(review)
        altered['decisions'][0][field] = bad
        p.write_text(json.dumps(altered))
        assert h.apply_scope_review(original, {'S': row}, p, h.sha256_file(p))['entries'] == []
    with pytest.raises(SystemExit): h.apply_scope_review(original, {'S': row}, p, 'wrong')
