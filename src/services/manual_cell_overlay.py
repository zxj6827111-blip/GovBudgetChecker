"""人工补录/确认单元格：把审核员对照原页填入的格值写回解析对象，再按常规链路生成事实。

只允许落在已有表格矩阵内的数据格；原生文字层已读出的金额不在这里改写（改值走事实校正）。
每个补录格在 processing_manifest["manual_cells"] 留下来源记录，事实与证据据此标注"人工补录"。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional

from src.services.fiscal_table_rules import normalize_text
from src.services.table_import_receipt import source_cell_id
from src.services.table_reading_assessment import number, valid_box

MANUAL_METHOD = "manual"
_MANUAL_SUFFIX = "manual-overlay-v2"
_SUFFIX = re.compile(r":(?:cell-fill-v1|manual-overlay-v2):[0-9a-f]{64}")
# 表头词只在同一行命中两个及以上时拒绝；单个“单位/合计”可能是正常数据标签。
_HEADER_NAMES = {
    "项目", "科目编码", "科目名称", "功能分类科目编码", "功能分类科目名称",
    "单位", "金额单位", "预算数", "决算数", "本年收入合计", "财政拨款收入",
    "上级补助收入", "事业收入", "经营收入", "附属单位上缴收入", "其他收入",
    "本年支出合计", "基本支出", "项目支出",
}


def _looks_like_header(values: Iterable[str]) -> bool:
    hits = 0
    for value in values:
        if re.sub(r"\s+", "", str(value or "")) in _HEADER_NAMES:
            hits += 1
    return hits >= 2


def _row_cells(raw: Any) -> Optional[Dict[int, str]]:
    if not isinstance(raw, dict):
        return None
    cells = raw.get("cells")
    if not isinstance(cells, dict):
        return None
    parsed: Dict[int, str] = {}
    for key, value in cells.items():
        try:
            col = int(key)
        except (TypeError, ValueError):
            return None
        if col < 0 or col > 49 or isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return None
        text = str(value).strip()
        if len(text) > 100:
            return None
        parsed[col] = text
    return parsed if parsed and any(parsed.values()) else None


def _confirm_table_code(artifact: Any, table: Any, requested: Any) -> Optional[str]:
    from src.services.fiscal_table_rules import NINE_TABLE_RULES, is_unknown_table_code

    if requested in (None, ""):
        return None
    requested_code = str(requested).strip()
    current = str(table.table_code or "").strip()
    # 只拦“已识别表种”之间的改写；解析器未定表种（UNKNOWN_P1_T0 等）必须允许人工确认。
    if current and not is_unknown_table_code(current) and current != requested_code:
        return f"已识别表种 {current} 与提交表种 {requested_code} 不一致"
    if requested_code not in NINE_TABLE_RULES:
        return "提交的表种未登记"
    if current != requested_code:
        table.table_code = requested_code
        for instance in getattr(artifact, "logical_tables", []) or []:
            if table.fragment_uid in (getattr(instance, "fragment_uids", []) or []):
                instance.table_code = requested_code
    return None


def _restore_table_code(table: Any, previous_code: str, instances: List[Any]) -> None:
    """把表种恢复到确认前的状态。

    整行补录会先确认表种再校验金额；校验失败时若只返回不恢复，就会出现
    “回执说未应用、解析对象身份已被改掉”的不一致。
    """
    table.table_code = previous_code
    for instance, code in instances:
        instance.table_code = code


def _sync_row_cells(artifact: Any, table: Any, start: int, rows: List[Dict[int, str]],
                    entry: Dict[str, Any], record_id: str, record: Dict[str, Any],
                    change: Dict[str, Any], amount_columns: set[int], known_amount_columns: set[int],
                    manual: Dict[str, Any], report: Dict[str, Any]) -> None:
    from src.schemas.parse_artifact import ParsedCell

    width = max([len(row) for row in table.rows] + [max(cells) + 1 for cells in rows])
    for row in table.rows:
        row.extend([""] * (width - len(row)))
    kept_cells = [cell for cell in table.cells if cell.row_idx < start]
    table.cells = kept_cells
    by_position = {(cell.row_idx, cell.col_idx): cell for cell in kept_cells}
    scope = "table_fill" if str((change.get("value") or {}).get("mode") or "") == "replace_data_rows" else "row_fill"
    for offset, cells in enumerate(rows):
        row_idx = start + offset
        row_cids: List[str] = []
        values = [""] * width
        for col, text in cells.items():
            values[col] = text
            current = by_position.get((row_idx, col))
            if current is None:
                current = ParsedCell(
                    row_idx=row_idx, col_idx=col, raw_text="", normalized_text="", numeric_value=None,
                    page_number=entry["physical_page"] or table.page_number, bbox=entry["bbox"],
                    is_header=False, confidence=1.0, unit_hint=table.unit_hint,
                    extraction_method=MANUAL_METHOD)
            if not text:
                if current is not None:
                    table.cells.append(current)
                continue
            previous = manual.get(source_cell_id(table, current), {}).get("previous_text", current.raw_text)
            filled = _filled(current, text, entry)
            table.cells.append(filled)
            cid = source_cell_id(table, filled)
            manual[cid] = {
                "record_id": record_id, "actor": str(record.get("actor") or ""),
                "reason": str(record.get("reason") or ""), "raw_value": text,
                "previous_text": previous, "created_at": record.get("created_at"),
                "evidence_ids": [str(item) for item in change.get("evidence_ids") or []],
                "scope": scope,
                "column_identity_confirmed": (
                    col not in known_amount_columns or col in amount_columns),
            }
            row_cids.append(cid)
        if row_idx < len(table.rows):
            table.rows[row_idx] = values
        else:
            table.rows.append(values)
        report["targets"].append({"target_id": table.fragment_uid, "record_id": record_id,
                                  "source_cell_ids": row_cids, "row_idx": row_idx,
                                  "field": "table_rows"})


def _apply_table_identity(artifact: Any, table: Any, record_id: str, record: Dict[str, Any],
                          change: Dict[str, Any], report: Dict[str, Any]) -> None:
    value = change.get("value") if isinstance(change.get("value"), dict) else {}
    code = value.get("table_code")
    where = {"target_id": table.fragment_uid, "record_id": record_id, "row_idx": None, "col_idx": None,
             "field": "table_identity"}
    if not isinstance(code, str) or not code.strip():
        report["unresolved"].append({**where, "reason": "表种确认需要 table_code"})
        return
    error = _confirm_table_code(artifact, table, code.strip())
    if error:
        report["unresolved"].append({**where, "reason": error})
        return
    location = change.get("location") if isinstance(change.get("location"), dict) else {}
    identities = dict(artifact.processing_manifest.get("manual_table_identity") or {})
    identities[table.fragment_uid] = {
        "record_id": record_id, "actor": str(record.get("actor") or ""),
        "reason": str(record.get("reason") or ""), "table_code": str(table.table_code or ""),
        "original_header_missing": bool(value.get("original_header_missing")),
        "created_at": record.get("created_at"),
        "evidence_ids": [str(item) for item in change.get("evidence_ids") or []],
        "location": {"physical_page": location.get("physical_page"),
                     "bbox": list(valid_box(location.get("bbox"))) if valid_box(location.get("bbox")) else None},
    }
    artifact.processing_manifest["manual_table_identity"] = identities
    report["applied"] += 1
    report["targets"].append({"target_id": table.fragment_uid, "record_id": record_id,
                              "source_cell_id": None, "row_idx": None, "col_idx": None,
                              "field": "table_identity"})


def _apply_table_rows(artifact: Any, table: Any, record_id: str, record: Dict[str, Any],
                      change: Dict[str, Any], report: Dict[str, Any], manual: Dict[str, Any]) -> None:
    value = change.get("value") if isinstance(change.get("value"), dict) else {}
    mode = str(value.get("mode") or "").strip()
    where = {"target_id": table.fragment_uid, "record_id": record_id, "row_idx": None, "col_idx": None,
             "field": "table_rows"}
    if mode not in {"append_rows", "replace_data_rows"}:
        report["unresolved"].append({**where, "reason": "整行补录必须指定 append_rows 或 replace_data_rows"})
        return
    raw_rows = value.get("rows")
    if not isinstance(raw_rows, list) or not 1 <= len(raw_rows) <= 300:
        report["unresolved"].append({**where, "reason": "整行补录需要 1-300 行数据"})
        return
    rows: List[Dict[int, str]] = []
    for raw in raw_rows:
        cells = _row_cells(raw)
        if cells is None:
            report["unresolved"].append({**where, "reason": "每一行都需要非空的 cells 对象"})
            return
        if _looks_like_header(cells.values()):
            report["unresolved"].append({**where, "reason": "表头行不接受补录；原件缺表头只能确认缺失，不能替原件补列名"})
            return
        rows.append(cells)
    # 表种确认会就地改写 table 与逻辑实例；后续校验若失败必须整体回滚，
    # 否则回执报“未应用”而解析对象身份已经变了。快照必须在确认之前取。
    previous_code = str(table.table_code or "")
    previous_instances = [
        (instance, str(instance.table_code or ""))
        for instance in getattr(artifact, "logical_tables", []) or []
        if table.fragment_uid in (getattr(instance, "fragment_uids", []) or [])
    ]
    code_error = _confirm_table_code(artifact, table, value.get("table_code"))
    if code_error:
        report["unresolved"].append({**where, "reason": code_error})
        return
    amount_columns = {m.get("source_col_idx") for m in _instance_mappings(artifact, table)}
    # 未映射但已有数字数据的列也按金额列校验，避免整行补录把文字塞进金额列。
    known_amount_columns = set(amount_columns)
    for row in table.rows:
        for col, cell_text in enumerate(row):
            # 前两列是科目编码/名称，编码本身也是数字，不能据此当成金额列。
            if col >= 2 and number(cell_text) is not None:
                known_amount_columns.add(col)
    for cells in rows:
        for col, text in cells.items():
            if col in known_amount_columns and text and number(text) is None:
                _restore_table_code(table, previous_code, previous_instances)
                report["unresolved"].append({**where, "reason": "金额列只能补录数字"})
                return
    location = change.get("location") if isinstance(change.get("location"), dict) else {}
    entry = {"physical_page": location.get("physical_page") if isinstance(location.get("physical_page"), int) else None,
             "bbox": tuple(valid_box(location.get("bbox"))) if valid_box(location.get("bbox")) else None}
    start = len(table.rows) if mode == "append_rows" else min(table.header_row_count, len(table.rows))
    if mode == "replace_data_rows":
        table.rows = table.rows[:start]
    _sync_row_cells(artifact, table, start, rows, entry, record_id, record, change,
                    amount_columns, known_amount_columns, manual, report)
    row_indices = list(range(start, start + len(rows)))
    manual_rows = dict(artifact.processing_manifest.get("manual_rows") or {})
    manual_rows[f"{table.fragment_uid}:{row_indices[0]}:{record_id}"] = {
        "record_id": record_id, "actor": str(record.get("actor") or ""),
        "reason": str(record.get("reason") or ""), "mode": mode,
        "row_indices": row_indices, "table_code": str(table.table_code or ""),
        "original_header_missing": bool(value.get("original_header_missing")),
        "created_at": record.get("created_at"),
        "evidence_ids": [str(item) for item in change.get("evidence_ids") or []],
        "location": {"physical_page": entry["physical_page"], "bbox": list(entry["bbox"]) if entry["bbox"] else None},
    }
    artifact.processing_manifest["manual_rows"] = manual_rows
    report["applied"] += len(rows)


def _instance_mappings(artifact: Any, table: Any) -> List[Dict[str, Any]]:
    from src.engine.fact_context import _column_mappings_for_instance
    from src.services.fiscal_column_identity import canonical_column_mappings
    from src.services.fiscal_table_rules import NINE_TABLE_RULES

    rule = NINE_TABLE_RULES.get(table.table_code)
    if rule is None:
        return []
    instance = next((item for item in artifact.logical_tables
                     if table.fragment_uid in item.fragment_uids or table.fragment_uid == item.instance_uid), None)
    return _column_mappings_for_instance(instance, table) if instance else canonical_column_mappings(table, rule)


def _filled(cell: Any, text: str, entry: Dict[str, Any]) -> Any:
    value = number(text)
    return dataclasses.replace(
        cell, raw_text=text, normalized_text=normalize_text(text),
        numeric_value=float(value) if value is not None else None, normalized_value_wan=None,
        confidence=1.0, extraction_method=MANUAL_METHOD, ocr_provenance={}, native_text_sources=[],
        native_blank_verified=False, bbox=entry.get("bbox") or cell.bbox,
        page_number=entry.get("physical_page") or cell.page_number)


def apply_table_cell_overlays(artifact: Any, records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply audited ``cell_value`` corrections to the logical (and matching physical) cells."""
    from src.schemas.parse_artifact import ParsedCell

    report: Dict[str, Any] = {"applied": 0, "unresolved": [], "targets": []}
    if artifact is None:
        return report
    tables = {table.fragment_uid: table for table in artifact.table_fragments}
    physical = {source_cell_id(fragment, cell): (fragment, index)
                for fragment in artifact.physical_fragments for index, cell in enumerate(fragment.cells)}
    manual = dict(artifact.processing_manifest.get("manual_cells") or {})
    for record in records or []:
        if not isinstance(record, dict):
            continue
        record_id = str(record.get("record_id") or "")
        for change in record.get("changes") if isinstance(record.get("changes"), list) else []:
            if not isinstance(change, dict):
                continue
            target_id = str(change.get("target_id") or "")
            if change.get("field") in {"table_rows", "table_identity"}:
                table = tables.get(target_id)
                if table is None:
                    report["unresolved"].append({
                        "target_id": target_id, "record_id": record_id,
                        "row_idx": None, "col_idx": None, "field": change["field"],
                        "reason": "补录目标表不在当前解析结果中"})
                elif change.get("field") == "table_identity":
                    _apply_table_identity(artifact, table, record_id, record, change, report)
                else:
                    _apply_table_rows(artifact, table, record_id, record, change, report, manual)
                continue
            if change.get("field") != "cell_value":
                continue
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            where = {"target_id": target_id, "record_id": record_id,
                     "row_idx": value.get("row_idx"), "col_idx": value.get("col_idx"),
                     "field": "cell_value"}

            table = tables.get(target_id)
            if table is None:
                report["unresolved"].append({**where, "reason": "补录目标表不在当前解析结果中"})
                continue
            row, col, text = value.get("row_idx"), value.get("col_idx"), str(value.get("raw_value") or "").strip()
            if not isinstance(row, int) or not isinstance(col, int) or not 0 <= row < len(table.rows) \
                    or not 0 <= col < len(table.rows[row]):
                report["unresolved"].append({**where, "reason": "补录位置超出表格矩阵"})
                continue
            if row < table.header_row_count:
                report["unresolved"].append({**where, "reason": "表头行不接受补录"})
                continue
            amount_columns = {m.get("source_col_idx") for m in _instance_mappings(artifact, table)}
            if col in amount_columns and number(text) is None:
                report["unresolved"].append({**where, "reason": "金额列只能补录数字"})
                continue
            index = next((i for i, cell in enumerate(table.cells) if (cell.row_idx, cell.col_idx) == (row, col)), None)
            current = table.cells[index] if index is not None else None
            if (current is not None and current.raw_text.strip() and current.extraction_method not in {"ocr", MANUAL_METHOD}
                    and number(current.raw_text) is not None):
                report["unresolved"].append({**where, "reason": "该格已有文字层读出的金额，改值请走事实校正"})
                continue
            location = change.get("location") if isinstance(change.get("location"), dict) else {}
            entry = {"physical_page": location.get("physical_page") if isinstance(location.get("physical_page"), int) else None,
                     "bbox": tuple(valid_box(location.get("bbox"))) if valid_box(location.get("bbox")) else None}
            if current is None:
                current = ParsedCell(row_idx=row, col_idx=col, raw_text="", normalized_text="", numeric_value=None,
                                     page_number=entry["physical_page"] or table.page_number, bbox=entry["bbox"],
                                     is_header=False, confidence=1.0, unit_hint=table.unit_hint,
                                     extraction_method=MANUAL_METHOD)
            # 同一格多次补录时保留系统最初读到的内容，补录结果与应用次数无关。
            previous = manual.get(source_cell_id(table, current), {}).get("previous_text", current.raw_text)
            filled = _filled(current, text, entry)
            if index is None:
                table.cells.append(filled)
            else:
                table.cells[index] = filled
            table.rows[row][col] = text
            cid = source_cell_id(table, filled)
            if cid in physical:
                fragment, at = physical[cid]
                if fragment is not table:
                    fragment.cells[at] = _filled(fragment.cells[at], text, entry)
                    pr, pc = fragment.cells[at].row_idx, fragment.cells[at].col_idx
                    if pr < len(fragment.rows) and pc < len(fragment.rows[pr]):
                        fragment.rows[pr][pc] = text
            manual[cid] = {"record_id": record_id, "actor": str(record.get("actor") or ""),
                           "reason": str(record.get("reason") or ""), "raw_value": text,
                           "previous_text": previous, "created_at": record.get("created_at"),
                           "evidence_ids": [str(item) for item in change.get("evidence_ids") or []]}
            report["applied"] += 1
            report["targets"].append({"target_id": target_id, "record_id": record_id,
                                      "source_cell_id": cid, "row_idx": row, "col_idx": col,
                                      "field": "cell_value"})
    if report["applied"]:
        artifact.processing_manifest["manual_cells"] = manual
        digest_payload = {
            "cells": manual,
            "rows": artifact.processing_manifest.get("manual_rows") or {},
            "identities": artifact.processing_manifest.get("manual_table_identity") or {},
        }
        digest = hashlib.sha256(json.dumps(digest_payload, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), default=str).encode()).hexdigest()
        # 补录值属于解析输入：同样的补录可复用缓存，改动补录必须产生新的解析运行身份。
        artifact.config_fingerprint = _SUFFIX.sub("", artifact.config_fingerprint or "") + f":{_MANUAL_SUFFIX}:{digest}"
        from src.services.input_processing_identity import refresh_parse_identity
        refresh_parse_identity(artifact)
    # 按校正字段分组：主链路要分别呈现“格/行补录”和“表种确认”的结果，
    # 否则只提交表种确认时，回执会错误地写成 not_requested。
    report["by_field"] = {
        field: {
            "applied": sum(1 for target in report["targets"] if target.get("field") == field),
            "unresolved": [item for item in report["unresolved"] if item.get("field") == field],
        }
        for field in ("cell_value", "table_rows", "table_identity")
    }
    return report


