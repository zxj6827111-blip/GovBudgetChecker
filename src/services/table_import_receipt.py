"""金额读取、列身份、事实覆盖和规则准入分别记账，未知项不能从分母消失。"""
from __future__ import annotations

import re
from collections import Counter

from src.services.fiscal_column_identity import canonical_column_mappings, code_dimension_columns, header_paths
from src.services.fiscal_row_roles import is_index_header, native_bilateral_padding_positions
from src.services.fiscal_table_rules import (
    NINE_TABLE_RULES, is_auxiliary_grant_expenditure_title, is_unit_breakdown_header, normalize_text)


def source_cell_id(table, cell):
    return (f"{cell.origin_fragment_uid or table.fragment_uid}:"
            f"r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}"
            f"c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}")


def _serial_lane_proofs(artifact):
    """序号续页须继承原页字面表头、相同列边界和连续编号，排除依据留存。"""
    proofs = {}
    previous = None
    for table in sorted(artifact.physical_fragments, key=lambda t: (t.page_number, t.table_index)):
        if table.extraction_method not in {"lines", "lines_strict"}:
            continue
        header = next((c for c in table.cells if normalize_text(c.raw_text) in {"序号", "序"}), None)
        if header and any(normalize_text(c.raw_text) == "单位名称" for c in table.cells if c.row_idx == header.row_idx):
            lane = header.col_idx
            basis = f"literal_serial_header:{table.fragment_uid}:r{header.row_idx}c{lane}"
        elif previous and table.page_number == previous[0].page_number + 1 and all(
                len(row) == len(previous[0].rows[0]) for row in table.rows):
            lane, basis = previous[1], previous[2]
        else:
            previous = None
            continue
        numeric = [c for c in table.cells if c.col_idx == lane and c.numeric_value is not None]
        if not numeric or any(not re.fullmatch(r"\d+", c.raw_text.strip()) or not c.bbox for c in numeric):
            previous = None
            continue
        values = [int(c.raw_text.strip()) for c in numeric]
        if values != list(range(values[0], values[0] + len(values))):
            previous = None
            continue
        if not header:
            last = previous[3]
            old_boxes = [c.bbox for c in previous[0].cells if c.col_idx == lane and c.bbox]
            if values[0] != last + 1 or not old_boxes or any(
                    abs(c.bbox[i] - old_boxes[-1][i]) > .5 for c in numeric for i in (0, 2)):
                previous = None
                continue
        for cell in numeric:
            proofs[source_cell_id(table, cell)] = basis
        previous = (table, lane, basis, values[-1])
    return proofs


def _numeric_exclusion(table, cell, mappings, serial_proofs=None):
    """只排除能指明原列/原行依据的数字；小数金额不能因落入标签列被吞掉。"""
    row = table.rows[cell.row_idx]
    if is_index_header(row):
        return "literal_index_header"
    if serial_proofs and source_cell_id(table, cell) in serial_proofs:
        return serial_proofs[source_cell_id(table, cell)]
    literal = cell.raw_text.strip()
    if not re.fullmatch(r"\d+", literal):
        return None
    paths = header_paths(table)
    path = paths[cell.col_idx] if cell.col_idx < len(paths) else []
    if (cell.col_idx in code_dimension_columns(table, paths)
            or any(part in {"科目编码", "功能分类科目编码", "经济分类科目编码", "类", "款", "项", "序号", "行次", "栏次", "年度", "年份"} for part in path)):
        return "literal_dimension_header:" + "/".join(path)
    label_cols = {col for mapping in mappings for col in mapping.get("source_label_cols", [])}
    if cell.col_idx in label_cols:
        return "mapped_subject_dimension"
    # 无表头续页的完整编码须有紧邻名称和右侧小数金额车道，不能仅按数值长度过滤。
    if (table.table_code.startswith("UNKNOWN_") and cell.col_idx == 0
            and re.fullmatch(r"\d{3}(?:\d{2}){0,2}", literal) and len(row) >= 3
            and re.search(r"[\u4e00-\u9fff]", str(row[1] or ""))
            and any(re.fullmatch(r"-?[\d,]+\.\d+", str(value or "").strip()) for value in row[2:])):
        return "native_code_name_amount_lanes"
    return None


