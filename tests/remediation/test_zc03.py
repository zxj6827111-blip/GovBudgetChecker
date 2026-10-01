"""ZC-03 acceptance tests: canonical registry, portable policy and receipts."""

from decimal import Decimal

from src.engine.amount_math import classify_amount_diff
from src.engine.check_obligations import build_check_receipts
from src.engine.rule_policy import classify_percentage_point_difference
from src.engine.rule_registry import (
    RULE_REGISTRY_VERSION,
    all_rule_ids,
    rule_catalog,
    select_rules,
)


def test_canonical_registry_routes_every_production_family() -> None:
    catalog = rule_catalog()
    assert catalog["version"] == RULE_REGISTRY_VERSION
    assert catalog["total"] == 82
    assert len(all_rule_ids()) == 82
    assert len(select_rules("budget")) == 25
    assert len(select_rules("final")) == 65
    assert len(select_rules("unknown")) == 8
    assert all(item["evaluation_basis"] == "legacy_document" for item in catalog["rules"])
    assert all(item["fact_migration"] == "pending" for item in catalog["rules"])
    assert all("implementation" in item and "obligation_ids" in item for item in catalog["rules"])


def test_portable_amount_and_percentage_tolerance_boundaries() -> None:
    assert classify_amount_diff(100, Decimal("100.02"), n_children=3)[0] == "rounding_hint"
    assert classify_amount_diff(100, Decimal("100.1"), n_children=3)[0] == "within_policy_tolerance"
    assert classify_amount_diff(100, Decimal("100.5"), n_children=3)[0] == "within_policy_tolerance"
    assert classify_amount_diff(100, Decimal("100.5001"), n_children=3)[0] == "mismatch"

    assert classify_percentage_point_difference("3.0", "3.1")[0] == "within_policy_tolerance"
    assert classify_percentage_point_difference("3.0", "3.1001")[0] == "mismatch"


def test_receipts_are_linked_and_mark_legacy_fact_migration_pending() -> None:
    coverage = {
        "instances": [
            {
                "obligation_id": "OBL-TEST",
                "status": "completed",
                "checkers": ["CMM-002"],
                "missing_checkers": [],
            },
            {
                "obligation_id": "OBL-MISSING",
                "status": "not_executed",
                "checkers": ["CMM-002"],
                "missing_checkers": ["RULE-NOT-REGISTERED"],
            },
        ]
    }
    result = {
        "issues": {
            "all": [
                {
                    "id": "finding-1",
                    "rule_id": "CMM-002",
                    "obligation_ids": ["OBL-TEST"],
                }
            ]
        }
    }
    receipts = build_check_receipts(
        result,
        report_kind="budget",
        rule_execution_summary={"rule_statuses": {"CMM-002": "fail"}},
        obligation_coverage=coverage,
    )
    by_id = {item["sub_check_id"]: item for item in receipts}

    linked = by_id["OBL-TEST:CMM-002"]
    assert linked["status"] == "fail"
    assert linked["issue_refs"] == ["finding-1"]
    assert linked["required_fact_ids"] == []
    assert linked["operand_fact_ids"] == []
    assert linked["evaluation_basis"] == "legacy_document"
    assert linked["fact_migration_status"] == "pending"
    assert linked["tolerance"]["configured_amount_tolerance_wan"] == "0.5"

    missing = by_id["OBL-MISSING:RULE-NOT-REGISTERED"]
    assert missing["status"] == "insufficient_data"
    assert missing["incomplete_reason"]
