"""
Materialize structured fiscal facts from parsed table cells.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
import json
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import asyncpg

from src.engine.amount_math import normalize_amount_to_wan
from src.schemas.parse_artifact import EvidenceRef, FiscalFact, ParsedTable, dumps_decimal
from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text
from src.services.fiscal_row_roles import is_index_header

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
    explicit={scope for keyword,scope in [('一般公共预算','general_public_budget'),('政府性基金','government_fund'),
              ('国有资本','state_owned')]if keyword in title}
    if len(explicit)>1:
        return 'fiscal_appropriation'if table_code.startswith('FIN_04')else None
    if "基金" in haystack or table_code.startswith("FIN_08"):
        return "government_fund"
    if "国有资本" in haystack or table_code.startswith("FIN_09"):
        return "state_owned"
    if "一般公共预算" in haystack or table_code.startswith(
        ("BUD_T2", "BUD_T3", "BUD_T4", "BUD_T5", "FIN_05", "FIN_06")
    ):
        return "general_public_budget"
    if table_code.startswith('FIN_04'):
        return 'fiscal_appropriation'
    if table_code.startswith('FIN_07')and '财政拨款' in title:
        return 'fiscal_appropriation'
    if table_code.startswith(('FIN_01','FIN_02','FIN_03')):
        return 'all_funds'
    return None


def measure_fund_scope(table_code,title,header):
    """子列可以细化全口径/财政拨款父表；单一资金表与子列冲突时不选一方。"""
    parent=infer_fund_scope(table_code,title)
    scopes={scope for keyword,scope in [('一般公共预算','general_public_budget'),('政府性基金','government_fund'),
            ('国有资本','state_owned')]if keyword in str(header or'')}
    if len(scopes)>1:return None,True
    child=next(iter(scopes))if scopes else None
    conflict=bool(child and parent not in {None,'all_funds','fiscal_appropriation',child})
    return (None if conflict else child or parent),conflict


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
        from src.qc.source_version_guard import require_legacy_qc_source
        await require_legacy_qc_source(self.conn, document_version_id)
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
        if "is_header" in columns:
            select_parts.append("is_header")
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
            if is_index_header(cell.get("raw_text") for cell in ordered):
                continue
            label_cells = [
                cell for cell in ordered if int(cell["col_idx"]) < first_measure_col
            ]
            measure_cells = [cell for cell in ordered if int(cell["col_idx"]) in mapping_by_col]
            if not measure_cells:
                continue

            numeric_measure_cells = [
                cell for cell in measure_cells
                if not cell.get("is_header") and self._get_numeric_value(cell) is not None
            ]
            if not numeric_measure_cells:
                continue

            source_page_number = self._pick_page_number(ordered)

            for measure_cell in numeric_measure_cells:
                col_idx = int(measure_cell["col_idx"])
                mapping = mapping_by_col[col_idx]
                local_labels = mapping.get("source_label_cols")
                bound_labels = [cell for cell in ordered if int(cell["col_idx"]) in local_labels] if local_labels else label_cells
                classification = self._extract_classification(bound_labels)
                if classification is None:
                    if table_code == "FIN_07_three_public" and first_measure_col == 0:
                        classification = {"classification_code": "total", "classification_name": "合计"}
                    else:
                        continue
                hierarchy = self._infer_hierarchy(
                    classification_code=classification["classification_code"],
                    classification_name=classification["classification_name"],
                )
                base_cell_ids = [int(cell["id"]) for cell in bound_labels
                                 if cell.get("id") is not None and str(cell.get("raw_text") or "").strip()]
                amount = self._get_numeric_value(measure_cell)
                if amount is None:
                    continue
                source_cell_ids = base_cell_ids[:]
                if measure_cell.get("id") is not None:
                    source_cell_ids.append(int(measure_cell["id"]))

                # 分数原样进入事实；来源、结构和身份另用证据判断，不能统一抬分。
                measure_confidence = float(measure_cell.get("confidence") or table_confidence)
                parse_confidence = float(
                    min(
                        table_confidence,
                        float(mapping.get("confidence") or table_confidence),
                        measure_confidence,
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
                            "source_label_cols": [int(cell['col_idx']) for cell in bound_labels],
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
            if is_index_header(cell.get("raw_text") for cell in ordered):
                continue
            measure_values = [
                self._get_numeric_value(cell)
                for cell in ordered
                if int(cell["col_idx"]) in measure_columns and not cell.get("is_header")
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

        # 只归一完整的合计/总计标记，原格及其来源原文保持不变。
        # 不能把正文科目名称中任意出现的“合计”当作这条特殊标记。
        for text in texts:
            total = re.sub(r"\s+", "", text)
            if total in {"合计", "总计"}:
                return {"classification_code": "total", "classification_name": total}

        if any("合计" in text or "总计" in text for text in texts):
            return {
                "classification_code": "total",
                "classification_name": next(
                    text for text in texts if "合计" in text or "总计" in text
                ),
            }

        # 窄编码列里的 7 位编码常在格内折行（"20700\n00"），折行不属于编码本身。
        compacts = [re.sub(r"\s+", "", text) for text in texts]
        code_parts = [compact for compact in compacts if compact.isdigit()]
        text_parts = [text for text, compact in zip(texts, compacts) if not compact.isdigit()]
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
        raw_text = str(cell.get("raw_text") or "").strip()
        if re.search(r"\d[\s\u3000]+\d", raw_text):
            return None  # Adjacent cells merged by text extraction are not one amount.
        value = cell.get("numeric_value")
        if value is not None:
            return float(value)

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
        from src.engine.fiscal_lexeme import amount_in_wan

        legacy_facts = self.build_facts_for_table(
            table_code=table.table_code,
            cells=[dataclasses.asdict(cell) for cell in table.cells],
            mappings=[dict(mapping) for mapping in mappings],
            table_confidence=table.confidence,
        )
        if not legacy_facts:
            return []

        rule = NINE_TABLE_RULES[table.table_code]
        row_widths = {len(row) for row in table.rows if any(str(cell or "").strip() for cell in row)}
        column_identity_safe = len(row_widths) <= 1
        from src.services.fiscal_column_identity import code_dimension_columns
        code_columns = code_dimension_columns(table)

        # 行×列→单元格索引：证据必须按物化时的来源列精确绑定（AC-08），
        # 不再"按金额相等反找首个单元格"——同额不同列会错绑同一证据。
        cells_by_position: Dict[Tuple[int, int], Any] = {}
        cells_by_row: Dict[int, List[Any]] = {}
        for cell in table.cells:
            cells_by_position[(cell.row_idx, cell.col_idx)] = cell
            cells_by_row.setdefault(cell.row_idx, []).append(cell)

        facts: List[FiscalFact] = []
        repeated_measures = {str(mapping["canonical_measure"]) for mapping in mappings
                             if sum(str(other["canonical_measure"]) == str(mapping["canonical_measure"]) for other in mappings) > 1}
        for legacy in legacy_facts:
            column_fund_scope,fund_identity_conflict=measure_fund_scope(table.table_code,table.title,
                (legacy.extra_dims or{}).get('source_header'))
            row_cells = cells_by_row.get(legacy.row_order, [])
            source_label_cols = (legacy.extra_dims or {}).get('source_label_cols', [])
            missing_subject_code = (bool(code_columns.intersection(source_label_cols))
                                    and not legacy.classification_code)
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
                elif raw_value:
                    normalized = amount_in_wan(raw_value, unit)
                    if normalized is not None and not unit:
                        unit = next((token for token in ("百万元", "亿元", "万元", "千元", "元")
                                     if token in raw_value), None)
            if normalized is None:
                # 兜底：表级 unit_hint（"单位：万元"行）对字面值归一
                unit = unit or table.unit_hint
                normalized = amount_in_wan(raw_value, unit) if unit and raw_value else None

            from src.engine.fact_context import normalized_amount_is_usable, _stage_for_fact

            amount_usable = normalized_amount_is_usable(normalized)

            raw_text_for_precision = raw_value or ""
            decimals = raw_text_for_precision.split(".")[-1] if "." in raw_text_for_precision else ""
            source_method = str(
                getattr(measure_cell, "extraction_method", "")
                or getattr(table, "extraction_method", "")
                or "native_text"
            )
            is_ocr_source = source_method.lower() == "ocr"
            # 人工补录格：审核员对照原页填入的字面值，等同已绑定原文；行列身份仍按常规校验。
            is_manual_source = source_method == "manual"
            ocr_provenance = getattr(measure_cell,"ocr_provenance",{}) if measure_cell is not None else {}
            ocr_qualified = ocr_provenance.get("qualification",{}).get("status") == "qualified"
            native_sources = list(measure_cell.native_text_sources) if measure_cell is not None else []
            # 跨格尾位证明了字符在内容流中存在，尚不能证明其可见行归属。
            # 原格投影与恢复值不同的候选保留双方证据，等待显式裁定。
            from src.services.source_reconstruction import native_source_conflict
            native_conflict = native_source_conflict(measure_cell)
            native_literal_bound = (source_method in {'lines', 'lines_strict'} and bool(native_sources)
                and not native_conflict and bool(raw_value)
                and normalize_text(''.join(str(p.get('text') or '') for p in native_sources)) == normalize_text(raw_value)
                ) or (is_manual_source and bool(raw_value))
            native_evidence_qualified = (native_literal_bound and column_identity_safe and not missing_subject_code
                and table.confidence >= .8 and amount_usable
                and bool(legacy.classification_code or legacy.classification_name)
                and measure_cell.bbox is not None
                and sum(m.get('source_col_idx') == legacy.source_col_idx for m in mappings) == 1)
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
                        f"c{measure_cell.origin_col_idx if measure_cell.origin_col_idx is not None else measure_cell.col_idx}"
                    )
                    if measure_cell is not None
                    else None
                ),
                quote=raw_value or "",
                bbox=measure_cell.bbox if measure_cell is not None else None,
                source_method="ocr" if is_ocr_source else "manual" if is_manual_source else "native_text",
                verification_status="unverified",
                source_provenance=(ocr_provenance if is_ocr_source else {
                    "grid_raw_text": measure_cell.grid_raw_text,
                    "native_text_sources": list(measure_cell.native_text_sources),
                } if measure_cell is not None and measure_cell.native_text_sources else {}),
                location_level=(
                    "exact_cell"
                    if measure_cell is not None and measure_cell.bbox
                    and (not is_ocr_source or ocr_qualified) and not is_manual_source
                    else ("region" if measure_cell is not None and measure_cell.bbox else "unresolved")
                ),
            )

            facts.append(
                FiscalFact(
                    fact_uid=(f"{table.fragment_uid}:{legacy.row_order}:{legacy.measure}"
                              + (f":c{legacy.source_col_idx}" if legacy.measure in repeated_measures else "")),
                    indicator=str(legacy.measure),
                    raw_value=raw_value,
                    unit=unit,
                    normalized_value=normalized,
                    display_precision=len(decimals) if decimals else (0 if raw_value else None),
                    org_name=org_name,
                    org_scope=org_scope,
                    fiscal_year=fiscal_year,
                    period=period,
                    fund_scope=column_fund_scope,
                    budget_stage=_stage_for_fact(
                        str(legacy.measure), str((legacy.extra_dims or {}).get("source_header") or ""),
                        "final" if budget_stage == "决算" else ("budget" if budget_stage == "年初预算" else ""),
                        budget_stage,
                    ),
                    subject_code=legacy.classification_code,
                    subject_name=legacy.classification_name or None,
                    classification_level=legacy.classification_level,
                    parent_subject_code=legacy.parent_classification_code,
                    hierarchy_path=list(legacy.hierarchy_path),
                    table_code=table.table_code,
                    instance_uid=instance_uid,
                    row_order=legacy.row_order,
                    recognition_status=(
                        "ambiguous" if native_conflict or fund_identity_conflict else "low_confidence"
                        if table.confidence < 0.8 or (legacy.parse_confidence < 0.8 and not native_evidence_qualified)
                        or not column_identity_safe
                        or missing_subject_code
                        or not amount_usable
                        or (is_ocr_source and not ocr_qualified)
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
                        "source_table_title": table.title,
                        "amount_binding_status": "normalized" if amount_usable else "unresolved",
                        "column_identity_status": "bound" if column_identity_safe else "cross_page_width_drift",
                        "row_identity_status": "missing_subject_code" if missing_subject_code else "bound",
                        'fund_scope_conflict':fund_identity_conflict,
                        "qualification_evidence": {
                            "literal": "source_bound" if native_literal_bound else "candidate",
                            "structure": "column_bound" if column_identity_safe else "ambiguous",
                            "identity": "bound" if not missing_subject_code else "missing_code",
                            "native_evidence_qualified": bool(native_evidence_qualified),
                            "source_conflict": native_conflict,
                            "raw_cell_score": measure_cell.confidence if measure_cell is not None else None,
                        },
                        # ZC-01（AC-08）：完整证据实体随事实一起传递/存储/读取，
                        # 不再只留 evidence_id——物理页、bbox、来源单元格、
                        # 文件哈希在持久化往返后必须全部可回溯。
                        "evidence_refs": [evidence.to_dict()],
                        "row_identity_sources": [
                            {"file_hash": file_hash, "physical_page": cell.page_number,
                             "source_cell_id": (f"{cell.origin_fragment_uid or table.fragment_uid}:"
                                  f"r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}"
                                  f"c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}"),
                             "grid_raw_text": cell.grid_raw_text, "recovered_text": cell.raw_text,
                             "native_text_sources": list(cell.native_text_sources)}
                            for cell in row_cells if cell.col_idx in (legacy.extra_dims or {}).get('source_label_cols', [])
                            and cell.native_text_sources
                        ],
                        **({"ocr_provenance":ocr_provenance} if is_ocr_source else {}),
                    },
                )
            )
        return facts
