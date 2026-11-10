"""D4：整份确认门禁与报告三分类回归。

计划 D4 要求：报告清晰区分 ①已确定材料问题 ②真实材料歧义 ③系统未完成；
confirm_report 默认关闭；只签认证据充分的确定性问题；系统失败不能要求
用户代算，未计算关系不能因确认变已完成。
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
        "analysis_revision": "rev-20261110T115235-dd191e4e",
        "analysis_conclusion": "incomplete",
        "result": {
            "rule_findings": [
                {"rule_id": "V33-243", "severity": "high", "message": "经济分类302明细归集",
                 "location": {"page": 22},
                 "evidence_verification": {"status": "verified"}},
                {"rule_id": "CMM-007", "severity": "manual_review", "message": "同比待复核",
                 "location": {"page": 36},
                 "evidence_verification": {"status": "unverified"}},
                {"rule_id": "V33-104", "severity": "warn", "message": "占比",
                 "location": {"page": 8},
                 "evidence_verification": {"status": "failed"}},
            ],
            "ai_findings": [],
            "meta": {
                "obligation_coverage": {
                    "instances": [
                        {"obligation_id": "OBL-AI-SEMANTIC-REVIEW", "status": "ai_not_run"},
                        {"obligation_id": "OBL-TXT-T5", "status": "completed"},
                    ]
                },
            },
        },
        "review_reasons": [
            {"code": "rules_insufficient_data", "unresolved_rules": [
                {"rule_id": "V33-222", "detail": "说明中的类在表内找不到同名科目（歧义）",
                 "diagnostics": [{"next_action": "system_recheck"}]},
            ]},
        ],
    }
    status.update(overrides)
    return status


def test_three_way_classification():
    report = build_report_confirmation(_status())
    counts = report["counts"]
    assert counts["confirmed_material_issues"] == 1
    assert counts["genuine_ambiguities"] >= 1  # manual_review finding + 歧义未决
    assert counts["system_incomplete"] >= 2    # 证据失败 finding + ai_not_run
    assert report["gate"]["can_confirm_whole_report"] is False


def test_gate_blocks_while_system_incomplete():
    report = build_report_confirmation(_status(), confirm_report_enabled=True)
    decision = confirm_report_allowed(report, report["analysis_revision"])
    assert decision == {"allowed": False, "code": "SYSTEM_INCOMPLETE"}


def test_clean_report_allows_confirmed_only_scope():
    status = _status()
    status["result"]["rule_findings"] = [
        {"rule_id": "V33-243", "severity": "high", "message": "x",
         "location": {"page": 22}, "evidence_verification": {"status": "verified"}},
    ]
    status["review_reasons"] = []
    status["result"]["meta"]["obligation_coverage"]["instances"] = [
        {"obligation_id": "OBL-TXT-T5", "status": "completed"},
    ]
    report = build_report_confirmation(status, confirm_report_enabled=True)
    assert report["gate"]["can_confirm_whole_report"] is True
    assert report["categories"]["confirmed_material_issues"]
    assert report["categories"]["system_incomplete"] == []
    decision = confirm_report_allowed(report, status["analysis_revision"])
    assert decision == {"allowed": True, "code": "OK"}


def test_default_off_and_revision_binding():
    report = build_report_confirmation(_status())
    decision = confirm_report_allowed(report, report["analysis_revision"])
    assert decision == {"allowed": False, "code": "CONFIRM_REPORT_DISABLED"}
    clean_status = _status()
    clean_status["result"]["rule_findings"] = [
        {"rule_id": "V33-243", "severity": "high", "message": "x",
         "location": {"page": 22}, "evidence_verification": {"status": "verified"}},
    ]
    clean_status["review_reasons"] = []
    clean_status["result"]["meta"]["obligation_coverage"]["instances"] = []
    clean = build_report_confirmation(clean_status, confirm_report_enabled=True)
    assert confirm_report_allowed(clean, "rev-other")["code"] == "REVISION_CHANGED"
