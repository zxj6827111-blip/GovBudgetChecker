"""人工补录/确认单元格：把审核员对照原页填入的格值写回解析对象，再按常规链路生成事实。

只允许落在已有表格矩阵内的数据格；原生文字层已读出的金额不在这里改写（改值走事实校正）。
每个补录格在 processing_manifest["manual_cells"] 留下来源记录，事实与证据据此标注"人工补录"。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from typing import Any, Dict, Iterable, List

from src.services.fiscal_table_rules import normalize_text
from src.services.table_import_receipt import source_cell_id
from src.services.table_reading_assessment import number, valid_box

MANUAL_METHOD = "manual"
_SUFFIX = re.compile(r":cell-fill-v1:[0-9a-f]{64}")


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
            if not isinstance(change, dict) or change.get("field") != "cell_value":
                continue
            target_id = str(change.get("target_id") or "")
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            where = {"target_id": target_id, "record_id": record_id,
                     "row_idx": value.get("row_idx"), "col_idx": value.get("col_idx")}

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
                                      "source_cell_id": cid, "row_idx": row, "col_idx": col})
    if report["applied"]:
        artifact.processing_manifest["manual_cells"] = manual
        digest = hashlib.sha256(json.dumps(manual, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), default=str).encode()).hexdigest()
        # 补录值属于解析输入：同样的补录可复用缓存，改动补录必须产生新的解析运行身份。
        artifact.config_fingerprint = _SUFFIX.sub("", artifact.config_fingerprint or "") + f":cell-fill-v1:{digest}"
        artifact.parse_run_uid = hashlib.sha256("|".join((
            artifact.source_file_hash, artifact.parser_fingerprint,
            artifact.config_fingerprint, artifact.ocr_model_fingerprint or "",
        )).encode()).hexdigest()[:32]
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
            marked.append(dataclasses.replace(
                fact, recognition_status="verified" if fact.recognition_status == "unverified" else fact.recognition_status,
                extra_dims={**fact.extra_dims, "manual_fill": entry}))
            continue
        # 金额是原件读出的，但科目名称等行标签由人工补录：只标注来源，不改变识别状态。
        row_entry = next((rows[cid.rsplit("c", 1)[0]] for cid in cells if cid.rsplit("c", 1)[0] in rows), None)
        marked.append(fact if row_entry is None else dataclasses.replace(
            fact, extra_dims={**fact.extra_dims, "manual_fill": {**row_entry, "scope": "row_label"}}))
    return marked
