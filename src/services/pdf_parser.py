"""
Structured PDF parser for fiscal tables.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import asyncpg

from src.services.fiscal_table_rules import detect_table_code, normalize_text

logger = logging.getLogger(__name__)

DEFAULT_TABLE_SETTINGS: Sequence[Tuple[str, Dict[str, Any]]] = (
    (
        "lines",
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "edge_min_length": 3,
        },
    ),
    (
        "lines_strict",
        {
            "vertical_strategy": "lines_strict",
            "horizontal_strategy": "lines_strict",
            "snap_tolerance": 2,
            "join_tolerance": 2,
            "edge_min_length": 2,
        },
    ),
    (
        "text",
        {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "min_words_vertical": 2,
            "min_words_horizontal": 1,
            "intersection_tolerance": 5,
        },
    ),
)

UNIT_PATTERN = re.compile(
    r"(单位[:：]?\s*(万元|元|亿元|千元|百万元))|((金额|单位)[:：]?\s*(万元|元|亿元|千元|百万元))"
)
NUMERIC_PATTERN = re.compile(
    r"""
    ^\(?-?
    (?:
        \d{1,3}(?:[,，]\d{3})*(?:\.\d+)?
        |
        \d+(?:\.\d+)?
    )
    \)?%?$
    """,
    re.VERBOSE,
)
TITLE_LINE_PATTERN = re.compile(r"(预算表|决算表|总表|经费预算表|经费支出表|分类预算表)")
TITLE_NOISE_PATTERN = re.compile(r"^(编制部门|编制单位|单位[:：])")


@dataclass
class ParsedCell:
    row_idx: int
    col_idx: int
    raw_text: str
    normalized_text: str
    numeric_value: Optional[float]
    page_number: int
    bbox: Optional[Tuple[float, float, float, float]]
    is_header: bool
    confidence: float
    unit_hint: Optional[str]
    extraction_method: str
    row_span: int = 1
    col_span: int = 1


@dataclass
class ParsedTable:
    table_code: str
    title: str
    page_number: int
    table_index: int
    bbox: Tuple[float, float, float, float]
    header_row_count: int
    confidence: float
    extraction_method: str
    unit_hint: Optional[str]
    rows: List[List[str]]
    cells: List[ParsedCell]


class PDFParser:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def parse_pdf(self, pdf_path: str, document_version_id: int) -> Dict[str, Any]:
        try:
            import pdfplumber
        except ImportError:
            logger.error("pdfplumber not installed. Run: pip install pdfplumber")
            return {
                "success": False,
                "tables_count": 0,
                "cells_count": 0,
                "error": "pdfplumber not installed",
            }

        path = Path(pdf_path)
        if not path.exists():
            return {
                "success": False,
                "tables_count": 0,
                "cells_count": 0,
                "error": f"File not found: {pdf_path}",
            }

        await self._clear_existing_cells(document_version_id)

        tables_count = 0
        cells_count = 0
        recognized_tables = 0
        low_confidence_tables = 0
        unknown_tables: List[str] = []
        errors: List[str] = []

        try:
            with pdfplumber.open(path) as pdf:
                logical_tables: List[ParsedTable] = []
                for page_number, page in enumerate(pdf.pages, start=1):
                    page_tables = self._extract_tables_from_page(page, page_number)
                    logical_tables.extend(page_tables)

                for table in self._coalesce_tables(logical_tables):
                        await self._save_table_cells(document_version_id, table)
                        tables_count += 1
                        cells_count += len(table.cells)
                        if table.table_code.startswith("UNKNOWN_"):
                            unknown_tables.append(table.table_code)
                        else:
                            recognized_tables += 1
                        if table.confidence < 0.8:
                            low_confidence_tables += 1
        except Exception as exc:
            logger.exception("Failed to parse PDF %s", pdf_path)
            errors.append(str(exc))

        return {
            "success": not errors,
            "tables_count": tables_count,
            "cells_count": cells_count,
            "recognized_tables": recognized_tables,
            "unknown_tables": unknown_tables,
            "low_confidence_tables": low_confidence_tables,
            "errors": errors or None,
        }

    def _extract_tables_from_page(self, page, page_number: int) -> List[ParsedTable]:
        best_candidates: List[ParsedTable] = []

        for method_name, settings in DEFAULT_TABLE_SETTINGS:
            try:
                candidates = []
                for table_index, table in enumerate(page.find_tables(table_settings=settings)):
                    rows = table.extract() or []
                    if not self._is_meaningful_table(rows):
                        continue
                    parsed = self._build_parsed_table(
                        page=page,
                        page_number=page_number,
                        table_index=table_index,
                        table=table,
                        rows=rows,
                        extraction_method=method_name,
                    )
                    candidates.append(parsed)
            except Exception:
                logger.debug(
                    "Table extraction failed on page %s with method %s",
                    page_number,
                    method_name,
                    exc_info=True,
                )
                continue

            best_candidates = self._merge_candidates(best_candidates, candidates)

        placeholder = self._build_page_text_placeholder(page, page_number, best_candidates)
        if placeholder is not None:
            best_candidates.append(placeholder)

        return sorted(best_candidates, key=lambda item: (item.page_number, item.bbox[1], item.bbox[0]))

    def _build_page_text_placeholder(
        self,
        page,
        page_number: int,
        candidates: Sequence[ParsedTable],
    ) -> Optional[ParsedTable]:
        page_text = (page.extract_text() or "").strip()
        if not page_text:
            return None

        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        if not lines:
            return None

        scored_lines = sorted(
            (
                (self._score_title_line(line, index), index, line)
                for index, line in enumerate(lines[:6])
            ),
            key=lambda item: (-item[0], item[1]),
        )
        best_score, best_index, title_line = scored_lines[0]
        if best_score <= 0:
            return None

        header_lines = [
            line
            for line in lines[best_index + 1 : best_index + 7]
            if line and not TITLE_NOISE_PATTERN.search(line)
        ]
        table_code, confidence = detect_table_code(
            title=title_line,
            headers=header_lines,
            source_hint=" ".join(header_lines[:4]),
        )
        if table_code is None or confidence < 0.75:
            return None
        if any(item.table_code == table_code and not item.table_code.startswith("UNKNOWN_") for item in candidates):
            return None

        rows = [[title_line], *[[line] for line in header_lines[:4]]]
        if len(rows) < 2:
            return None
        header_row_count = min(max(len(rows) - 1, 1), 4)
        unit_hint = self._detect_unit_hint(page, title_line, rows)
        page_width = float(getattr(page, "width", 0.0) or 0.0)
        page_height = float(getattr(page, "height", 0.0) or 0.0)
        bbox = (0.0, 0.0, page_width, page_height)
        cells: List[ParsedCell] = []
        for row_idx, row in enumerate(rows):
            raw_text = str(row[0] or "").strip()
            cells.append(
                ParsedCell(
                    row_idx=row_idx,
                    col_idx=0,
                    raw_text=raw_text,
                    normalized_text=normalize_text(raw_text),
                    numeric_value=self._parse_numeric(raw_text),
                    page_number=page_number,
                    bbox=None,
                    is_header=row_idx < header_row_count,
                    confidence=round(float(confidence), 4),
                    unit_hint=unit_hint,
                    extraction_method="page_text_fallback",
                )
            )

        return ParsedTable(
            table_code=table_code,
            title=title_line,
            page_number=page_number,
            table_index=999,
            bbox=bbox,
            header_row_count=header_row_count,
            confidence=round(float(confidence), 4),
            extraction_method="page_text_fallback",
            unit_hint=unit_hint,
            rows=rows,
            cells=cells,
        )

    def _coalesce_tables(self, tables: Sequence[ParsedTable]) -> List[ParsedTable]:
        merged: List[ParsedTable] = []
        for table in tables:
            if not merged:
                merged.append(table)
                continue

            previous = merged[-1]
            if self._is_continuation_table(previous, table):
                merged[-1] = self._append_table(previous, table)
                continue
            merged.append(table)
        return merged

    def _build_parsed_table(
        self,
        page,
        page_number: int,
        table_index: int,
        table,
        rows: List[List[str]],
        extraction_method: str,
    ) -> ParsedTable:
        title = self._extract_title(page, table.bbox, rows)
        header_row_count = self._detect_header_row_count(rows)
        flattened_headers = self._collect_headers(rows, header_row_count)
        normalized_title = normalize_text(title)
        if "编制说明" in normalized_title:
            table_code, match_confidence = None, 0.0
        else:
            table_code, match_confidence = detect_table_code(
                title=title,
                headers=flattened_headers,
                source_hint=" ".join(flattened_headers),
            )
        final_code = table_code or f"UNKNOWN_P{page_number}_T{table_index}"
        unit_hint = self._detect_unit_hint(page, title, rows)
        extraction_quality = self._score_table(rows)
        confidence = round(min(0.99, max(match_confidence, extraction_quality)), 4)
        cells = self._build_cells(
            page_number=page_number,
            rows=rows,
            table=table,
            header_row_count=header_row_count,
            unit_hint=unit_hint,
            extraction_method=extraction_method,
            confidence=confidence,
        )

        return ParsedTable(
            table_code=final_code,
            title=title,
            page_number=page_number,
            table_index=table_index,
            bbox=table.bbox,
            header_row_count=header_row_count,
            confidence=confidence,
            extraction_method=extraction_method,
            unit_hint=unit_hint,
            rows=rows,
            cells=cells,
        )

    def _build_cells(
        self,
        page_number: int,
        rows: List[List[str]],
        table,
        header_row_count: int,
        unit_hint: Optional[str],
        extraction_method: str,
        confidence: float,
    ) -> List[ParsedCell]:
        cells: List[ParsedCell] = []

        for row_idx, row in enumerate(rows):
            row_boxes = table.rows[row_idx].cells if row_idx < len(table.rows) else []
            for col_idx, raw_value in enumerate(row):
                raw_text = (raw_value or "").strip()
                bbox = row_boxes[col_idx] if col_idx < len(row_boxes) else None
                cells.append(
                    ParsedCell(
                        row_idx=row_idx,
                        col_idx=col_idx,
                        raw_text=raw_text,
                        normalized_text=normalize_text(raw_text),
                        numeric_value=self._parse_numeric(raw_text),
                        page_number=page_number,
                        bbox=bbox,
                        is_header=row_idx < header_row_count,
                        confidence=confidence if raw_text else max(0.5, confidence - 0.25),
                        unit_hint=unit_hint,
                        extraction_method=extraction_method,
                    )
                )

        return cells

    async def _clear_existing_cells(self, document_version_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM fiscal_table_cells WHERE document_version_id = $1",
            document_version_id,
        )

    async def _save_table_cells(self, document_version_id: int, table: ParsedTable) -> None:
        for cell in table.cells:
            await self._save_cell(document_version_id, table.table_code, cell)

    async def _save_cell(self, document_version_id: int, table_code: str, cell: ParsedCell) -> None:
        bbox_value = json.dumps(list(cell.bbox)) if cell.bbox else None
        try:
            await self.conn.execute(
                """
                INSERT INTO fiscal_table_cells
                (
                    document_version_id,
                    table_code,
                    row_idx,
                    col_idx,
                    raw_text,
                    normalized_text,
                    numeric_value,
                    page_number,
                    bbox,
                    is_header,
                    row_span,
                    col_span,
                    confidence,
                    unit_hint,
                    extraction_method
                )
                VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12, $13, $14, $15)
                ON CONFLICT (document_version_id, table_code, row_idx, col_idx)
                DO UPDATE SET
                    raw_text = EXCLUDED.raw_text,
                    normalized_text = EXCLUDED.normalized_text,
                    numeric_value = EXCLUDED.numeric_value,
                    page_number = EXCLUDED.page_number,
                    bbox = EXCLUDED.bbox,
                    is_header = EXCLUDED.is_header,
                    row_span = EXCLUDED.row_span,
                    col_span = EXCLUDED.col_span,
                    confidence = EXCLUDED.confidence,
                    unit_hint = EXCLUDED.unit_hint,
                    extraction_method = EXCLUDED.extraction_method
                """,
                document_version_id,
                table_code,
                cell.row_idx,
                cell.col_idx,
                cell.raw_text,
                cell.normalized_text,
                cell.numeric_value,
                cell.page_number,
                bbox_value,
                cell.is_header,
                cell.row_span,
                cell.col_span,
                cell.confidence,
                cell.unit_hint,
                cell.extraction_method,
            )
        except Exception as exc:
            if "normalized_text" not in str(exc) and "numeric_value" not in str(exc):
                raise
            await self.conn.execute(
                """
                INSERT INTO fiscal_table_cells
                (document_version_id, table_code, row_idx, col_idx, raw_text)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (document_version_id, table_code, row_idx, col_idx)
                DO UPDATE SET raw_text = EXCLUDED.raw_text
                """,
                document_version_id,
                table_code,
                cell.row_idx,
                cell.col_idx,
                cell.raw_text,
            )

    def _merge_candidates(
        self,
        existing: List[ParsedTable],
        incoming: List[ParsedTable],
    ) -> List[ParsedTable]:
        merged = existing[:]
        for candidate in incoming:
            replaced = False
            for idx, current in enumerate(merged):
                if current.page_number != candidate.page_number:
                    continue
                overlap = self._iou(current.bbox, candidate.bbox)
                containment = self._containment_ratio(current.bbox, candidate.bbox)
                if overlap < 0.75 and containment < 0.9:
                    continue
                if self._table_priority(candidate) > self._table_priority(current):
                    merged[idx] = candidate
                replaced = True
                break
            if not replaced:
                merged.append(candidate)
        return merged

    def _extract_title(self, page, bbox, rows: Sequence[Sequence[str]]) -> str:
        top = max(0, bbox[1] - 120)
        left = max(0, bbox[0] - 5)
        right = min(page.width, bbox[2] + 5)
        title_region = page.crop((left, top, right, bbox[1]))
        title_text = (title_region.extract_text() or "").strip()
        if title_text:
            lines = [line.strip() for line in title_text.splitlines() if line.strip()]
            if lines:
                scored_lines = sorted(
                    (
                        (
                            self._score_title_line(line, index),
                            index,
                            line,
                        )
                        for index, line in enumerate(lines)
                    ),
                    key=lambda item: (-item[0], item[1]),
                )
                best_score, _, best_line = scored_lines[0]
                if best_score > 0:
                    return best_line

        first_row = [str(value).strip() for value in rows[:1][0] if str(value).strip()] if rows else []
        return " ".join(first_row)

    def _collect_headers(self, rows: Sequence[Sequence[str]], header_row_count: int) -> List[str]:
        headers: List[str] = []
        for row in rows[:header_row_count]:
            headers.extend(str(value).strip() for value in row if str(value).strip())
        return headers

    def _detect_header_row_count(self, rows: Sequence[Sequence[str]]) -> int:
        if not rows:
            return 0

        header_rows = 0
        for row in rows[:4]:
            texts = [str(value).strip() for value in row if str(value).strip()]
            if not texts:
                header_rows += 1
                continue
            numeric_ratio = sum(1 for value in texts if self._looks_numeric(value)) / len(texts)
            merged_text = normalize_text("".join(texts))
            if "决算表" in merged_text or "总表" in merged_text:
                header_rows += 1
                continue
            if numeric_ratio <= 0.45:
                header_rows += 1
                continue
            break

        return max(1, min(header_rows, len(rows)))

    def _score_title_line(self, line: str, index: int) -> float:
        score = 0.0
        normalized = normalize_text(line)
        if not normalized:
            return score
        if TITLE_LINE_PATTERN.search(line):
            score += 4.0
        if any(keyword in normalized for keyword in ("预算", "决算", "总表", "三公", "财政拨款", "基本支出")):
            score += 2.0
        if TITLE_NOISE_PATTERN.search(line):
            score -= 3.0
        score -= index * 0.1
        return score

    def _detect_unit_hint(
        self,
        page,
        title: str,
        rows: Sequence[Sequence[str]],
    ) -> Optional[str]:
        page_text = page.extract_text() or ""
        haystack = "\n".join(
            [title, page_text, *[" ".join(str(item or "") for item in row[:4]) for row in rows[:2]]]
        )
        match = UNIT_PATTERN.search(haystack)
        if not match:
            return None
        groups = [group for group in match.groups() if group]
        return groups[-1] if groups else None

    def _score_table(self, rows: Sequence[Sequence[str]]) -> float:
        total_cells = sum(len(row) for row in rows)
        if total_cells == 0:
            return 0.0
        non_empty = sum(1 for row in rows for value in row if str(value or "").strip())
        numeric = sum(1 for row in rows for value in row if self._looks_numeric(str(value or "")))
        width = max((len(row) for row in rows), default=0)
        richness = min(non_empty / total_cells, 1.0)
        numeric_ratio = numeric / max(non_empty, 1)
        width_score = min(width / 6, 1.0)
        return round(0.4 * richness + 0.35 * numeric_ratio + 0.25 * width_score, 4)

    def _is_continuation_table(self, previous: ParsedTable, current: ParsedTable) -> bool:
        if current.page_number - self._last_page_number(previous) != 1:
            return False
        if not self._compatible_column_count(previous, current):
            return False

        previous_code = previous.table_code
        current_code = current.table_code
        if previous_code == current_code and not previous_code.startswith("UNKNOWN_"):
            return True

        if current_code.startswith("UNKNOWN_") and not previous_code.startswith("UNKNOWN_"):
            if self._looks_like_data_continuation(current) and (
                current.header_row_count <= 1
                or self._headers_look_repeated(previous, current)
            ):
                return True

        return False

    def _last_page_number(self, table: ParsedTable) -> int:
        cell_pages = [cell.page_number for cell in table.cells if getattr(cell, "page_number", None)]
        return max(cell_pages) if cell_pages else table.page_number

    def _headers_look_repeated(self, previous: ParsedTable, current: ParsedTable) -> bool:
        previous_headers = normalize_text(
            " ".join(self._collect_headers(previous.rows, previous.header_row_count))
        )
        current_headers = normalize_text(
            " ".join(self._collect_headers(current.rows, current.header_row_count))
        )
        if not previous_headers or not current_headers:
            return False
        return (
            previous_headers == current_headers
            or previous_headers in current_headers
            or current_headers in previous_headers
        )

    def _compatible_column_count(self, previous: ParsedTable, current: ParsedTable) -> bool:
        previous_width = max((len(row) for row in previous.rows), default=0)
        current_width = max((len(row) for row in current.rows), default=0)
        return abs(previous_width - current_width) <= 2

    def _looks_like_data_continuation(self, table: ParsedTable) -> bool:
        candidate_rows = table.rows[table.header_row_count :] if table.header_row_count < len(table.rows) else []
        data_rows = candidate_rows[: min(4, len(candidate_rows))] if candidate_rows else table.rows[: min(4, len(table.rows))]
        values = [str(value or "").strip() for row in data_rows for value in row if str(value or "").strip()]
        if not values:
            return False
        numeric_ratio = sum(1 for value in values if self._looks_numeric(value)) / len(values)
        return numeric_ratio >= 0.35

    def _append_table(self, previous: ParsedTable, current: ParsedTable) -> ParsedTable:
        skip_rows = self._repeated_header_rows(previous, current)
        base_row_count = len(previous.rows)
        appended_rows = previous.rows + current.rows[skip_rows:]
        appended_cells = list(previous.cells)
        for cell in current.cells:
            if cell.row_idx < skip_rows:
                continue
            appended_cells.append(
                replace(
                    cell,
                    row_idx=base_row_count + (cell.row_idx - skip_rows),
                )
            )

        return ParsedTable(
            table_code=previous.table_code,
            title=previous.title if self._score_title_line(previous.title, 0) >= self._score_title_line(current.title, 0) else current.title,
            page_number=previous.page_number,
            table_index=previous.table_index,
            bbox=previous.bbox,
            header_row_count=previous.header_row_count,
            confidence=max(previous.confidence, current.confidence),
            extraction_method=previous.extraction_method,
            unit_hint=previous.unit_hint or current.unit_hint,
            rows=appended_rows,
            cells=appended_cells,
        )

    def _repeated_header_rows(self, previous: ParsedTable, current: ParsedTable) -> int:
        max_header_rows = min(current.header_row_count, len(current.rows), len(previous.rows))
        previous_headers = [
            normalize_text(" ".join(str(value or "").strip() for value in row if str(value or "").strip()))
            for row in previous.rows[: previous.header_row_count]
        ]
        repeated = 0
        for row in current.rows[:max_header_rows]:
            normalized = normalize_text(
                " ".join(str(value or "").strip() for value in row if str(value or "").strip())
            )
            if not normalized:
                repeated += 1
                continue
            if normalized in previous_headers:
                repeated += 1
                continue
            break
        return repeated

    def _table_priority(self, table: ParsedTable) -> float:
        area = max((table.bbox[2] - table.bbox[0]) * (table.bbox[3] - table.bbox[1]), 1.0)
        return table.confidence + min(area / 1_000_000, 0.2)

    def _containment_ratio(
        self,
        left_bbox: Tuple[float, float, float, float],
        right_bbox: Tuple[float, float, float, float],
    ) -> float:
        left_x0, left_y0, left_x1, left_y1 = left_bbox
        right_x0, right_y0, right_x1, right_y1 = right_bbox

        inter_x0 = max(left_x0, right_x0)
        inter_y0 = max(left_y0, right_y0)
        inter_x1 = min(left_x1, right_x1)
        inter_y1 = min(left_y1, right_y1)
        if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
            return 0.0

        intersection = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
        min_area = min(
            max((left_x1 - left_x0) * (left_y1 - left_y0), 1.0),
            max((right_x1 - right_x0) * (right_y1 - right_y0), 1.0),
        )
        return intersection / min_area

    def _is_meaningful_table(self, rows: Sequence[Sequence[str]]) -> bool:
        if len(rows) < 2:
            return False
        width = max((len(row) for row in rows), default=0)
        if width < 2:
            return False
        non_empty = sum(1 for row in rows for value in row if str(value or "").strip())
        return non_empty >= max(4, len(rows))

    def _parse_numeric(self, text: str) -> Optional[float]:
        raw = (text or "").strip()
        if not raw:
            return None

        cleaned = (
            raw.replace(",", "")
            .replace("，", "")
            .replace(" ", "")
            .replace("\u3000", "")
            .replace("亿元", "")
            .replace("千元", "")
            .replace("万元", "")
            .replace("元", "")
            .replace("%", "")
        )
        cleaned = cleaned.replace("—", "").replace("--", "").replace("－", "-")
        if not cleaned:
            return None
        cleaned = cleaned.replace("（", "(").replace("）", ")")
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = f"-{cleaned[1:-1]}"
        if not NUMERIC_PATTERN.match(cleaned):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _looks_numeric(self, text: str) -> bool:
        return self._parse_numeric(text) is not None

    def _iou(
        self,
        left_bbox: Tuple[float, float, float, float],
        right_bbox: Tuple[float, float, float, float],
    ) -> float:
        left_x0, left_y0, left_x1, left_y1 = left_bbox
        right_x0, right_y0, right_x1, right_y1 = right_bbox

        inter_x0 = max(left_x0, right_x0)
        inter_y0 = max(left_y0, right_y0)
        inter_x1 = min(left_x1, right_x1)
        inter_y1 = min(left_y1, right_y1)

        if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
            return 0.0

        intersection = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
        left_area = (left_x1 - left_x0) * (left_y1 - left_y0)
        right_area = (right_x1 - right_x0) * (right_y1 - right_y0)
        union = left_area + right_area - intersection
        return intersection / union if union else 0.0


async def parse_pdf_document(
    pdf_path: str,
    document_version_id: int,
    conn: asyncpg.Connection,
) -> Dict[str, Any]:
    parser = PDFParser(conn)
    return await parser.parse_pdf(pdf_path, document_version_id)
