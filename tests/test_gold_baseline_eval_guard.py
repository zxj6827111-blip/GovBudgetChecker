"""独立评估入口的拒收测试：歧义对齐不得自动继承裁决，实质字段漂移必须失败。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "gold_baseline_eval_fp_batch1", REPO / "scripts" / "gold_baseline_eval_fp_batch1.py")
guard = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(guard)


def item(item_id, message, *, tier="F", rule="CMM-013", pages=(2,), sev="warn",
         quotes=(), title="目录与正文标题不一致", doc="GS-TEST", text=None):
    return {"id": item_id, "doc": doc, "tier": tier, "rule": rule, "raw_id": item_id.rsplit("|", 1)[-1],
            "pages": list(pages), "sev": sev, "title": title, "message": message,
            "quotes": list(quotes), "text": text if text is not None else title + " " + message,
            "bucket": "issues", "checklist_item": "A-05", "obligation_ids": []}


def test_ambiguous_exact_candidates_require_review():
    old = [item("old|F|X-1", "一、同一处问题")]
    new = [item("new|F|X-1", "一、同一处问题"), item("new|F|X-2", "一、同一处问题")]
    pairs, old_only, new_only, review, resolved = guard.align(old, new, lambda _: "TP")
    assert not pairs and review and not resolved
    assert review[0]["reason"].startswith("旧同指纹组与候选数量不一致") or "无法" in review[0]["reason"]


def test_duplicate_group_with_same_decision_is_paired_by_group():
    old = [item("old|P|X-1", "一、重复提示", tier="P"), item("old|P|X-2", "一、重复提示", tier="P")]
    new = [item("new|P|X-1", "一、重复提示", tier="P"), item("new|P|X-2", "一、重复提示", tier="P")]
    pairs, old_only, new_only, review, resolved = guard.align(old, new, lambda _: "NOISE")
    assert len(pairs) == 2 and not review and len(resolved) == 1 and resolved[0]["decision"] == "NOISE"


def test_duplicate_group_with_different_decisions_requires_review():
    old = [item("old|F|X-1", "一、重复问题"), item("old|F|X-2", "一、重复问题")]
    new = [item("new|F|X-1", "一、重复问题"), item("new|F|X-2", "一、重复问题")]
    decisions = {"old|F|X-1": "TP", "old|F|X-2": "FP_GOLD"}
    pairs, old_only, new_only, review, resolved = guard.align(
        old, new, lambda it: decisions.get(it["id"]))
    assert not pairs and review and not resolved
    assert "签名不一致" in review[0]["reason"]


def test_duplicate_group_same_final_but_different_links_requires_review():
    old = [item("old|F|X-1", "一、重复问题"), item("old|F|X-2", "一、重复问题")]
    new = [item("new|F|X-1", "一、重复问题"), item("new|F|X-2", "一、重复问题")]
    signatures = {"old|F|X-1": ("TP", ("unit-A",), "", False),
                  "old|F|X-2": ("TP", ("unit-B",), "", False)}
    pairs, old_only, new_only, review, resolved = guard.align(
        old, new, lambda it: signatures.get(it["id"]))
    assert not pairs and review and not resolved
    assert "签名不一致" in review[0]["reason"]


def test_duplicate_group_same_final_and_links_but_different_cause_requires_review():
    old = [item("old|F|X-1", "一、重复问题"), item("old|F|X-2", "一、重复问题")]
    new = [item("new|F|X-1", "一、重复问题"), item("new|F|X-2", "一、重复问题")]
    signatures = {"old|F|X-1": ("TP", ("unit-A",), "R16", False),
                  "old|F|X-2": ("TP", ("unit-A",), "R26", False)}
    pairs, old_only, new_only, review, resolved = guard.align(
        old, new, lambda it: signatures.get(it["id"]))
    assert not pairs and review and not resolved
    assert "签名不一致" in review[0]["reason"]


def test_duplicate_group_missing_decision_requires_review():
    old = [item("old|F|X-1", "一、重复问题"), item("old|F|X-2", "一、重复问题")]
    new = [item("new|F|X-1", "一、重复问题"), item("new|F|X-2", "一、重复问题")]
    pairs, old_only, new_only, review, resolved = guard.align(
        old, new, lambda it: None if it["id"].endswith("X-2") else ("TP", (), "", False))
    assert not pairs and review and not resolved
    assert "缺失" in review[0]["reason"]


def test_decision_signature_reads_final_links_cause_amend():
    row = {"final": "TP", "links": ["unit-B", "unit-A"], "cause": "R16", "amend": True}
    assert guard.decision_signature(row) == ("TP", ("unit-A", "unit-B"), "R16", True)
    assert guard.decision_signature(None) is None


def _raw(bbox, *, pre="CMM-013-2", merged=None, members=None, related=None):
    raw = {"id": "CMM-013-1", "rule_id": "CMM-013", "severity": "warn", "title": "目录与正文标题不一致",
           "message": "一、问题", "bbox": bbox,
           "location": {"bbox": bbox, "pre_group_issue_id": pre},
           "evidence": [{"bbox": bbox, "page": 2, "quote": "原文"}],
           "analysis_revision": "rev-old", "created_at": 1}
    if merged is not None:
        raw["merged_issue_ids"] = merged
    if members is not None:
        raw["location"]["member_issue_ids"] = members
    if related is not None:
        raw["location"]["related_findings"] = [{"location": {"pre_group_issue_id": value}} for value in related]
    return raw


def test_raw_fingerprint_detects_bbox_drift():
    left = guard.canonical_raw(_raw([10.0, 20.0, 30.0, 40.0]))
    right = guard.canonical_raw(_raw([1.0, 1.0, 10.0, 10.0]))
    paths = guard.diff_paths(left, right)
    assert any(path.startswith("/bbox[") for path in paths)
    assert any(path.startswith("/location/bbox[") for path in paths)
    assert any(path.startswith("/evidence[0]/bbox[") for path in paths)


def test_reference_path_detection():
    assert guard.is_reference_path("/location/pre_group_issue_id")
    assert guard.is_reference_path("/location/member_issue_ids[1]")
    assert guard.is_reference_path("/merged_issue_ids[1]")
    assert not guard.is_reference_path("/bbox[0]")


def test_reference_change_requires_frozen_exception_and_bounds():
    doc, old_id, new_id = "DOC", "old|F|X", "new|F|X"
    path, old_value, new_value = "/location/pre_group_issue_id", "CMM-013-8", "CMM-013-999999"
    maxima = {("DOC", "CMM-013"): 8}
    allowed, reason, entry = guard.reference_change_allowed(
        doc, old_id, new_id, path, old_value, new_value, {}, maxima)
    assert not allowed and "冻结例外" in reason and entry is None

    key = (doc, old_id, new_id, path,
           guard.json.dumps(old_value, ensure_ascii=False, sort_keys=True),
           guard.json.dumps(new_value, ensure_ascii=False, sort_keys=True))
    exceptions = {key: {"doc": doc, "old_value": old_value, "new_value": new_value}}
    allowed, reason, entry = guard.reference_change_allowed(
        doc, old_id, new_id, path, old_value, new_value, exceptions, maxima)
    assert not allowed and "超出基线 ID 范围" in reason

    allowed, _, _ = guard.reference_change_allowed(
        doc, old_id, new_id, path, "CMM-013-8", "CMM-013-7",
        {(doc, old_id, new_id, path,
          guard.json.dumps("CMM-013-8", ensure_ascii=False, sort_keys=True),
          guard.json.dumps("CMM-013-7", ensure_ascii=False, sort_keys=True)): {"ok": True}},
        maxima)
    assert allowed

    # 错误现存成员：新值在范围内但不在冻结例外清单 → 拒收
    allowed, reason, _ = guard.reference_change_allowed(
        doc, old_id, new_id, path, "CMM-013-8", "CMM-013-7", {}, maxima)
    assert not allowed and "冻结例外" in reason
    # 跨文档：例外条目属于别的文档 → 拒收
    cross_doc_exceptions = {("OTHER", old_id, new_id, path,
                             guard.json.dumps("CMM-013-8", ensure_ascii=False, sort_keys=True),
                             guard.json.dumps("CMM-013-7", ensure_ascii=False, sort_keys=True)): {"ok": True}}
    allowed, _, _ = guard.reference_change_allowed(
        doc, old_id, new_id, path, "CMM-013-8", "CMM-013-7", cross_doc_exceptions, maxima)
    assert not allowed


def test_reference_id_bounds_and_unknown_prefix():
    maxima = {("DOC", "CMM-013"): 8, ("DOC", "CMM-011"): 18}
    assert guard.reference_id_allowed("CMM-013-8", "DOC", maxima)
    assert guard.reference_id_allowed("CMM-011-15", "DOC", maxima)
    assert not guard.reference_id_allowed("CMM-013-999999", "DOC", maxima)
    assert not guard.reference_id_allowed("CMM-999-1", "DOC", maxima)
    assert not guard.reference_id_allowed(16, "DOC", maxima)


def test_membership_must_match_related_findings():
    good = _raw([1.0, 2.0, 3.0, 4.0], merged=["V33-1", "CMM-011-16"],
                related=["V33-1", "CMM-011-16"])
    assert guard.membership_consistent(good)
    bad = _raw([1.0, 2.0, 3.0, 4.0], merged=["V33-1", "CMM-011-999999"],
               related=["V33-1", "CMM-011-16"])
    assert not guard.membership_consistent(bad)


def test_cross_representation_reference_view_detects_mismatch():
    review = _raw([1.0, 2.0, 3.0, 4.0], pre="CMM-013-999999")
    entity = _raw([1.0, 2.0, 3.0, 4.0], pre="CMM-013-8")
    assert guard.reference_view(review) != guard.reference_view(entity)


def test_exception_bidirectional_conflicts():
    entries = [
        {"doc": "DOC", "path": "/location/pre_group_issue_id", "old_value": "A-1", "new_value": "A-2"},
        {"doc": "DOC", "path": "/location/pre_group_issue_id", "old_value": "A-1", "new_value": "A-2"},
        {"doc": "DOC", "path": "/location/pre_group_issue_id", "old_value": "A-1", "new_value": "A-3"},
    ]
    conflicts = guard.exception_bidirectional_conflicts(entries)
    assert conflicts and conflicts[0]["family"] == "pre"


def test_count_exception_requires_exact_values_and_removed_count():
    path = "/unresolved_rules[1]/partial_findings_count"
    key = ("DOC", "old|N|X", "new|N|X", path)
    exceptions = {key: {"old_count": 1, "new_count": None, "rule_id": "R1", "removed_count": 1}}
    assert guard.count_change_allowed("DOC", "old|N|X", "new|N|X", path, 1, None, "R1", 1, exceptions)[0]
    assert guard.count_change_allowed("DOC", "old|N|X", "new|N|X", path, 1, 0, "R1", 1, exceptions)[0]
    assert not guard.count_change_allowed("DOC", "old|N|X", "new|N|X", path, 1, 999, "R1", 1, exceptions)[0]
    assert not guard.count_change_allowed("DOC", "old|N|X", "new|N|X", path, 1, 2, "R1", 1, exceptions)[0]
    assert not guard.count_change_allowed("DOC", "old|N|X", "new|N|X", path, 2, None, "R1", 1, exceptions)[0]
    assert not guard.count_change_allowed("DOC", "old|N|X", "new|N|X", path, 1, None, "R2", 1, exceptions)[0]


def test_fingerprint_rejects_severity_drift():
    old = item("old|F|X-1", "一、问题")
    new = item("new|F|X-1", "一、问题", sev="error")
    assert "sev" in guard.fingerprint_diffs(old, new)


def test_fingerprint_ignores_show_ordinal_only():
    old = item("old|F|X-1", "十一、问题正文")
    new = item("new|F|X-1", "十、问题正文")
    assert guard.fingerprint_diffs(old, new) == []


def test_fingerprint_rejects_quote_and_expected_value_drift():
    old = item("old|F|X-1", "一、表名不一致：“A”应为“B”", quotes=["原文A"])
    new = item("new|F|X-1", "一、表名不一致：“A”应为“C”", quotes=["原文A"])
    assert "message" in guard.fingerprint_diffs(old, new)
    new_quote = item("new|F|X-2", "一、表名不一致：“A”应为“B”", quotes=["原文A改"])
    assert "quotes" in guard.fingerprint_diffs(old, new_quote)
