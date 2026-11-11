"""IR-01 回归（Codex 独立核验 RETURN_FOR_REWORK 2026-11-10）。

控制试验：同一规则、同一数字、同一 fail 状态——
- "金额100万元与分项之和90万元不一致" → 交付 1 条
- 仅改为"金额格式错误：…" → 必须仍交付 1 条（旧实现 0 条）

消息关键词不再是任何抑制依据；合法抑制的唯一入口是规则在
location["delivery_suppressed"] 写入结构化决定（policy_id/relation_id/
reason），被抑制条目进入 doc 级台账并随 payload 持久化可对账。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.pipeline import build_issues_payload  # noqa: E402
from src.engine.rules_v33 import order_and_number_issues  # noqa: E402


def _issue(rule="V33-000", message="正常问题", **location):
    return SimpleNamespace(rule=rule, message=message, desc=None,
                           location={"page": 1, "pos": 0, **location})


def _doc():
    return SimpleNamespace(
        page_texts=["某局2025年度部门决算"], page_tables=[], pages=1,
        anchors={}, years_per_page=[[]], units_per_page=[None],
    )


def test_message_wording_never_suppresses_delivery():
    """IR-01 控制试验：两种措辞必须交付相同业务结果。"""
    doc = _doc()
    plain = order_and_number_issues(doc, [_issue("V33-999", "金额100万元与分项之和90万元不一致")])
    doc2 = _doc()
    keyword = order_and_number_issues(doc2, [_issue("V33-999", "金额格式错误：金额100万元与分项之和90万元不一致")])
    assert len(plain) == 1 and len(keyword) == 1, (
        f"措辞不得改变交付：plain={len(plain)} keyword={len(keyword)}")
    assert getattr(doc2, "delivery_suppression_ledger", []) == []


def test_structured_suppression_is_ledgered_and_passes_policy():
    doc = _doc()
    suppressed = _issue(
        "V33-998", "已由上游政策合并的重复项",
        delivery_suppressed={"policy_id": "POLICY-MERGE-001",
                             "relation_id": "rel-1", "reason": "同源合并，见关系回执"},
    )
    kept = _issue("V33-997", "正常问题")
    out = order_and_number_issues(doc, [suppressed, kept])
    assert len(out) == 1 and "正常问题" in str(out[0].message)
    ledger = getattr(doc, "delivery_suppression_ledger", [])
    assert ledger and ledger[0]["policy_id"] == "POLICY-MERGE-001"
    assert ledger[0]["relation_id"] == "rel-1"


def test_suppression_ledger_reaches_payload():
    """抑制台账随 build_issues_payload 持久化返回（读取/导出可对账）。"""
    from src.engine.pipeline import build_document

    doc = build_document("supp.pdf", ["某局2025年度部门决算"], [], 0)
    doc.delivery_suppression_ledger = [{
        "rule": "V33-998", "policy_id": "POLICY-MERGE-001",
        "relation_id": "rel-1", "reason": "同源合并"}]
    payload = build_issues_payload(doc, use_ai_assist=False)
    assert payload.get("delivery_suppressions") == doc.delivery_suppression_ledger


def test_incomplete_suppression_basis_never_deletes_failure():
    """R2-06：只有 policy_id（缺 relation_id/reason）不是合法处理依据。"""
    doc = _doc()
    out = order_and_number_issues(doc, [_issue(
        "V33-996", "仍然失败的金额不一致",
        delivery_suppressed={"policy_id": "POLICY-ONLY"})])
    assert len(out) == 1, "依据不完整时不得删除失败结果"
    assert getattr(doc, "delivery_suppression_ledger", []) == []


def test_suppression_ledger_reaches_canonical_report_export():
    """R2-06：抑制台账进入标准报告导出（JSON 读回可对账）。"""
    from src.schemas.review_report import build_review_report

    ledger = [{"rule": "V33-998", "policy_id": "POLICY-MERGE-001",
               "relation_id": "rel-1", "reason": "同源合并"}]
    report = build_review_report({
        "job_id": "job-1",
        "analysis_revision": "rev-1",
        "result": {"rule_findings": [], "ai_findings": [], "merged": {},
                   "delivery_suppressions": ledger},
    })
    assert report["delivery_suppressions"] == ledger
