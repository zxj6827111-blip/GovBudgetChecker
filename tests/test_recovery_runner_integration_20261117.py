"""RR-05B：真实 EngineRuleRunner 的恢复闭环集成测试（隔离假 I/O）。

覆盖 2026-11-17 第二次独立复核的两个入口缺陷与一个收敛缺陷：

1. 原因码只写在关系回执、信号不带 reason_codes 时，恢复仍要触发；
2. 恢复成功后旧失败回执移出有效集合，规则结论以重算后的有效回执为准；
3. 重跑后关系仍未完成时按 not_recovered 处理，规则保持未完成（fail-closed）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import build_document
from src.schemas.issues import AnalysisConfig, JobContext
from src.services.bounded_recovery import RecoveryCoordinator, RecoveryStrategy
from src.services.engine_rule_runner import EngineRuleRunner

_FAILING = {"insufficient_data", "parse_error", "execution_error"}


def _make_rule(*, signal_codes, repaired_state_ok: bool = True):
    """测试规则：首次执行写 insufficient 回执并抛 RuleDeferred；重跑按修复状态写 pass。

    原因码按真实规则的做法先经 ``record_gap`` 登记可定位失败，回执上的
    reason_codes 才能通过定位门禁存留下来（RR-05B 的入口场景）。
    ``repaired_state_ok=False`` 时即使文档已“修复”，重跑仍写 insufficient——
    用于验证恢复判定以有效回执为准，而不是 rerun 返回了列表。
    """
    calls = []

    class Rule:
        code = "V33-TEST-RECOVERY"
        desc = "只读审计测试规则"

        def apply(self, document):
            repaired = bool(getattr(document, "repaired", False))
            calls.append("rerun" if repaired else "initial")
            effective = repaired and repaired_state_ok
            from src.engine.rules_v33 import _v33_relation_receipt

            if not effective:
                from src.engine.diagnostic_fields import (
                    record_gap,
                    text_missing_operand,
                )

                record_gap(
                    document, "缺少操作数",
                    reason_codes=["operand_missing"],
                    missing_operands=[text_missing_operand(
                        section="测试段", role="金额", page=8, column="金额")],
                )
            _v33_relation_receipt(
                document, self.code, "same-relation",
                status="pass" if effective else "insufficient_data",
                formula="test relation",
                incomplete_reason=None if effective else "缺少操作数",
                reason_codes=[] if effective else ["operand_missing"],
            )
            if not effective:
                raise RuleDeferred(
                    self.code, "缺少操作数", reason_codes=list(signal_codes or [])
                )
            return []

    return Rule(), calls


def _make_document():
    doc = build_document("probe.pdf", ["2024年度部门决算"], [[]], 100)
    doc.parse_artifact = SimpleNamespace(source_file_hash="a" * 64, parse_run_uid="run-test")
    doc.fact_context = SimpleNamespace(
        file_hash="a" * 64, parse_run_uid="run-test", facts=[]
    )
    return doc


async def _run_with(rule, repair):
    doc = _make_document()
    runner = EngineRuleRunner()
    runner._recovery = RecoveryCoordinator([
        RecoveryStrategy("test-repair", "fp-test", repair)
    ])
    locator = MagicMock()
    ctx = JobContext(job_id="audit-no-storage", pdf_path="probe.pdf", meta={"report_kind": "final"})
    with patch.object(runner, "_prepare_document", AsyncMock(return_value=doc)), \
            patch.object(runner, "_select_rule_set", return_value=[rule]), \
            patch("src.engine.rule_registry.restrict_rules_to_profile",
                  side_effect=lambda rules, profile: rules), \
            patch("src.services.material_writing.ensure_document_resolution"), \
            patch("src.services.engine_rule_runner.update_progress"), \
            patch("src.services.engine_rule_runner.PDFBBoxLocator", return_value=locator):
        findings = await runner.run_rules(ctx, [], AnalysisConfig())
    return doc, runner, findings


def test_receipt_only_reason_codes_trigger_recovery_and_converge():
    """原因码只在关系回执上：恢复触发、旧失败回执移出、规则结论收敛为 pass。"""
    rule, calls = _make_rule(signal_codes=[])

    def repair(document):
        document.repaired = True
        return True

    doc, runner, findings = asyncio.run(_run_with(rule, repair))
    assert calls == ["initial", "rerun"]
    record = runner._recovery.records[0]
    assert record["status"] == "recovered"
    # 有效回执只剩重跑写下的 pass；旧 insufficient 回执留审计。
    statuses = [receipt["status"] for receipt in doc.fact_receipts]
    assert statuses == ["pass"]
    superseded = getattr(doc, "superseded_fact_receipts", [])
    assert [receipt["status"] for receipt in superseded] == ["insufficient_data"]
    assert [receipt["status"] for receipt in
            runner._stats["recovery_superseded_receipts"]] == ["insufficient_data"]
    summary = runner.get_rule_execution_summary()
    assert summary.get("pass") == 1
    assert not summary.get("insufficient_data")
    assert findings == []


def test_rerun_still_incomplete_keeps_rule_unresolved():
    """重跑后关系仍 insufficient：not_recovered，规则保持未完成（fail-closed）。"""
    rule, calls = _make_rule(signal_codes=["operand_missing"], repaired_state_ok=False)

    def repair(document):
        document.repaired = True
        return True

    doc, runner, findings = asyncio.run(_run_with(rule, repair))
    assert calls == ["initial", "rerun"]
    assert runner._recovery.records[0]["status"] == "not_recovered"
    # 重跑仍然延期（rerun 抛出 RuleDeferred）：自然走 not_recovered，
    # 新旧回执都在有效集合，规则结论保持 insufficient_data。
    assert runner._recovery.records[0]["attempts"][0]["error"].startswith("rerun_RuleDeferred")
    statuses = [receipt["status"] for receipt in doc.fact_receipts]
    assert statuses.count("insufficient_data") == 2
    summary = runner.get_rule_execution_summary()
    assert summary.get("insufficient_data") == 1


def test_material_defect_codes_never_enter_recovery_in_runner():
    """材料缺陷类原因（item_disclosure_missing 族）在真实 runner 中不触发恢复。"""

    class DefectRule:
        code = "V33-TEST-DEFECT"
        desc = "只读审计测试规则"

        def apply(self, document):
            from src.engine.rules_v33 import _v33_relation_receipt

            _v33_relation_receipt(
                document, self.code, "same-relation",
                status="insufficient_data",
                formula="test relation",
                incomplete_reason="分项未披露",
                reason_codes=["item_disclosure_missing"],
            )
            raise RuleDeferred(self.code, "分项未披露", reason_codes=["item_disclosure_missing"])

    def repair(document):
        document.repaired = True
        return True

    doc, runner, findings = asyncio.run(_run_with(DefectRule(), repair))
    assert runner._recovery.records == []
    statuses = [receipt["status"] for receipt in doc.fact_receipts]
    assert statuses == ["insufficient_data"]


def test_verify_rejects_when_rerun_skips_a_failing_relation():
    """重跑未覆盖原失败关系（旧回执仍在）：按未恢复处理，不清理旧回执。"""

    calls = []

    class PartialRule:
        code = "V33-TEST-PARTIAL"
        desc = "只读审计测试规则"

        def apply(self, document):
            from src.engine.rules_v33 import _v33_relation_receipt

            repaired = bool(getattr(document, "repaired", False))
            calls.append("rerun" if repaired else "initial")
            if not repaired:
                _v33_relation_receipt(
                    document, self.code, "relation-a",
                    status="insufficient_data", formula="t",
                    incomplete_reason="缺操作数A", reason_codes=["operand_missing"],
                )
                _v33_relation_receipt(
                    document, self.code, "relation-b",
                    status="insufficient_data", formula="t",
                    incomplete_reason="缺操作数B", reason_codes=["operand_missing"],
                )
                raise RuleDeferred(self.code, "缺操作数", reason_codes=["operand_missing"])
            # 修复后的重跑只重写了 relation-a，relation-b 未再登记。
            _v33_relation_receipt(
                document, self.code, "relation-a",
                status="pass", formula="t",
            )
            return []

    def repair(document):
        document.repaired = True
        return True

    doc, runner, findings = asyncio.run(_run_with(PartialRule(), repair))
    assert calls == ["initial", "rerun"]
    assert runner._recovery.records[0]["status"] == "not_recovered"
    # relation-b 旧回执未被覆盖，继续留在有效集合阻塞。
    ids = {(r["sub_check_id"], r["status"]) for r in doc.fact_receipts}
    assert ("relation-b", "insufficient_data") in ids
    assert ("relation-a", "insufficient_data") in ids
