"""
Run structured ingest against PostgreSQL without affecting legacy checks.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from copy import deepcopy
import hashlib
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.db.connection import DatabaseConnection
from src.db.migrations import run_migrations
from src.schemas.organization import Organization
from src.services.fiscal_fact_materializer import FiscalFactMaterializer
from src.services.fiscal_table_rules import NINE_TABLE_RULES
from src.services.org_storage import get_org_storage
from src.services.parse_artifact_store import (
    find_cached_parse_run,
    load_parse_artifact,
    persist_facts_and_evidence,
    persist_parse_artifact,
)
from src.services.pdf_parser import PARSER_FINGERPRINT, PDFParser
from src.schemas.parse_artifact import ParseArtifact, ParsedTable
from src.services.input_processing_identity import artifact_content_hash, journal_cache_conflict, processing_config_fingerprint
from src.services.analysis_fact_snapshot import (verify_persisted_facts, make_snapshot, persist_snapshot)
from src.services.ps_schema_sync import PSSharedSchemaSync
from src.services.table_recognizer import TableRecognizer
from src.utils.report_year import parse_report_year

logger = logging.getLogger(__name__)

_DB_READY = False
_DB_READY_LOCK: asyncio.Lock | None = None

CORE_TABLES = (
    "FIN_01_income_expenditure_total",
    "FIN_02_income",
    "FIN_03_expenditure",
    "FIN_04_fiscal_grant_total",
    "FIN_05_general_public_expenditure",
    "FIN_06_basic_expenditure",
    "FIN_07_three_public",
)
CONDITIONAL_TABLES = (
    "FIN_08_gov_fund",
    "FIN_09_state_capital",
)


def _legacy_artifact_projection(artifact: ParseArtifact) -> tuple[ParseArtifact, List[Dict[str, Any]]]:
    """Select existing logical tables for the one-code legacy schema.

    Keep the unified artifact and its independent instance identities intact.
    Page-text fallbacks are discovery candidates, not a grid to overwrite a
    resolved logical table with. Multiple genuine instances cannot be flattened
    into one legacy table, even when their amounts happen to agree.
    """
    by_code: Dict[str, List[ParsedTable]] = {}
    for table in artifact.table_fragments:
        by_code.setdefault(table.table_code, []).append(table)
    projected: List[ParsedTable] = []
    review_items: List[Dict[str, Any]] = []
    for code, tables in by_code.items():
        candidates = [table for table in tables if table.extraction_method != "page_text_fallback"]
        if code not in NINE_TABLE_RULES:
            projected.extend(candidates)
            continue
        logical_instances = [
            instance for instance in artifact.logical_tables
            if instance.table_code == code and any(
                table.fragment_uid in instance.fragment_uids
                or table.fragment_uid == instance.instance_uid
                for table in candidates
            )
        ]
        conflicts = any(instance.identity_conflict for instance in logical_instances)
        if len(candidates) == 1 and not conflicts and len(logical_instances) <= 1:
            projected.append(candidates[0])
            continue
        reason = ("logical_identity_conflict" if conflicts else
                  "multiple_logical_instances" if len(candidates) > 1 or len(logical_instances) > 1 else
                  "only_page_text_candidates")
        review_items.append({
            "id": f"{code}:legacy_projection",
            "type": "legacy_table_projection_unavailable",
            "severity": "warn",
            "table_code": code,
            "recognition_status": "manual_review",
            "reason": reason,
            "page_numbers": sorted({table.page_number for table in tables}),
            "instance_uids": [instance.instance_uid for instance in logical_instances],
            "message": "该表存在独立实例或缺少可靠表格来源，旧表兼容口径无法安全合并，待人工复核",
            "recommended_action": "review_logical_table_identity",
        })
    projected.sort(key=lambda table: (table.page_number, table.table_index, table.fragment_uid))
    return replace(artifact, table_fragments=projected), review_items


async def close_structured_ingest_resources() -> None:
    global _DB_READY
    if DatabaseConnection.is_initialized():
        await DatabaseConnection.close()
    _DB_READY = False


async def ensure_structured_ingest_ready() -> bool:
    global _DB_READY
    if _DB_READY:
        return True

    if not (os.getenv("DATABASE_URL") or "").strip():
        return False

    global _DB_READY_LOCK
    active_loop = asyncio.get_running_loop()
    if _DB_READY_LOCK is None or getattr(_DB_READY_LOCK, "_loop", None) not in (None, active_loop):
        _DB_READY_LOCK = asyncio.Lock()
    async with _DB_READY_LOCK:
        if _DB_READY:
            return True
        try:
            await DatabaseConnection.initialize()
            await run_migrations()
            _DB_READY = True
        except Exception:
            logger.exception("Failed to initialize structured ingest database")
            return False
    return True


def build_ingest_metadata(
    *,
    organization_id: Any = None,
    organization_name: Any = None,
    fiscal_year: Any = None,
    doc_type: Any = None,
    report_year: Any = None,
    report_kind: Any = None,
    checksum: Any = None,
    document_profile: Any = None,
    check_subject_name: Any = None,
) -> Dict[str, Any]:
    """构建结构化入库的元数据。

    抽成独立函数是为了让"画像到底有没有被传进来"成为**可测的事实**，
    而不是靠读源码断言。

    为什么必须把 ``document_profile`` 传进来：Slot 归属判定要靠它识别
    "文种在不同来源间冲突""任务年度与材料实际年度不一致""材料口径"这三件事。
    画像里本来就带着 ``conflicts`` 与被否决的候选值，只传最终选中的那个值，
    冲突信息在进入台账之前就被丢掉了——本应停在"待人工确认"的材料
    会被当成已确认，而且事后无从察觉。

    这里复用主分析链路**已经算好的同一个** DocumentProfile，不重新解析 PDF、
    也不在入库侧另造一套业务画像：两套画像必然漂移，而漂移的表现就是
    "分析说是这份材料、台账说是另一份"。

    ``document_profile`` 既有的**输出**字段（``canonical_nine_table`` 这类
    结构化解析类型字符串）与本函数的**输入**参数是两回事，不要混淆。

    ``check_subject_name`` 是规则视为本单位的名称（见
    ``src/services/check_subject.py``）：只用于事实主体判定与读取回执，
    文档归档仍按 ``organization_name``。
    """
    payload: Dict[str, Any] = {
        "organization_id": organization_id,
        "organization_name": organization_name,
        "check_subject_name": check_subject_name,
        "fiscal_year": fiscal_year,
        "doc_type": doc_type,
        "report_year": report_year,
        "report_kind": report_kind,
        "checksum": checksum,
    }
    profile = _normalize_document_profile(document_profile)
    if profile is not None:
        payload["document_profile"] = profile
    return payload


def _normalize_document_profile(value: Any) -> Optional[Dict[str, Any]]:
    """把画像归一为字典；None 表示"本次没有画像"。

    传入无法识别的类型时抛错而不是静默丢弃：静默丢弃正是本轮要修的缺陷类型
    ——画像没传进来不会报错，只会让台账少识别一批冲突，而且事后查不出来。
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return payload
    raise TypeError(
        "document_profile 必须是 dict、带 to_dict() 的画像对象或 None，"
        f"实际收到 {type(value).__name__}"
    )


