"""
Materialize structured fiscal facts from parsed table cells.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
import json
import logging
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import asyncpg

from src.engine.amount_math import normalize_amount_to_wan
from src.schemas.parse_artifact import EvidenceRef, FiscalFact, ParsedTable, dumps_decimal
from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text

logger = logging.getLogger(__name__)

CODE_PATTERN = re.compile(r"^\d{3,12}$")

#: 表种 → 资金范围（只按表种语义映射，不做无据推断）
_FUND_SCOPE_BY_TABLE_KEYWORD = (
    ("基金", "government_fund"),
    ("国有资本", "state_owned"),
)


def infer_fund_scope(table_code: str, title: str = "") -> Optional[str]:
    """按表种/表题关键词推断资金范围；无证据时返回 None（不猜）。"""
    haystack = f"{table_code} {title}"
    if "基金" in haystack or table_code.startswith("FIN_08"):
        return "government_fund"
    if "国有资本" in haystack or table_code.startswith("FIN_09"):
        return "state_owned"
    if "一般公共预算" in haystack or table_code.startswith(
        ("BUD_T2", "BUD_T3", "BUD_T4", "BUD_T5", "FIN_05")
    ):
        return "general_public_budget"
    return None


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
    #: 金额所在单元格的精确列号（ZC-01 AC-08：证据按来源列映射绑定，
    #: 不允许"同额不同列"反查首个相等金额格）
    source_col_idx: Optional[int] = None


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
                        source_col_idx=col_idx,
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

    # ------------------------------------------------------------------
    # ZC-01：统一财政事实构建（身份 + 原值 + 万元归一值 + 证据）
    # ------------------------------------------------------------------
    def build_fiscal_facts_for_table(
        self,
        table: ParsedTable,
        mappings: Sequence[Mapping[str, Any]],
        *,
        org_name: Optional[str] = None,
        org_scope: Optional[str] = None,
        fiscal_year: Optional[int] = None,
        period: Optional[str] = "本年",
        budget_stage: Optional[str] = None,
        instance_uid: Optional[str] = None,
        file_hash: str = "",
    ) -> List[FiscalFact]:
        """从统一解析对象构建 FiscalFact 列表（不访问数据库，纯函数式）。

        - 事实身份包含主体/年度/资金范围/指标/科目/表实例；金额不参与身份。
        - 原始值（raw_text）与单位保留不改写；normalized_value 为万元 Decimal。
        - 每条事实携带证据引用（单元格原文 + 页码 + bbox），
          证据默认 ``verification_status="unverified"``——"已验证"只能由
          真实核验（ZC-00 证据门/人工）写入，不随解析自动获得。
        """
        if table.table_code not in NINE_TABLE_RULES:
            return []

        legacy_facts = self.build_facts_for_table(
            table_code=table.table_code,
            cells=[dataclasses.asdict(cell) for cell in table.cells],
            mappings=[dict(mapping) for mapping in mappings],
            table_confidence=table.confidence,
        )
        if not legacy_facts:
            return []

        rule = NINE_TABLE_RULES[table.table_code]
        fund_scope = infer_fund_scope(table.table_code, table.title)

        # 行×列→单元格索引：证据必须按物化时的来源列精确绑定（AC-08），
        # 不再"按金额相等反找首个单元格"——同额不同列会错绑同一证据。
        cells_by_position: Dict[Tuple[int, int], Any] = {}
        cells_by_row: Dict[int, List[Any]] = {}
        for cell in table.cells:
            cells_by_position[(cell.row_idx, cell.col_idx)] = cell
            cells_by_row.setdefault(cell.row_idx, []).append(cell)

        facts: List[FiscalFact] = []
        for legacy in legacy_facts:
            row_cells = cells_by_row.get(legacy.row_order, [])
            measure_cell = None
            if legacy.source_col_idx is not None:
                measure_cell = cells_by_position.get((legacy.row_order, legacy.source_col_idx))
            if measure_cell is None:
                # 兼容旧调用（无列映射来源）：仅在金额唯一匹配时回退，
                # 多个相等金额格时保持 unresolved，不猜。
                equal_cells = [
                    cell
                    for cell in row_cells
                    if cell.numeric_value is not None
                    and abs(cell.numeric_value - float(legacy.amount)) < 1e-9
                ]
                if len(equal_cells) == 1:
                    measure_cell = equal_cells[0]
            raw_value = (
                str(measure_cell.raw_text).strip()
                if measure_cell is not None and str(measure_cell.raw_text or "").strip()
                else None
            )
            unit = None
            normalized = None
            if measure_cell is not None:
                unit = measure_cell.unit_hint or None
                # 优先用单元格已归一的值；无单位时不猜口径
                if measure_cell.normalized_value_wan is not None:
                    normalized = measure_cell.normalized_value_wan
                elif raw_value and unit:
                    normalized = normalize_amount_to_wan(raw_value, unit)
            if normalized is None:
                # 兜底：表级 unit_hint（"单位：万元"行）对字面值归一
                unit = unit or table.unit_hint
                normalized = normalize_amount_to_wan(float(legacy.amount), unit) if unit else None

            raw_text_for_precision = raw_value or ""
            decimals = raw_text_for_precision.split(".")[-1] if "." in raw_text_for_precision else ""
            evidence = EvidenceRef(
                file_hash=file_hash,
                physical_page=(
                    measure_cell.page_number
                    if measure_cell is not None
                    else legacy.source_page_number
                ),
                source_cell_id=(
                    (
                        f"{measure_cell.origin_fragment_uid or table.fragment_uid}"
                        f":r{measure_cell.origin_row_idx if measure_cell.origin_row_idx is not None else measure_cell.row_idx}"
                        f"c{measure_cell.col_idx}"
                    )
                    if measure_cell is not None
                    else None
                ),
                quote=raw_value or "",
                bbox=measure_cell.bbox if measure_cell is not None else None,
                source_method=(
                    "ocr"
                    if str(getattr(table, "extraction_method", "") or "") == "ocr"
                    else "native_text"
                ),
                verification_status="unverified",
                location_level=(
                    "exact_cell"
                    if measure_cell is not None and measure_cell.bbox
                    and str(getattr(table, "extraction_method", "") or "") != "ocr"
                    else ("region" if measure_cell is not None and measure_cell.bbox else "unresolved")
                ),
            )

            facts.append(
                FiscalFact(
                    fact_uid=f"{table.fragment_uid}:{legacy.row_order}:{legacy.measure}",
                    indicator=str(legacy.measure),
                    raw_value=raw_value,
                    unit=unit,
                    normalized_value=normalized,
                    display_precision=len(decimals) if decimals else (0 if raw_value else None),
                    org_name=org_name,
                    org_scope=org_scope,
                    fiscal_year=fiscal_year,
                    period=period,
                    fund_scope=fund_scope,
                    budget_stage=budget_stage,
                    subject_code=legacy.classification_code,
                    subject_name=legacy.classification_name or None,
                    classification_level=legacy.classification_level,
                    parent_subject_code=legacy.parent_classification_code,
                    hierarchy_path=list(legacy.hierarchy_path),
                    table_code=table.table_code,
                    instance_uid=instance_uid,
                    row_order=legacy.row_order,
                    recognition_status=(
                        "low_confidence"
                        if table.confidence < 0.8 or legacy.parse_confidence < 0.8
                        else "unverified"
                    ),
                    confidence=legacy.parse_confidence,
                    evidence_ids=[evidence.evidence_id],
                    extra_dims={
                        "classification_type": legacy.classification_type,
                        "statement_code": legacy.statement_code,
                        "source_header": (legacy.extra_dims or {}).get("source_header"),
                        "unit_hint": (legacy.extra_dims or {}).get("unit_hint"),
                        "rule_classification": rule.classification_type,
                        # ZC-01（AC-08）：完整证据实体随事实一起传递/存储/读取，
                        # 不再只留 evidence_id——物理页、bbox、来源单元格、
                        # 文件哈希在持久化往返后必须全部可回溯。
                        "evidence_refs": [evidence.to_dict()],
                    },
                )
            )
        return facts
