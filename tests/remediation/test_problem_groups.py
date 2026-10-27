"""同源问题组：一次确认、稳定组 ID、成员兼容与关联症状不合并。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine.native_issue_dedup import (
    _unit_magnitude_anchor,
    deduplicate_native_issues,
    problem_group_id,
)
from src.services import issue_workflow_store
from src.services.review_problem_set import (
    build_issue_counts,
    collect_review_problems,
    problem_status,
)

PAGE20 = "社会保障和就业支出（类）行政事业单位养老支出（款）机关事业单位职业年金缴费支出（项）。主要用于缴费。年初预算 191.16 万元，支出决算为 179.82\n元。决算数小于预算数。"
PAGE21 = "住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算 467.28 万元，支出决算为 444.71 元，决算数小于预算数。"


def _doc(pages=None):
    pages = pages or [""] * 19 + [PAGE20, PAGE21]
    return SimpleNamespace(page_texts=pages,
                           fact_context=SimpleNamespace(file_hash="file-hash"))


def _v33_222(amount="179.82", page=20, quote="179.82"):
    return {
        "id": "V33-222-6", "rule_id": "V33-222", "severity": "error",
        "message": f"金额单位口径错误：原文{amount}元",
        "location": {
            "page": page, "unit_magnitude_error": True, "declared_unit": "元",
            "table_refs": [{
                "role": "原文决算金额声明", "page": page,
                "span": f"年初预算 191.16 万元，支出决算为 {amount}\n元",
                "file_hash": "file-hash",
            }],
            "table_code": "FIN_05_general_public_expenditure",
        },
    }


def _cmm_011(amount="179.82", page=20):
    return {
        "id": "CMM-011-7", "rule_id": "CMM-011", "severity": "warn",
        "message": f"金额单位疑似漏写“万”字：“{amount}元”，说明正文金额应以万元为单位。",
        "location": {"page": page, "pos": 302, "checklist_item": "C-14"},
    }


def test_v33_222_and_cmm_011_same_literal_merge_once():
    doc = _doc()
    merged = deduplicate_native_issues([_v33_222(), _cmm_011()], doc)
    assert len(merged) == 1
    location = merged[0]["location"]
    assert location["problem_group_id"].startswith("pg-")
    assert set(location["related_rule_ids"]) == {"V33-222", "CMM-011"}
    assert set(location["merged_issue_ids"]) >= {"V33-222-6", "CMM-011-7"}
    assert location["source_anchor"]["literal"] == "179.82元"
    assert location["source_anchor"]["physical_page"] == 20
    assert location["defect_type"] == "amount_unit_missing_wan"
    assert len(location["member_evidence"]) == 2


def test_split_decision_phrase_still_anchors():
    text = "住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算 467.28 万元，支\n出决算为 444.71 元"
    doc = _doc(pages=[""] * 20 + [text])
    item = _v33_222(amount="444.71", page=21)
    item["location"]["table_refs"][0]["span"] = "支\n出决算为 444.71 元"
    anchor = _unit_magnitude_anchor(item, doc)
    assert anchor is not None
    assert anchor["literal"] == "444.71元"
    assert text[anchor["text_span"][0]:anchor["text_span"][1]] == "444.71 元"


def test_group_id_is_deterministic_not_random():
    doc = _doc()
    first = deduplicate_native_issues([_v33_222(), _cmm_011()], doc)[0]["location"]["problem_group_id"]
    second = deduplicate_native_issues([_v33_222(), _cmm_011()], doc)[0]["location"]["problem_group_id"]
    assert first == second


def test_different_amount_or_page_does_not_merge():
    doc = _doc()
    assert len(deduplicate_native_issues([_v33_222(), _cmm_011(amount="444.71")], doc)) == 2
    assert len(deduplicate_native_issues([_v33_222(), _cmm_011(page=21)], doc)) == 2


def test_duplicate_occurrence_on_page_is_fail_closed():
    doubled = PAGE20 + "另见 179.82 元。"
    doc = _doc(pages=[""] * 19 + [doubled, PAGE21])
    assert len(deduplicate_native_issues([_v33_222(), _cmm_011()], doc)) == 2


def test_different_file_hash_changes_group_id():
    key_a = ("amount_unit_missing_wan", "hash-a", 20, "179.82元", (100, 109))
    key_b = ("amount_unit_missing_wan", "hash-b", 20, "179.82元", (100, 109))
    assert problem_group_id(key_a) != problem_group_id(key_b)


def test_v33_251_is_linked_not_merged():
    symptom = {
        "id": "V33-251-8", "rule_id": "V33-251", "severity": "warn",
        "message": "支出决算合计与总额不一致",
        "location": {
            "page": 19,
            "unit_normalized_items": [
                {"page": 20, "amount": "179.82", "unit": "元", "value_wan": "0.017982"},
            ],
        },
    }
    result = deduplicate_native_issues([_v33_222(), _cmm_011(), symptom], _doc())
    assert len(result) == 2
    unit_group = next(item for item in result if item["rule_id"] == "V33-222")
    symptom_out = next(item for item in result if item["rule_id"] == "V33-251")
    assert symptom_out["location"]["related_problem_group_ids"] == [
        unit_group["location"]["problem_group_id"]
    ]
    assert symptom_out["location"].get("problem_group_id") is None


def _merged_payload():
    doc = _doc()
    merged = deduplicate_native_issues([_v33_222(), _cmm_011()], doc)[0]
    return merged


def test_review_problem_members_and_alias_status():
    merged = _merged_payload()
    problems = collect_review_problems(
        job_uuid="job", rule_findings=[merged],
        result={"issues": {"all": [merged]}},
    )
    assert len(problems) == 1
    problem = problems[0]
    assert "CMM-011-7" in problem.member_issue_ids
    assert "V33-222-6" not in problem.member_issue_ids  # 规范 id 自己不再算成员
    assert problem_status(problem, {"CMM-011-7": "confirmed"}) == "confirmed"
    # 规范 id 的决定是重新确认，优先于旧成员决定。
    assert problem_status(problem, {"V33-222-6": "confirmed", "CMM-011-7": "no_issue"}) == "confirmed"
    # 没有规范决定、成员决定互相冲突 → 必须重新确认。
    assert problem_status(problem, {"V33-222-1": "confirmed", "CMM-011-7": "no_issue"}) == "needs_review"
    assert problem_status(problem, {}) == "pending"
    counts = build_issue_counts(problems, {"CMM-011-7": "confirmed"})
    assert counts == {"total": 1, "confirmed": 1, "no_issue": 0, "in_package": 0,
                      "pending": 0, "needs_review": 0}


def test_workflow_find_issue_resolves_member_alias_uniquely():
    merged = _merged_payload()
    payload = {"rule_findings": [merged]}
    assert issue_workflow_store._find_issue(payload, "V33-222-6")["id"] == merged["id"]
    assert issue_workflow_store._find_issue(payload, "CMM-011-7")["id"] == merged["id"]
    # 歧义：两组都声称同一个旧成员 id → 不返回任何一组。
    other = {**merged, "id": "other", "location": {**merged["location"],
             "merged_issue_ids": ["CMM-011-7"]}}
    assert issue_workflow_store._find_issue(
        {"rule_findings": [merged, other]}, "CMM-011-7") is None


def test_decision_record_marks_resolved_alias_source():
    issue = _merged_payload()
    payload = {"job_id": "job", "analysis_revision": "rev-1"}
    record = issue_workflow_store._decision_record(
        payload, issue, status="confirmed", actor="tester", note=None,
        resolved_from_issue_id="CMM-011-7",
    )
    assert record["issue_id"] == issue["id"]
    assert record["resolved_from_issue_id"] == "CMM-011-7"


# ---------------------------------------------------------------------------
# B1-A01 返工：冲突成员决定必须在投影和门禁两层都保持未解决
# ---------------------------------------------------------------------------

from src.services import issue_workflow_store as _workflow
from src.services.review_lifecycle_service import _problem_blockers as _lifecycle_blockers


def _conflict_issue():
    return {
        "id": "pg-canonical", "rule_id": "V33-222", "severity": "high",
        "title": "单位错误", "message": "同源单位问题", "evidence_status": "complete",
        "location": {"member_issue_ids": ["member-a", "member-b"]},
    }


def _decision_event(issue_id, status, revision=1, analysis_revision="rev-current"):
    return {
        "job_id": "job", "issue_id": issue_id,
        "analysis_revision": analysis_revision,
        "status": status, "decision_revision": str(revision),
        "decision_actor": "tester", "updated_at": f"2026-10-27T00:00:0{min(revision, 9)}Z",
    }


def _project(events, issue=None, analysis_revision="rev-current"):
    payload = {"analysis_revision": analysis_revision,
               "result": {"rule_findings": [issue or _conflict_issue()]}}
    return _workflow.project_job_issue_decisions(
        "job", payload, _state={"decision_events": events})


def test_conflicting_member_decisions_project_to_needs_review_and_block_gate():
    projected = _project([
        _decision_event("member-a", "confirmed"),
        _decision_event("member-b", "no_issue"),
    ])
    assert len(projected) == 1
    assert projected[0]["status"] == "needs_review"
    assert projected[0]["conflict"] is True
    assert {item["issue_id"] for item in projected[0]["member_decisions"]} == {"member-a", "member-b"}
    problems = collect_review_problems(job_uuid="job", rule_findings=[_conflict_issue()])
    decisions = {item["issue_id"]: item["status"] for item in projected}
    blockers = _lifecycle_blockers(problems, decisions)
    assert any(blocker.code == "needs_review_findings" and blocker.count == 1 for blocker in blockers)
    counts = build_issue_counts(problems, decisions)
    assert counts["needs_review"] == 1 and counts["total"] == 1


def test_conflicting_member_decisions_are_order_independent():
    for events in (
        [_decision_event("member-b", "no_issue"), _decision_event("member-a", "confirmed")],
        [_decision_event("member-a", "confirmed"), _decision_event("member-b", "no_issue")],
    ):
        projected = _project(events)
        assert projected[0]["status"] == "needs_review"
        assert projected[0]["conflict"] is True


def test_same_member_conclusions_converge():
    projected = _project([
        _decision_event("member-a", "confirmed"),
        _decision_event("member-b", "confirmed"),
    ])
    assert projected[0]["status"] == "confirmed"
    assert projected[0]["conflict"] is False
    assert len(projected[0]["member_decisions"]) == 2


def test_latest_member_decision_wins_within_member():
    projected = _project([
        _decision_event("member-a", "no_issue", revision=1),
        _decision_event("member-a", "confirmed", revision=2),
        _decision_event("member-b", "confirmed", revision=1),
    ])
    assert projected[0]["status"] == "confirmed"
    assert projected[0]["decision_revision"] == "2"


def test_explicit_current_group_decision_supersedes_conflicting_members():
    projected = _project([
        _decision_event("member-a", "confirmed"),
        _decision_event("member-b", "no_issue"),
        _decision_event("pg-canonical", "confirmed", revision=3),
    ])
    assert projected[0]["status"] == "confirmed"
    assert projected[0]["conflict"] is False
    assert projected[0]["resolved_from_issue_id"] is None
    assert set(projected[0]["resolved_from_issue_ids"]) == {"member-a", "member-b"}
    assert len(projected[0]["superseded_member_decisions"]) == 2
    problems = collect_review_problems(job_uuid="job", rule_findings=[_conflict_issue()])
    decisions = {item["issue_id"]: item["status"] for item in projected}
    assert not _lifecycle_blockers(problems, decisions)


def test_current_group_decision_wins_over_inherited_members():
    projected = _project([
        _decision_event("member-a", "confirmed", analysis_revision="rev-old"),
        _decision_event("member-b", "no_issue", analysis_revision="rev-old"),
        _decision_event("pg-canonical", "no_issue", revision=2),
    ])
    assert projected[0]["status"] == "no_issue"
    assert projected[0]["inherited"] is False
    assert projected[0]["conflict"] is False


def test_inherited_group_decision_yields_to_current_conflicting_members():
    projected = _project([
        _decision_event("pg-canonical", "confirmed", analysis_revision="rev-old"),
        _decision_event("member-a", "confirmed"),
        _decision_event("member-b", "no_issue"),
    ])
    assert projected[0]["status"] == "needs_review"
    assert projected[0]["inherited"] is False
    assert projected[0]["conflict"] is True


def test_inherited_group_decision_yields_to_current_agreeing_members():
    projected = _project([
        _decision_event("pg-canonical", "no_issue", analysis_revision="rev-old"),
        _decision_event("member-a", "confirmed"),
        _decision_event("member-b", "confirmed"),
    ])
    assert projected[0]["status"] == "confirmed"
    assert projected[0]["inherited"] is False


def test_old_member_events_without_identity_are_not_inherited_for_positive_decisions():
    projected = _project([
        _decision_event("member-a", "confirmed", analysis_revision="rev-old"),
        _decision_event("member-b", "confirmed", analysis_revision="rev-old"),
    ])
    assert projected == []


def test_raw_member_decisions_block_gate_even_without_projection():
    problems = collect_review_problems(job_uuid="job", rule_findings=[_conflict_issue()])
    blockers = _lifecycle_blockers(problems, {"member-a": "confirmed", "member-b": "no_issue"})
    assert any(blocker.code == "needs_review_findings" and blocker.count == 1 for blocker in blockers)