async def run_structured_ingest(
    job_id: str,
    pdf_path: Path,
    metadata: Dict[str, Any],
    *,
    parse_artifact: Optional["ParseArtifact"] = None,
    parse_config_fingerprint: str = "",
    raw_fact_context: Any = None,
    effective_fact_context: Any = None,
    analysis_revision: str = "",
    candidate_only: bool = False,
) -> Dict[str, Any]:
    """结构化入库主链路。

    顺序上有一条硬约束：**文档版本建立之后立刻分配材料槽位，然后才解析**。
    两者回答的是不同问题——台账回答"这份材料有没有收到"，解析回答"这份材料
    看得懂看不懂"。把分配放在末尾，等于要求解析、识别、物化全部成功材料
    才算进台账，而解析失败的那批恰恰是最需要被台账看见的。

    错误返回必须保留已经建立的 ``document_version_id`` 与 ``material_slot``：
    没有这两个字段，调用方只能看到一个"什么都没发生"的失败，
    无从判断材料到底进没进台账。

    ZC-01（SYS-01）：``parse_artifact`` 允许调用方传入主链已生成的统一解析
    对象——入库只消费该对象，**不再重开 PDF 解析第二遍**。未传入时先查
    解析缓存（按原件哈希 + 解析器 + 配置指纹），命中即从统一存储重建；
    都没有才实际解析一次并落库。无数据库时整体 skipped 的行为不变。
    """
    if not await ensure_structured_ingest_ready():
        return {
            "job_id": job_id,
            "status": "skipped",
            "reason": "database_unavailable",
            "storage_consistent": False,
            "review_item_count": 0,
            "review_items": [],
        }

    conn = None
    storage_tx = None
    # 下面这些值在 try 之外声明并逐步填充：任何一步抛错时，错误结果里仍要能
    # 回答"是哪份材料""文档版本建了没有""槽位分到没有"。
    org_name: Optional[str] = None
    fiscal_year: Optional[int] = None
    doc_type: Optional[str] = None
    document_info: Optional[Dict[str, int]] = None
    material_slot: Optional[Dict[str, Any]] = None
    ingest_stage = "setup"
    parse_run_id: Optional[int] = None
    try:
        ingest_stage = "source_identity_verification"
        checksum = _sha256_file(pdf_path)
        supplied_checksum = str(metadata.get("checksum") or "").strip().lower()
        if supplied_checksum and supplied_checksum != checksum:
            raise ValueError("source_file_hash_mismatch: upload metadata differs from original file")
        if parse_artifact is not None and parse_artifact.source_file_hash != checksum:
            raise ValueError("source_file_hash_mismatch: supplied artifact differs from original file")
        conn = await DatabaseConnection.acquire()
        org_name = _normalize_org_name(metadata.get("organization_name"), pdf_path)
        fiscal_year = _parse_year(metadata.get("report_year") or metadata.get("fiscal_year"))
        doc_type = str(metadata.get("doc_type") or metadata.get("report_kind") or "unknown")

        if candidate_only:
            existing = await conn.fetch('''SELECT v.id AS document_version_id,v.document_id,d.org_unit_id
                FROM fiscal_document_versions v JOIN fiscal_documents d ON d.id=v.document_id
                JOIN org_units o ON o.id=d.org_unit_id
                WHERE v.file_hash=$1 AND d.fiscal_year IS NOT DISTINCT FROM $2
                  AND d.doc_type=$3 AND o.org_name=$4 AND d.subject_org_id=$5''', checksum,fiscal_year,doc_type,org_name,str(metadata.get('organization_id') or ''))
            if len(existing) > 1:
                raise ValueError('candidate original document identity is ambiguous')
            document_info = dict(existing[0]) if existing else None
        if document_info is None:
            document_info = await _ensure_document_version(
            conn=conn,
            org_name=org_name,
            fiscal_year=fiscal_year,
            doc_type=doc_type,
            checksum=checksum,
            storage_key=_resolve_storage_key(job_id, pdf_path, metadata),
            storage_backend=_resolve_storage_backend(metadata),
            original_filename=str(metadata.get("filename") or pdf_path.name),
            file_size_bytes=_resolve_file_size(pdf_path, metadata),
            content_type=str(metadata.get("content_type") or "application/pdf"),
            subject_org_id=str(metadata.get("organization_id") or ""),
            )
        version_id = document_info["document_version_id"]

        # 材料槽位：文件版本一旦建立就立即尝试进入材料台账，**不等解析结果**。
        #
        # 这里的位置不能后移。台账记的是"这份材料有没有收到"，解析记的是
        # "这份材料看不看得懂"，前者不该依赖后者；原先放在整条链路末尾，
        # 结果是"拿到文件但解析失败"的材料在台账里根本不存在——那正是最需要
        # 人工补录的一批，被看不见就等于被丢掉。
        #
        # 后续任何环节都**不得**再次调用本函数：一次分析只允许分配一次，
        # 二次分配会把"何时进入台账"重新绑回解析是否成功。
        # 槽位仍是旁路能力，safe_* 版本不抛异常——它失败只在结果里留下一条
        # error 摘要，不阻断解析/识别/物化/入库主流程。
        if not candidate_only:
            material_slot = await _allocate_material_slot(
                conn=conn, metadata=metadata, checksum=checksum,
                document_version_id=version_id)

        if analysis_revision:
            # 文档/材料登记已完成；一版解析、原始事实和有效快照必须同事务落地。
            storage_tx = conn.transaction()
            await storage_tx.start()

        # ---- ZC-01：一次解析、全程消费 ----
        # 1) 查解析缓存（原件哈希+解析器+配置+OCR 指纹）：命中则复用既有
        #    解析运行，既不重开 PDF 也不新建运行；
        # 2) 调用方已带 artifact（主链产物）→ 直接消费，不重开 PDF；
        # 3) 都没有 → 实际解析一次，并把产物写入统一存储（幂等）。
        # 旧表由 artifact 适配写入（persist_artifact_to_legacy_tables）。
        # RC-08：缓存键必须与"本次将要消费的解析对象"的指纹一致。
        # 调用方已带 artifact 时以其自身 config/OCR 指纹为准（那才是这次
        # 事实的真实来源），不能用固定参数指纹去命中旧 run——否则会把
        # 新配置/新模型解析出的事实挂到旧 run 上，证据与版本彻底失配。
        lookup_config_fingerprint = (
            (parse_artifact.config_fingerprint or parse_config_fingerprint)
            if parse_artifact is not None
            else processing_config_fingerprint(parse_config_fingerprint)
        )
        lookup_ocr_fingerprint = (
            parse_artifact.ocr_model_fingerprint
            if parse_artifact is not None
            else None
        )
        cached_run_id = await find_cached_parse_run(
            conn,
            document_version_id=version_id,
            source_file_hash=checksum,
            parser_fingerprint=(parse_artifact.parser_fingerprint or PARSER_FINGERPRINT) if parse_artifact is not None else PARSER_FINGERPRINT,
            config_fingerprint=lookup_config_fingerprint,
            ocr_model_fingerprint=lookup_ocr_fingerprint,
        )
        if cached_run_id is not None:
            # 命中即完整恢复被命中 run 的 artifact——事实物化、证据引用
            # 全部来自同一 run 的同一份解析对象（RC-08：不旧 run 配新事实）。
            ingest_stage = "unified_cache_readback"
            cached_artifact = await load_parse_artifact(conn, cached_run_id)
            if parse_artifact is not None:
                if artifact_content_hash(parse_artifact) != artifact_content_hash(cached_artifact):
                    journal = journal_cache_conflict(job_id, parse_artifact, cached_artifact)
                    raise ValueError("cache_key_conflict: cached artifact differs from supplied current artifact; evidence=" + journal)
                # 主链已执行规则的产物不能被旧run替换；同内容的新尝试有独立run身份。
                if parse_artifact.parse_run_uid == cached_artifact.parse_run_uid:
                    parse_run_id = cached_run_id
            else:
                parse_artifact = cached_artifact
                parse_run_id = cached_run_id

        parser = PDFParser(conn)
        if parse_artifact is None:
            # 缓存未命中且调用方未带产物：这是唯一一次真正打开 PDF
            parse_artifact = parser.build_parse_artifact(
                str(pdf_path), config_fingerprint=parse_config_fingerprint
            )
        # 缓存和新解析都必须证明来自当前字节，不能把调用方的哈希写回产物掩盖差异。
        if parse_artifact.source_file_hash != checksum:
            raise ValueError("source_file_hash_mismatch: parsed or cached artifact differs from original file")

        if parse_run_id is None:
            ingest_stage = "unified_artifact_write"
            parse_run_id = await persist_parse_artifact(
                conn, parse_artifact, document_version_id=version_id,
                org_name=org_name, fiscal_year=fiscal_year,
            )

        ingest_stage = "unified_artifact_readback"
        stored_artifact = await load_parse_artifact(conn, parse_run_id)
        if stored_artifact.to_dict() != parse_artifact.to_dict():
            raise RuntimeError("unified artifact readback differs from complete parser output")

        # 旧表兼容写入：由统一解析对象适配产出，不再重开 PDF（ZC-01 / SYS-01）
        ingest_stage = "legacy_table_write"
        if candidate_only or analysis_revision:
            # 候选版本只能追加解析运行和不可变快照，不能清空当前旧表或同步台账。
            parse_result = {'success': True, 'tables_count': len(parse_artifact.table_fragments),
                            'unknown_tables': [t.table_code for t in parse_artifact.table_fragments
                                               if t.table_code.startswith('UNKNOWN_')]}
            instances = list(parse_artifact.logical_tables)
            materialize_result = {'facts_count': 0, 'low_confidence_tables': []}
        else:
            legacy_artifact, legacy_review_items = _legacy_artifact_projection(parse_artifact)
            parse_result = await parser.persist_artifact_to_legacy_tables(version_id, legacy_artifact)
            parse_result['legacy_projection_review_items'] = legacy_review_items
            recognizer = TableRecognizer(conn)
            instances = await recognizer.recognize_tables(version_id)
            await recognizer.save_table_instances(version_id, instances)
            materializer = FiscalFactMaterializer(conn)
            materialize_result = await materializer.materialize(version_id)

        # 统一事实：从 artifact 直接物化 FiscalFact（含身份+归一值+证据），
        # 与旧表物化并行落库；两条路径来自同一份解析对象（不再二次解析）。
        ingest_stage = "unified_fact_materialization"
        if raw_fact_context is not None:
            from src.services.analysis_fact_snapshot import context_is_source_bound
            if not context_is_source_bound(parse_artifact, raw_fact_context, raw=True):
                raise ValueError("supplied raw fact context differs from current artifact")
            fiscal_facts = list(raw_fact_context.facts)
        else:
            from src.engine.fact_context import build_fact_context_from_artifact
            raw_fact_context = build_fact_context_from_artifact(
                parse_artifact, org_name=_text_or_none(metadata.get("check_subject_name")) or org_name,
                fiscal_year=fiscal_year,
                doc_type=doc_type, report_kind=metadata.get("report_kind"),
                require_complete_identity=bool(analysis_revision or candidate_only))
            fiscal_facts = list(raw_fact_context.facts) if raw_fact_context else []
        if effective_fact_context is None:
            effective_fact_context = raw_fact_context
        if candidate_only or analysis_revision:
            materialize_result['facts_count'] = len(fiscal_facts)
        ingest_stage = "unified_facts_evidence_write"
        fiscal_facts_count = await persist_facts_and_evidence(
            conn, parse_run_id=parse_run_id, facts=fiscal_facts, file_hash=checksum,
        )
        if fiscal_facts_count != len(fiscal_facts):
            raise RuntimeError("unified facts write count does not match current materialized facts")
        ingest_stage = "unified_facts_readback"
        storage_verification = await verify_persisted_facts(
            conn, parse_run_id=parse_run_id, facts=fiscal_facts)
        snapshot_summary = None
        if analysis_revision:
            ingest_stage = "analysis_fact_snapshot"
            snapshot = make_snapshot(job_uuid=job_id, revision_id=analysis_revision,
                artifact=parse_artifact, raw_context=raw_fact_context,
                effective_context=effective_fact_context)
            snapshot_summary = await persist_snapshot(conn, snapshot=snapshot, parse_run_id=parse_run_id)
        if storage_tx is not None:
            await storage_tx.commit()
            storage_tx = None
        from src.services.table_import_receipt import build_table_import_receipt
        # 回执要反映人工补录、列映射和改值之后的实际入库状态，否则校正永远消不掉读取报警。
        receipt_facts = list(effective_fact_context.facts) if effective_fact_context is not None else fiscal_facts
        reading_import = build_table_import_receipt(
            parse_artifact, receipt_facts,
            org_name=_text_or_none(metadata.get("check_subject_name")) or org_name)
        reading_import["storage_roundtrip_verified"] = True
        document_profile = _detect_document_profile(pdf_path)
        ps_sync_summary: Dict[str, Any]
        try:
            ps_sync_summary = ({'status': 'pending_publication', 'source': 'effective_fact_snapshot'}
                               if analysis_revision else {'status': 'skipped_candidate'}) if (candidate_only or analysis_revision) else await PSSharedSchemaSync(conn).sync(
                document_version_id=version_id,
                org_name=org_name,
                fiscal_year=fiscal_year,
                doc_type=doc_type,
                pdf_path=pdf_path,
                checksum=checksum,
                organization_id=_text_or_none(metadata.get("organization_id")),
            )
        except Exception as exc:
            logger.exception(
                "PS shared schema sync failed for document_version %s",
                version_id,
            )
            ps_sync_summary = {
                "status": "error",
                "error": str(exc),
            }
        local_org_backfill = None if (candidate_only or analysis_revision) else _backfill_local_organization_catalog(
            org_name=org_name,
            pdf_path=pdf_path,
            ps_sync_summary=ps_sync_summary,
            metadata=metadata,
        )

        review_items = _build_review_items(
            parse_result=parse_result,
            table_instances=instances,
            materialize_result=materialize_result,
            pdf_path=pdf_path,
        )
        review_items.extend(_reading_review_items(reading_import["reading_quality"]))
        recognized_codes = {instance.table_code for instance in instances}
        missing_optional_tables = [
            code for code in CONDITIONAL_TABLES if code not in recognized_codes
        ]
        payload = {
            "job_id": job_id,
            "status": "done" if parse_result.get("success", False) else "warning",
            "organization_name": org_name,
            "fiscal_year": fiscal_year,
            "doc_type": doc_type,
            "document_id": document_info["document_id"],
            "document_version_id": version_id,
            "tables_count": int(parse_result.get("tables_count") or 0),
            "recognized_tables": len(instances),
            "facts_count": int(materialize_result.get("facts_count") or 0),
            # 统一事实存储（ZC-01）：FiscalFact 身份+归一值+证据的落库条数
            "unified_facts_count": int(fiscal_facts_count or 0),
            "parse_run_id": parse_run_id,
            "fact_snapshot": snapshot_summary,
            "storage_verification": storage_verification,
            "reading_import": reading_import,
            "unified_storage": {"status": "completed", "artifact_status": "completed",
                                "facts_status": "completed", "evidence_status": "completed",
                                "parse_run_id": parse_run_id, "document_version_id": version_id,
                                "expected_facts_count": len(fiscal_facts)},
            "parse_cache_hit": cached_run_id is not None,
            "cache_readback": {"cache_hit": cached_run_id is not None,
                               "cell_provenance_decoded": cached_run_id is not None,
                               "parse_run_id": cached_run_id},
            "parse_cache": {"hit": cached_run_id is not None, "readback_completed": cached_run_id is not None,
                            "restored_cell_count": len(parse_artifact.cells()) if cached_run_id is not None else 0,
                            "restored_ocr_provenance_cells": sum(bool(cell.ocr_provenance) for _, cell in parse_artifact.cells()) if cached_run_id is not None else 0},
            "unknown_tables": parse_result.get("unknown_tables") or [],
            "missing_optional_tables": missing_optional_tables,
            "low_confidence_tables": materialize_result.get("low_confidence_tables") or [],
            "document_profile": document_profile,
            "ps_sync": ps_sync_summary,
            "material_slot": material_slot,
            "review_item_count": len(review_items),
            "low_confidence_item_count": sum(
                1 for item in review_items if item.get("type") == "low_confidence_table"
            ),
            "review_items": review_items,
        }
        if local_org_backfill:
            payload["local_org_backfill"] = local_org_backfill
        if parse_result.get("errors"):
            payload["errors"] = parse_result["errors"]
        return payload
    except Exception as exc:
        if storage_tx is not None:
            await storage_tx.rollback()
            storage_tx = None
        logger.exception("Structured ingest failed for job %s", job_id)
        error_payload: Dict[str, Any] = {
            "job_id": job_id,
            "status": "error",
            "error": str(exc),
            "ingest_stage": ingest_stage,
            "unified_storage": {"status": "error", "failed_stage": ingest_stage,
                                "parse_run_id": parse_run_id},
            "review_item_count": 1,
            "review_items": [
                {
                    "id": f"{job_id}:structured_ingest_error",
                    "type": "ingest_error",
                    "severity": "error",
                    "message": str(exc),
                    "recommended_action": "check_database_and_pdf_parser",
                }
            ],
        }
        # 已经建立的上下文一律带回：文档版本与槽位一旦落库就是既成事实，
        # 从错误结果里丢掉它们，调用方就只能看到一个"什么都没发生"的失败，
        # 既判断不出材料有没有进台账，也做不了后续的人工补录。
        if org_name is not None:
            error_payload["organization_name"] = org_name
            error_payload["fiscal_year"] = fiscal_year
            error_payload["doc_type"] = doc_type
        if document_info is not None:
            error_payload["document_id"] = document_info["document_id"]
            error_payload["document_version_id"] = document_info["document_version_id"]
        if material_slot is not None:
            error_payload["material_slot"] = material_slot
        return error_payload
    finally:
        if conn is not None:
            await DatabaseConnection.release(conn)


