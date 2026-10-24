"""统一解析产物的持久化与读取（ZCode 整改 ZC-01）。

职责：
- 把 :class:`ParseArtifact` 原子落库（parse_runs / parse_table_instances /
  parse_table_cells / parse_facts / parse_evidence）；
- 解析缓存：按（文档版本 + 原件哈希 + 解析器指纹 + 配置指纹 + OCR 指纹）
  命中已完成的解析运行，直接从库内重建 artifact，不重开 PDF；
- 幂等：同版本重复执行写入同一 parse_run（ON CONFLICT DO NOTHING），
  不产生重复行、不删除历史。

旧表（fiscal_table_cells 等）的兼容写入仍在原服务内完成——旧表保留
历史读取，新存储才是唯一权威来源（任务包 §二.1：数据库解析对象通过
适配器转换，避免第三套独立解析语义）。
"""

from __future__ import annotations

import json
import hashlib
import re
import logging
import math
from dataclasses import replace
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

import asyncpg

from src.schemas.parse_artifact import (
    EvidenceRef,
    FiscalFact,
    LogicalTableInstance,
    OcrTableCandidate,
    ParseArtifact,
    ParsePageState,
    ParsedCell,
    ParsedTable,
    ParsedTextBlock,
)

logger = logging.getLogger(__name__)


def artifact_payload_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _restore_snapshot(run) -> Optional[ParseArtifact]:
    """金额/文字/合并占位/物理来源均来自原始快照，不由 SQL 格子猜回表头。"""
    if run.get("artifact_snapshot") is None:
        version = re.match(r"pdfplumber-cells-v(\d+)", str(run.get("parser_fingerprint") or ""))
        if version and int(version[1]) >= 17:
            raise ValueError("complete parse snapshot missing")
        return None  # 旧运行保留兼容读取；新解析指纹会重新建立完整快照。
    payload = _decode_jsonb(run["artifact_snapshot"], dict, field="artifact_snapshot")
    if not run.get("artifact_snapshot_hash") or artifact_payload_hash(payload) != run["artifact_snapshot_hash"]:
        raise ValueError("complete parse snapshot hash mismatch")
    for field in ("source_file_hash", "parse_run_uid", "parser_name", "parser_fingerprint",
                  "config_fingerprint", "ocr_model_fingerprint", "page_count"):
        if payload.get(field) != run.get(field):
            raise ValueError(f"complete parse snapshot identity mismatch: {field}")
    artifact = ParseArtifact.from_dict(payload)
    if artifact.to_dict() != payload:
        raise ValueError("complete parse snapshot roundtrip mismatch")
    return artifact


def _bbox_to_json(bbox) -> Optional[str]:
    if not bbox:
        return None
    return json.dumps([float(item) for item in bbox])


