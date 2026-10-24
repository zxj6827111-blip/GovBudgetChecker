"""Canonical registry for production Python rule execution.

This registry is the single routing source for the legacy ``Document`` rule
implementations. ``evaluation_basis`` is intentionally explicit: rules that
have not migrated to :class:`FiscalFact` consume the legacy ``Document`` and
are marked ``fact_migration="pending"``; migrated rules declare their fact
inputs here and are marked ``complete``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Tuple

from . import budget_rules, common_rules, rules_v33

RULE_REGISTRY_VERSION = "python-rule-registry-v2-profile-scope"
EVALUATION_BASIS_LEGACY = "legacy_document"
EVALUATION_BASIS_FACT = "fiscal_fact"

#: 已迁移到统一财政事实的规则及其事实输入（ZC-01/03，AC-01）。
#: fact_inputs 以指标名登记；规则在运行时消费 Document.fact_context 中
#: 同一版本的事实，缺失时回退 legacy 路径并如实记回执。
FACT_INPUTS_BY_RULE: Dict[str, Tuple[str, ...]] = {
    "BUD-107": (
        "FIN_01:income_actual",
        "FIN_01:expenditure_actual",
        'FIN_04:expenditure_actual',
        "FIN_07:budget",
    ),
    "CMM-001": ("FIN_07:budget", "FIN_07:actual", "FIN_07:total_actual"),
    "V33-103": ("FIN_02:total_actual", "FIN_02:fiscal_allocation"),
    "V33-104": ("FIN_03:total_actual", "FIN_03:basic_actual", "FIN_03:project_actual"),
    "V33-214": ("FIN_01:income_actual", "FIN_01:expenditure_actual"),
    "V33-248": ("FIN_08:income_actual", "FIN_08:total_actual"),
    "V33-249": ("FIN_05:total_actual", "FIN_03:total_actual"),
}


@dataclass(frozen=True)
class RuleRegistration:
    rule_id: str
    implementation: Any
    report_kinds: Tuple[str, ...]
    rule_family: str
    evaluation_basis: str = EVALUATION_BASIS_LEGACY
    fact_inputs: Tuple[str, ...] = ()
    fact_migration: str = "pending"


def _registrations() -> List[RuleRegistration]:
    """Read live implementation lists so registry validation sees runtime changes."""
    result: List[RuleRegistration] = []
    for family, kinds, rules in (
        ("budget", ("budget",), budget_rules.ALL_BUDGET_RULES),
        ("final", ("final",), rules_v33.ALL_RULES),
        ("common", ("budget", "final", "unknown"), common_rules.ALL_COMMON_RULES),
    ):
        for rule in rules:
            rule_id = str(getattr(rule, "code", ""))
            fact_inputs = FACT_INPUTS_BY_RULE.get(rule_id, ())
            registration = RuleRegistration(
                rule_id=rule_id,
                implementation=rule,
                report_kinds=kinds,
                rule_family=family,
                evaluation_basis=(
                    EVALUATION_BASIS_FACT if fact_inputs else EVALUATION_BASIS_LEGACY
                ),
                fact_inputs=fact_inputs,
                fact_migration="complete" if fact_inputs else "pending",
            )
            if not registration.rule_id:
                raise RuntimeError("rule registry contains an implementation without a code")
            if any(item.rule_id == registration.rule_id for item in result):
                raise RuntimeError(f"duplicate production rule id: {registration.rule_id}")
            result.append(registration)
    return result


def select_rules(report_kind: str) -> List[Any]:
    """Return canonical execution order for a known kind, or common rules only."""
    kind = str(report_kind or "unknown").strip().lower()
    return [*select_specific_rules(kind), *common_rules.ALL_COMMON_RULES]


def restrict_rules_to_profile(rules, profile):
    """检查能力与报告口径一致；排除专项检查时账本必须保留能力缺口。"""
    if getattr(profile, 'unsupported_reason', None) == 'government_mixed_period_report':
        # 重复词检查不需要部门九表，也不将不同期间的金额串起来复算。
        return [rule for rule in rules if getattr(rule, 'code', None) == 'CMM-002']
    return rules


def select_specific_rules(report_kind: str) -> List[Any]:
    """Return only budget/final rules, without the shared common rule family."""
    kind = str(report_kind or "unknown").strip().lower()
    if kind == "budget":
        return list(budget_rules.ALL_BUDGET_RULES)
    if kind == "final":
        return list(rules_v33.ALL_RULES)
    return []


def registered_rule_ids(report_kind: str) -> FrozenSet[str]:
    """Registered ids available to the selected report kind."""
    kind = str(report_kind or "unknown").strip().lower()
    return frozenset(
        registration.rule_id
        for registration in _registrations()
        if kind in registration.report_kinds
    )


def all_rule_ids() -> List[str]:
    """All unique production rule ids in canonical registry order."""
    return [item.rule_id for item in _registrations()]


def rule_catalog() -> Dict[str, Any]:
    """Serializable implementation/obligation/input-basis mapping."""
    from .check_obligations import obligation_ids_for_rule

    return {
        "version": RULE_REGISTRY_VERSION,
        "evaluation_basis": EVALUATION_BASIS_LEGACY,
        "total": len(_registrations()),
        "rules": [
            {
                "rule_id": item.rule_id,
                "implementation": (
                    f"{type(item.implementation).__module__}.{type(item.implementation).__name__}"
                ),
                "rule_family": item.rule_family,
                "report_kinds": list(item.report_kinds),
                "evaluation_basis": item.evaluation_basis,
                "obligation_ids": obligation_ids_for_rule(item.rule_id),
                "fact_inputs": list(item.fact_inputs),
                "fact_migration": item.fact_migration,
            }
            for item in _registrations()
        ],
    }
