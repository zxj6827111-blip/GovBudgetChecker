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


def _raw(bbox, *, pre="CMM-013-2", merged=None):
    raw = {"id": "CMM-013-1", "rule_id": "CMM-013", "severity": "warn", "title": "目录与正文标题不一致",
           "message": "一、问题", "bbox": bbox,
           "location": {"bbox": bbox, "pre_group_issue_id": pre},
           "evidence": [{"bbox": bbox, "page": 2, "quote": "原文"}],
           "analysis_revision": "rev-old", "created_at": 1}
    if merged is not None:
        raw["merged_issue_ids"] = merged
    return raw


def test_raw_fingerprint_detects_bbox_drift():
    left = guard.canonical_raw(_raw([10.0, 20.0, 30.0, 40.0]))
    right = guard.canonical_raw(_raw([1.0, 1.0, 10.0, 10.0]))
    left, right = guard.reconcile_references(left, right, {}, {})
    paths = guard.diff_paths(left, right)
    assert any(path.startswith("/bbox[") for path in paths)
    assert any(path.startswith("/location/bbox[") for path in paths)
    assert any(path.startswith("/evidence[0]/bbox[") for path in paths)


def test_raw_reference_requires_literal_or_verified_mapping():
    left = guard.canonical_raw(_raw([1.0, 2.0, 3.0, 4.0], pre="CMM-013-2"))
    right = guard.canonical_raw(_raw([1.0, 2.0, 3.0, 4.0], pre="CMM-013-3"))
    mapped_left, mapped_right = guard.reconcile_references(
        left, right, {}, {"CMM-013-3": "CMM-013-2"})
    assert guard.diff_paths(mapped_left, mapped_right) == []
    plain_left, plain_right = guard.reconcile_references(left, right, {}, {})
    assert "/location/pre_group_issue_id" in guard.diff_paths(plain_left, plain_right)


def test_raw_member_ids_use_verified_item_mapping():
    left = guard.canonical_raw(_raw([1.0, 2.0, 3.0, 4.0], merged=["V33-1", "CMM-011-16"]))
    right = guard.canonical_raw(_raw([1.0, 2.0, 3.0, 4.0], merged=["V33-1", "CMM-011-15"]))
    mapped_left, mapped_right = guard.reconcile_references(
        left, right, {"CMM-011-15": "CMM-011-16"}, {})
    assert guard.diff_paths(mapped_left, mapped_right) == []
    plain_left, plain_right = guard.reconcile_references(left, right, {}, {})
    assert "/merged_issue_ids[1]" in guard.diff_paths(plain_left, plain_right)


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