async def _ensure_document_version(
    conn,
    org_name: str,
    fiscal_year: Optional[int],
    doc_type: str,
    checksum: str,
    storage_key: str,
    storage_backend: str,
    original_filename: str,
    file_size_bytes: int,
    content_type: str,
    subject_org_id: str = "",
) -> Dict[str, int]:
    org_id = await conn.fetchval(
        """
        INSERT INTO org_units (org_name)
        VALUES ($1)
        ON CONFLICT (org_name)
        DO UPDATE SET org_name = EXCLUDED.org_name
        RETURNING id
        """,
        org_name,
    )
    # 冲突目标用 COALESCE(fiscal_year, -1) 表达式索引：Postgres 认为多个 NULL 互不冲突，
    # 若沿用 (org_unit_id, fiscal_year, doc_type) 会让年份未知的文档每次都新插一行。
    document_id = await conn.fetchval(
        """
        INSERT INTO fiscal_documents (org_unit_id, fiscal_year, doc_type, subject_org_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (org_unit_id, COALESCE(fiscal_year, -1), doc_type, subject_org_id)
        DO UPDATE SET doc_type = EXCLUDED.doc_type
        RETURNING id
        """,
        org_id,
        fiscal_year,
        doc_type,
        subject_org_id,
    )
    version_id = await conn.fetchval(
        """
            INSERT INTO fiscal_document_versions (
            document_id,
            file_hash,
            storage_key,
            storage_backend,
            original_filename,
            file_size_bytes,
            content_type
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (document_id, file_hash)
            DO UPDATE SET
                storage_key = CASE
                    WHEN fiscal_document_versions.storage_key IS NULL
                        OR fiscal_document_versions.storage_key = ''
                        OR fiscal_document_versions.storage_key ~ '^(?:[A-Za-z]:)?[/\\\\]'
                    THEN EXCLUDED.storage_key
                    ELSE fiscal_document_versions.storage_key
                END,
            storage_backend = COALESCE(NULLIF(fiscal_document_versions.storage_backend, ''), EXCLUDED.storage_backend),
            original_filename = COALESCE(NULLIF(fiscal_document_versions.original_filename, ''), EXCLUDED.original_filename),
            file_size_bytes = GREATEST(COALESCE(fiscal_document_versions.file_size_bytes, 0), EXCLUDED.file_size_bytes),
            content_type = COALESCE(NULLIF(fiscal_document_versions.content_type, ''), EXCLUDED.content_type)
        RETURNING id
        """,
        document_id,
        checksum,
        storage_key,
        storage_backend,
        original_filename,
        file_size_bytes,
        content_type,
    )
    return {
        "org_unit_id": int(org_id),
        "document_id": int(document_id),
        "document_version_id": int(version_id),
    }


