"""规则显式声明身份维度；仅材料内已确认关系的勾稽可不依赖汇总口径。"""

from contextlib import contextmanager
from contextvars import ContextVar

FULL_IDENTITY = ("organization", "year", "fund_scope", "budget_stage", "instance_uid", "org_scope")
INTERNAL_RELATION_RULES = frozenset(
    {
        "CMM-001",
        "V33-005",
        "V33-103",
        "V33-104",
        "V33-119",
        "V33-120",
        "V33-122",
        "V33-214",
        "V33-240",
        "V33-241",
        "V33-248",
        "V33-249",
        "BUD-107",
        "V33-001",
        "V33-002",
        "V33-003",
        "V33-004",
        "V33-101",
        "V33-102",
        "V33-105",
        "V33-106",
        "V33-107",
        "V33-108",
        "V33-109",
        "V33-110",
        "V33-111",
        "V33-112",
        "V33-113",
        "V33-114",
        "V33-115",
        "V33-117",
        "V33-121",
        "V33-200",
        "V33-201",
        "V33-202",
        "V33-203",
        "V33-204",
        "V33-220",
        "V33-221",
        "V33-222",
        "V33-223",
        "V33-224",
        "V33-225",
        "V33-226",
        "V33-227",
        "V33-230",
        "V33-232",
        "V33-233",
        "V33-235",
        "V33-242",
        "V33-243",
        "V33-244",
        "V33-246",
        "V33-250",
        "V33-251",
        "V33-252",
        "V33-SG-COMPLETION",
        "V33-CROSS-SAN-GONG-ECON",
        "V33-TXT-FUND-DETAIL",
        "V33-NARRATIVE-INDICATOR-REPEAT",
        "V33-TREND-COMPLETION-RATE",
        "V33-DISCLOSURE-PERCENT-UNIT",
        "CMM-002",
        "CMM-003",
        "CMM-004",
        "CMM-008",
        "CMM-009",
        "CMM-010",
        "CMM-011",
        "CMM-012",
        "CMM-013",
        "CMM-014",
        "CMM-015",
        "CMM-016",
        "CMM-017",
        "CMM-019",
        "CMM-020",
        "CMM-021",
        "CMM-022",
        "CMM-023",
        "CMM-024",
        "CMM-025",
        "CMM-026",
    }
)
# 规则外统计只判断原始事实能否供材料内检查使用；执行时再套用规则自己的全部声明。
ACTIVE_IDENTITY = ContextVar(
    "rule_required_identity", default=tuple(dim for dim in FULL_IDENTITY if dim != "org_scope")
)


def required_identity_dimensions(rule_id):
    return (
        tuple(dim for dim in FULL_IDENTITY if dim != "org_scope")
        if rule_id in INTERNAL_RELATION_RULES
        else FULL_IDENTITY
    )


LEGACY_TABLE_INPUTS = {
    "V33-004": None,
    "V33-005": None,
    "V33-101": {"FIN_01_income_expenditure_total"},
    "V33-103": {"FIN_02_income"},
    "V33-104": {"FIN_03_expenditure"},
    "V33-109": {"FIN_08_gov_fund", "FIN_09_state_capital"},
    "V33-114": {"FIN_07_three_public", "FIN_08_gov_fund", "FIN_09_state_capital"},
    "V33-119": {"FIN_04_fiscal_grant_total"},
    "V33-120": {"FIN_02_income", "FIN_03_expenditure", "FIN_05_general_public_expenditure"},
    "V33-122": {"FIN_04_fiscal_grant_total", "FIN_08_gov_fund", "FIN_09_state_capital"},
    "V33-240": {"FIN_02_income"},
    "V33-241": {"FIN_03_expenditure"},
}


def enforce_legacy_table_admission(document, rule_id):
    artifact = getattr(document, "parse_artifact", None)
    if artifact is None or rule_id not in LEGACY_TABLE_INPUTS:
        return
    from .rule_outcome import RuleDeferred

    context = getattr(document, "fact_context", None)
    if context is None:
        raise RuleDeferred(rule_id, "已解析表格没有当前财政事实上下文，不能绕过事实准入检查")
    cache_key = (artifact.source_file_hash, artifact.parse_run_uid, context.version_token)
    cached = getattr(document, "_table_admission_receipt", None)
    if not cached or cached[0] != cache_key:
        from src.services.table_import_receipt import build_table_import_receipt

        cached = (
            cache_key,
            build_table_import_receipt(artifact, context.facts, org_name=context.org_name),
        )
        document._table_admission_receipt = cached
    codes = LEGACY_TABLE_INPUTS[rule_id]
    blocked = [
        table
        for table in cached[1].get("mapping_incomplete_tables", [])
        if (codes is None and str(table["table_code"]).startswith(("FIN_", "BUD_")))
        or (codes is not None and table["table_code"] in codes)
    ]
    if blocked:
        partial = []
        if rule_id == "V33-240":
            from .native_income_sources import missing_header_findings
            from .rules_v33 import R33240_Table2_IncomeAdvancedCheck

            partial = missing_header_findings(R33240_Table2_IncomeAdvancedCheck(), document)
        raise RuleDeferred(
            rule_id,
            "来源表映射/读取未完成："
            + "；".join(
                f"第{table['page']}页{table['table_code']} {table['reasons']}" for table in blocked
            ),
            partial_issues=partial,
        )


@contextmanager
def rule_identity_requirements(document, rule_id):
    required = required_identity_dimensions(rule_id)
    context = getattr(document, "fact_context", None)
    if (
        context is not None
        and any(f.extra_dims.get("org_scope_pending") for f in context.facts)
        and "org_scope" in required
    ):
        from .rule_outcome import RuleDeferred

        raise RuleDeferred("org_scope_pending", "材料汇总口径待确认；本规则依赖该维度，未完成检查")
    token = ACTIVE_IDENTITY.set(required)
    try:
        enforce_legacy_table_admission(document, rule_id)
        yield
    finally:
        ACTIVE_IDENTITY.reset(token)