def _json(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode_jsonb(value: Any, expected_type: type, *, field: str) -> Any:
    """Handle both asyncpg's default JSON text and a configured JSON codec.

    JSON null/SQL NULL means an empty collection. Other scalar/collection types
    are corruption, not empty metadata: fail the cache read instead of silently
    dropping source provenance.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid JSONB for {field}") from exc
    if value is None:
        return expected_type()
    if not isinstance(value, expected_type):
        raise ValueError(f"invalid JSONB object type for {field}: expected {expected_type.__name__}")
    return value


def _decode_bbox(value: Any, *, field: str) -> Optional[tuple]:
    if value is None or value == "null":
        return None
    values = _decode_jsonb(value, list, field=field)
    try:
        box = tuple(float(item) for item in values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid JSONB coordinates for {field}") from exc
    if len(box) != 4 or not all(math.isfinite(item) for item in box):
        raise ValueError(f"invalid JSONB coordinates for {field}")
    return box


async def find_cached_parse_run(
    conn: asyncpg.Connection,
    *,
    document_version_id: int,
    source_file_hash: str,
    parser_fingerprint: str,
    config_fingerprint: str,
    ocr_model_fingerprint: Optional[str],
) -> Optional[int]:
    """按缓存键查找已完成的解析运行；命中则不重开 PDF。"""
    row = await conn.fetchrow(
        """
        SELECT id FROM parse_runs
        WHERE document_version_id = $1
          AND source_file_hash = $2
          AND parser_fingerprint = $3
          AND config_fingerprint = $4
          AND COALESCE(ocr_model_fingerprint, '') = COALESCE($5, '')
          AND status = 'completed'
        ORDER BY id DESC
        LIMIT 1
        """,
        document_version_id,
        source_file_hash,
        parser_fingerprint,
        config_fingerprint,
        ocr_model_fingerprint,
    )
    return int(row["id"]) if row else None


async def persist_parse_artifact(
    conn: asyncpg.Connection,
    artifact: ParseArtifact,
    *,
    document_version_id: int,
    org_name: Optional[str] = None,
    fiscal_year: Optional[int] = None,
) -> int:
    """把解析产物写入统一存储，返回 parse_run_id。

    幂等性：同（parse_run_uid）重复执行时行级 DO NOTHING；
    缓存命中（同指纹已完成运行）由调用方先用 :func:`find_cached_parse_run`
    判断，避免重复解析。
    """
    from src.utils.report_year import parse_report_year

    fiscal_year = parse_report_year(fiscal_year)
    snapshot = artifact.to_dict()
    snapshot_hash = artifact_payload_hash(snapshot)
    parse_run_id: Optional[int] = None
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO parse_runs (
                parse_run_uid, document_version_id, source_file_hash,
                parser_name, parser_fingerprint, config_fingerprint,
                ocr_model_fingerprint, page_count, page_states, text_blocks,
                ocr_table_candidates, coordinate_space, coordinate_transforms,
                artifact_snapshot, artifact_snapshot_hash, status
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, 'completed')
            ON CONFLICT (parse_run_uid) DO UPDATE
                SET parse_run_uid = EXCLUDED.parse_run_uid
            RETURNING id, artifact_snapshot_hash
            """,
            artifact.parse_run_uid,
            document_version_id,
            artifact.source_file_hash,
            artifact.parser_name,
            artifact.parser_fingerprint,
            artifact.config_fingerprint,
            artifact.ocr_model_fingerprint,
            artifact.page_count,
            _json([page.to_dict() for page in artifact.page_states]),
            _json([block.to_dict() for block in artifact.text_blocks]),
            _json([table.to_dict() for table in artifact.ocr_table_candidates]),
            artifact.coordinate_space,
            _json(artifact.coordinate_transforms),
            _json(snapshot),
            snapshot_hash,
        )
        parse_run_id = int(row["id"])
        if row["artifact_snapshot_hash"] != snapshot_hash:
            raise ValueError("parse_run_uid already binds a different or legacy artifact")

        for instance in artifact.logical_tables:
            await conn.execute(
                """
                INSERT INTO parse_table_instances (
                    parse_run_id, instance_uid, document_version_id, table_code,
                    title, org_name, fiscal_year, fund_scope,
                    page_start, page_end, row_count, confidence,
                    column_mappings, identity_conflict, conflict_reasons
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                ON CONFLICT (parse_run_id, instance_uid) DO NOTHING
                """,
                parse_run_id,
                instance.instance_uid,
                document_version_id,
                instance.table_code,
                instance.title,
                instance.org_name or org_name,
                parse_report_year(instance.fiscal_year) or fiscal_year,
                instance.fund_scope,
                instance.page_start,
                instance.page_end,
                instance.row_count,
                instance.confidence,
                _json(instance.column_mappings),
                instance.identity_conflict,
                _json(instance.conflict_reasons),
            )

        physical_by_uid = {
            str(fragment.fragment_uid): fragment
            for fragment in (artifact.physical_fragments or artifact.table_fragments)
            if fragment.fragment_uid
        }
        for instance in artifact.logical_tables:
            for fragment_uid in dict.fromkeys(instance.fragment_uids or [instance.instance_uid]):
                fragment = physical_by_uid.get(str(fragment_uid))
                if fragment is None:
                    continue
                await conn.execute(
                    """
                    INSERT INTO parse_table_fragments (
                        parse_run_id, instance_uid, fragment_uid, table_code, title,
                        page_number, table_index, bbox, header_row_count, confidence,
                        extraction_method, unit_hint, raw_rows, raw_cells
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    ON CONFLICT (parse_run_id, instance_uid, fragment_uid) DO NOTHING
                    """,
                    parse_run_id,
                    instance.instance_uid,
                    str(fragment_uid),
                    fragment.table_code,
                    fragment.title,
                    fragment.page_number,
                    fragment.table_index,
                    _bbox_to_json(fragment.bbox),
                    fragment.header_row_count,
                    fragment.confidence,
                    fragment.extraction_method,
                    fragment.unit_hint,
                    _json(fragment.rows),
                    _json([cell.to_dict() for cell in fragment.cells]),
                )

        for fragment, cell in artifact.cells():
            instance_uid = _instance_uid_for(artifact, fragment)
            await conn.execute(
                """
                INSERT INTO parse_table_cells (
                    parse_run_id, instance_uid, row_idx, col_idx,
                    raw_text, normalized_text, numeric_value, normalized_value_wan,
                    unit_hint, presence, page_number, bbox, is_header,
                    confidence, extraction_method, row_span, col_span,
                    origin_fragment_uid, origin_row_idx, ocr_provenance, origin_col_idx
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21)
                ON CONFLICT (parse_run_id, instance_uid, row_idx, col_idx) DO NOTHING
                """,
                parse_run_id,
                instance_uid,
                cell.row_idx,
                cell.col_idx,
                cell.raw_text,
                cell.normalized_text,
                cell.numeric_value,
                str(cell.normalized_value_wan) if cell.normalized_value_wan is not None else None,
                cell.unit_hint,
                cell.presence,
                cell.page_number,
                _bbox_to_json(cell.bbox),
                cell.is_header,
                cell.confidence,
                cell.extraction_method,
                cell.row_span,
                cell.col_span,
                cell.origin_fragment_uid,
                cell.origin_row_idx,
                _json(cell.ocr_provenance),
                cell.origin_col_idx,
            )

        for block in artifact.text_blocks:
            evidence = EvidenceRef(
                file_hash=artifact.source_file_hash,
                physical_page=block.page_number,
                source_block_id=block.block_uid,
                quote=block.text,
                bbox=block.bbox,
                source_method=block.extraction_method,
                verification_status="unverified",
                location_level="exact_span" if block.bbox else "unresolved",
            )
            await conn.execute(
                """
                INSERT INTO parse_evidence (
                    parse_run_id, evidence_id, file_hash, physical_page,
                    source_block_id, quote, bbox, source_method,
                    verification_status, location_level
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (parse_run_id, evidence_id) DO NOTHING
                """,
                parse_run_id,
                evidence.evidence_id,
                evidence.file_hash,
                evidence.physical_page,
                evidence.source_block_id,
                evidence.quote,
                _bbox_to_json(evidence.bbox),
                evidence.source_method,
                evidence.verification_status,
                evidence.location_level,
            )

    return parse_run_id


