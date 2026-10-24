from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext, operand_rejection_reason
from src.engine.rule_identity import rule_identity_requirements
from src.engine.rule_outcome import RuleDeferred
from src.schemas.parse_artifact import FiscalFact
from src.services.org_binding_decision import source_scope_evidence


def fact(**extra):
    return FiscalFact(
        fact_uid="f",
        indicator="total_actual",
        raw_value="100.00",
        unit="万元",
        normalized_value=Decimal("100"),
        org_name="甲市服务中心",
        fiscal_year=2025,
        table_code="FIN_02_income",
        instance_uid="i",
        recognition_status="unverified",
        extra_dims={"org_scope_pending": True, **extra},
    )


def test_scope_waiver_is_rule_specific_and_does_not_leak():
    operand = fact()
    doc = SimpleNamespace(fact_context=FactContext(facts=[operand]))
    assert operand_rejection_reason(operand) is None
    with rule_identity_requirements(doc, "V33-240"):
        assert operand_rejection_reason(operand) is None
    assert operand_rejection_reason(operand) is None
    with pytest.raises(RuleDeferred, match="汇总口径待确认"):
        with rule_identity_requirements(doc, "CMM-018"):
            pytest.fail("依赖口径的规则不应进入执行")


def test_missing_other_identity_still_blocks_internal_relation():
    operand = fact(identity_incomplete=True)
    doc = SimpleNamespace(fact_context=FactContext(facts=[operand]))
    with rule_identity_requirements(doc, "V33-240"):
        assert operand_rejection_reason(operand) == "候选事实的完整业务身份未确认"


def test_org_level_and_template_majority_do_not_determine_scope():
    pages = [
        "甲市服务中心\n2025年度决算",
        "目录\n部门决算单位构成\n年度部门决算表",
        "本表反映单位收入情况",
    ]
    assert source_scope_evidence(pages)[0] is None


def test_context_roundtrip_preserves_level_without_filling_scope():
    context = FactContext(facts=[fact()], org_level="unit")
    restored = FactContext.from_dict(context.to_dict())
    assert restored.org_level == "unit"
    assert restored.org_scope is None