def _inherited_dimension_basis(table, cell, mappings, width_safe):
    """列宽已安全对齐的续页可继承字面编码表头，不能用漂移视图吞掉整数金额。"""
    if not width_safe or cell.origin_col_idx not in {None, cell.col_idx}:
        return None
    if not re.fullmatch(r"\d+", cell.raw_text.strip()):
        return None
    basis = _numeric_exclusion(table, cell, mappings)
    if not basis or not basis.startswith("literal_dimension_header:") or not cell.bbox:
        return None
    # 合并表头（如「经济分类科目编码」横跨类、款两列）只落在首列，按物理范围覆盖判断。
    headers = [c for c in table.cells if c.row_idx < table.header_row_count
               and c.col_idx <= cell.col_idx and c.bbox and c.raw_text.strip()]
    if any(h.bbox[0] - .5 <= cell.bbox[0] and cell.bbox[2] <= h.bbox[2] + .5 for h in headers):
        return f"inherited_physical_dimension:{table.fragment_uid}:c{cell.col_idx}:{basis}"
    return None


_AMOUNT_LITERAL = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d{5,}")
# 功能分类 2xx、部门经济分类 3xx、政府经济分类 5xx，可带款、项两位。
_SUBJECT_CODE = re.compile(r"[235]\d{2}(?:\d{2}){0,2}")
_SENTENCE_MARK = re.compile(r"[，。；！？]")
_TOTAL_LABELS = {"合计", "总计", "人员经费合计", "公用经费合计"}
#: 绩效目标申报表/汇总表的栏目词；金额是项目资金，不是九表科目金额。
_PERFORMANCE_MARKERS = ("绩效目标", "绩效指标", "一级指标", "二级指标", "三级指标", "指标值", "年度总体目标")


def _tabular_row(texts):
    """不带句读的行里并列两个以上金额字面、或有科目编码加名称、或以合计标签起头，才是表格行。"""
    if any(_SENTENCE_MARK.search(text) for text in texts):
        return False
    compact = [text.replace(" ", "") for text in texts]
    named = any(len(re.findall(r"[\u4e00-\u9fff]", text)) >= 2 and not re.search(r"\d", text) for text in compact)
    first = next((normalize_text(text) for text in compact if text), "")
    return (sum(bool(_AMOUNT_LITERAL.fullmatch(text)) for text in compact) >= 2
            or named and any(_SUBJECT_CODE.fullmatch(text) for text in compact)
            or first in _TOTAL_LABELS)


def unclassified_table_scope(table):
    """未归入九表的片段分三类记账，只有像表格却认不出表种的才进入待复核。

    - 没有任何表格行（见 _tabular_row）：正文段落、单位名单里的数字；
    - 按预算单位逐行列示的附加表、财政拨款支出预算表、项目明细表、项目绩效目标表：
      九表之外的附表，单独计数；
    - 其余：有表格结构却认不出表种，可能是漏识别或没接上的核心表续页。
    """
    literal = normalize_text("".join(str(value or "") for row in table.rows for value in row))
    if sum(marker in literal for marker in _PERFORMANCE_MARKERS) >= 2:
        return "auxiliary_performance_targets"
    if is_auxiliary_grant_expenditure_title(table.title):
        return "auxiliary_grant_expenditure"
    leading = {normalize_text(str(value or "")) for row in table.rows[:max(table.header_row_count, 2)] for value in row}
    if {"序号", "项目名称"} <= leading:
        return "auxiliary_project_detail"
    tabular = any(_tabular_row([str(value or "").strip() for value in row]) for row in table.rows)
    if not tabular:
        return "narrative_numbers"
    headers = [normalize_text(str(value or ""))
               for row in table.rows[:max(table.header_row_count, 2)] for value in row]
    if is_unit_breakdown_header(headers):
        return "auxiliary_unit_breakdown"
    return "unidentified_table"


