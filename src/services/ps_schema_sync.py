"""
Sync structured ingest results into a PS / tianbaoxitong-aligned schema.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import asyncpg

try:
    from src.services.org_storage import get_org_storage
except Exception:
    get_org_storage = None


class PSSharedSchemaSync:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def sync(
        self,
        document_version_id: int,
        org_name: str,
        fiscal_year: int,
        doc_type: str,
        pdf_path: Path,
        checksum: str,
        organization_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        scope = self.resolve_scope(
            org_name=org_name,
            organization_id=organization_id,
        )
        department_name = str(scope["department_name"])
        unit_name = str(scope["unit_name"])
        department_id = await self._ensure_department(
            department_name,
            preferred_code=scope.get("department_code"),
        )
        unit_id = await self._ensure_unit(
            department_id,
            unit_name,
            preferred_code=scope.get("unit_code"),
        )
        report_type = self._normalize_report_type(doc_type)
        report_id = await self._upsert_report(
            department_id=department_id,
            unit_id=unit_id,
            fiscal_year=fiscal_year,
            report_type=report_type,
            pdf_path=pdf_path,
            checksum=checksum,
        )

        table_count = await self._sync_table_data(
            report_id=report_id,
            department_id=department_id,
            fiscal_year=fiscal_year,
            report_type=report_type,
            document_version_id=document_version_id,
        )
        line_item_count = await self._sync_line_items(
            report_id=report_id,
            department_id=department_id,
            fiscal_year=fiscal_year,
            report_type=report_type,
            document_version_id=document_version_id,
        )

        return {
            "status": "done",
            "report_id": str(report_id),
            "department_name": department_name,
            "unit_name": unit_name,
            "report_type": report_type,
            "match_mode": scope.get("match_mode"),
            "matched_organization_id": scope.get("matched_organization_id"),
            "table_data_count": table_count,
            "line_item_count": line_item_count,
        }

    def resolve_scope(
        self,
        org_name: str,
        organization_id: Optional[str] = None,
        org_records: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Optional[str]]:
        records = (
            [self._to_org_record(record) for record in org_records]
            if org_records is not None
            else self._load_org_records()
        )
        if not records:
            department_name, unit_name = self._derive_scope_names(org_name)
            return {
                "department_name": department_name,
                "unit_name": unit_name,
                "department_code": None,
                "unit_code": None,
                "matched_organization_id": None,
                "match_mode": "fallback_name",
            }

        by_id = {str(record["id"]): record for record in records if record.get("id")}
        if organization_id:
            selected = by_id.get(str(organization_id))
            if selected is not None:
                resolved = self._resolve_from_selected_record(selected, by_id)
                if resolved is not None:
                    resolved["match_mode"] = "organization_id"
                    return resolved

        unit_records = [record for record in records if record.get("level") == "unit"]
        department_records = [record for record in records if record.get("level") == "department"]

        unit_match = self._best_org_match(org_name, unit_records)
        if unit_match is not None:
            parent = by_id.get(str(unit_match.get("parent_id") or ""))
            if self._should_promote_unit_scope(unit_match, parent):
                return {
                    "department_name": str(unit_match["name"]),
                    "unit_name": str(unit_match["name"]),
                    "department_code": None,
                    "unit_code": self._text_or_none(unit_match.get("code")),
                    "matched_organization_id": str(unit_match["id"]),
                    "match_mode": "name_unit_promoted",
                }
            if parent is not None:
                return {
                    "department_name": str(parent["name"]),
                    "unit_name": str(unit_match["name"]),
                    "department_code": self._text_or_none(parent.get("code")),
                    "unit_code": self._text_or_none(unit_match.get("code")),
                    "matched_organization_id": str(unit_match["id"]),
                    "match_mode": "name_unit",
                }

        department_match = self._best_org_match(org_name, department_records)
        if department_match is not None:
            return {
                "department_name": str(department_match["name"]),
                "unit_name": str(org_name or department_match["name"]).strip() or str(department_match["name"]),
                "department_code": self._text_or_none(department_match.get("code")),
                "unit_code": self._synthetic_unit_code(
                    self._text_or_none(department_match.get("code")),
                    str(org_name or department_match["name"]).strip() or str(department_match["name"]),
                ),
                "matched_organization_id": str(department_match["id"]),
                "match_mode": "name_department",
            }

        department_name, unit_name = self._derive_scope_names(org_name)
        return {
            "department_name": department_name,
            "unit_name": unit_name,
            "department_code": None,
            "unit_code": None,
            "matched_organization_id": None,
            "match_mode": "fallback_name",
        }

    def _load_org_records(self) -> List[Dict[str, Any]]:
        if get_org_storage is None:
            return []
        try:
            storage = get_org_storage()
            return [self._to_org_record(record) for record in storage.get_all()]
        except Exception:
            return []

    def _to_org_record(self, record: Any) -> Dict[str, Any]:
        if isinstance(record, dict):
            payload = dict(record)
        elif hasattr(record, "model_dump"):
            payload = dict(record.model_dump())
        else:
            payload = {
                "id": getattr(record, "id", None),
                "name": getattr(record, "name", None),
                "level": getattr(record, "level", None),
                "parent_id": getattr(record, "parent_id", None),
                "code": getattr(record, "code", None),
                "keywords": getattr(record, "keywords", None),
            }
        payload["id"] = self._text_or_none(payload.get("id"))
        payload["name"] = self._text_or_none(payload.get("name"))
        payload["level"] = self._text_or_none(payload.get("level"))
        payload["parent_id"] = self._text_or_none(payload.get("parent_id"))
        payload["code"] = self._text_or_none(payload.get("code"))
        keywords = payload.get("keywords") or []
        payload["keywords"] = [
            str(keyword).strip()
            for keyword in keywords
            if str(keyword).strip()
        ]
        return payload

    def _resolve_from_selected_record(
        self,
        selected: Dict[str, Any],
        by_id: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Optional[str]]]:
        if selected.get("level") == "unit":
            parent = by_id.get(str(selected.get("parent_id") or ""))
            if self._should_promote_unit_scope(selected, parent):
                return {
                    "department_name": str(selected["name"]),
                    "unit_name": str(selected["name"]),
                    "department_code": None,
                    "unit_code": self._text_or_none(selected.get("code")),
                    "matched_organization_id": str(selected["id"]),
                }
            if parent is None:
                return None
            return {
                "department_name": str(parent["name"]),
                "unit_name": str(selected["name"]),
                "department_code": self._text_or_none(parent.get("code")),
                "unit_code": self._text_or_none(selected.get("code")),
                "matched_organization_id": str(selected["id"]),
            }

        if selected.get("level") == "department":
            return {
                "department_name": str(selected["name"]),
                "unit_name": str(selected["name"]),
                "department_code": self._text_or_none(selected.get("code")),
                "unit_code": self._synthetic_unit_code(
                    self._text_or_none(selected.get("code")),
                    str(selected["name"]),
                ),
                "matched_organization_id": str(selected["id"]),
            }

        return None

    def _should_promote_unit_scope(
        self,
        unit_record: Dict[str, Any],
        parent_record: Optional[Dict[str, Any]],
    ) -> bool:
        unit_name = str(unit_record.get("name") or "").strip()
        if not self._is_department_like_name(unit_name):
            return False
        if parent_record is None:
            return True
        parent_name = str(parent_record.get("name") or "").strip()
        return not self._names_are_related(unit_name, parent_name)

    def _is_department_like_name(self, name: str) -> bool:
        clean_name = str(name or "").strip()
        if not clean_name or clean_name.endswith("本级"):
            return False
        if clean_name.endswith("局"):
            return True
        patterns = (
            "人民政府办公室",
            "街道办事处",
            "镇人民政府",
            "委员会",
            "总工会",
            "人民法院",
            "人民检察院",
        )
        return any(clean_name.endswith(pattern) for pattern in patterns)

    def _names_are_related(self, left: str, right: str) -> bool:
        left_norm = self._normalize_match_text(left)
        right_norm = self._normalize_match_text(right)
        if not left_norm or not right_norm:
            return False
        if left_norm in right_norm or right_norm in left_norm:
            return True
        left_core = self._strip_admin_prefix(left_norm)
        right_core = self._strip_admin_prefix(right_norm)
        if not left_core or not right_core:
            return False
        return left_core in right_core or right_core in left_core

    def _strip_admin_prefix(self, text: str) -> str:
        value = str(text or "")
        for token in (
            "上海市普陀区",
            "上海市",
            "普陀区",
            "上海",
            "人民政府",
            "中国共产党",
            "中共",
        ):
            value = value.replace(token, "")
        return value

    def _best_org_match(
        self,
        target_name: str,
        records: Sequence[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        target_variants = self._name_variants(target_name)
        best_record: Optional[Dict[str, Any]] = None
        best_score = 0
        for record in records:
            candidate_variants = self._name_variants(record.get("name"))
            candidate_variants.extend(
                variant
                for keyword in record.get("keywords") or []
                for variant in self._name_variants(keyword)
            )
            score = self._match_score(target_variants, candidate_variants)
            if score > best_score:
                best_score = score
                best_record = record
        if best_score >= 100:
            return best_record
        return None

    def _name_variants(self, text: Any) -> List[str]:
        base = self._normalize_match_text(text)
        if not base:
            return []
        variants = {base}
        if base.endswith("单位") and len(base) > 2:
            variants.add(base[:-2])
        else:
            variants.add(f"{base}单位")
        return sorted(variants)

    def _normalize_match_text(self, text: Any) -> str:
        value = str(text or "").strip()
        value = re.sub(r"20\d{2}(?:年度|年)?", "", value)
        value = re.sub(r"\d{2}(?:年度|年)", "", value)
        value = re.sub(r"(预算|决算|报告|公开|年度|pdf)", "", value, flags=re.IGNORECASE)
        value = re.sub(r"[\s（）()【】\[\]<>《》·,，、.\-_/]+", "", value)
        return value

    def _match_score(self, targets: Sequence[str], candidates: Sequence[str]) -> int:
        target_set = {item for item in targets if item}
        candidate_set = {item for item in candidates if item}
        if not target_set or not candidate_set:
            return 0
        if target_set & candidate_set:
            return 120
        if any(target in candidate or candidate in target for target in target_set for candidate in candidate_set):
            return 100
        return 0

    def _text_or_none(self, value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    def _synthetic_unit_code(self, department_code: Optional[str], unit_name: str) -> str:
        if department_code:
            return f"{department_code}__SELF"
        return self._auto_code("AUTO_UNIT", unit_name)

    def _should_reuse_existing_named_unit(
        self,
        existing_code: Optional[str],
        preferred_code: Optional[str],
    ) -> bool:
        existing_text = self._text_or_none(existing_code)
        preferred_text = self._text_or_none(preferred_code)
        if not existing_text:
            return True
        if not preferred_text:
            return True
        if existing_text == preferred_text:
            return True
        if existing_text.endswith("__SELF"):
            return False
        if existing_text.startswith("AUTO_UNIT_"):
            return False
        return True

    def _derive_scope_names(self, org_name: str) -> Tuple[str, str]:
        clean_name = str(org_name or "").strip()
        if clean_name.endswith("单位") and len(clean_name) > 2:
            return clean_name[:-2], clean_name
        return clean_name, clean_name

    def _normalize_report_type(self, doc_type: str) -> str:
        normalized = str(doc_type or "").strip().lower()
        if normalized in {"budget", "预算", "ys"}:
            return "BUDGET"
        if normalized in {"final", "决算", "js", "settlement"}:
            return "FINAL"
        return "BUDGET"

    def _auto_code(self, prefix: str, name: str) -> str:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16].upper()
        return f"{prefix}_{digest}"

    async def _ensure_department(self, department_name: str, preferred_code: Optional[str] = None):
        preferred_code = self._text_or_none(preferred_code)
        if preferred_code:
            existing = await self.conn.fetchrow(
                """
                SELECT id, code
                FROM org_department
                WHERE code = $1
                LIMIT 1
                """,
                preferred_code,
            )
            if existing:
                await self.conn.execute(
                    """
                    UPDATE org_department
                    SET name = $2, updated_at = NOW()
                    WHERE id = $1
                    """,
                    existing["id"],
                    department_name,
                )
                return existing["id"]

        existing = await self.conn.fetchrow(
            """
            SELECT id, code
            FROM org_department
            WHERE name = $1
            ORDER BY created_at ASC NULLS LAST, id ASC
            LIMIT 1
            """,
            department_name,
        )
        if existing:
            if preferred_code and str(existing["code"] or "").startswith("AUTO_DEPT_"):
                await self.conn.execute(
                    """
                    UPDATE org_department
                    SET code = $2, updated_at = NOW()
                    WHERE id = $1
                    """,
                    existing["id"],
                    preferred_code,
                )
            return existing["id"]

        return await self.conn.fetchval(
            """
            INSERT INTO org_department (code, name)
            VALUES ($1, $2)
            ON CONFLICT (code)
            DO UPDATE SET
                name = EXCLUDED.name,
                updated_at = NOW()
            RETURNING id
            """,
            preferred_code or self._auto_code("AUTO_DEPT", department_name),
            department_name,
        )

    async def _ensure_unit(
        self,
        department_id,
        unit_name: str,
        preferred_code: Optional[str] = None,
    ):
        preferred_code = self._text_or_none(preferred_code)
        if preferred_code:
            existing = await self.conn.fetchrow(
                """
                SELECT id, code
                FROM org_unit
                WHERE code = $1
                LIMIT 1
                """,
                preferred_code,
            )
            if existing:
                await self.conn.execute(
                    """
                    UPDATE org_unit
                    SET department_id = $2, name = $3, updated_at = NOW()
                    WHERE id = $1
                    """,
                    existing["id"],
                    department_id,
                    unit_name,
                )
                return existing["id"]

        existing = await self.conn.fetchrow(
            """
            SELECT id, code
            FROM org_unit
            WHERE department_id = $1 AND name = $2
            ORDER BY created_at ASC NULLS LAST, id ASC
            LIMIT 1
            """,
            department_id,
            unit_name,
        )
        if existing:
            if not self._should_reuse_existing_named_unit(existing.get("code"), preferred_code):
                existing = None
            elif preferred_code and str(existing["code"] or "").startswith("AUTO_UNIT_"):
                await self.conn.execute(
                    """
                    UPDATE org_unit
                    SET code = $2, updated_at = NOW()
                    WHERE id = $1
                    """,
                    existing["id"],
                    preferred_code,
                )
            if existing is not None:
                return existing["id"]

        return await self.conn.fetchval(
            """
            INSERT INTO org_unit (department_id, code, name)
            VALUES ($1, $2, $3)
            ON CONFLICT (code)
            DO UPDATE SET
                department_id = EXCLUDED.department_id,
                name = EXCLUDED.name,
                updated_at = NOW()
            RETURNING id
            """,
            department_id,
            preferred_code or self._auto_code("AUTO_UNIT", unit_name),
            unit_name,
        )

    async def _upsert_report(
        self,
        department_id,
        unit_id,
        fiscal_year: int,
        report_type: str,
        pdf_path: Path,
        checksum: str,
    ):
        return await self.conn.fetchval(
            """
            INSERT INTO org_dept_annual_report (
                department_id,
                unit_id,
                year,
                report_type,
                file_name,
                file_path,
                file_hash,
                file_size
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (department_id, unit_id, year, report_type)
            DO UPDATE SET
                file_name = EXCLUDED.file_name,
                file_path = EXCLUDED.file_path,
                file_hash = EXCLUDED.file_hash,
                file_size = EXCLUDED.file_size,
                updated_at = NOW()
            RETURNING id
            """,
            department_id,
            unit_id,
            fiscal_year,
            report_type,
            pdf_path.name,
            str(pdf_path),
            checksum,
            int(pdf_path.stat().st_size),
        )

    async def _sync_table_data(
        self,
        report_id,
        department_id,
        fiscal_year: int,
        report_type: str,
        document_version_id: int,
    ) -> int:
        await self.conn.execute(
            "DELETE FROM org_dept_table_data WHERE report_id = $1",
            report_id,
        )
        instances = await self.conn.fetch(
            """
            SELECT table_code, source_title
            FROM fiscal_table_instances
            WHERE document_version_id = $1
            ORDER BY page_number NULLS LAST, row_start NULLS LAST, id
            """,
            document_version_id,
        )
        inserted = 0
        for instance in instances:
            cells = await self.conn.fetch(
                """
                SELECT row_idx, col_idx, raw_text, normalized_text, numeric_value, page_number,
                       bbox, is_header, confidence
                FROM fiscal_table_cells
                WHERE document_version_id = $1 AND table_code = $2
                ORDER BY row_idx, col_idx
                """,
                document_version_id,
                instance["table_code"],
            )
            payload = self.build_table_payload(
                table_code=str(instance["table_code"]),
                source_title=str(instance["source_title"] or ""),
                cells=[dict(cell) for cell in cells],
            )
            await self.conn.execute(
                """
                INSERT INTO org_dept_table_data (
                    report_id,
                    department_id,
                    year,
                    report_type,
                    table_key,
                    table_title,
                    page_numbers,
                    row_count,
                    col_count,
                    data_json
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
                ON CONFLICT (report_id, table_key)
                DO UPDATE SET
                    table_title = EXCLUDED.table_title,
                    page_numbers = EXCLUDED.page_numbers,
                    row_count = EXCLUDED.row_count,
                    col_count = EXCLUDED.col_count,
                    data_json = EXCLUDED.data_json,
                    updated_at = NOW()
                """,
                report_id,
                department_id,
                fiscal_year,
                report_type,
                instance["table_code"],
                instance["source_title"],
                payload["page_numbers"],
                payload["row_count"],
                payload["col_count"],
                json.dumps(payload["data_json"], ensure_ascii=False),
            )
            inserted += 1
        return inserted

    def build_table_payload(
        self,
        table_code: str,
        source_title: str,
        cells: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        rows: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        page_numbers = sorted(
            {
                int(cell["page_number"])
                for cell in cells
                if cell.get("page_number") is not None
            }
        )
        max_col_idx = -1
        for cell in cells:
            row_idx = int(cell["row_idx"])
            col_idx = int(cell["col_idx"])
            max_col_idx = max(max_col_idx, col_idx)
            rows[row_idx].append(
                {
                    "col_index": col_idx,
                    "raw_text": cell.get("raw_text"),
                    "normalized_text": cell.get("normalized_text"),
                    "numeric_value": cell.get("numeric_value"),
                    "page_number": cell.get("page_number"),
                    "bbox": cell.get("bbox"),
                    "is_header": bool(cell.get("is_header") or False),
                    "confidence": cell.get("confidence"),
                }
            )

        ordered_rows = []
        for row_idx in sorted(rows):
            ordered_cells = sorted(rows[row_idx], key=lambda item: int(item["col_index"]))
            ordered_rows.append(
                {
                    "row_index": row_idx,
                    "cells": ordered_cells,
                }
            )

        return {
            "page_numbers": page_numbers,
            "row_count": len(rows),
            "col_count": max_col_idx + 1 if max_col_idx >= 0 else 0,
            "data_json": {
                "source_system": "GovBudgetChecker",
                "table_key": table_code,
                "table_title": source_title,
                "rows": ordered_rows,
            },
        }

    async def _sync_line_items(
        self,
        report_id,
        department_id,
        fiscal_year: int,
        report_type: str,
        document_version_id: int,
    ) -> int:
        await self.conn.execute(
            "DELETE FROM org_dept_line_items WHERE report_id = $1",
            report_id,
        )
        fact_rows = await self.conn.fetch(
            """
            SELECT table_code, row_order, classification_type, classification_code,
                   classification_name, measure, amount, source_page_number,
                   parse_confidence, extra_dims
            FROM fact_fiscal_line_items
            WHERE document_version_id = $1
            ORDER BY table_code, row_order, classification_code NULLS FIRST, measure
            """,
            document_version_id,
        )
        line_items = self.build_line_item_rows([dict(row) for row in fact_rows])
        for item in line_items:
            await self.conn.execute(
                """
                INSERT INTO org_dept_line_items (
                    report_id,
                    department_id,
                    year,
                    report_type,
                    table_key,
                    row_index,
                    class_code,
                    type_code,
                    item_code,
                    item_name,
                    values_json
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                ON CONFLICT (report_id, table_key, row_index)
                DO UPDATE SET
                    class_code = EXCLUDED.class_code,
                    type_code = EXCLUDED.type_code,
                    item_code = EXCLUDED.item_code,
                    item_name = EXCLUDED.item_name,
                    values_json = EXCLUDED.values_json,
                    updated_at = NOW()
                """,
                report_id,
                department_id,
                fiscal_year,
                report_type,
                item["table_key"],
                item["row_index"],
                item["class_code"],
                item["type_code"],
                item["item_code"],
                item["item_name"],
                json.dumps(item["values_json"], ensure_ascii=False),
            )
        return len(line_items)

    def build_line_item_rows(self, fact_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[Tuple[str, int, Optional[str], str], Dict[str, Any]] = {}
        for row in fact_rows:
            row_index = int(row.get("row_order") or 0)
            item_name = str(row.get("classification_name") or "")
            key = (
                str(row.get("table_code") or ""),
                row_index,
                str(row["classification_code"]) if row.get("classification_code") is not None else None,
                item_name,
            )
            entry = grouped.setdefault(
                key,
                {
                    "table_key": key[0],
                    "row_index": row_index,
                    "item_name": item_name,
                    "classification_type": row.get("classification_type"),
                    "class_code": None,
                    "type_code": None,
                    "item_code": None,
                    "values_json": {},
                    "_pages": set(),
                    "_confidence": [],
                },
            )

            class_code, type_code, item_code = self._split_classification_code(
                row.get("classification_code")
            )
            if entry["class_code"] is None:
                entry["class_code"] = class_code
            if entry["type_code"] is None:
                entry["type_code"] = type_code
            if entry["item_code"] is None:
                entry["item_code"] = item_code

            measure = str(row.get("measure") or "")
            if measure:
                entry["values_json"][measure] = (
                    float(row["amount"]) if row.get("amount") is not None else None
                )
            if row.get("source_page_number") is not None:
                entry["_pages"].add(int(row["source_page_number"]))
            if row.get("parse_confidence") is not None:
                entry["_confidence"].append(float(row["parse_confidence"]))

        result: List[Dict[str, Any]] = []
        for _, item in sorted(grouped.items(), key=lambda pair: (pair[0][0], pair[0][1], pair[0][3])):
            pages = sorted(item.pop("_pages"))
            confidences = item.pop("_confidence")
            if pages or confidences or item.get("classification_type"):
                item["values_json"]["_meta"] = {
                    "classification_type": item.pop("classification_type", None),
                    "source_page_numbers": pages,
                    "parse_confidence": min(confidences) if confidences else None,
                }
            else:
                item.pop("classification_type", None)
            result.append(item)
        return result

    def _split_classification_code(
        self,
        classification_code: Optional[str],
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        if classification_code is None:
            return None, None, None
        text = str(classification_code).strip()
        if not text.isdigit():
            return None, None, None
        class_code = text[:3] if len(text) >= 3 else None
        type_code = text[:5] if len(text) >= 5 else None
        item_code = text[:7] if len(text) >= 7 else None
        return class_code, type_code, item_code
