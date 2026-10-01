"""Canonical registry for production Python rule execution.

This registry is the single routing source for the legacy ``Document`` rule
implementations. ``evaluation_basis`` is intentionally explicit: the current
rules do not all consume ``FiscalFact`` yet, so callers must not represent this
registry as a completed fact-based migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Tuple

from . import budget_rules, common_rules, rules_v33

RULE_REGISTRY_VERSION = "python-rule-registry-v1"
EVALUATION_BASIS_LEGACY = "legacy_document"


@dataclass(frozen=True)
class RuleRegistration:
    rule_id: str
    implementation: Any
    report_kinds: Tuple[str, ...]
    rule_family: str
    evaluation_basis: str = EVALUATION_BASIS_LEGACY


def _registrations() -> List[RuleRegistration]:
    """Read live implementation lists so registry validation sees runtime changes."""
    result: List[RuleRegistration] = []
    for family, kinds, rules in (
        ("budget", ("budget",), budget_rules.ALL_BUDGET_RULES),
        ("final", ("final",), rules_v33.ALL_RULES),
        ("common", ("budget", "final", "unknown"), common_rules.ALL_COMMON_RULES),
    ):
        for rule in rules:
            registration = RuleRegistration(
                rule_id=str(getattr(rule, "code", "")),
                implementation=rule,
                report_kinds=kinds,
                rule_family=family,
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
                "fact_inputs": [],
                "fact_migration": "pending",
            }
            for item in _registrations()
        ],
    }