def build_table_import_receipt(artifact, facts, *, org_name=None):
    from src.engine.fact_context import _column_mappings_for_instance, operand_rejection_reason
    from src.services.table_reading_assessment import assess_table_reading, qualified_cell

    by_source = {}
    arithmetic_by_instance = {}
    for fact in facts:
        for conflict in fact.extra_dims.get("column_arithmetic_conflicts", []):
            known = arithmetic_by_instance.setdefault(fact.instance_uid, [])
            if conflict not in known:
                known.append(conflict)
        for ref in fact.extra_dims.get("evidence_refs", []):
            if ref.get("source_cell_id"):
                by_source.setdefault((fact.instance_uid, ref["source_cell_id"]), []).append(fact)
    tables = []
    observed_ids = set()
    serial_proofs = _serial_lane_proofs(artifact)
    physical = {}
    for fragment in artifact.physical_fragments:
        rule = NINE_TABLE_RULES.get(fragment.table_code)
        original_mappings = canonical_column_mappings(fragment, rule) if rule else []
        for cell in fragment.cells:
            physical[source_cell_id(fragment, cell)] = (fragment, cell, original_mappings)
    for table in artifact.table_fragments:
        if table.extraction_method == "page_text_fallback":
            continue  # 正文提示没有物理金额格，不能重复计入金额分母。
        instance = next((i for i in artifact.logical_tables
                         if table.fragment_uid in i.fragment_uids or table.fragment_uid == i.instance_uid), None)
        rule = NINE_TABLE_RULES.get(table.table_code)
        mappings = (_column_mappings_for_instance(instance, table) if instance and rule
                    else canonical_column_mappings(table, rule) if rule else [])
        mapping_by_col = {m["source_col_idx"]: m for m in mappings}
        width_identity_safe = len({len(row) for row in table.rows if any(str(v or "").strip() for v in row)}) <= 1
        padding = native_bilateral_padding_positions(table)
        scope = "core" if rule else unclassified_table_scope(table)
        bound, unresolved, padding_cells, excluded, observations, out_of_scope = [], [], [], [], [], []
        present = columns_bound = admitted = conflicts = blocked = 0
        for cell in table.cells:
            if cell.numeric_value is None:
                continue
            cid = source_cell_id(table, cell)
            observed_ids.add(cid)
            observation = {"source_cell_id": cid, "raw_text": cell.raw_text,
                           "page": cell.page_number, "bbox": list(cell.bbox) if cell.bbox else None,
                           "row": cell.row_idx, "column": cell.col_idx,
                           "physical_row": cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx,
                           "physical_column": cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx,
                           **({"manual": True} if cell.extraction_method == "manual" else {})}
            # 排除判据必须回到物理列；不安全合并视图可能已把整数金额挪进“款/项”。
            source_table, source_cell, source_mappings = physical.get(cid, (table, cell, mappings))
            basis = _numeric_exclusion(source_table, source_cell, source_mappings, serial_proofs)
            if not basis and source_table.fragment_uid != table.fragment_uid:
                basis = _inherited_dimension_basis(table, cell, mappings, width_identity_safe)
            if not basis and table.extraction_method == "text" and cell.bbox:
                # 文字策略可把闭合网格连同附近正文再次投影；仅豁免同页同位置的唯一原格。
                duplicates = [(uid, original) for uid, (fragment, original, _) in physical.items()
                              if fragment.extraction_method in {"lines", "lines_strict"}
                              and original.page_number == cell.page_number and original.bbox
                              and normalize_text(original.raw_text) == normalize_text(cell.raw_text)
                              and original.bbox[0] <= (cell.bbox[0] + cell.bbox[2]) / 2 <= original.bbox[2]
                              and original.bbox[1] <= (cell.bbox[1] + cell.bbox[3]) / 2 <= original.bbox[3]]
                if len(duplicates) == 1:
                    basis = f"duplicate_text_projection_of_physical_cell:{duplicates[0][0]}"
            if not basis and scope == "narrative_numbers":
                basis = "no_tabular_amount_structure"
            if basis:
                excluded.append({**observation, "basis": basis})
                continue
            if scope.startswith("auxiliary_"):
                out_of_scope.append({**observation, "basis": f"{scope}_table"})
                continue
            if (cell.row_idx, cell.col_idx) in padding:
                padding_cells.append({**observation, "role": "source_padding",
                                      "basis": "native_bilateral_closing_grid"})
                continue
            matches = by_source.get((instance.instance_uid if instance else None, cid), [])
            mapping = mapping_by_col.get(cell.col_idx)
            # 人工改值后的事实仍绑定在这一格：按改值前读到的原文核对来源。
            exact = len(matches) == 1 and cell.raw_text.strip() in {
                matches[0].raw_value,
                *(item.get("raw_value") for item in matches[0].extra_dims.get("pre_correction_values", [])[:1])}
            source_consistent = (source_cell.raw_text == cell.raw_text
                                 and source_cell.page_number == cell.page_number
                                 and source_cell.bbox == cell.bbox)
            present += bool(matches)
            column_safe = bool(mapping) and width_identity_safe and all(
                f.extra_dims.get("column_identity_status", "bound") == "bound"
                and f.indicator == mapping["canonical_measure"] for f in matches)
            columns_bound += column_safe
            rejection = operand_rejection_reason(matches[0], org_name) if exact else None
            conflict = not source_consistent or bool(matches) and (not exact or bool(mapping) and any(
                f.indicator != mapping["canonical_measure"] for f in matches))
            conflicts += conflict
            blocked += bool(exact and rejection)
            reason = None
            if conflict:
                reason = "fact_binding_conflict"
            elif not mapping:
                reason = "amount_column_unmapped"
            elif not column_safe:
                reason = ("column_arithmetic_conflict" if any(
                    f.extra_dims.get("column_identity_status") == "arithmetic_conflict" for f in matches)
                    else "column_identity_unresolved")
            elif not exact:
                label_cols = mapping.get("source_label_cols", [])
                labels = [c for c in table.cells if c.row_idx == cell.row_idx
                          and c.col_idx in label_cols and c.raw_text.strip()]
                reason = "source_label_missing" if not labels else "fact_unbound"
            else:
                bound.append(cid)
                if rejection:
                    reason = "rule_admission_blocked"
                else:
                    admitted += 1
            observations.append({**observation, "expected_indicator": mapping["canonical_measure"] if mapping else None,
                                 "observation_kind": ("unclassified_numeric_projection" if not rule and table.extraction_method == "text"
                                                      else "fiscal_amount_candidate"),
                                 "fact_count": len(matches), "fact_uids": [f.fact_uid for f in matches],
                                 "column_identity_bound": column_safe,
                                 "rule_rejection": rejection, "reason": reason})
            if reason:
                unresolved.append(observations[-1])
        positions = {(c.row_idx, c.col_idx) for c in table.cells}
        grid_positions = {(r, c) for r, row in enumerate(table.rows) for c in range(len(row))}
        required_positions = {(r, c) for r, row in enumerate(table.rows)
                              for c, value in enumerate(row) if value is not None}
        arithmetic = arithmetic_by_instance.get(instance.instance_uid, []) if instance else []
        if scope == "narrative_numbers":
            mapping_status = "not_tabular"
        elif scope.startswith("auxiliary_"):
            mapping_status = "out_of_scope"
        elif unresolved or arithmetic:
            mapping_status = "mapping_incomplete"
        else:
            mapping_status = "complete" if rule else "no_unresolved_amounts"
        # 扫描表中未通过识别资格的非空格：整表区域要全部确认后才算读完。
        unqualified_ocr = [{"row": c.row_idx, "column": c.col_idx, "raw_text": c.raw_text, "page": c.page_number,
                            "bbox": list(c.bbox) if c.bbox else None, "is_header": c.is_header}
                           for c in table.cells if table.extraction_method == "ocr" and c.raw_text.strip()
                           and not qualified_cell(table, c)]
        tables.append({"fragment_uid": table.fragment_uid, "table_code": table.table_code,
                       "instance_uid": instance.instance_uid if instance else None,
                       "header_row_count": table.header_row_count, "extraction_method": table.extraction_method,
                       "bbox": list(table.bbox) if table.bbox else None,
                       "column_mappings": [{"source_col_idx": m["source_col_idx"],
                                            "canonical_measure": m["canonical_measure"],
                                            "mapping_source": m.get("mapping_source")} for m in mappings],
                       "unqualified_ocr_cells": unqualified_ocr,
                       "title": table.title, "page": table.page_number, "raw_cell_count": len(table.cells),
                       "matrix_cell_count": len(grid_positions),
                       "merged_placeholder_count": len(grid_positions - required_positions),
                       "matrix_complete": required_positions <= positions <= grid_positions,
                       "amount_column_count": len(mapping_by_col), "amount_cell_count": len(observations),
                       "fact_present_count": present, "fact_bound_count": len(bound),
                       "column_identity_bound_count": columns_bound, "fact_conflict_count": conflicts,
                       "rule_admitted_count": admitted, "rule_blocked_count": blocked,
                       "padding_cells": padding_cells, "excluded_numeric_cells": excluded,
                       "amount_cells": observations, "unresolved_money_cells": unresolved,
                       "out_of_scope_money_cells": out_of_scope, "scope": scope,
                       "mapping_status": mapping_status, "arithmetic_conflicts": arithmetic,
                       "classification_status": "identified" if rule else "retained_unclassified"})
    # 逻辑视图丢失的原物理金额也必须进回执。金额按来源身份检查，不能按同额反找。
    orphaned = []
    for table in artifact.physical_fragments:
        if table.extraction_method == "page_text_fallback":
            continue
        rule = NINE_TABLE_RULES.get(table.table_code)
        if not rule and unclassified_table_scope(table) != "unidentified_table":
            continue
        mappings = canonical_column_mappings(table, rule) if rule else []
        for cell in table.cells:
            cid = source_cell_id(table, cell)
            if cell.numeric_value is None or cid in observed_ids or _numeric_exclusion(table, cell, mappings, serial_proofs):
                continue
            orphaned.append({"source_cell_id": cid, "raw_text": cell.raw_text,
                             "page": cell.page_number, "bbox": list(cell.bbox) if cell.bbox else None,
                             "row": cell.row_idx, "column": cell.col_idx, "table_code": table.table_code,
                             "reason": "physical_amount_missing_from_logical_view", "fact_count": 0})
    unresolved = [dict(table_code=t["table_code"], fragment_uid=t["fragment_uid"], **cell)
                  for t in tables for cell in t["unresolved_money_cells"]] + orphaned
    reading_quality = assess_table_reading(artifact)
    reading_quality["unbound_money_cells"] = unresolved
    # 完整性闸门：核心表或认不出表种的表格，只要有金额没对应到列含义、或合计与分项系统性对不上，
    # 整张表标为映射不完整，相关检查不能视为已完成。
    mapping_incomplete = [{
        "fragment_uid": t["fragment_uid"], "table_code": t["table_code"], "title": t["title"], "page": t["page"],
        "scope": t["scope"], "unresolved_money_cell_count": len(t["unresolved_money_cells"]),
        "reasons": dict(Counter(c["reason"] for c in t["unresolved_money_cells"])),
        "arithmetic_conflicts": t["arithmetic_conflicts"],
    } for t in tables if t["mapping_status"] == "mapping_incomplete"]
    reading_quality["mapping_incomplete_tables"] = mapping_incomplete
    # 校正面板需要对照原表补录：只给有待处理项的表附文字矩阵，避免回执随整份材料膨胀。
    gap_tables = {uid for gap in reading_quality.get("gaps", []) for uid in gap.get("affected_fragment_uids", [])}
    by_uid = {t.fragment_uid: t for t in artifact.table_fragments}
    for entry in tables:
        source = by_uid.get(entry["fragment_uid"])
        if source is None or not (entry["unresolved_money_cells"] or entry["arithmetic_conflicts"]
                                  or entry["unqualified_ocr_cells"] or entry["fragment_uid"] in gap_tables):
            continue
        rows = source.rows[:200]
        entry["rows"] = [[str(value) if value is not None else None for value in row] for row in rows]
        # 每行在原页的位置（首个有坐标格所在页内的并集），补录证据据此落到原页区域。
        row_boxes: list = [None] * len(rows)
        for cell in source.cells:
            if not cell.bbox or cell.row_idx >= len(rows):
                continue
            known = row_boxes[cell.row_idx]
            if known is None:
                row_boxes[cell.row_idx] = {"page": cell.page_number, "bbox": list(cell.bbox)}
            elif known["page"] == cell.page_number:
                box = known["bbox"]
                known["bbox"] = [min(box[0], cell.bbox[0]), min(box[1], cell.bbox[1]),
                                 max(box[2], cell.bbox[2]), max(box[3], cell.bbox[3])]
        entry["row_boxes"] = row_boxes
    reading_quality["organization_status"] = "identified" if org_name else "unresolved"
    # 身份闸门：主体为空、科目名称只剩编码的事实，不能计为已完成入库。
    reading_quality["fact_identity_gaps"] = {
        "organization_missing": sum(not fact.org_name for fact in facts),
        "subject_name_is_code": sum(bool(re.fullmatch(r"[\d\s]+", fact.subject_name or "")) for fact in facts),
    }
    coverage = {
        "basis": "all_retained_numeric_cells_with_explicit_exclusions",
        "original_pdf_full_coverage_verified": False,
        "amount_cell_count": sum(t["amount_cell_count"] for t in tables) + len(orphaned),
        "identified_fiscal_amount_cell_count": sum(t["amount_cell_count"] for t in tables if t["classification_status"] == "identified"),
        "unclassified_numeric_projection_count": sum(c["observation_kind"] == "unclassified_numeric_projection"
                                                    for t in tables for c in t["amount_cells"]),
        "column_identity_bound_count": sum(t["column_identity_bound_count"] for t in tables),
        "unmapped_amount_cell_count": sum(c["reason"] == "amount_column_unmapped" for c in unresolved),
        "fact_present_count": sum(t["fact_present_count"] for t in tables),
        "fact_bound_count": sum(t["fact_bound_count"] for t in tables),
        "fact_conflict_count": sum(t["fact_conflict_count"] for t in tables),
        "rule_admitted_count": sum(t["rule_admitted_count"] for t in tables),
        "rule_blocked_count": sum(t["rule_blocked_count"] for t in tables),
        "pending_review_count": len(unresolved),
        "mapping_incomplete_table_count": len(mapping_incomplete),
        "column_arithmetic_conflict_count": sum(c["reason"] == "column_arithmetic_conflict" for c in unresolved),
        "out_of_scope_amount_cell_count": sum(len(t["out_of_scope_money_cells"]) for t in tables),
        "out_of_scope_table_count": sum(t["mapping_status"] == "out_of_scope" for t in tables),
    }
    coverage["fact_missing_count"] = coverage["amount_cell_count"] - coverage["fact_present_count"]
    coverage["column_identity_unresolved_count"] = coverage["amount_cell_count"] - coverage["column_identity_bound_count"]
    reading_quality["fiscal_coverage"] = coverage
    if unresolved:
        reading_quality["status"] = "partial"
    return {"original_pdf_full_coverage_verified": False,
            "physical_table_count": len(artifact.physical_fragments),
            "physical_cell_count": sum(len(t.cells) for t in artifact.physical_fragments),
            "source_grid_count": len(artifact.source_grids),
            "source_grid_cell_count": sum(len(t.cells) for t in artifact.source_grids),
            "logical_table_count": len(tables), "logical_cell_count": len(artifact.cells()),
            "matrix_complete": all(t["matrix_complete"] for t in tables),
            **coverage,
            "basis": "parser_output_and_storage_projection",
            "fiscal_coverage": coverage,
            "padding_cell_count": sum(len(t["padding_cells"]) for t in tables),
            "excluded_numeric_cell_count": sum(len(t["excluded_numeric_cells"]) for t in tables),
            "unresolved_money_cell_count": len(unresolved),
            "mapping_incomplete_tables": mapping_incomplete,
            "reading_quality": reading_quality,
            "orphaned_physical_amounts": orphaned,
            "unclassified_table_count": sum(t["classification_status"] == "retained_unclassified" for t in tables),
            "organization_status": "identified" if org_name else "unresolved",
            "tables": tables}