def _instance_uid_for(artifact: ParseArtifact, fragment: ParsedTable) -> str:
    for instance in artifact.logical_tables:
        if fragment.fragment_uid in instance.fragment_uids:
            return instance.instance_uid
    return fragment.fragment_uid


async def persist_facts_and_evidence(
    conn: asyncpg.Connection,
    *,
    parse_run_id: int,
    facts: Sequence[FiscalFact],
    file_hash: str,
) -> int:
    """把 FiscalFact 与其证据引用落库；返回写入条数。幂等（DO NOTHING）。

    ZC-01（AC-08）：证据按 ``extra_dims["evidence_refs"]`` 中的完整
    EvidenceRef 实体写入——物理页、bbox、来源单元格、定位等级与
    验证状态原样落库；旧格式（只有 evidence_ids）退回 quote+unverified。
    """
    written = 0
    async with conn.transaction():
        for fact in facts:
            evidence_refs: List[Dict[str, Any]] = []
            raw_refs = (fact.extra_dims or {}).get("evidence_refs")
            if isinstance(raw_refs, list):
                evidence_refs = [dict(item) for item in raw_refs if isinstance(item, dict)]

            evidence_ids = list(fact.evidence_ids)
            if evidence_refs:
                # 以事实声明的 evidence_ids 为准，补齐丢失的引用实体
                known = {str(ref.get("evidence_id") or "") for ref in evidence_refs}
                for evidence_id in evidence_ids:
                    if evidence_id not in known:
                        evidence_refs.append(
                            {
                                "evidence_id": evidence_id,
                                "file_hash": file_hash,
                                "quote": fact.raw_value or "",
                                "verification_status": "unverified",
                                "location_level": "unresolved",
                            }
                        )
            for ref in evidence_refs:
                bbox_value = ref.get("bbox")
                await conn.execute(
                    """
                    INSERT INTO parse_evidence (
                        parse_run_id, evidence_id, file_hash, physical_page,
                        printed_page, source_cell_id, source_block_id, quote,
                        bbox, source_method, verification_status, location_level, source_provenance
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8,
                        $9::jsonb, $10, $11, $12, $13::jsonb
                    )
                    ON CONFLICT (parse_run_id, evidence_id) DO NOTHING
                    """,
                    parse_run_id,
                    str(ref.get("evidence_id") or ""),
                    str(ref.get("file_hash") or file_hash),
                    ref.get("physical_page"),
                    ref.get("printed_page"),
                    ref.get("source_cell_id"),
                    ref.get("source_block_id"),
                    str(ref.get("quote") if ref.get("quote") is not None else fact.raw_value or ""),
                    json.dumps(list(bbox_value), ensure_ascii=False) if bbox_value else None,
                    str(ref.get("source_method") or "native_text"),
                    str(ref.get("verification_status") or "unverified"),
                    str(ref.get("location_level") or "unresolved"),
                    _json(ref.get("source_provenance") or {}),
                )
            await conn.execute(
                """
                INSERT INTO parse_facts (
                    parse_run_id, fact_uid, instance_uid, table_code, indicator,
                    org_name, org_scope, fiscal_year, period, fund_scope, budget_stage,
                    subject_code, subject_name, classification_level, parent_subject_code,
                    hierarchy_path, raw_value, unit, display_precision, normalized_value,
                    recognition_status, confidence, evidence_ids, extra_dims, row_order
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                    $12, $13, $14, $15, $16, $17, $18, $19, $20,
                    $21, $22, $23, $24, $25
                )
                ON CONFLICT (parse_run_id, fact_uid) DO NOTHING
                """,
                parse_run_id,
                fact.fact_uid,
                fact.instance_uid,
                fact.table_code,
                fact.indicator,
                fact.org_name,
                fact.org_scope,
                fact.fiscal_year,
                fact.period,
                fact.fund_scope,
                fact.budget_stage,
                fact.subject_code,
                fact.subject_name,
                fact.classification_level,
                fact.parent_subject_code,
                _json(fact.hierarchy_path),
                fact.raw_value,
                fact.unit,
                fact.display_precision,
                str(fact.normalized_value) if fact.normalized_value is not None else None,
                fact.recognition_status,
                fact.confidence,
                _json(evidence_ids),
                _json(fact.extra_dims),
                fact.row_order,
            )
            written += 1
    return written


