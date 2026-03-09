"""
Run structured ingest against PostgreSQL without affecting legacy checks.
"""

from __future__ import annotations

import asyncio
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
from src.services.org_storage import get_org_storage
from src.services.pdf_parser import PDFParser
from src.services.ps_schema_sync import PSSharedSchemaSync
from src.services.table_recognizer import TableRecognizer

logger = logging.getLogger(__name__)

_DB_READY = False
_DB_READY_LOCK = asyncio.Lock()

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


async def run_structured_ingest(
    job_id: str,
    pdf_path: Path,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    if not await ensure_structured_ingest_ready():
        return {
            "job_id": job_id,
            "status": "skipped",
            "reason": "database_unavailable",
            "review_item_count": 0,
            "review_items": [],
        }

    conn = None
    try:
        conn = await DatabaseConnection.acquire()
        checksum = metadata.get("checksum") or _sha256_file(pdf_path)
        org_name = _normalize_org_name(metadata.get("organization_name"), pdf_path)
        fiscal_year = _parse_year(metadata.get("report_year") or metadata.get("fiscal_year"))
        doc_type = str(metadata.get("doc_type") or metadata.get("report_kind") or "unknown")

        document_info = await _ensure_document_version(
            conn=conn,
            org_name=org_name,
            fiscal_year=fiscal_year,
            doc_type=doc_type,
            checksum=checksum,
            storage_key=str(pdf_path),
        )
        version_id = document_info["document_version_id"]

        parser = PDFParser(conn)
        parse_result = await parser.parse_pdf(str(pdf_path), version_id)

        recognizer = TableRecognizer(conn)
        instances = await recognizer.recognize_tables(version_id)
        await recognizer.save_table_instances(version_id, instances)

        materializer = FiscalFactMaterializer(conn)
        materialize_result = await materializer.materialize(version_id)
        document_profile = _detect_document_profile(pdf_path)
        ps_sync_summary: Dict[str, Any]
        try:
            ps_sync_summary = await PSSharedSchemaSync(conn).sync(
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
        local_org_backfill = _backfill_local_organization_catalog(
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
            "unknown_tables": parse_result.get("unknown_tables") or [],
            "missing_optional_tables": missing_optional_tables,
            "low_confidence_tables": materialize_result.get("low_confidence_tables") or [],
            "document_profile": document_profile,
            "ps_sync": ps_sync_summary,
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
        logger.exception("Structured ingest failed for job %s", job_id)
        return {
            "job_id": job_id,
            "status": "error",
            "error": str(exc),
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
    finally:
        if conn is not None:
            await DatabaseConnection.release(conn)


async def _ensure_document_version(
    conn,
    org_name: str,
    fiscal_year: int,
    doc_type: str,
    checksum: str,
    storage_key: str,
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
    document_id = await conn.fetchval(
        """
        INSERT INTO fiscal_documents (org_unit_id, fiscal_year, doc_type)
        VALUES ($1, $2, $3)
        ON CONFLICT (org_unit_id, fiscal_year, doc_type)
        DO UPDATE SET doc_type = EXCLUDED.doc_type
        RETURNING id
        """,
        org_id,
        fiscal_year,
        doc_type,
    )
    version_id = await conn.fetchval(
        """
        INSERT INTO fiscal_document_versions (document_id, file_hash, storage_key)
        VALUES ($1, $2, $3)
        ON CONFLICT (document_id, file_hash)
        DO UPDATE SET storage_key = EXCLUDED.storage_key
        RETURNING id
        """,
        document_id,
        checksum,
        storage_key,
    )
    return {
        "org_unit_id": int(org_id),
        "document_id": int(document_id),
        "document_version_id": int(version_id),
    }


def _build_review_items(
    parse_result: Dict[str, Any],
    table_instances: Sequence[Any],
    materialize_result: Dict[str, Any],
    pdf_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    review_items: List[Dict[str, Any]] = []
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
        if suppress_low_confidence_review:
            continue
        instance = instance_by_code.get(table_code)
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


def _parse_year(raw_year: Any) -> int:
    try:
        value = int(str(raw_year).strip())
        if 2000 <= value <= 2099:
            return value
    except Exception:
        pass
    return 2000


def _text_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
