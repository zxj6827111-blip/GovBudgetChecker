"""规则显式声明身份维度；仅材料内已确认关系的勾稽可不依赖汇总口径。"""
from contextlib import contextmanager
from contextvars import ContextVar

FULL_IDENTITY = ("organization", "year", "fund_scope", "budget_stage", "instance_uid", "org_scope")
INTERNAL_RELATION_RULES = frozenset({
    "CMM-001", "V33-005", "V33-103", "V33-104", "V33-119", "V33-120", "V33-122",
    "V33-214", "V33-240", "V33-241", "V33-248", "V33-249", "BUD-107",
})
ACTIVE_IDENTITY = ContextVar("rule_required_identity", default=FULL_IDENTITY)


def required_identity_dimensions(rule_id):
    return tuple(dim for dim in FULL_IDENTITY if dim != "org_scope") if rule_id in INTERNAL_RELATION_RULES else FULL_IDENTITY


@contextmanager
def rule_identity_requirements(document, rule_id):
    required = required_identity_dimensions(rule_id)
    context = getattr(document, "fact_context", None)
    if context is not None and context.facts and not context.org_scope and "org_scope" in required:
        from .rule_outcome import RuleDeferred
        raise RuleDeferred("org_scope_pending", "材料汇总口径待确认；本规则依赖该维度，未完成检查")
    token = ACTIVE_IDENTITY.set(required)
    try:
        yield
    finally:
        ACTIVE_IDENTITY.reset(token)
