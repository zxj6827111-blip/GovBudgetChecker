"""
Table recognition for the canonical fiscal nine tables.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Dict, Iterable, List, Optional

import asyncpg

from src.services.fiscal_table_rules import (
    NINE_TABLE_RULES,
    detect_table_code,
    match_measure,
    normalize_text,
)

logger = logging.getLogger(__name__)


@dataclass
class ColumnMapping:
    source_col_idx: int
    source_col_name: str
    canonical_measure: str
    confidence: float


@dataclass
class TableInstance:
    table_code: str
    source_title: str
    confidence: float
    page_number: Optional[int]
    row_start: Optional[int]
    row_end: Optional[int]
    column_mappings: List[ColumnMapping]


def _build_legacy_rules() -> Dict[str, Dict[str, object]]:
    legacy: Dict[str, Dict[str, object]] = {}
    for code, rule in NINE_TABLE_RULES.items():
        legacy[code] = {
            "title_keywords": list(rule.aliases),
            "required_columns": list(rule.required_headers),
            "optional_columns": list(rule.optional_headers),
            "measure_patterns": {
                alias: measure
                for measure, aliases in rule.measure_aliases.items()
                for alias in aliases
            },
        }
    return legacy


TABLE_RECOGNITION_RULES = _build_legacy_rules()


class TableRecognizer:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def recognize_tables(self, document_version_id: int) -> List[TableInstance]:
        cells = await self.conn.fetch(
            """
            SELECT table_code, row_idx, col_idx, raw_text, page_number
            FROM fiscal_table_cells
            WHERE document_version_id = $1
            ORDER BY table_code, row_idx, col_idx
            """,
            document_version_id,
        )

        if not cells:
            logger.warning("No cells found for document_version %s", document_version_id)
            return []

        tables_by_code: Dict[str, List[asyncpg.Record]] = {}
        for cell in cells:
            tables_by_code.setdefault(cell["table_code"], []).append(cell)

        recognized: List[TableInstance] = []
        for source_table_code, table_cells in tables_by_code.items():
            instance = self._recognize_single_table(source_table_code, table_cells)
            if instance is None:
                continue
            recognized.append(instance)
            logger.info(
                "Recognized %s from %s (confidence %.2f)",
                instance.table_code,
                source_table_code,
                instance.confidence,
            )

        return recognized

    def _recognize_single_table(
        self,
        source_table_code: str,
        cells: List[asyncpg.Record],
    ) -> Optional[TableInstance]:
        title = self._extract_title(cells)
        header_columns = self._extract_header_columns(cells)
        headers = [header for _, header in header_columns]
        table_code, confidence = detect_table_code(
            title=title,
            headers=headers,
            source_hint=source_table_code,
        )

        if table_code is None:
            logger.warning(
                "Could not recognize table %s with title '%s'",
                source_table_code,
                title[:80],
            )
            return None

        measure_columns = self._detect_measure_columns(cells)
        column_mappings = self._create_column_mappings(
            header_columns,
            NINE_TABLE_RULES[table_code],
            measure_columns=measure_columns,
        )
        page_number = self._extract_page_number(cells)

        return TableInstance(
            table_code=table_code,
            source_title=title,
            confidence=confidence,
            page_number=page_number,
            row_start=min(int(cell["row_idx"]) for cell in cells),
            row_end=max(int(cell["row_idx"]) for cell in cells),
            column_mappings=column_mappings,
        )

    def _extract_title(self, cells: Iterable[asyncpg.Record]) -> str:
        title_cells = sorted(
            (cell for cell in cells if int(cell["row_idx"]) == 0 and cell["raw_text"]),
            key=lambda item: item["col_idx"],
        )
        if title_cells:
            return " ".join(str(cell["raw_text"]).strip() for cell in title_cells)

        first_row = sorted(
            (cell for cell in cells if int(cell["row_idx"]) == 1 and cell["raw_text"]),
            key=lambda item: item["col_idx"],
        )
        return " ".join(str(cell["raw_text"]).strip() for cell in first_row)

    def _extract_page_number(self, cells: Iterable[asyncpg.Record]) -> Optional[int]:
        for cell in cells:
            try:
                value = cell.get("page_number")
            except AttributeError:
                value = cell["page_number"] if "page_number" in cell else None
            if value is not None:
                return int(value)
        return None

    def _extract_headers(self, cells: Iterable[asyncpg.Record]) -> List[str]:
        return [header for _, header in self._extract_header_columns(cells)]

    def _extract_header_columns(self, cells: Iterable[asyncpg.Record]) -> List[tuple[int, str]]:
        rows: Dict[int, List[asyncpg.Record]] = {}
        for cell in cells:
            rows.setdefault(int(cell["row_idx"]), []).append(cell)

        candidate_rows: List[tuple[int, List[tuple[int, str]]]] = []
        for row_idx in sorted(rows):
            if row_idx == 0:
                continue
            ordered = sorted(rows[row_idx], key=lambda item: int(item["col_idx"]))
            entries = [
                (int(cell["col_idx"]), str(cell["raw_text"]).strip())
                for cell in ordered
                if str(cell["raw_text"]).strip()
            ]
            if not entries:
                continue
            numeric_cells = sum(1 for _, text in entries if self._looks_numeric(text))
            if numeric_cells >= max(1, len(entries) // 2):
                continue
            candidate_rows.append((row_idx, entries))
            if len(candidate_rows) == 3:
                break

        if not candidate_rows:
            return []

        base_row_idx, base_row = max(candidate_rows, key=lambda item: (len(item[1]), -item[0]))
        header_by_col = {col_idx: text for col_idx, text in base_row}

        for row_idx, row in candidate_rows:
            if row_idx == base_row_idx:
                continue
            if len(row) == 1 and len(base_row) >= 3:
                continue
            for col_idx, text in row:
                existing = header_by_col.get(col_idx)
                if not existing:
                    header_by_col[col_idx] = text
                    continue
                if text == existing or text in existing or existing in text:
                    continue
                if row_idx < base_row_idx:
                    header_by_col[col_idx] = f"{text} {existing}"
                else:
                    header_by_col[col_idx] = f"{existing} {text}"

        return sorted(header_by_col.items())

    def _match_table(self, title: str, headers: List[str], rule: Dict[str, object]) -> float:
        normalized_title = normalize_text(title)
        normalized_headers = [normalize_text(header) for header in headers if normalize_text(header)]

        title_score = 0.0
        for keyword in rule.get("title_keywords", []):
            normalized_keyword = normalize_text(str(keyword))
            if not normalized_keyword:
                continue
            if normalized_keyword in normalized_title:
                title_score = max(title_score, 1.0)
                continue
            overlap = sum(1 for token in normalized_keyword if token and token in normalized_title)
            title_score = max(title_score, overlap / max(len(normalized_keyword), 1))

        required_columns = [normalize_text(str(item)) for item in rule.get("required_columns", [])]
        required_hits = 0
        for required in required_columns:
            if any(required in header for header in normalized_headers):
                required_hits += 1
        required_score = required_hits / len(required_columns) if required_columns else 0.0

        optional_columns = [normalize_text(str(item)) for item in rule.get("optional_columns", [])]
        optional_hits = 0
        for optional in optional_columns:
            if any(optional in header for header in normalized_headers):
                optional_hits += 1
        optional_score = optional_hits / len(optional_columns) if optional_columns else 0.0

        return round(title_score * 0.58 + required_score * 0.32 + optional_score * 0.10, 4)

    def _find_column(self, keyword: str, headers: List[str]) -> bool:
        normalized_keyword = normalize_text(keyword)
        return any(normalized_keyword in normalize_text(header) for header in headers)

    def _create_column_mappings(
        self,
        headers: List[str] | List[tuple[int, str]],
        rule,
        measure_columns: Optional[set[int]] = None,
    ) -> List[ColumnMapping]:
        mappings: List[ColumnMapping] = []
        for idx, header in self._normalize_header_columns(headers):
            if measure_columns is not None and idx not in measure_columns:
                continue
            if self._is_dimension_header(header):
                continue
            measure = match_measure(header, rule)
            if measure is None:
                continue
            confidence = 0.92 if normalize_text(header) else 0.75
            mappings.append(
                ColumnMapping(
                    source_col_idx=idx,
                    source_col_name=header,
                    canonical_measure=measure,
                    confidence=confidence,
                )
            )
        return mappings

    def _normalize_header_columns(
        self,
        headers: List[str] | List[tuple[int, str]],
    ) -> List[tuple[int, str]]:
        normalized: List[tuple[int, str]] = []
        for idx, header in enumerate(headers):
            if isinstance(header, tuple):
                col_idx, text = header
            else:
                col_idx, text = idx, header
            normalized.append((int(col_idx), str(text)))
        return normalized

    def _detect_measure_columns(self, cells: Iterable[asyncpg.Record]) -> set[int]:
        measure_columns: set[int] = set()
        for cell in cells:
            text = str(cell["raw_text"]).strip()
            if not text:
                continue
            if self._looks_amount_like(text):
                measure_columns.add(int(cell["col_idx"]))
        return measure_columns

    def _is_dimension_header(self, header: str) -> bool:
        normalized = normalize_text(header)
        if not normalized:
            return True

        if normalized in {
            normalize_text("项目"),
            normalize_text("类"),
            normalize_text("款"),
            normalize_text("项"),
            normalize_text("科目编码"),
            normalize_text("功能分类科目名称"),
            normalize_text("经济分类科目名称"),
            normalize_text("部门经济分类科目名称"),
            normalize_text("政府经济分类科目名称"),
        }:
            return True

        return any(
            keyword in normalized
            for keyword in (
                normalize_text("分类科目名称"),
                normalize_text("科目名称"),
                normalize_text("科目编码"),
            )
        )

    def _looks_numeric(self, value: str) -> bool:
        cleaned = normalize_text(value)
        if not cleaned:
            return False
        cleaned = cleaned.replace(",", "").replace("，", "")
        cleaned = cleaned.replace("万元", "").replace("元", "").replace("%", "")
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = f"-{cleaned[1:-1]}"
        try:
            float(cleaned)
            return True
        except ValueError:
            return False

    def _looks_amount_like(self, value: str) -> bool:
        cleaned = normalize_text(value)
        if not cleaned:
            return False

        normalized = cleaned.replace(",", "").replace("，", "")
        normalized = normalized.replace("万元", "").replace("元", "").replace("%", "")
        if normalized.startswith("(") and normalized.endswith(")"):
            normalized = f"-{normalized[1:-1]}"
        try:
            amount = float(normalized)
        except ValueError:
            return False

        if "," in value or "." in value:
            return True
        return abs(amount) >= 1000

    async def save_table_instances(
        self,
        document_version_id: int,
        instances: List[TableInstance],
    ) -> None:
        await self.conn.execute(
            """
            DELETE FROM fiscal_column_mappings
            WHERE table_instance_id IN (
                SELECT id FROM fiscal_table_instances WHERE document_version_id = $1
            )
            """,
            document_version_id,
        )
        await self.conn.execute(
            "DELETE FROM fiscal_table_instances WHERE document_version_id = $1",
            document_version_id,
        )

        for instance in instances:
            instance_id = await self.conn.fetchval(
                """
                INSERT INTO fiscal_table_instances
                (document_version_id, table_code, source_title, confidence, page_number, row_start, row_end)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (document_version_id, table_code)
                DO UPDATE SET
                    source_title = EXCLUDED.source_title,
                    confidence = EXCLUDED.confidence,
                    page_number = EXCLUDED.page_number,
                    row_start = EXCLUDED.row_start,
                    row_end = EXCLUDED.row_end
                RETURNING id
                """,
                document_version_id,
                instance.table_code,
                instance.source_title,
                instance.confidence,
                instance.page_number,
                instance.row_start,
                instance.row_end,
            )

            for mapping in instance.column_mappings:
                await self.conn.execute(
                    """
                    INSERT INTO fiscal_column_mappings
                    (table_instance_id, source_col_idx, source_col_name, canonical_measure, confidence)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (table_instance_id, source_col_idx)
                    DO UPDATE SET
                        source_col_name = EXCLUDED.source_col_name,
                        canonical_measure = EXCLUDED.canonical_measure,
                        confidence = EXCLUDED.confidence
                    """,
                    instance_id,
                    mapping.source_col_idx,
                    mapping.source_col_name,
                    mapping.canonical_measure,
                    mapping.confidence,
                )

        logger.info(
            "Saved %s table instances for document_version %s",
            len(instances),
            document_version_id,
        )


async def recognize_and_save_tables(document_version_id: int, conn: asyncpg.Connection) -> int:
    recognizer = TableRecognizer(conn)
    instances = await recognizer.recognize_tables(document_version_id)
    await recognizer.save_table_instances(document_version_id, instances)
    return len(instances)
