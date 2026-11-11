"""D4/IR-06：整份确认门禁与报告三分类回归。

IR-06（Codex 独立核验）：遍历复用 iter_findings（legacy issues.all 不再漏读）；
结构化 next_action 优先于消息措辞；verified+manual_review 归歧义；
人工已有决定（ignored/questioned/needs_review）不进确定问题；
任何非 completed/not_applicable 的义务状态进入系统未完成。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.report_confirmation_gate import (  # noqa: E402
    build_report_confirmation,
    confirm_report_allowed,
)


def _status(**overrides):
    status = {
        "analysis_revision": "rev-1",
        "analysis_conclusion": "incomplete",
        "result": {
            "rule_findings": [
                {"id": "issue-1", "rule_id": "V33-243", "severity": "high",
                 "message": "m", "location": {"page": 22},
                 "evidence_verification": {"status": "verified"}},
                {"id": "issue-2", "rule_id": "CMM-007", "severity": "manual_review",
                 "message": "同比待复核", "location": {"page": 36},
                 "evidence_verification": {"status": "verified"}},
                {"id": "issue-3", "rule_id": "V33-104", "severity": "warn",
                 "message": "占比", "location": {"page": 8},
                 "evidence_verification": {"status": "failed"}},
            ],
            "ai_findings": [],
            "meta": {"obligation_coverage": {
                "instances": [
                    {"obligation_id": "OBL-AI", "status": "ai_not_run"},
                    {"obligation_id": "OBL-TXT-T5", "status": "completed"},
                    {"obligation_id": "OBL-UNKNOWN", "status": "execution_error"},
                ]},
            },
        },
        "review_reasons": [
            {"code": "rules_insufficient_data", "unresolved_rules": [
                {"rule_id": "V33-222", "detail": "原件清晰但字段映射失败，需人工复核",
                 "diagnostics": [{"next_action": "tech_fix"}]},
            ]},
        ],
        "human_decisions": [],
    }
    status.update(overrides)
    return status


def test_legacy_issues_all_structure_is_not_missed():
    """IR-06：legacy result.issues.all 结构必须被遍历到（不再 0 项）。"""
    status = _status()
    status["result"] = {
        "issues": {"all": [
            {"id": "legacy-1", "rule_id": "V33-001", "severity": "error", "message": "m",
             "location": {"page": 3}, "evidence_verification": {"status": "verified"}},
        ], "error": [], "warn": [], "info": []},
        "meta": {"obligation_coverage": {"instances": []}},
    }
    report = build_report_confirmation(status)
    assert report["counts"]["confirmed_material_issues"] == 1
    assert report["categories"]["confirmed_material_issues"][0]["id"] == "legacy-1"


def test_verified_manual_review_is_ambiguity_not_confirmed():
    report = build_report_confirmation(_status())
    confirmed = report["categories"]["confirmed_material_issues"]
    ambiguities = report["categories"]["genuine_ambiguities"]
    assert [c["id"] for c in confirmed] == ["issue-1"]
    assert any(a["id"] == "issue-2" for a in ambiguities)
    assert not any(c["id"] == "issue-2" for c in confirmed)


def test_structured_next_action_beats_wording():
    """IR-06：tech_fix + '需人工复核' 措辞 → 系统未完成，不得归为歧义。"""
    report = build_report_confirmation(_status())
    system = report["categories"]["system_incomplete"]
    ambiguities = report["categories"]["genuine_ambiguities"]
    assert any(s.get("next_action") == "tech_fix" for s in system)
    assert not any("字段映射" in str(a.get("detail")) for a in ambiguities)


def test_obligation_failure_statuses_block_gate():
    report = build_report_confirmation(_status())
    system = report["categories"]["system_incomplete"]
    statuses = {s["reason"] for s in system}
    assert "obligation_ai_not_run" in statuses
    assert "obligation_execution_error" in statuses  # 非白名单状态同样受门禁
    assert report["gate"]["can_confirm_whole_report"] is False


def test_human_decisions_exclude_from_confirmed_scope():
    status = _status()
    status["human_decisions"] = [{"issue_id": "issue-1", "status": "ignored"}]
    report = build_report_confirmation(status)
    assert report["categories"]["confirmed_material_issues"] == []
    ambiguities = report["categories"]["genuine_ambiguities"]
    assert any(a["id"] == "issue-1" and a.get("decision") == "ignored" for a in ambiguities)


def test_clean_report_allows_confirmed_only_scope():
    status = _status()
    status["result"]["rule_findings"] = [
        {"id": "issue-1", "rule_id": "V33-243", "severity": "high", "message": "x",
         "location": {"page": 22}, "evidence_verification": {"status": "verified"}},
    ]
    status["review_reasons"] = []
    status["result"]["meta"]["obligation_coverage"]["instances"] = [
        {"obligation_id": "OBL-TXT-T5", "status": "completed"},
    ]
    report = build_report_confirmation(status, confirm_report_enabled=True)
    assert report["gate"]["can_confirm_whole_report"] is True
    assert report["categories"]["confirmed_material_issues"][0]["id"] == "issue-1"
    assert report["categories"]["system_incomplete"] == []
    decision = confirm_report_allowed(report, status["analysis_revision"])
    assert decision == {"allowed": True, "code": "OK"}


def test_default_off_and_revision_binding():
    report = build_report_confirmation(_status())
    decision = confirm_report_allowed(report, report["analysis_revision"])
    assert decision == {"allowed": False, "code": "CONFIRM_REPORT_DISABLED"}
    # 版本漂移检查用干净门禁（系统未完成优先于版本比较，见 confirm_report_allowed）
    clean = _status()
    clean["result"]["rule_findings"] = [
        {"id": "issue-1", "rule_id": "V33-243", "severity": "high", "message": "x",
         "location": {"page": 22}, "evidence_verification": {"status": "verified"}},
    ]
    clean["review_reasons"] = []
    clean["result"]["meta"]["obligation_coverage"]["instances"] = []
    enabled = build_report_confirmation(clean, confirm_report_enabled=True)
    assert confirm_report_allowed(enabled, "rev-other")["code"] == "REVISION_CHANGED"


# ---------------------------------------------------------------------------
# IR-08：非阻塞未完成项可见但不阻塞；阻塞项仍必须阻塞
# ---------------------------------------------------------------------------

def _clean_with_instances(instances):
    status = _status()
    status["result"]["rule_findings"] = [
        {"id": "issue-1", "rule_id": "V33-243", "severity": "high", "message": "x",
         "location": {"page": 22}, "evidence_verification": {"status": "verified"}},
    ]
    status["review_reasons"] = []
    status["result"]["meta"]["obligation_coverage"]["instances"] = instances
    return status


def test_nonblocking_ai_not_run_visible_but_not_blocking():
    """AI 未执行且非必需：仍列入 system_incomplete，但不阻塞确定性签认。"""
    status = _clean_with_instances([
        {"obligation_id": "OBL-AI", "status": "ai_not_run", "blocks_gate": False},
        {"obligation_id": "OBL-TXT-T5", "status": "completed", "blocks_gate": True},
    ])
    report = build_report_confirmation(status, confirm_report_enabled=True)
    assert report["gate"]["can_confirm_whole_report"] is True
    assert report["gate"]["nonblocking_system_incomplete"] == 1
    assert report["gate"]["blocking_system_incomplete"] == 0
    assert report["counts"]["system_incomplete"] == 1
    item = report["categories"]["system_incomplete"][0]
    assert item["rule_id"] == "OBL-AI" and item["blocking"] is False
    # 排除项仍在记录范围外
    assert [c["id"] for c in report["categories"]["confirmed_material_issues"]] == ["issue-1"]


def test_blocking_obligation_still_blocks_even_with_clean_findings():
    status = _clean_with_instances([
        {"obligation_id": "OBL-TXT-T5", "status": "insufficient_data", "blocks_gate": True},
    ])
    report = build_report_confirmation(status, confirm_report_enabled=True)
    assert report["gate"]["can_confirm_whole_report"] is False
    assert report["gate"]["blocking_system_incomplete"] == 1
    assert confirm_report_allowed(report, "rev-1")["code"] == "SYSTEM_INCOMPLETE"


def test_legacy_instance_without_blocks_gate_is_conservative():
    """旧快照缺 blocks_gate 字段：按阻塞处理，不因缺字段放行。"""
    status = _clean_with_instances([
        {"obligation_id": "OBL-LEGACY", "status": "ai_not_run"},
    ])
    report = build_report_confirmation(status, confirm_report_enabled=True)
    assert report["gate"]["can_confirm_whole_report"] is False
    assert report["categories"]["system_incomplete"][0]["blocking"] is True


def test_concurrent_same_key_append_leaves_single_record(monkeypatch, tmp_path):
    """IR-08：同 key 并发双击只留一条记录，后到者拿先到者记录。"""
    import concurrent.futures

    from api import runtime as api_runtime
    from src.services.report_confirmation_gate import (
        append_confirm_record,
        read_confirm_records,
    )

    monkeypatch.setattr(api_runtime, "UPLOAD_ROOT", tmp_path, raising=False)
    base = {"job_id": "job-1", "analysis_revision": "rev-1",
            "idempotency_key": "key-abcdefgh", "confirmed_issue_ids": ["a"],
            "confirmed_count": 1, "scope": "deterministic_findings_only",
            "excluded": {}, "actor": "tester", "note": ""}

    def _submit(index):
        return append_confirm_record("job-1", {**base, "ts": 1000 + index})

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_submit, range(8)))
    assert all(result is not None for result in results)
    records = read_confirm_records("job-1")
    assert len(records) == 1, records
    assert {result["ts"] for result in results} == {records[0]["ts"]}