async def _allocate_material_slot(
    conn,
    *,
    metadata: Dict[str, Any],
    checksum: str,
    document_version_id: int,
) -> Dict[str, Any]:
    """把当前文档挂到材料槽位上。

    独立成函数是为了让"能不能关掉"这件事显式：材料台账是新增旁路，
    故障时运维需要一条不重新部署就能停掉它的开关。默认开启——
    默认关闭就等于这段代码在真实环境里从不执行，那它是不是能用永远无人知道。
    """
    if str(os.getenv("MATERIAL_LEDGER_DISABLED") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {"status": "skipped", "reason": "material_ledger_disabled"}

    from src.services.material_slot_service import safe_allocate_for_document

    return await safe_allocate_for_document(
        conn,
        metadata=metadata,
        checksum=checksum,
        document_version_id=document_version_id,
    )


def _resolve_storage_key(job_id: str, pdf_path: Path, metadata: Dict[str, Any]) -> str:
    configured_key = str(metadata.get("storage_key") or metadata.get("saved_path") or "").strip()
    if configured_key:
        normalized = configured_key.replace("\\", "/")
        if not re.match(r"^(?:[A-Za-z]:)?/", normalized):
            return normalized.lstrip("/")

    try:
        upload_root = Path(os.getenv("UPLOAD_DIR", "uploads")).resolve()
        return pdf_path.resolve().relative_to(upload_root).as_posix()
    except Exception:
        return f"{str(job_id).strip() or 'unknown'}/{pdf_path.name}"


def _resolve_storage_backend(metadata: Dict[str, Any]) -> str:
    return str(
        metadata.get("storage_backend")
        or os.getenv("DOCUMENT_STORAGE_BACKEND", "filesystem")
        or "filesystem"
    ).strip() or "filesystem"


def _resolve_file_size(pdf_path: Path, metadata: Dict[str, Any]) -> int:
    try:
        explicit_size = int(metadata.get("size") or 0)
    except (TypeError, ValueError):
        explicit_size = 0
    if explicit_size > 0:
        return explicit_size
    try:
        return int(pdf_path.stat().st_size)
    except OSError:
        return 0


def _union_box(boxes: Sequence[Any]) -> Optional[List[float]]:
    valid = [box for box in boxes if isinstance(box, (list, tuple)) and len(box) == 4]
    if not valid:
        return None
    return [min(b[0] for b in valid), min(b[1] for b in valid),
            max(b[2] for b in valid), max(b[3] for b in valid)]


def _page_ranges(pages: Sequence[int]) -> str:
    """[1, 2, 3, 5] → "1–3、5"。"""
    spans: List[List[int]] = []
    for page in sorted(set(pages)):
        if spans and page == spans[-1][1] + 1:
            spans[-1][1] = page
        else:
            spans.append([page, page])
    return "、".join(f"{a}" if a == b else f"{a}–{b}" for a, b in spans)


def _cell_ref(cell: Dict[str, Any]) -> Dict[str, Any]:
    return {key: cell.get(key) for key in ("source_cell_id", "page", "bbox", "raw_text", "reason")}


def _reading_review_items(reading_quality: Dict[str, Any]) -> List[Dict[str, Any]]:
    """读取回执 → 待复核项：读取缺口按页汇总，未绑定金额格按表汇总。

    一张表列映射不完整、一页取词出缺口，都是同一个根因；逐格各挂一条会把一张表
    变成上百条待办，人工实际只需按表 / 按页核一次。逐格位置仍保留在
    ``cells`` / ``regions`` 里供定位；只有一处时沿用原单条 id，已有决定不失效。
    """
    items: List[Dict[str, Any]] = []
    gaps_by_page: Dict[int, List[Dict[str, Any]]] = {}
    image_gaps: List[Dict[str, Any]] = []
    for gap in reading_quality.get("gaps", []):
        if gap["reason"] == "image_region_not_structured":
            image_gaps.append(gap)  # 扫描件逐页都是同一原因：整份只挂一条
        else:
            gaps_by_page.setdefault(gap["page"], []).append(gap)
    if image_gaps:
        pages = sorted({g["page"] for g in image_gaps})
        digest = hashlib.sha1("|".join(sorted(g["id"] for g in image_gaps)).encode()).hexdigest()[:12]
        items.append({
            "id": image_gaps[0]["id"] if len(image_gaps) == 1 else f"reading:images:{digest}",
            "type": "table_reading_gap", "severity": "warn",
            "page_number": pages[0], "page_numbers": pages,
            "reason": "image_region_not_structured",
            "bbox": image_gaps[0]["bbox"] if len(image_gaps) == 1 else None,
            "regions": [{key: g[key] for key in ("id", "page", "reason", "bbox") if key in g} for g in image_gaps],
            "message": (f"第 {_page_ranges(pages)} 页为图片或扫描内容（共 {len(image_gaps)} 处），"
                        "尚未完成结构化读取，补识别尚未通过验证"),
            "recommended_action": "inspect_original_table_reading"})
    for page, gaps in sorted(gaps_by_page.items()):
        tables = sorted({g["table_code"] for g in gaps if g.get("table_code")})
        digest = hashlib.sha1("|".join(sorted(g["id"] for g in gaps)).encode()).hexdigest()[:12]
        items.append({
            "id": gaps[0]["id"] if len(gaps) == 1 else f"reading:p{page}:gaps:{digest}",
            "type": "table_reading_gap", "severity": "warn",
            **({"table_code": tables[0]} if len(tables) == 1 else {}),
            "page_number": page, "page_numbers": [page],
            "reason": ",".join(sorted({g["reason"] for g in gaps})),
            "bbox": _union_box([g["bbox"] for g in gaps]),
            "regions": [{key: g[key] for key in ("id", "reason", "bbox", "raw_text") if key in g} for g in gaps],
            "message": (f"第 {page} 页存在未完成结构化读取的表格区域，补识别尚未通过验证" if len(gaps) == 1
                        else f"第 {page} 页有 {len(gaps)} 处表格区域未完成结构化读取，补识别尚未通过验证"),
            "recommended_action": "inspect_original_table_reading"})

    tables = reading_quality.get("mapping_incomplete_tables", [])
    cells_by_table: Dict[str, List[Dict[str, Any]]] = {table["fragment_uid"]: [] for table in tables}
    loose: Dict[tuple, List[Dict[str, Any]]] = {}
    for cell in reading_quality.get("unbound_money_cells", []):
        if cell.get("fragment_uid") in cells_by_table:
            cells_by_table[cell["fragment_uid"]].append(cell)
        else:
            loose.setdefault((cell.get("table_code") or "", cell.get("page")), []).append(cell)
    for table in tables:
        cells = cells_by_table[table["fragment_uid"]]
        arithmetic = "；合计与分项在多行系统性对不上，列映射可能错位" if table["arithmetic_conflicts"] else ""
        items.append({"id": f"reading:mapping:{table['fragment_uid']}",
            "type": "table_mapping_incomplete", "severity": "warn", "table_code": table["table_code"],
            "page_number": table["page"],
            "page_numbers": sorted({table["page"], *(c["page"] for c in cells if isinstance(c.get("page"), int))}),
            "reason": ",".join(sorted(table["reasons"])) or "column_arithmetic_conflict",
            "cells": [_cell_ref(c) for c in cells],
            "message": (f"第 {table['page']} 页《{table['title'] or table['table_code']}》映射不完整："
                        f"{table['unresolved_money_cell_count']} 个金额格没有对应到列含义{arithmetic}，"
                        "相关检查不能视为已完成"),
            "recommended_action": "inspect_table_column_mapping"})
    # 不属于任何映射不完整表的金额（如逻辑视图丢失的原物理格），按表种 + 页汇总。
    for (table_code, page), cells in sorted(loose.items(), key=lambda item: (item[0][0], item[0][1] or 0)):
        single = len(cells) == 1
        digest = hashlib.sha1("|".join(sorted(c["source_cell_id"] for c in cells)).encode()).hexdigest()[:12]
        items.append({
            "id": (f"reading:unbound:{cells[0]['source_cell_id']}" if single
                   else f"reading:unbound:{table_code or 'document'}:p{page}:{digest}"),
            "type": "source_cell_unbound", "severity": "warn", "table_code": table_code or None,
            "page_number": page, "page_numbers": [page],
            "bbox": cells[0]["bbox"] if single else _union_box([c["bbox"] for c in cells]),
            "reason": ",".join(sorted({c["reason"] for c in cells})),
            "cells": [_cell_ref(c) for c in cells],
            "message": (f"第 {page} 页金额 {cells[0]['raw_text']} 已保存，科目、列归属或规则准入需要复核" if single
                        else f"第 {page} 页有 {len(cells)} 个金额已保存，科目、列归属或规则准入需要复核"),
            "recommended_action": "inspect_original_cell_identity"})
    return items


def _build_review_items(
    parse_result: Dict[str, Any],
    table_instances: Sequence[Any],
    materialize_result: Dict[str, Any],
    pdf_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    review_items: List[Dict[str, Any]] = list(parse_result.get("legacy_projection_review_items") or [])
    instance_by_code = {instance.table_code: instance for instance in table_instances}
    recognized_codes = set(instance_by_code)
    core_coverage = len(recognized_codes.intersection(CORE_TABLES))
    missing_core = [code for code in CORE_TABLES if code not in recognized_codes]
    facts_count = int(materialize_result.get("facts_count") or 0)
    low_confidence_tables = list(materialize_result.get("low_confidence_tables") or [])
    document_profile = _detect_document_profile(pdf_path)
    skip_core_gap_review = document_profile in {"execution_budget_packet", "narrative_report"}
    suppress_sparse_core_gap_review = _should_suppress_sparse_core_gap_review(
        recognized_codes=recognized_codes,
        missing_core=missing_core,
        facts_count=facts_count,
        document_profile=document_profile,
    )
    suppress_low_confidence_review = _should_suppress_isolated_low_confidence_review(
        recognized_codes=recognized_codes,
        missing_core=missing_core,
        low_confidence_tables=low_confidence_tables,
        facts_count=facts_count,
        document_profile=document_profile,
    )
    skip_unknown_table_review = (
        skip_core_gap_review
        or suppress_sparse_core_gap_review
        or core_coverage >= len(CORE_TABLES) - 1
    )

    if missing_core and not skip_unknown_table_review:
        for table_code in parse_result.get("unknown_tables") or []:
            review_items.append(
                {
                    "id": f"{table_code}:unknown",
                    "type": "unknown_table",
                    "severity": "warn",
                    "table_code": table_code,
                    "message": "表格未能稳定识别为九张表之一",
                    "recommended_action": "review_table_title_and_headers",
                }
            )

    for table_code in low_confidence_tables:
        instance = instance_by_code.get(table_code)
        if suppress_low_confidence_review and not getattr(instance, "source_table_code", None):
            continue
        # A real source-bound low-confidence table remains reviewable even
        # when the other core tables are stable. Coverage cannot verify it.
        review_items.append(
            {
                "id": f"{table_code}:low_confidence",
                "type": "low_confidence_table",
                "severity": "warn",
                "table_code": table_code,
                "confidence": round(float(getattr(instance, "confidence", 0.0) or 0.0), 4),
                "page_number": getattr(instance, "page_number", None),
                "message": "该表识别置信度偏低，建议只复核该表头和关键金额列",
                "recommended_action": "review_header_and_amount_columns",
            }
        )

    if not skip_core_gap_review and not suppress_sparse_core_gap_review:
        for table_code in missing_core:
            review_items.append(
                {
                    "id": f"{table_code}:missing",
                    "type": "missing_core_table",
                    "severity": "warn",
                    "table_code": table_code,
                    "message": "核心九表未识别到，建议核对 PDF 中的表题或分页切分",
                    "recommended_action": "review_missing_table",
                }
            )

    if recognized_codes and not facts_count:
        if not _should_suppress_empty_fact_review(
            recognized_codes=recognized_codes,
            missing_core=missing_core,
            low_confidence_tables=low_confidence_tables,
            document_profile=document_profile,
        ):
            review_items.append(
                {
                    "id": "facts:none",
                    "type": "fact_materialization_empty",
                    "severity": "error",
                    "message": "已识别到表格，但未成功生成结构化 facts",
                    "recommended_action": "review_column_mappings_and_numeric_cells",
                }
            )

    review_items.sort(
        key=lambda item: (
            {"error": 0, "warn": 1, "info": 2}.get(str(item.get("severity")), 9),
            str(item.get("table_code") or item.get("id")),
        )
    )
    return review_items


def _should_suppress_sparse_core_gap_review(
    recognized_codes: set[str],
    missing_core: Sequence[str],
    facts_count: int,
    document_profile: str,
) -> bool:
    if document_profile != "canonical_nine_table":
        return False
    if facts_count <= 0:
        return False
    missing_core_set = set(missing_core)
    if missing_core_set == {"FIN_02_income", "FIN_05_general_public_expenditure"}:
        return len(recognized_codes) >= len(CORE_TABLES)
    if len(recognized_codes) < len(CORE_TABLES) + 1:
        return False
    return missing_core_set in (
        {"FIN_02_income"},
        {"FIN_03_expenditure"},
        {"FIN_04_fiscal_grant_total"},
    )


def _should_suppress_isolated_low_confidence_review(
    recognized_codes: set[str],
    missing_core: Sequence[str],
    low_confidence_tables: Sequence[str],
    facts_count: int,
    document_profile: str,
) -> bool:
    if document_profile != "canonical_nine_table":
        return False
    if len(recognized_codes) < len(CORE_TABLES) + 1:
        return False
    low_confidence_set = set(low_confidence_tables)
    if len(low_confidence_set) != 1:
        return False
    if not low_confidence_set.issubset({"FIN_02_income", "FIN_04_fiscal_grant_total"}):
        return False
    if facts_count <= 0 and low_confidence_set != {"FIN_04_fiscal_grant_total"}:
        return False
    return set(missing_core).issubset(
        {"FIN_02_income", "FIN_03_expenditure", "FIN_04_fiscal_grant_total"}
    )


def _should_suppress_empty_fact_review(
    recognized_codes: set[str],
    missing_core: Sequence[str],
    low_confidence_tables: Sequence[str],
    document_profile: str,
) -> bool:
    if document_profile in {"execution_budget_packet", "narrative_report"}:
        return True
    if document_profile != "canonical_nine_table":
        return False
    if len(recognized_codes) < len(CORE_TABLES) + 1:
        return False
    low_confidence_set = set(low_confidence_tables)
    if low_confidence_set != {"FIN_04_fiscal_grant_total"}:
        return False
    return set(missing_core).issubset(
        {"FIN_02_income", "FIN_03_expenditure", "FIN_04_fiscal_grant_total"}
    )


def _detect_document_profile(pdf_path: Optional[Path]) -> str:
    filename = str(getattr(pdf_path, "name", "") or "")
    if not filename:
        return "canonical_nine_table"
    if (
        ("预算执行和" in filename and "预算表" in filename)
        or ("棰勭畻鎵ц鍜" in filename and "棰勭畻琛" in filename)
    ):
        return "execution_budget_packet"
    if (
        "预算草案的报告" in filename
        or ("执行情况" in filename and "草案" in filename)
        or "棰勭畻鑽夋鐨勬姤鍛" in filename
        or ("鎵ц鎯呭喌" in filename and "鑽夋" in filename)
    ):
        return "narrative_report"
    return "canonical_nine_table"


def _backfill_local_organization_catalog(
    org_name: str,
    pdf_path: Path,
    ps_sync_summary: Optional[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not isinstance(ps_sync_summary, dict):
        return None
    if ps_sync_summary.get("status") != "done":
        return None
    if str(metadata.get("organization_id") or "").strip():
        return None

    department_name = str(ps_sync_summary.get("department_name") or "").strip()
    unit_name = str(ps_sync_summary.get("unit_name") or "").strip()
    if not department_name or not unit_name:
        return None

    storage = get_org_storage()
    created: List[Dict[str, str]] = []

    department = _find_org_by_name_and_level(storage, department_name, "department")
    if department is None:
        district = _find_best_district_parent(storage, department_name)
        department = Organization(
            id=Organization.generate_id(department_name, "department", district.id if district else None),
            name=department_name,
            level="department",
            parent_id=district.id if district else None,
            code=None,
            keywords=_catalog_keywords(department_name),
        )
        storage.add(department)
        created.append({"level": "department", "name": department_name})
    else:
        _merge_keywords(storage, department, _catalog_keywords(department_name))

    unit = _find_org_by_name_and_level(storage, unit_name, "unit", parent_id=department.id)
    if unit is None:
        unit = Organization(
            id=Organization.generate_id(unit_name, "unit", department.id),
            name=unit_name,
            level="unit",
            parent_id=department.id,
            code=None,
            keywords=_catalog_keywords(unit_name),
        )
        storage.add(unit)
        created.append({"level": "unit", "name": unit_name})
    else:
        _merge_keywords(storage, unit, _catalog_keywords(unit_name))

    if not created:
        return None
    return {
        "created": created,
        "source": "structured_ingest_ps_sync",
        "pdf": str(pdf_path),
        "org_name": org_name,
    }


def _find_org_by_name_and_level(
    storage,
    name: str,
    level: str,
    parent_id: Optional[str] = None,
):
    clean_name = str(name or "").strip()
    for org in storage.get_all():
        if org.level != level or org.name != clean_name:
            continue
        if parent_id is not None and org.parent_id != parent_id:
            continue
        return org
    return None


def _find_best_district_parent(storage, department_name: str):
    name = str(department_name or "")
    district_name = None
    if "普陀区" in name:
        district_name = "普陀区"
    elif "上海市" in name:
        district_name = "上海市"
    if not district_name:
        return None
    level = "district" if district_name.endswith("区") else "city"
    return _find_org_by_name_and_level(storage, district_name, level)


def _catalog_keywords(name: str) -> List[str]:
    clean_name = str(name or "").strip()
    variants = {clean_name}
    for suffix in ("单位", "部门", "委员会", "人民政府", "办公室", "办事处", "执法大队"):
        if clean_name.endswith(suffix) and len(clean_name) > len(suffix):
            variants.add(clean_name[: -len(suffix)])
    for bracket_open, bracket_close in (("（", "）"), ("(", ")")):
        if bracket_open in clean_name and bracket_close in clean_name:
            prefix, _, remainder = clean_name.partition(bracket_open)
            alias, _, suffix = remainder.partition(bracket_close)
            if prefix.strip():
                variants.add(prefix.strip() + suffix.strip())
            if alias.strip():
                variants.add(alias.strip())
    return sorted(item for item in variants if item)


def _merge_keywords(storage, org, keywords: Sequence[str]) -> None:
    merged = list(dict.fromkeys([*(org.keywords or []), *[str(item) for item in keywords if str(item).strip()]]))
    if merged != list(org.keywords or []):
        storage.update(org.id, {"keywords": merged})


def _normalize_org_name(raw_org_name: Any, pdf_path: Path) -> str:
    text = str(raw_org_name or "").strip()
    if text:
        cleaned = _strip_report_words(text)
        return cleaned or text

    raw_stem = str(pdf_path.stem or "").strip()
    parts = [part for part in re.split(r"[_]+", raw_stem) if str(part).strip()]
    candidates = [
        candidate
        for candidate in (_strip_report_words(part) for part in parts)
        if candidate
    ]
    if not candidates:
        fallback = _strip_report_words(raw_stem)
        return fallback or f"未命名单位_{pdf_path.stem}"

    unique_candidates = list(dict.fromkeys(candidates))
    unique_candidates.sort(
        key=lambda item: (
            _org_candidate_score(item),
            len(item),
        ),
        reverse=True,
    )
    return unique_candidates[0]


def _strip_report_words(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"20\d{2}(?:年度|年)?", "", value)
    value = re.sub(r"\d{2}(?:年度|年)", "", value)
    for token in (
        "预算执行情况和",
        "预算执行情况",
        "预算执行和",
        "预算草案",
        "单位预算公开",
        "部门预算公开",
        "单位预算",
        "部门预算",
        "区级单位",
        "区级部门",
        "区级",
        "编制说明",
        "预算表",
        "预算",
        "决算",
        "报告",
        "公开",
        "年度",
        "单位",
        "部门",
    ):
        value = value.replace(token, "")
    value = re.sub(r"[—\-_/]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"^[\s\-_（）()]+|[\s\-_（）()]+$", "", value)
    return value


def _org_candidate_score(name: str) -> int:
    value = str(name or "")
    score = 0
    if "上海市" in value:
        score += 3
    if "普陀区" in value:
        score += 3
    if any(token in value for token in ("中心", "局", "委员会", "办事处", "人民政府", "联合会", "办公室", "学校", "医院")):
        score += 2
    if any(token in value for token in ("本部", "本级")):
        score += 1
    return score


def _parse_year(raw_year: Any) -> Optional[int]:
    """解析年度，识别不到就返回 None。

    这里曾经兜底 return 2000，导致无法识别年份的材料在 PostgreSQL 里被写成
    fiscal_year=2000（缺口 B-02 / P0-03），并放大 report_id 碰撞。
    现在直接复用与内存路径同源的权威解析实现。
    """
    return parse_report_year(raw_year)


def _text_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _materialize_fiscal_facts_from_artifact(
    artifact: "ParseArtifact",
    *,
    org_name: Optional[str],
    fiscal_year: Optional[int],
    doc_type: Optional[str],
    file_hash: str,
):
    """把统一解析对象物化为 FiscalFact 列表（ZC-01 / SYS-15/16）。

    实现收敛到 :mod:`src.engine.fact_context`（AC-01）：规则链与结构化入库
    消费**同一个**事实构建实现，避免两套物化语义各自演化。每个逻辑表实例
    独立物化：实例身份（instance_uid）+ 主体/年度 + 资金范围进入事实身份，
    同文档同种表的不同实例不互相覆盖；跨页实例按合并视图物化，证据保留
    各自物理页。
    """
    from src.engine.fact_context import (
        _canonical_mappings_for_fragment,
        _fragment_for_instance,
    )
    from src.services.fiscal_fact_materializer import FiscalFactMaterializer
    from src.services.document_profile_resolver import _kind_from_doc_type

    materializer = FiscalFactMaterializer(None)
    budget_stage = {"budget": "年初预算", "final": "决算"}.get(
        _kind_from_doc_type(doc_type), None
    )
    facts = []
    for instance in artifact.logical_tables:
        fragment = _fragment_for_instance(artifact, instance)
        if fragment is None:
            continue
        mappings = (
            [dict(item) for item in instance.column_mappings]
            or _canonical_mappings_for_fragment(fragment, instance.table_code)
        )
        facts.extend(
            materializer.build_fiscal_facts_for_table(
                fragment,
                mappings,
                org_name=instance.org_name or org_name,
                fiscal_year=instance.fiscal_year or fiscal_year,
                period="本年",
                budget_stage=budget_stage,
                instance_uid=instance.instance_uid,
                file_hash=file_hash,
            )
        )
    from src.services.fact_column_arithmetic import constrain_column_arithmetic
    from src.services.table_reading_assessment import constrain_reading_facts
    return constrain_column_arithmetic(constrain_reading_facts(artifact, facts))


def _canonical_mappings_for_fragment(fragment: Any, table_code: str) -> List[Dict[str, Any]]:
    """按表片段表头与九表规则的 measure_aliases 推导列映射（纯函数）。

    委托 :mod:`src.engine.fact_context` 的同源实现（AC-01）：列映射语义只有
    一份，规则链与入库不可能各猜各的。匹配不到的列不进入映射。
    """
    from src.engine.fact_context import _canonical_mappings_for_fragment as _shared

    return _shared(fragment, table_code)
