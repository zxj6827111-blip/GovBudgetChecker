"""Compatibility table views whose monetary cells come only from current facts."""

from __future__ import annotations

from src.engine.fact_context import (
    _fragment_for_instance,
    _column_mappings_for_instance,
    operand_rejection_reason,
    _stage_for_fact,
)
from src.services.fiscal_row_roles import is_index_header, native_bilateral_padding_positions
from src.schemas.parse_artifact import CellPresence

TABLE_CODES = {
    "BUD_T1": "FIN_01_income_expenditure_total",
    "BUD_T2": "FIN_02_income",
    "BUD_T3": "FIN_03_expenditure",
    "BUD_T4": "FIN_04_fiscal_grant_total",
    "BUD_T5": "FIN_05_general_public_expenditure",
    "BUD_T6": "FIN_08_gov_fund",
    "BUD_T7": "FIN_09_state_capital",
    "BUD_T8": "FIN_06_basic_expenditure",
    "BUD_T9": "FIN_07_three_public",
    "收入支出决算总表": "FIN_01_income_expenditure_total",
    "收入决算表": "FIN_02_income",
    "支出决算表": "FIN_03_expenditure",
    "财政拨款收入支出决算总表": "FIN_04_fiscal_grant_total",
    "一般公共预算财政拨款支出决算表": "FIN_05_general_public_expenditure",
    "一般公共预算财政拨款基本支出决算表": "FIN_06_basic_expenditure",
    "财政拨款“三公”经费支出决算表": "FIN_07_three_public",
    "一般公共预算财政拨款“三公”经费支出决算表": "FIN_07_three_public",
    '一般公共预算财政拨款"三公"经费支出决算表': "FIN_07_three_public",
    "政府性基金预算财政拨款收入支出决算表": "FIN_08_gov_fund",
    "国有资本经营预算财政拨款收入支出决算表": "FIN_09_state_capital",
}


class ProjectionUnavailable(ValueError):
    pass


def fact_bound_table_rows(doc, table_name):
    artifact = getattr(doc, "parse_artifact", None)
    context = getattr(doc, "fact_context", None)
    if artifact is None:
        return None
    code = TABLE_CODES.get(table_name)
    if code is None:
        return None
    instances = [instance for instance in artifact.logical_tables if instance.table_code == code
                 and (fragment := _fragment_for_instance(artifact, instance)) is not None
                 and fragment.extraction_method != "page_text_fallback"]
    if not instances:
        return []
    if context is not None and (
        context.file_hash != artifact.source_file_hash
        or context.parse_run_uid != artifact.parse_run_uid
    ):
        raise ProjectionUnavailable("统一事实与解析原件或运行身份不一致")
    if len(instances) != 1:
        raise ProjectionUnavailable(f"{table_name} 存在多个实例，需确认主体/年度/口径")
    instance = instances[0]
    table = _fragment_for_instance(artifact, instance)
    if table is None:
        raise ProjectionUnavailable(f"{table_name} 逻辑实例没有物理片段")
    from src.services.fiscal_empty_disclosure import disclosed_empty_fiscal_table

    if disclosed_empty_fiscal_table(doc, instance, table):
        # Preserve the literal blank cells. An explicit no-budget statement is
        # usable disclosure evidence, not a source of zero monetary facts.
        return [list(row) for row in table.rows]
    if context is None:
        raise ProjectionUnavailable(f"{table_name} 已识别，但没有当前统一事实")
    rows = [list(row) for row in table.rows]
    widths = {len(row) for row in rows if any(str(cell or "").strip() for cell in row)}
    if len(widths) > 1:
        raise ProjectionUnavailable(f"{table_name} 跨页列宽/列身份变化，无法使用同一列映射")
    mappings = _column_mappings_for_instance(instance, table)
    measure_columns = {int(mapping["source_col_idx"]) for mapping in mappings}
    if not measure_columns:
        raise ProjectionUnavailable(f"{table_name} 金额列尚未绑定")
    by_cell = {}
    for fact in context.facts:
        if fact.table_code != code or fact.instance_uid != instance.instance_uid:
            continue
        for ref in fact.extra_dims.get("evidence_refs", []):
            cell_id = ref.get("source_cell_id")
            if cell_id:
                by_cell.setdefault(cell_id, []).append(fact)
    consumed = []
    source_units = set()
    mappings_by_col = {int(m["source_col_idx"]): m for m in mappings}
    padding_positions = native_bilateral_padding_positions(table)
    for cell in table.cells:
        if (
            cell.col_idx not in measure_columns
            or cell.is_header
            or cell.row_idx < table.header_row_count
            or is_index_header(rows[cell.row_idx])
        ):
            continue
        if not str(cell.raw_text or "").strip():
            continue  # Blank means not disclosed; never manufacture zero.
        if (cell.row_idx, cell.col_idx) in padding_positions:
            continue  # 真实占位零仍在表格中；它没有右侧业务行，不能借左侧标签生成事实。
        if (cell.presence == CellPresence.DASH and cell.numeric_value is None
                and cell.raw_text.strip() in {'-', '—', '–', '－'}):
            # 保留原件破折号的展示角色，不要求它拥有数字事实，也不将其转换成零。
            # 后续需要金额的关系仍会如实报取数不足。
            continue
        cell_id = f"{cell.origin_fragment_uid or table.fragment_uid}:r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}"
        facts = by_cell.get(cell_id, [])
        if len(facts) != 1:
            raise ProjectionUnavailable(f"{table_name} 单元格 {cell_id} 没有唯一当前事实")
        fact = facts[0]
        reason = operand_rejection_reason(fact, context.org_name)
        mapping = mappings_by_col[cell.col_idx]
        expected_stage = _stage_for_fact(
            fact.indicator,
            str(mapping.get("source_col_name") or ""),
            context.report_kind,
            context.budget_stage,
        )
        if (
            reason
            or (context.fiscal_year and fact.fiscal_year != context.fiscal_year)
            or (expected_stage and fact.budget_stage != expected_stage)
        ):
            raise ProjectionUnavailable(reason or f"{table_name} 单元格年度/阶段不符")
        # Compatibility rules retain the source unit. Reconstruct from the
        # current normalized fact, including overlays; never read old amounts.
        from decimal import Decimal

        unit = str(fact.unit or "")
        factor = {"元": Decimal("0.0001"), "万元": Decimal("1"), "亿元": Decimal("10000")}.get(unit)
        if factor is None:
            raise ProjectionUnavailable(f"{table_name} 单元格金额单位不明确")
        source_units.add(unit)
        rows[cell.row_idx][cell.col_idx] = str(fact.normalized_value / factor)
        consumed.append(fact.fact_uid)
    journal = getattr(doc, "structured_consumption", None)
    if journal is None:
        journal = doc.structured_consumption = []
    journal.append(
        {
            "rule_id": getattr(doc, "current_rule_code", None),
            "table_code": code,
            "instance_uid": instance.instance_uid,
            "fact_uids": sorted(set(consumed)),
            "fact_version": context.version_token,
            "normalized_unit": "万元",
            "source_units": sorted(source_units),
        }
    )
    if not consumed:
        raise ProjectionUnavailable(f"{table_name} 没有可用金额事实")
    return rows
