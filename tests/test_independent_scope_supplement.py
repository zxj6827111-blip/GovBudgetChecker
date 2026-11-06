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
    decision.update(approved_case_id=h.receipt_scope_id(row,row['receipts'][0]),
                    approved_semantic_digest=h.scope_semantic_digest(row['receipts'][0]),
                    approved_status='not_applicable', approved_reason='source')
    review = {'version': 'independent-diagnostic-scope-review-v2', 'decisions': [decision]}
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
        assert h.apply_scope_review(original, {"S": row}, p, h.sha256_file(p))["entries"] == []
    with pytest.raises(SystemExit):
        h.apply_scope_review(original, {"S": row}, p, "wrong")


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "pass"),
        ("incomplete_reason", "披露了但解析失败"),
        ("formula", "different basis"),
        ("reason_codes", ["parse_error"]),
    ],
)
def test_scope_approval_does_not_move_to_changed_semantics(tmp_path, field, value):
    row = {
        "sample_id": "S",
        "sha256": "a" * 64,
        "obligations": {"O": {"status": "insufficient_data"}},
        "receipts": [
            {
                "obligation_id": "O",
                "rule_id": "R",
                "sub_check_id": "x",
                "status": "not_applicable",
                "incomplete_reason": "未披露",
            }
        ],
    }
    old = row["receipts"][0]
    d = {
        "sample_id": "S",
        "pdf_sha256": "a" * 64,
        "key": ["O", "R", "x"],
        "scope_review": "APPROVED_OBJECT_SCOPE_ONLY",
        "diagnostic_scope_decision": "explanation_only",
        "reason": "independent original review",
        "sources": [{"page": 1}],
        "approved_case_id": h.receipt_scope_id(row, old),
        "approved_semantic_digest": h.scope_semantic_digest(old),
        "approved_status": old["status"],
        "approved_reason": old["incomplete_reason"],
    }
    p = tmp_path / "scope.json"
    p.write_text(
        json.dumps({"version": "independent-diagnostic-scope-review-v2", "decisions": [d]})
    )
    applied = h.apply_scope_review({"entries": []}, {"S": row}, p, h.sha256_file(p))
    assert not h.diagnostic_scope(row, applied)[1]
    row["receipts"][0][field] = value
    assert h.diagnostic_scope(row, applied)[1]  # 已应用的决定也不能通过随后改写当前对象绕过。
    reapplied = h.apply_scope_review({"entries": []}, {"S": row}, p, h.sha256_file(p))
    assert not reapplied["entries"]
    assert h.diagnostic_scope(row, reapplied)[1]


def test_legacy_unbound_review_stays_pending(tmp_path):
    p = tmp_path / "old.json"
    p.write_text(
        json.dumps({"version": "independent-diagnostic-scope-review-20261106-v1", "decisions": []})
    )
    result = h.apply_scope_review({"entries": []}, {}, p, h.sha256_file(p))
    assert (
        result["entries"] == []
        and result["unapplied_review"] == "LEGACY_UNBOUND_REQUIRES_V2_REVIEW"
    )
