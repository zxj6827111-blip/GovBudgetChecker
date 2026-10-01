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
import logging
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


def _bbox_to_json(bbox) -> Optional[str]:
    if not bbox:
        return None
    return json.dumps([float(item) for item in bbox])


def _json(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


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
    parse_run_id: Optional[int] = None
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO parse_runs (
                parse_run_uid, document_version_id, source_file_hash,
                parser_name, parser_fingerprint, config_fingerprint,
                ocr_model_fingerprint, page_count, page_states, text_blocks,
                ocr_table_candidates, coordinate_space, coordinate_transforms, status
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, 'completed')
            ON CONFLICT (parse_run_uid) DO UPDATE
                SET parse_run_uid = EXCLUDED.parse_run_uid
            RETURNING id
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
        )
        parse_run_id = int(row["id"])

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
                instance.fiscal_year or fiscal_year,
                instance.fund_scope,
                instance.page_start,
                instance.page_end,
                instance.row_count,
                instance.confidence,
                _json(instance.column_mappings),
                instance.identity_conflict,
                _json(instance.conflict_reasons),
            )

        for fragment, cell in artifact.cells():
            instance_uid = _instance_uid_for(artifact, fragment)
            await conn.execute(
                """
                INSERT INTO parse_table_cells (
                    parse_run_id, instance_uid, row_idx, col_idx,
                    raw_text, normalized_text, numeric_value, normalized_value_wan,
                    unit_hint, presence, page_number, bbox, is_header,
                    confidence, extraction_method, row_span, col_span
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
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
    """把 FiscalFact 与其证据引用落库；返回写入条数。幂等（DO NOTHING）。"""
    written = 0
    async with conn.transaction():
        for fact in facts:
            evidence_ids = list(fact.evidence_ids)
            for evidence_id in evidence_ids:
                await conn.execute(
                    """
                    INSERT INTO parse_evidence (
                        parse_run_id, evidence_id, file_hash, quote,
                        source_method, verification_status, location_level
                    ) VALUES ($1, $2, $3, $4, 'native_text', 'unverified', 'unresolved')
                    ON CONFLICT (parse_run_id, evidence_id) DO NOTHING
                    """,
                    parse_run_id,
                    evidence_id,
                    file_hash,
                    fact.raw_value or "",
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

    page_states = [
        ParsePageState.from_dict(item)
        for item in json.loads(run["page_states"] or "[]")
    ]
    text_blocks = [
        ParsedTextBlock.from_dict(item)
        for item in json.loads(run["text_blocks"] or "[]")
    ]
    ocr_table_candidates = [
        OcrTableCandidate.from_dict(item)
        for item in json.loads(run["ocr_table_candidates"] or "[]")
    ]
    instance_rows = await conn.fetch(
        "SELECT * FROM parse_table_instances WHERE parse_run_id = $1 ORDER BY id",
        parse_run_id,
    )
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
                bbox=tuple(row["bbox"]) if row["bbox"] else None,
                is_header=bool(row["is_header"]),
                confidence=float(row["confidence"] or 0.0),
                unit_hint=row["unit_hint"],
                extraction_method=row["extraction_method"] or "lines",
                row_span=row["row_span"] or 1,
                col_span=row["col_span"] or 1,
                normalized_value_wan=(
                    Decimal(str(row["normalized_value_wan"]))
                    if row["normalized_value_wan"] is not None
                    else None
                ),
                presence=row["presence"] or "value",
            )
        )

    fragments: List[ParsedTable] = []
    for row in instance_rows:
        instance_cells = cells_by_instance.get(row["instance_uid"], [])
        # bbox 以该片段首个非空单元格近似（缓存重建场景仅用于导航）
        bbox = next(
            (cell.bbox for cell in instance_cells if cell.bbox), (0.0, 0.0, 0.0, 0.0)
        )
        rows_grid: List[List[str]] = []
        by_row: Dict[int, List[ParsedCell]] = {}
        for cell in instance_cells:
            by_row.setdefault(cell.row_idx, []).append(cell)
        for row_idx in sorted(by_row):
            ordered = sorted(by_row[row_idx], key=lambda item: item.col_idx)
            rows_grid.append([cell.raw_text for cell in ordered])
        fragments.append(
            ParsedTable(
                table_code=row["table_code"],
                title=row["title"] or "",
                page_number=row["page_start"] or 1,
                table_index=0,
                bbox=bbox,
                header_row_count=0,
                confidence=float(row["confidence"] or 0.0),
                extraction_method="stored",
                unit_hint=None,
                rows=rows_grid,
                cells=instance_cells,
                fragment_uid=row["instance_uid"],
            )
        )

    logical_tables = [
        LogicalTableInstance(
            instance_uid=row["instance_uid"],
            table_code=row["table_code"],
            title=row["title"] or "",
            fragment_uids=[row["instance_uid"]],
            page_start=row["page_start"] or 1,
            page_end=row["page_end"] or 1,
            org_name=row["org_name"],
            fiscal_year=row["fiscal_year"],
            fund_scope=row["fund_scope"],
            row_count=row["row_count"] or 0,
            confidence=float(row["confidence"] or 0.0),
            column_mappings=json.loads(row["column_mappings"] or "[]"),
            identity_conflict=bool(row["identity_conflict"]),
            conflict_reasons=json.loads(row["conflict_reasons"] or "[]"),
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
        coordinate_space=run["coordinate_space"] or "pdf_page_points",
        coordinate_transforms=json.loads(run["coordinate_transforms"] or "{}"),
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