def mark_manual_facts(artifact: Any, facts: List[Any]) -> List[Any]:
    """Tag facts read from manual cells; a clean manual literal counts as human-verified."""
    manual = (getattr(artifact, "processing_manifest", {}) or {}).get("manual_cells") or {}
    if not manual:
        return facts
    # 行键 = 来源格去掉列号；只有补录的是文字（科目名称、编码说明等）才算行标签补录。
    rows = {cid.rsplit("c", 1)[0]: entry for cid, entry in manual.items() if number(entry.get("raw_value")) is None}
    marked = []
    for fact in facts:
        cells = [ref.get("source_cell_id") for ref in fact.extra_dims.get("evidence_refs") or [] if ref.get("source_cell_id")]
        entry = next((manual[cid] for cid in cells if cid in manual), None)
        if entry is not None:
            # 行身份、列身份或读取缺口仍未解决时保持原状态，不因人工填了数字就放行。
            column_unconfirmed = entry.get("column_identity_confirmed") is False
            marked.append(dataclasses.replace(
                fact, recognition_status=(
                    "verified" if fact.recognition_status == "unverified" and not column_unconfirmed
                    else fact.recognition_status),
                extra_dims={**fact.extra_dims, "manual_fill": entry}))
            continue
        # 金额是原件读出的，但科目名称等行标签由人工补录：只标注来源，不改变识别状态。
        row_entry = next((rows[cid.rsplit("c", 1)[0]] for cid in cells if cid.rsplit("c", 1)[0] in rows), None)
        marked.append(fact if row_entry is None else dataclasses.replace(
            fact, extra_dims={**fact.extra_dims, "manual_fill": {**row_entry, "scope": "row_label"}}))
    return marked
