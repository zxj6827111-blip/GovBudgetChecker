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
        # 仅比较同一材料内的明确语句，主体、年度等准入维度仍保留。
        "CMM-005",
        "CMM-006",
        "CMM-007",
        "V33-234",
        "V33-245",
        # 是否需要口径由规则按实际命中的“决算安排”表达判定。
        "V33-236",
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
        "CMM-027",
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
        receipt = cached[1]
        material, technical = _classify_blocked_tables(receipt, blocked)
        message = _table_block_message(receipt, material, technical)
        if material and not technical:
            _register_material_format_gap(document, material, message)
        raise RuleDeferred(rule_id, message, partial_issues=partial)


def _cells_by_fragment(receipt: dict) -> dict:
    grouped: dict = {}
    for cell in receipt.get("unbound_money_cells") or []:
        grouped.setdefault(cell.get("fragment_uid"), []).append(cell)
    return grouped


def _classify_blocked_tables(receipt: dict, blocked: list) -> tuple:
    """把阻断表分成“只为原件值非法”和“确有技术/映射冲突”两组。

    判定条件必须全部成立才算材料缺陷：该表所有未绑定格都是单个数词且原文
    小数点重复、原因恰为 ``fact_unbound``、且与上游计数一致。任何一条不满足
    （身份冲突、列未映射、标签缺失、算术冲突、计数不吻合）都按技术冲突处理，
    不借格式归类放行。
    """
    from src.services.cell_number_text import invalid_number_text

    grouped = _cells_by_fragment(receipt)
    material: list = []
    technical: list = []
    for table in blocked:
        cells = grouped.get(table.get("fragment_uid"), [])
        reasons = {str(k): int(v) for k, v in (table.get("reasons") or {}).items()}
        format_cells = [
            cell for cell in cells
            if invalid_number_text(cell.get("raw_text"))
            and str(cell.get("reason") or "") == "fact_unbound"
        ]
        format_only = (
            bool(format_cells)
            and len(format_cells) == len(cells)
            and set(reasons) == {"fact_unbound"}
            and reasons.get("fact_unbound") == len(format_cells)
        )
        if format_only:
            material.append((table, format_cells))
        else:
            technical.append(table)
    return material, technical


def _cell_pages(table: dict, cells: list) -> list:
    pages = {cell.get("page") for cell in cells if isinstance(cell.get("page"), int)}
    return sorted(pages) or [table["page"]]


def _table_block_message(receipt: dict, material: list, technical: list) -> str:
    """区分“原件值非法（材料补正）”与“真实绑定/读取失败（技术处理）”。

    第 9 份真实形态：物理页 9 三个基本支出格原文 ``2.359.46``，旧口径一律
    说成“第 8 页映射/读取未完成”，用户收到的指引与复核项里的“原件格式
    缺陷”互相矛盾。这里按单元格原因分类并落到真实格所在页；两者并存时
    分别列出，不互相吞掉。
    """
    grouped = _cells_by_fragment(receipt)
    parts: list = []
    for table, cells in material:
        refs = "、".join(f"第{page}页{table['table_code']}" for page in _cell_pages(table, cells))
        raws = sorted({str(cell.get("raw_text") or "") for cell in cells})
        parts.append(
            f"原件金额格式非法：{refs} {len(cells)} 个金额格原文「{'」「'.join(raws)}」"
            "无法解析为数值，数值不确定，需回原件补正；依赖勾稽保持材料阻断"
        )
    for table in technical:
        cells = grouped.get(table.get("fragment_uid"), [])
        refs = "、".join(f"第{page}页{table['table_code']}" for page in _cell_pages(table, cells))
        parts.append(f"来源表映射/读取未完成：{refs} {table['reasons']}")
    return "；".join(parts)


def _register_material_format_gap(document, material: list, message: str) -> None:
    """把材料格式缺陷登记成单元格级结构化事实（不是整表技术故障）。

    原因码只从冻结表里选：``cell_unbound`` 的允许动作含人工确认/系统重读，
    比 ``table_mapping_incomplete``（只允许 tech_fix）更符合“回原件补正”。
    阻断位置指向真实格 ID 与物理页，供义务、复核与导出保持同一来源。
    """
    from .diagnostic_fields import record_gap, table_missing_operand

    blocked_by: list = []
    missing: list = []
    for table, cells in material:
        for cell in cells:
            page = cell.get("page") if isinstance(cell.get("page"), int) else table["page"]
            ref = f"第{page}页{table['table_code']}:{cell.get('source_cell_id') or cell.get('row')}"
            blocked_by.append({"dimension": "cell", "ref": ref, "reason_code": "cell_unbound"})
            missing.append(table_missing_operand(
                table_code=str(table["table_code"]),
                role="金额格原文非法，数值不确定",
                page=page,
                row_key=cell.get("source_cell_id") or cell.get("row"),
                column="金额",
            ))
    if not blocked_by:
        return
    record_gap(document, [message], reason_codes=["cell_unbound"],
               missing_operands=missing, blocked_by=blocked_by,
               preferred_action="human_confirm")



@contextmanager
def rule_identity_requirements(document, rule_id):
    required = required_identity_dimensions(rule_id)
    if rule_id == "CMM-018" and (getattr(getattr(document, "document_profile", None), "kind", None) == "final" or ((getattr(getattr(document, "document_profile", None), "writing_scope", {}) or {}).get("domain") == "budget")):
        required = tuple(dim for dim in required if dim != "org_scope")
    context = getattr(document, "fact_context", None)
    if (
        context is not None
        and any(f.extra_dims.get("org_scope_pending") for f in context.facts)
        and "org_scope" in required
    ):
        from .rule_outcome import RuleDeferred

        partial = []
        if rule_id == "CMM-005":
            from .common_rules import CMM005_ComparativeNarrativeLogic
            # 同一条规则还负责纯文字模板残留；缺口径不能把已有原文问题一起压掉。
            partial = CMM005_ComparativeNarrativeLogic().scope_independent_issues(document)
        raise RuleDeferred("org_scope_pending", "材料汇总口径待确认；本规则依赖该维度，未完成检查",
                           partial_issues=partial)
    token = ACTIVE_IDENTITY.set(required)
    try:
        enforce_legacy_table_admission(document, rule_id)
        yield
    finally:
        ACTIVE_IDENTITY.reset(token)
