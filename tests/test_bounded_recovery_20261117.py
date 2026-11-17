"""RR-05：有界恢复协调器（策略指纹、来源复核、重跑与次数上限）。"""

from __future__ import annotations

from types import SimpleNamespace

from src.services.bounded_recovery import (
    MAX_STRATEGIES_PER_RUN,
    RecoveryCoordinator,
    RecoveryStrategy,
)


def _doc(file_hash="a" * 64, run="run-1"):
    return SimpleNamespace(
        parse_artifact=SimpleNamespace(source_file_hash=file_hash, parse_run_uid=run),
        fact_context=SimpleNamespace(file_hash=file_hash, parse_run_uid=run),
    )


def test_recovery_runs_once_per_strategy_and_source_identity():
    calls = []

    def strategy_run(document):
        calls.append(1)
        document.changed = True
        return True

    strategy = RecoveryStrategy("fake", "fp-1", strategy_run)
    coordinator = RecoveryCoordinator([strategy])
    doc = _doc()
    result = coordinator.recover(
        doc, rule_id="V33-TEST", reason_codes=["operand_missing"], rerun=lambda: "ok",
    )
    assert result == "ok"
    assert len(calls) == 1
    assert coordinator.records[0]["status"] == "recovered"
    assert coordinator.records[0]["attempts"][0]["source_verified"] is True
    # 同一次分析、同规则/关系/策略指纹不再重复尝试。
    assert coordinator.recover(
        doc, rule_id="V33-TEST", reason_codes=["operand_missing"], rerun=lambda: "again",
    ) is None
    assert len(calls) == 1


def test_recovery_is_bounded_to_two_strategies():
    attempts = []

    def make(name):
        def run(document):
            attempts.append(name)
            return False
        return RecoveryStrategy(name, name, run)

    coordinator = RecoveryCoordinator([make("a"), make("b"), make("c")])
    assert coordinator.recover(
        _doc(), rule_id="V33-TEST", reason_codes=["table_mapping_incomplete"],
    ) is None
    assert attempts == ["a", "b"]
    assert len(coordinator.records[0]["attempts"]) == MAX_STRATEGIES_PER_RUN


def test_material_defect_or_execution_codes_do_not_trigger_recovery():
    coordinator = RecoveryCoordinator([
        RecoveryStrategy("fake", "fp", lambda document: True),
    ])
    assert coordinator.recover(
        _doc(), rule_id="V33-TEST",
        reason_codes=["item_disclosure_missing", "execution_error"],
    ) is None
    assert coordinator.records == []


def test_source_identity_mismatch_is_not_recovered():
    coordinator = RecoveryCoordinator([
        RecoveryStrategy("fake", "fp", lambda document: True),
    ])
    doc = _doc(file_hash="a" * 64, run="run-1")
    doc.fact_context.file_hash = "b" * 64
    assert coordinator.recover(
        doc, rule_id="V33-TEST", reason_codes=["operand_missing"],
    ) is None
    assert coordinator.records[0]["reason"] == "source_identity_unverified"


def test_rerun_failure_keeps_failed_attempt_and_original_contract():
    coordinator = RecoveryCoordinator([
        RecoveryStrategy("fake", "fp", lambda document: True),
    ])

    def broken_rerun():
        raise RuntimeError("still broken")

    assert coordinator.recover(
        _doc(), rule_id="V33-TEST", reason_codes=["operand_missing"], rerun=broken_rerun,
    ) is None
    record = coordinator.records[0]
    assert record["status"] == "not_recovered"
    assert "still broken" in (record["attempts"][0]["error"] or "")


def test_verify_false_means_not_recovered_even_when_rerun_returns():
    """恢复是否成功以重算后的有效状态为准：verify 不过就算 rerun 返回也不算。"""
    coordinator = RecoveryCoordinator([
        RecoveryStrategy("fake", "fp", lambda document: True),
    ])
    assert coordinator.recover(
        _doc(), rule_id="V33-TEST", reason_codes=["operand_missing"],
        rerun=lambda: "ok", verify=lambda result: False,
    ) is None
    record = coordinator.records[0]
    assert record["status"] == "not_recovered"
    assert record["attempts"][0]["error"] == "rerun_relations_still_incomplete"
    # 同一策略指纹仍被占用：不重复尝试。
    assert coordinator.recover(
        _doc(), rule_id="V33-TEST", reason_codes=["operand_missing"],
        rerun=lambda: "ok", verify=lambda result: True,
    ) is None


def test_verify_true_and_verify_error_paths():
    coordinator = RecoveryCoordinator([
        RecoveryStrategy("fake", "fp", lambda document: True),
    ])
    assert coordinator.recover(
        _doc(), rule_id="V33-TEST", reason_codes=["operand_missing"],
        rerun=lambda: "ok", verify=lambda result: True,
    ) == "ok"
    assert coordinator.records[0]["status"] == "recovered"

    def broken_verify(_result):
        raise RuntimeError("verifier exploded")

    coordinator2 = RecoveryCoordinator([
        RecoveryStrategy("fake", "fp", lambda document: True),
    ])
    assert coordinator2.recover(
        _doc(), rule_id="V33-TEST", reason_codes=["operand_missing"],
        rerun=lambda: "ok", verify=broken_verify,
    ) is None
    record = coordinator2.records[0]
    assert record["status"] == "not_recovered"
    assert "verifier exploded" in (record["attempts"][0]["error"] or "")