async def load_parse_artifact(conn: asyncpg.Connection, parse_run_id: int) -> ParseArtifact:
    """从统一存储重建 ParseArtifact（缓存命中路径，不重开 PDF）。"""
    run = await conn.fetchrow("SELECT * FROM parse_runs WHERE id = $1", parse_run_id)
    if run is None:
        raise ValueError(f"parse_run not found: {parse_run_id}")

    snapshot = _restore_snapshot(run)
    if snapshot is not None:
        # 快照和可查询投影必须都完整。只对数量做检查不够：逐格对照文字、
        # 原始金额、来源位置和表头角色，防止静默少写或覆盖。
        stored = await conn.fetch("SELECT * FROM parse_table_cells WHERE parse_run_id = $1", parse_run_id)
        expected = {(_instance_uid_for(snapshot, table), cell.row_idx, cell.col_idx): cell
                    for table, cell in snapshot.cells()}
        if len(expected) != len(snapshot.cells()) or len(stored) != len(expected):
            raise ValueError("complete parse cell projection count mismatch")
        for row in stored:
            cell = expected.pop((row["instance_uid"], row["row_idx"], row["col_idx"]), None)
            if cell is None or any(row[field] != getattr(cell, field) for field in (
                "raw_text", "normalized_text", "numeric_value", "page_number", "is_header",
                "row_span", "col_span", "unit_hint", "origin_fragment_uid", "origin_row_idx", "origin_col_idx",
            )) or _decode_bbox(row["bbox"], field="parse_table_cells.bbox") != cell.bbox:
                raise ValueError("complete parse cell projection content mismatch")
        return snapshot

    page_states = [
        ParsePageState.from_dict(item)
        for item in _decode_jsonb(run["page_states"], list, field="page_states")
    ]
    text_blocks = [
        ParsedTextBlock.from_dict(item)
        for item in _decode_jsonb(run["text_blocks"], list, field="text_blocks")
    ]
    ocr_table_candidates = [
        OcrTableCandidate.from_dict(item)
        for item in _decode_jsonb(run["ocr_table_candidates"], list, field="ocr_table_candidates")
    ]
    instance_rows = await conn.fetch(
        "SELECT * FROM parse_table_instances WHERE parse_run_id = $1 ORDER BY id",
        parse_run_id,
    )
    physical_fragment_rows = await conn.fetch(
        "SELECT * FROM parse_table_fragments WHERE parse_run_id = $1 ORDER BY id",
        parse_run_id,
    )
    physical_meta_by_instance: Dict[str, Dict[str, Any]] = {}
    for row in physical_fragment_rows:
        physical_meta_by_instance.setdefault(str(row["instance_uid"]), {})[
            str(row["fragment_uid"])
        ] = dict(row)
    cell_rows = await conn.fetch(
        """
        SELECT * FROM parse_table_cells WHERE parse_run_id = $1
        ORDER BY instance_uid, row_idx, col_idx
        """,
        parse_run_id,
    )

    cells_by_instance: Dict[str, List[ParsedCell]] = {}
    for row in cell_rows:
        cells_by_instance.setdefault(row["instance_uid"], []).append(
            ParsedCell(
                row_idx=row["row_idx"],
                col_idx=row["col_idx"],
                raw_text=row["raw_text"] or "",
                normalized_text=row["normalized_text"] or "",
                numeric_value=row["numeric_value"],
                page_number=row["page_number"] or 1,
                bbox=_decode_bbox(row["bbox"], field="parse_table_cells.bbox"),
                is_header=bool(row["is_header"]),
                confidence=float(row["confidence"] or 0.0),
                unit_hint=row["unit_hint"],
                extraction_method=row["extraction_method"] or "lines",
                row_span=row["row_span"] or 1,
                col_span=row["col_span"] or 1,
                origin_fragment_uid=(
                    str(row["origin_fragment_uid"]) if row["origin_fragment_uid"] else None
                ),
                origin_row_idx=row["origin_row_idx"],
                origin_col_idx=row.get("origin_col_idx"),
                normalized_value_wan=(
                    Decimal(str(row["normalized_value_wan"]))
                    if row["normalized_value_wan"] is not None
                    else None
                ),
                presence=row["presence"] or "value",
                ocr_provenance=_decode_jsonb(row.get("ocr_provenance"), dict, field="ocr_provenance"),
            )
        )

    fragments: List[ParsedTable] = []
    physical_fragments: List[ParsedTable] = []
    physical_uids_by_instance: Dict[str, List[str]] = {}
    for row in instance_rows:
        instance_cells = cells_by_instance.get(row["instance_uid"], [])
        # bbox 以该片段首个非空单元格近似（缓存重建场景仅用于导航）
        logical_boxes = [cell.bbox for cell in instance_cells if cell.bbox]
        logical_bbox = (
            min(box[0] for box in logical_boxes), min(box[1] for box in logical_boxes),
            max(box[2] for box in logical_boxes), max(box[3] for box in logical_boxes),
        ) if logical_boxes else (0.0, 0.0, 0.0, 0.0)
        rows_grid: List[List[str]] = []
        by_row: Dict[int, List[ParsedCell]] = {}
        for cell in instance_cells:
            by_row.setdefault(cell.row_idx, []).append(cell)
        logical_width = max((cell.col_idx + 1 for cell in instance_cells), default=0)
        for row_idx in sorted(by_row):
            values = [""] * logical_width
            for cell in by_row[row_idx]:
                values[cell.col_idx] = cell.raw_text
            rows_grid.append(values)
        by_physical: Dict[str, List[ParsedCell]] = {}
        for cell in instance_cells:
            physical_uid = str(cell.origin_fragment_uid or row["instance_uid"])
            restored_cell = replace(
                cell,
                row_idx=(cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx),
                col_idx=(cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx),
                origin_fragment_uid=None,
                origin_row_idx=None,
                origin_col_idx=None,
            )
            by_physical.setdefault(physical_uid, []).append(restored_cell)
        physical_uids = sorted(by_physical)
        physical_uids_by_instance[str(row["instance_uid"])] = physical_uids
        for physical_uid, physical_cells in by_physical.items():
            physical_meta = physical_meta_by_instance.get(str(row["instance_uid"]), {}).get(physical_uid, {})
            physical_rows: Dict[int, List[ParsedCell]] = {}
            for cell in physical_cells:
                physical_rows.setdefault(cell.row_idx, []).append(cell)
            physical_grid = [
                [cell.raw_text for cell in sorted(physical_rows[row_idx], key=lambda item: item.col_idx)]
                for row_idx in sorted(physical_rows)
            ]
            boxes = [cell.bbox for cell in physical_cells if cell.bbox]
            physical_bbox = (
                min(box[0] for box in boxes), min(box[1] for box in boxes),
                max(box[2] for box in boxes), max(box[3] for box in boxes),
            ) if boxes else (0.0, 0.0, 0.0, 0.0)
            methods = {str(cell.extraction_method or "stored") for cell in physical_cells}
            extraction_method = (
                "mixed" if len(methods) > 1 else next(iter(methods), "stored")
            )
            header_rows = [cell.row_idx for cell in physical_cells if cell.is_header]
            raw_rows = _decode_jsonb(physical_meta.get("raw_rows"), list, field="raw_rows")
            raw_cells = _decode_jsonb(physical_meta.get("raw_cells"), list, field="raw_cells")
            restored_cells = (
                [ParsedCell.from_dict(item) for item in raw_cells if isinstance(item, dict)]
                if isinstance(raw_cells, list) and raw_cells
                else physical_cells
            )
            physical_fragments.append(
                ParsedTable(
                    table_code=row["table_code"],
                    title=physical_meta.get("title") or row["title"] or "",
                    page_number=physical_meta.get("page_number") or min((cell.page_number for cell in physical_cells), default=row["page_start"] or 1),
                    table_index=physical_meta.get("table_index") or 0,
                    bbox=_decode_bbox(physical_meta.get("bbox"), field="parse_table_fragments.bbox") or physical_bbox,
                    header_row_count=(
                        physical_meta.get("header_row_count")
                        if physical_meta.get("header_row_count") is not None
                        else (max(header_rows) + 1 if header_rows else 0)
                    ),
                    confidence=float(physical_meta.get("confidence") or row["confidence"] or 0.0),
                    extraction_method=physical_meta.get("extraction_method") or extraction_method,
                    unit_hint=physical_meta.get("unit_hint") or next((cell.unit_hint for cell in physical_cells if cell.unit_hint), None),
                    rows=[list(values) for values in raw_rows] if raw_rows else physical_grid,
                    cells=restored_cells,
                    fragment_uid=physical_uid,
                )
            )

        methods = {str(cell.extraction_method or "stored") for cell in instance_cells}
        extraction_method = "mixed" if len(methods) > 1 else next(iter(methods), "stored")
        fragments.append(
            ParsedTable(
                table_code=row["table_code"],
                title=row["title"] or "",
                page_number=row["page_start"] or 1,
                table_index=0,
                bbox=logical_bbox,
                header_row_count=max((cell.row_idx + 1 for cell in instance_cells if cell.is_header), default=0),
                confidence=float(row["confidence"] or 0.0),
                extraction_method=extraction_method,
                unit_hint=next((cell.unit_hint for cell in instance_cells if cell.unit_hint), None),
                rows=rows_grid,
                cells=instance_cells,
                fragment_uid=row["instance_uid"],
                source_fragment_uids=[uid for uid in physical_uids if uid != str(row["instance_uid"])],
            )
        )

    logical_tables = [
        LogicalTableInstance(
            instance_uid=row["instance_uid"],
            table_code=row["table_code"],
            title=row["title"] or "",
            fragment_uids=list(dict.fromkeys([
                *physical_uids_by_instance.get(str(row["instance_uid"]), []),
                str(row["instance_uid"]),
            ])),
            page_start=row["page_start"] or 1,
            page_end=row["page_end"] or 1,
            org_name=row["org_name"],
            fiscal_year=row["fiscal_year"],
            fund_scope=row["fund_scope"],
            row_count=row["row_count"] or 0,
            confidence=float(row["confidence"] or 0.0),
            column_mappings=_decode_jsonb(row["column_mappings"], list, field="column_mappings"),
            identity_conflict=bool(row["identity_conflict"]),
            conflict_reasons=_decode_jsonb(row["conflict_reasons"], list, field="conflict_reasons"),
        )
        for row in instance_rows
    ]

    return ParseArtifact(
        source_file_hash=run["source_file_hash"],
        parser_name=run["parser_name"],
        parser_fingerprint=run["parser_fingerprint"],
        config_fingerprint=run["config_fingerprint"],
        ocr_model_fingerprint=run["ocr_model_fingerprint"],
        parse_run_uid=run["parse_run_uid"],
        page_count=run["page_count"] or 0,
        page_states=page_states,
        text_blocks=text_blocks,
        ocr_table_candidates=ocr_table_candidates,
        table_fragments=fragments,
        logical_tables=logical_tables,
        physical_fragments=physical_fragments,
        coordinate_space=run["coordinate_space"] or "pdf_page_points",
        coordinate_transforms=_decode_jsonb(run["coordinate_transforms"], dict, field="coordinate_transforms"),
    )


async def load_parse_facts(
    conn: asyncpg.Connection, parse_run_id: int
) -> List[Dict[str, Any]]:
    """读取已落库的事实（dict 形态），供一致性比对与后续规则消费。"""
    rows = await conn.fetch(
        "SELECT * FROM parse_facts WHERE parse_run_id = $1 ORDER BY id",
        parse_run_id,
    )
    return [dict(row) for row in rows]


async def load_parse_evidence(
    conn: asyncpg.Connection, parse_run_id: int
) -> List[Dict[str, Any]]:
    """按解析运行读取完整证据行（含物理页、bbox、来源单元格）。

    ZC-01（AC-08）：证据往返后必须能还原定位实体——读取端返回写入端的
    全部字段，不再退化为 quote+unresolved。
    """
    rows = await conn.fetch(
        "SELECT * FROM parse_evidence WHERE parse_run_id = $1 ORDER BY id",
        parse_run_id,
    )
    results: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if isinstance(item.get("bbox"), str):
            try:
                item["bbox"] = json.loads(item["bbox"])
            except (TypeError, ValueError):
                pass
        results.append(item)
    return results
