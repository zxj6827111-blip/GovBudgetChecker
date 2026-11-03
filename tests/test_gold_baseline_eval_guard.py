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
    assert "裁决不一致" in review[0]["reason"]


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
