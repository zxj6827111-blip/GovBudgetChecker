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
