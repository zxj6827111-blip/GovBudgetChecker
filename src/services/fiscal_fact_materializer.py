"""
Materialize structured fiscal facts from parsed table cells.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Dict, Iterable, List, Optional, Sequence

import asyncpg

from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text

logger = logging.getLogger(__name__)

CODE_PATTERN = re.compile(r"^\d{3,12}$")


@dataclass
class MaterializedFact:
    table_code: str
    statement_code: str
    classification_type: str
    classification_code: Optional[str]
    classification_name: str
    measure: str
    amount: float
    row_order: int
    classification_level: Optional[int]
    parent_classification_code: Optional[str]
    hierarchy_path: List[str]
    source_page_number: Optional[int]
    source_cell_ids: List[int]
    parse_confidence: float
    extra_dims: Dict[str, object]


class FiscalFactMaterializer:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn
        self._table_columns_cache: Dict[str, set[str]] = {}

    async def materialize(self, document_version_id: int) -> Dict[str, object]:
        await self.conn.execute(
            "DELETE FROM fact_fiscal_line_items WHERE document_version_id = $1",
            document_version_id,
        )

        instances = await self._load_table_instances(document_version_id)
        facts_count = 0
        low_confidence_tables: List[str] = []

        for instance in instances:
            if instance["confidence"] is not None and instance["confidence"] < 0.8:
                low_confidence_tables.append(instance["table_code"])
            cells = await self._load_cells(document_version_id, instance["table_code"])
            mappings = await self._load_column_mappings(instance["id"])
            facts = self.build_facts_for_table(
                table_code=instance["table_code"],
                cells=cells,
                mappings=mappings,
                table_confidence=float(instance["confidence"] or 0.75),
            )
            for fact in facts:
                await self._insert_fact(document_version_id, fact)
            facts_count += len(facts)

        return {
            "tables_count": len(instances),
            "facts_count": facts_count,
            "low_confidence_tables": low_confidence_tables,
        }

    async def _load_table_instances(self, document_version_id: int):
        return await self.conn.fetch(
            """
            SELECT id, table_code, confidence
            FROM fiscal_table_instances
            WHERE document_version_id = $1
            ORDER BY page_number NULLS LAST, row_start NULLS LAST, id
            """,
            document_version_id,
        )

    async def _load_column_mappings(self, table_instance_id: int):
        return await self.conn.fetch(
            """
            SELECT source_col_idx, source_col_name, canonical_measure, confidence
            FROM fiscal_column_mappings
            WHERE table_instance_id = $1
            ORDER BY source_col_idx
            """,
            table_instance_id,
        )

    async def _load_cells(self, document_version_id: int, table_code: str):
        columns = await self._get_table_columns("fiscal_table_cells")
        select_parts = ["id", "row_idx", "col_idx", "raw_text"]
        if "numeric_value" in columns:
            select_parts.append("numeric_value")
        if "page_number" in columns:
            select_parts.append("page_number")
        if "unit_hint" in columns:
            select_parts.append("unit_hint")
        if "confidence" in columns:
            select_parts.append("confidence")
        query = f"""
            SELECT {", ".join(select_parts)}
            FROM fiscal_table_cells
            WHERE document_version_id = $1 AND table_code = $2
            ORDER BY row_idx, col_idx
        """
        return await self.conn.fetch(query, document_version_id, table_code)

    async def _get_table_columns(self, table_name: str) -> set[str]:
        if table_name not in self._table_columns_cache:
            rows = await self.conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = $1
                """,
                table_name,
            )
            self._table_columns_cache[table_name] = {row["column_name"] for row in rows}
        return self._table_columns_cache[table_name]

    def build_facts_for_table(
        self,
        table_code: str,
        cells: Sequence[asyncpg.Record | Dict[str, object]],
        mappings: Sequence[asyncpg.Record | Dict[str, object]],
        table_confidence: float,
    ) -> List[MaterializedFact]:
        if table_code not in NINE_TABLE_RULES:
            return []

        mapping_by_col = {
            int(mapping["source_col_idx"]): mapping
            for mapping in mappings
        }
        if not mapping_by_col:
            return []
        first_measure_col = min(mapping_by_col)

        rows: Dict[int, List[Dict[str, object]]] = {}
        for cell in cells:
            row_idx = int(cell["row_idx"])
            rows.setdefault(row_idx, []).append(dict(cell))

        if not rows:
            return []

        measure_columns = sorted(mapping_by_col)
        data_row_start = self._find_data_row_start(rows, measure_columns)
        rule = NINE_TABLE_RULES[table_code]
        facts: List[MaterializedFact] = []

        for row_idx in sorted(rows):
            if row_idx < data_row_start:
                continue
            ordered = sorted(rows[row_idx], key=lambda item: int(item["col_idx"]))
            label_cells = [
                cell for cell in ordered if int(cell["col_idx"]) < first_measure_col
            ]
            measure_cells = [cell for cell in ordered if int(cell["col_idx"]) in mapping_by_col]
            if not measure_cells:
                continue

            classification = self._extract_classification(label_cells)
            if classification is None:
                continue

            numeric_measure_cells = [
                cell for cell in measure_cells if self._get_numeric_value(cell) is not None
            ]
            if not numeric_measure_cells:
                continue

            hierarchy = self._infer_hierarchy(
                classification_code=classification["classification_code"],
                classification_name=classification["classification_name"],
            )
            base_cell_ids = [
                int(cell["id"])
                for cell in label_cells
                if cell.get("id") is not None and str(cell.get("raw_text") or "").strip()
            ]
            source_page_number = self._pick_page_number(ordered)

            for measure_cell in numeric_measure_cells:
                col_idx = int(measure_cell["col_idx"])
                mapping = mapping_by_col[col_idx]
                amount = self._get_numeric_value(measure_cell)
                if amount is None:
                    continue
                source_cell_ids = base_cell_ids[:]
                if measure_cell.get("id") is not None:
                    source_cell_ids.append(int(measure_cell["id"]))

                parse_confidence = float(
                    min(
                        table_confidence,
                        float(mapping.get("confidence") or table_confidence),
                        float(measure_cell.get("confidence") or table_confidence),
                    )
                )

                facts.append(
                    MaterializedFact(
                        table_code=table_code,
                        statement_code=table_code,
                        classification_type=rule.classification_type,
                        classification_code=classification["classification_code"],
                        classification_name=classification["classification_name"],
                        measure=str(mapping["canonical_measure"]),
                        amount=amount,
                        row_order=row_idx,
                        classification_level=hierarchy["classification_level"],
                        parent_classification_code=hierarchy["parent_classification_code"],
                        hierarchy_path=hierarchy["hierarchy_path"],
                        source_page_number=source_page_number,
                        source_cell_ids=source_cell_ids,
                        parse_confidence=parse_confidence,
                        extra_dims={
                            "source_header": mapping.get("source_col_name"),
                            "unit_hint": self._pick_unit_hint(ordered),
                        },
                    )
                )

        return facts

    def _find_data_row_start(
        self,
        rows: Dict[int, List[Dict[str, object]]],
        measure_columns: Sequence[int],
    ) -> int:
        for row_idx in sorted(rows):
            ordered = rows[row_idx]
            measure_values = [
                self._get_numeric_value(cell)
                for cell in ordered
                if int(cell["col_idx"]) in measure_columns
            ]
            if any(value is not None for value in measure_values):
                return row_idx
        return min(rows)

    def _extract_classification(
        self,
        label_cells: Iterable[Dict[str, object]],
    ) -> Optional[Dict[str, Optional[str]]]:
        texts = [
            str(cell.get("raw_text") or "").strip()
            for cell in label_cells
            if str(cell.get("raw_text") or "").strip()
        ]
        if not texts:
            return None

        if any("合计" in text or "总计" in text for text in texts):
            return {
                "classification_code": "total",
                "classification_name": next(
                    text for text in texts if "合计" in text or "总计" in text
                ),
            }

        code_parts = [text for text in texts if text.isdigit()]
        text_parts = [text for text in texts if not text.isdigit()]
        if code_parts:
            code = "".join(code_parts)
            name = " ".join(text_parts).strip() or code
            return {
                "classification_code": code,
                "classification_name": name,
            }

        name = " ".join(texts).strip()
        return {
            "classification_code": None,
            "classification_name": name,
        }

    def _infer_hierarchy(
        self,
        classification_code: Optional[str],
        classification_name: str,
    ) -> Dict[str, object]:
        if classification_code in (None, "", "total"):
            return {
                "classification_level": 0 if classification_code == "total" else None,
                "parent_classification_code": None,
                "hierarchy_path": [],
            }

        if classification_code.isdigit():
            code_length = len(classification_code)
            if code_length >= 7:
                parent_length = code_length - 2
                level = 3 + max(0, (code_length - 7) // 2)
            elif code_length == 5:
                parent_length = 3
                level = 2
            else:
                parent_length = 0
                level = 1
            path: List[str] = []
            if code_length >= 3:
                path.append(classification_code[:3])
            if code_length >= 5:
                path.append(classification_code[:5])
            if code_length >= 7:
                path.append(classification_code[:7])
            if code_length > 7:
                path.append(classification_code)
            return {
                "classification_level": level,
                "parent_classification_code": (
                    classification_code[:parent_length] if parent_length else None
                ),
                "hierarchy_path": path,
            }

        indent = len(classification_name) - len(classification_name.lstrip())
        level = max(1, indent // 2 + 1) if indent else None
        return {
            "classification_level": level,
            "parent_classification_code": None,
            "hierarchy_path": [],
        }

    def _pick_page_number(self, row: Sequence[Dict[str, object]]) -> Optional[int]:
        for cell in row:
            if cell.get("page_number") is not None:
                return int(cell["page_number"])
        return None

    def _pick_unit_hint(self, row: Sequence[Dict[str, object]]) -> Optional[str]:
        for cell in row:
            unit_hint = cell.get("unit_hint")
            if unit_hint:
                return str(unit_hint)
        return None

    def _get_numeric_value(self, cell: Dict[str, object]) -> Optional[float]:
        value = cell.get("numeric_value")
        if value is not None:
            return float(value)

        raw_text = str(cell.get("raw_text") or "").strip()
        normalized = normalize_text(raw_text)
        normalized = (
            normalized.replace(",", "")
            .replace("，", "")
            .replace("万元", "")
            .replace("亿元", "")
            .replace("千元", "")
            .replace("元", "")
            .replace("%", "")
        )
        if normalized.startswith("(") and normalized.endswith(")"):
            normalized = f"-{normalized[1:-1]}"
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None

    async def _insert_fact(self, document_version_id: int, fact: MaterializedFact) -> None:
        columns = await self._get_table_columns("fact_fiscal_line_items")
        insert_columns = [
            "document_version_id",
            "table_code",
            "statement_code",
            "classification_type",
            "classification_code",
            "classification_name",
            "measure",
            "amount",
            "extra_dims",
            "row_order",
        ]
        values: List[object] = [
            document_version_id,
            fact.table_code,
            fact.statement_code,
            fact.classification_type,
            fact.classification_code,
            fact.classification_name,
            fact.measure,
            fact.amount,
            json.dumps(fact.extra_dims, ensure_ascii=False),
            fact.row_order,
        ]

        optional_values = {
            "classification_level": fact.classification_level,
            "parent_classification_code": fact.parent_classification_code,
            "hierarchy_path": fact.hierarchy_path,
            "source_page_number": fact.source_page_number,
            "source_cell_ids": fact.source_cell_ids,
            "parse_confidence": fact.parse_confidence,
        }
        for column_name, value in optional_values.items():
            if column_name in columns:
                insert_columns.append(column_name)
                values.append(value)

        placeholders = ", ".join(f"${index}" for index in range(1, len(values) + 1))
        query = f"""
            INSERT INTO fact_fiscal_line_items
            ({", ".join(insert_columns)})
            VALUES ({placeholders})
        """
        await self.conn.execute(query, *values)
