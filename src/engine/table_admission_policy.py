"""表格准入延期的二次分类：把“材料格式缺陷”与“技术映射失败”分开。

为什么单独成模块（2026-11-05，复审 RF-02a + 160 冻结包约束）：

验收包 `v1-regression-r4` 的 `rule_identity_dimensions.json` 冻结了
``src/engine/rule_identity.py`` 的整文件 sha256，且 ``compare`` 的 M1 要求
两次采集使用同一 `kit_digest`。因此基线 ``4cda0edd`` 与候选必须在该文件上
**逐字节相同**，否则 160 双采集不成立。

本模块因此把新的分类逻辑挂在 ``pipeline`` 捕获 ``RuleDeferred`` 之后：
``rule_identity.enforce_legacy_table_admission`` 保持原样（仍抛
“来源表映射/读取未完成：第N页…”），这里按同一份表格读取回执重新判定：

- 整表阻断只由“原文无法解析成数值”造成 → 改写为材料缺陷表述，并登记
  单元格级结构化事实（cell_unbound + 真实格 ID + 物理页），首选动作人工确认；
- 存在身份冲突/列未映射/标签缺失/算术冲突 → 保留技术表述，但把位置改到
  真实未绑定格所在页；
- 两者并存 → 分别列出，不互相吞掉。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

#: 只有“这一格取值本身不可解析”能由数字格式解释；列归属、身份冲突、
#: 标签缺失各有独立原因，不能因为归类格式缺陷就一并消失。
FORMAT_EXPLAINED_REASONS = frozenset({"fact_unbound"})

_ADMISSION_PREFIX = "来源表映射/读取未完成"


def cells_by_fragment(receipt: dict) -> Dict[Any, List[dict]]:
    """未绑定金额格在回执的两层结构里都可能出现。

    ``build_table_import_receipt`` 返回外层回执，逐格明细挂在
    ``reading_quality.unbound_money_cells``；持久化的 reading_import 则把
    部分字段摊平。两处都取，避免因结构层级差异漏掉真实格。
    """
    cells = list(receipt.get("unbound_money_cells") or [])
    cells += list((receipt.get("reading_quality") or {}).get("unbound_money_cells") or [])
    grouped: Dict[Any, List[dict]] = {}
    for cell in cells:
        grouped.setdefault(cell.get("fragment_uid"), []).append(cell)
    return grouped


EMPTY_PREFIX = "原件表格无可用金额数据"


def classify_blocked_tables(receipt: dict, blocked: list) -> Tuple[list, list, list]:
    """把阻断表分成“只为原件值非法”和“确有技术/映射冲突”两组。

    判定条件必须全部成立才算材料缺陷：该表所有未绑定格都是单个数词且原文
    小数点重复、原因恰为 ``fact_unbound``、且与上游计数一致。任何一条不满足
    （身份冲突、列未映射、标签缺失、算术冲突、计数不吻合）都按技术冲突处理，
    不借格式归类放行。
    """
    from src.services.cell_number_text import invalid_number_text

    grouped = cells_by_fragment(receipt)
    material: List[tuple] = []
    technical: list = []
    empty: list = []
    for table in blocked:
        cells = grouped.get(table.get("fragment_uid"), [])
        reasons = {str(k): int(v) for k, v in (table.get("reasons") or {}).items()}
        format_cells = [
            cell for cell in cells
            if invalid_number_text(cell.get("raw_text"))
            and str(cell.get("reason") or "") in FORMAT_EXPLAINED_REASONS
        ]
        format_only = (
            bool(format_cells)
            and len(format_cells) == len(cells)
            and set(reasons) == {"fact_unbound"}
            and reasons.get("fact_unbound") == len(format_cells)
        )
        if format_only:
            material.append((table, format_cells))
        elif not cells and not reasons and not table.get("unresolved_money_cell_count"):
            # 没有任何未绑定格、也没有任何逐格原因，却被标为映射不完整：
            # 这是“表里没有可用金额事实”（空表/缺表），属材料侧，不是系统
            # 读取故障。第 4/1 份的 p11/p12 空表与缺表即此形态。
            empty.append(table)
        else:
            technical.append(table)
    return material, technical, empty


def _cell_pages(table: dict, cells: list) -> list:
    pages = {cell.get("page") for cell in cells if isinstance(cell.get("page"), int)}
    return sorted(pages) or [table["page"]]


def build_message(receipt: dict, material: list, technical: list, empty: list = ()) -> str:
    """材料侧与技术侧分别列示，不互相吞掉。"""
    grouped = cells_by_fragment(receipt)
    parts: List[str] = []
    for table, cells in material:
        refs = "、".join(f"第{page}页{table['table_code']}" for page in _cell_pages(table, cells))
        raws = sorted({str(cell.get("raw_text") or "") for cell in cells})
        parts.append(
            f"原件金额格式非法：{refs} {len(cells)} 个金额格原文「{'」「'.join(raws)}」"
            "无法解析为数值，数值不确定，需回原件补正；依赖勾稽保持材料阻断"
        )
    for table in empty:
        pages = "、".join(str(page) for page in _cell_pages(table, []))
        parts.append(
            f"{EMPTY_PREFIX}：第{pages}页{table['table_code']}《{table.get('title') or table['table_code']}》"
            "没有可用金额事实（空表或缺表），需按材料补正核对"
        )
    for table in technical:
        cells = grouped.get(table.get("fragment_uid"), [])
        refs = "、".join(f"第{page}页{table['table_code']}" for page in _cell_pages(table, cells))
        parts.append(f"{_ADMISSION_PREFIX}：{refs} {table['reasons']}")
    return "；".join(parts)


def register_material_format_gap(document, material: list, message: str) -> None:
    """把材料格式缺陷登记成单元格级结构化事实（不是整表技术故障）。

    原因码只从冻结表里选：``cell_unbound`` 的允许动作含人工确认/系统重读，
    比 ``table_mapping_incomplete``（只允许 tech_fix）更符合“回原件补正”。
    """
    from .diagnostic_fields import record_gap, table_missing_operand

    blocked_by: List[dict] = []
    missing: List[dict] = []
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


def register_empty_table_gap(document, empty: list, message: str) -> None:
    """空表/缺表按材料侧登记：原因码 table_missing，位置是表所在页。"""
    from .diagnostic_fields import record_gap, table_missing_operand

    blocked_by: List[dict] = []
    missing: List[dict] = []
    for table in empty:
        ref = f"第{table['page']}页{table['table_code']}"
        blocked_by.append({"dimension": "instance_uid", "ref": ref, "reason_code": "table_missing"})
        missing.append(table_missing_operand(
            table_code=str(table["table_code"]), role="整表无可用金额事实",
            page=table["page"], column="金额"))
    if not blocked_by:
        return
    record_gap(document, [message], reason_codes=["table_missing"],
               missing_operands=missing, blocked_by=blocked_by,
               preferred_action="human_confirm")


def reclassify_admission_deferral(document, rule_id: str, detail: str) -> str | None:
    """改写表格准入延期文本；不是该前缀时返回 None。

    在 ``pipeline`` 捕获 ``RuleDeferred`` 之后调用，规则文件本身保持冻结原样。
    """
    if not detail.startswith(_ADMISSION_PREFIX):
        return None
    cached = getattr(document, "_table_admission_receipt", None)
    if not cached:
        return None
    from .rule_identity import LEGACY_TABLE_INPUTS

    if rule_id not in LEGACY_TABLE_INPUTS:
        return None
    codes = LEGACY_TABLE_INPUTS[rule_id]
    receipt = cached[1]
    blocked = [
        table
        for table in receipt.get("mapping_incomplete_tables", [])
        if (codes is None and str(table["table_code"]).startswith(("FIN_", "BUD_")))
        or (codes is not None and table["table_code"] in codes)
    ]
    if not blocked:
        return None
    material, technical, empty = classify_blocked_tables(receipt, blocked)
    if not material and not technical and not empty:
        return None
    message = build_message(receipt, material, technical, empty)
    if material and not technical:
        register_material_format_gap(document, material, message)
    elif empty and not technical and not material:
        register_empty_table_gap(document, empty, message)
    return message
