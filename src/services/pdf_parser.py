"""
Structured PDF parser for fiscal tables.
"""

from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import asyncpg

from src.services.fiscal_table_rules import (
    NINE_TABLE_RULES, detect_table_code, is_auxiliary_grant_expenditure_title, is_table_preamble_row,
    normalize_text)
from src.services.fiscal_row_roles import is_index_header

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
#: 右上角表号角标（「部门预算06表」「公开01-1表」），是表的编号而不是表题。
FORM_NUMBER_PATTERN = re.compile(r"^[一-鿿]{0,6}\d{1,2}(?:[-－]\d{1,2})?表$")
_PART_HEADING_RE = re.compile(r"^第[一二三四五六七八九十]+部分")
#: 表题中的年份（AC-02 片段身份：年度是逻辑表实例身份的一部分）
#: 表题年度只认独立的四位数：续页没有表题时以首行文字充当标题，2022 版 7 位编码
#: （2060301）和金额（2050.00）里的“20xx”不是年度，误读会把真实续页判成异年新表。
_TITLE_YEAR_RE = re.compile(r"(?<![\d.,])20\d{2}(?![\d.,])")
#: 表题中的主体机构 token（AC-02 片段身份：主体是逻辑表实例身份的一部分）
_TITLE_ORG_RE = re.compile(r"[\u4e00-\u9fa5]{1,8}?(?:局|中心|委员会|办事处|人民政府|管理所|执法队|学校|医院|图书馆|馆|公司)")
#: 表题里明写「编制单位：」时取标签后的完整名称；上面的短匹配会把长名截掉开头。
_TITLE_ORG_LABEL_RE = re.compile(r"(?:编制部门|编制单位)[:：]\s*(\S+?)(?=\s|(?:金额)?单位[:：]|$)")


def _stable_fragment_uid(*parts: Any, rows: Optional[Sequence[Sequence[str]]] = None) -> str:
    """确定性片段 UID：同一 PDF 同一解析器重复解析得到同一 UID（RC-10）。

    事实 UID、实例 UID、证据格 ID 都由片段 UID 派生——随机 UID 会让
    人工校正在重解析后找不到目标（AC-10 复核实证的闭环阻断）。
    UID 只需在同一 artifact 内唯一：跨文件同位同名同内容的碰撞可忽略，
    且证据 ID 另有原件哈希分量，不依赖片段 UID 的跨文件唯一性。
    """
    payload = "|".join(str(part or "") for part in parts)
    if rows is not None:
        rows_json = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        payload += "|" + hashlib.sha1(rows_json.encode("utf-8")).hexdigest()
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

#: 解析器指纹：表抽取策略组合变化时必须更新（缓存按指纹失效）
from src.services.input_processing_identity import processing_manifest, processing_config_fingerprint, SCHEMA_VERSION

PARSER_FINGERPRINT = "pdfplumber-cells-v27-trusted-input-" + processing_manifest()["sha256"][:16]


# ---------------------------------------------------------------------------
# 统一解析契约（ZC-01）：ParsedCell/ParsedTable 的权威定义收敛到
# src/schemas/parse_artifact.py，规则、证据、入库共用同一套对象。
# 这里按原导入路径兼容导出，既有消费方 import 路径不变。
# ---------------------------------------------------------------------------
from src.schemas.parse_artifact import (  # noqa: E402,F401
    CellPresence,
    CheckReceipt,
    EvidenceRef,
    FiscalFact,
    LogicalTableInstance,
    ParseArtifact,
    ParsePageState,
    ParsedCell,
    ParsedTable,
    ParsedTextBlock,
    dumps_decimal,
    parse_decimal,
)


class PDFParser:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn
        self._source_grid_fragments: List[ParsedTable] = []
        self._cell_candidate_tables: List[ParsedTable] = []
        self._reconstruction_decisions: List[dict] = []

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

    def build_parse_artifact(
        self,
        pdf_path: str,
        *,
        config_fingerprint: str = "",
        ocr_model_fingerprint: Optional[str] = None,
    ) -> ParseArtifact:
        """打开一次 PDF，产出统一解析对象（不访问数据库，纯函数式）。

        ZC-01（SYS-01）：规则、证据、结构化入库共用这一份产物；
        之后的持久化、物化都只消费 artifact，不再重开 PDF。
        """
        import hashlib

        import pdfplumber

        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {pdf_path}")

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        file_hash = digest.hexdigest()

        with pdfplumber.open(path) as pdf:
            return self.build_parse_artifact_from_handle(
                pdf,
                file_hash=file_hash,
                config_fingerprint=config_fingerprint,
                ocr_model_fingerprint=ocr_model_fingerprint,
            )

    def build_parse_artifact_from_handle(
        self,
        pdf: Any,
        *,
        file_hash: str,
        config_fingerprint: str = "",
        ocr_model_fingerprint: Optional[str] = None,
    ) -> ParseArtifact:
        """从已打开的 pdfplumber 句柄构建解析对象（供子进程复用同一次打开）。"""
        page_states: List[ParsePageState] = []
        text_blocks: List[ParsedTextBlock] = []
        fragments: List[ParsedTable] = []
        reading_probes = []
        self._source_grid_fragments = []
        self._cell_candidate_tables = []
        self._reconstruction_decisions = []

        page_count = len(pdf.pages)
        for page_number, page in enumerate(pdf.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            page_states.append(
                ParsePageState(
                    page_number=page_number,
                    has_text_layer=bool(page_text),
                    text_chars=len(page_text),
                    is_scanned=not bool(page_text),
                    rotation=int(getattr(page, "rotation", 0) or 0),
                )
            )
            # 保留原生文字块及其页坐标，供规则上下文与证据层共享。
            # 文本抽取异常时只影响该页文字块，不丢弃可用的表格片段。
            try:
                words = page.extract_words() or []
            except Exception:
                words = []
            from src.services.table_reading_assessment import collect_page_probe
            reading_probes.append(collect_page_probe(page, page_number, page_text, words))
            for word_index, word in enumerate(words):
                text = str(word.get("text") or "").strip()
                if not text:
                    continue
                bbox_values = [word.get("x0"), word.get("top"), word.get("x1"), word.get("bottom")]
                try:
                    bbox = tuple(float(value) for value in bbox_values)
                except (TypeError, ValueError):
                    bbox = None
                text_blocks.append(
                    ParsedTextBlock(
                        block_uid=f"native-p{page_number}-w{word_index}",
                        page_number=page_number,
                        text=text,
                        bbox=bbox if bbox and bbox[2] > bbox[0] and bbox[3] > bbox[1] else None,
                        confidence=None,
                        extraction_method="native_text",
                    )
                )
            previous = [t for t in self._coalesce_tables(fragments)
                        if t.extraction_method != 'page_text_fallback']
            current = self._extract_tables_from_page(
                page, page_number, continuation=previous[-1] if previous else None)
            if previous and current and page_number > 1:
                candidates = [t for t in current if t.extraction_method in {'lines', 'lines_strict'}]
                if candidates and self._is_continuation_table(previous[-1], candidates[0]) and self._native_continuation_columns_match(previous[-1], candidates[0]):
                    from src.services.native_dimension_recovery import repair_native_cross_page_name
                    fragments = repair_native_cross_page_name(pdf.pages[page_number - 2], page,
                                                              previous[-1], candidates[0], fragments)
            fragments.extend(current)

        merged = self._coalesce_tables(fragments)
        manifest = processing_manifest()
        artifact = ParseArtifact(
            source_file_hash=file_hash,
            schema_version=SCHEMA_VERSION,
            processing_manifest=manifest,
            parser_name="pdfplumber",
            parser_fingerprint=PARSER_FINGERPRINT,
            config_fingerprint=processing_config_fingerprint(config_fingerprint, manifest),
            ocr_model_fingerprint=ocr_model_fingerprint,
            page_count=page_count,
            page_states=page_states,
            text_blocks=text_blocks,
            # 合并后的逻辑表作为片段承载（跨页续表按重排行号物化）；
            # RC-02：合并前的物理片段原样保留在 physical_fragments，
            # 证据格 origin_* 字段可回溯到原页原格。
            table_fragments=merged,
            physical_fragments=list(fragments),
            source_grids=self._source_grid_fragments,
            reading_assessment={"version": "table-reading-v1", "pages": reading_probes},
            logical_tables=[
                self._build_logical_instance(table)
                for table in merged
            ],
        )
        from src.services.source_reconstruction import attach_reconstruction
        attach_reconstruction(artifact, self._cell_candidate_tables + fragments)
        artifact.reconstruction_decisions = list(self._reconstruction_decisions)
        from src.services.input_processing_identity import artifact_content_hash
        artifact.parse_run_uid = artifact_content_hash(artifact)[:32]
        return artifact

    async def persist_artifact_to_legacy_tables(
        self, document_version_id: int, artifact: ParseArtifact
    ) -> Dict[str, Any]:
        """把统一解析对象适配写入旧表（fiscal_table_cells），并返回 parse_pdf 同构摘要。

        ZC-01（SYS-01）：结构化入库不再重开 PDF；旧表保留历史读取与既有
        消费方，写入内容来自 artifact 的同一批单元格——两条存储路径共享
        一份解析事实，不产生第二套解析语义。
        """
        await self._clear_existing_cells(document_version_id)

        tables_count = 0
        cells_count = 0
        recognized_tables = 0
        low_confidence_tables = 0
        unknown_tables: List[str] = []
        errors: List[str] = []

        try:
            for table in artifact.table_fragments:
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
            logger.exception(
                "Failed to persist artifact to legacy tables for version %s",
                document_version_id,
            )
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

    def _extract_tables_from_page(self, page, page_number: int,
                                  continuation: Optional[ParsedTable] = None) -> List[ParsedTable]:
        best_candidates: List[ParsedTable] = []

        for method_name, settings in DEFAULT_TABLE_SETTINGS:
            try:
                candidates = []
                for table_index, table in enumerate(page.find_tables(table_settings=settings)):
                    rows = table.extract() or []
                    footer = (continuation is not None and len(rows) == 1 and len(rows[0]) >= 3
                              and normalize_text(str(rows[0][0] or '')) in {'合计', '总计'}
                              and all(self._parse_numeric(str(value or '')) is not None for value in rows[0][1:]))
                    if not self._is_meaningful_table(rows, native_grid=method_name in {"lines", "lines_strict"}) and not footer:
                        continue
                    parsed = self._build_parsed_table(
                        page=page,
                        page_number=page_number,
                        table_index=table_index,
                        table=table,
                        rows=rows,
                        extraction_method=method_name,
                    )
                    if method_name == "lines":
                        original = deepcopy(parsed)
                        self._certify_native_blank_cells(page, original.cells, method_name)
                        self._source_grid_fragments.append(original)
                    if footer:
                        parsed = self._align_native_continuation_footer(continuation, parsed)
                    parsed = self._recover_open_top_grid_row(page, parsed, reference=continuation)
                    parsed = self._recover_open_bottom_grid_row(page, parsed)
                    from src.services.native_structure_recovery import repair_native_structure
                    reference = (continuation if continuation is not None
                                 and page_number == self._last_page_number(continuation) + 1
                                 and not self._unit_hints_conflict(continuation, parsed)
                                 and self._native_continuation_columns_match(continuation, parsed) else None)
                    parsed = repair_native_structure(page, parsed, reference=reference)
                    from src.services.native_dimension_recovery import repair_native_dimensions, repair_native_code_baselines
                    # 标签归属也要观察金额明确留空的前行，先给原格空白判据。
                    self._certify_native_blank_cells(page, parsed.cells, method_name)
                    parsed = repair_native_dimensions(page, parsed, reference=reference)
                    parsed = repair_native_code_baselines(page, parsed)
                    candidates.append(parsed)
            except Exception:
                logger.debug(
                    "Table extraction failed on page %s with method %s",
                    page_number,
                    method_name,
                    exc_info=True,
                )
                continue

            self._cell_candidate_tables.extend(deepcopy(candidates))
            best_candidates = self._merge_candidates(best_candidates, candidates)

        from src.services.source_reconstruction import resolve_empty_table_shadows
        best_candidates, decisions = resolve_empty_table_shadows(best_candidates, page.extract_text() or "")
        self._reconstruction_decisions.extend(decisions)
        footer = self._extract_native_basic_footer(page, page_number)
        # 网格里的合计标签可能粘上相邻折行字（如「出\n公用经费合计」），按包含判断已有该行。
        if footer is not None and not any(
            any("人员经费合计" in normalize_text(str(value or "")) for value in row)
            and any("公用经费合计" in normalize_text(str(value or "")) for value in row)
            for candidate in best_candidates for row in candidate.rows
        ):
            best_candidates.append(footer)
        placeholder = self._build_page_text_placeholder(page, page_number, best_candidates)
        if (placeholder is not None and continuation is not None
                and continuation.table_code == placeholder.table_code
                and page_number - self._last_page_number(continuation) == 1):
            # 上一页已有同表种真实网格：本页只是过宽表格溢出的表题/表头残片，
            # 造占位表会凭空多出第二个实例。
            placeholder = None
        if placeholder is not None:
            best_candidates.append(placeholder)

        best_candidates = [self._attach_bilateral_total_line(page, candidate) for candidate in best_candidates]
        # 续表调列和余额事实须能回到原字符；这里只附加证明，不改字面值或原网格。
        from src.services.native_structure_recovery import bind_native_grid_character_sources
        best_candidates = [bind_native_grid_character_sources(page, candidate)
                           for candidate in best_candidates]

        return sorted(best_candidates, key=lambda item: (item.page_number, item.bbox[1], item.bbox[0]))

    _BILATERAL_TOTAL_LABELS = {"总计", "收入总计", "支出总计"}

    def _attach_bilateral_total_line(self, page, table: ParsedTable) -> ParsedTable:
        """双栏总表的「总计」行印在网格下沿之外时，按原字和原坐标接回表尾。

        只认收支总表/财政拨款收支总表、网格里还没有总计行、紧贴下沿一行高以内；
        行内只能是总计标签和金额，每个金额的左右边界都须落在网格对应金额列内。
        """
        if (table.table_code not in {"FIN_01_income_expenditure_total", "FIN_04_fiscal_grant_total"}
                or table.extraction_method not in {"lines", "lines_strict"}
                or any(normalize_text(str(value or "")) in self._BILATERAL_TOTAL_LABELS
                       for row in table.rows for value in row)):
            return table
        from src.services.fiscal_column_identity import canonical_column_mappings

        mappings = canonical_column_mappings(table, NINE_TABLE_RULES[table.table_code])
        heights = sorted(cell.bbox[3] - cell.bbox[1] for cell in table.cells
                         if cell.bbox and not cell.is_header and cell.row_span == 1)
        if not mappings or not heights:
            return table
        reach = table.bbox[3] + 1.5 * heights[len(heights) // 2]
        words = page.extract_words()
        labels = [word for word in words if normalize_text(word["text"]) in self._BILATERAL_TOTAL_LABELS
                  and table.bbox[3] - 1 <= word["top"] <= reach]
        if not labels:
            return table
        line = sorted((word for word in words if abs(word["top"] - labels[0]["top"]) <= 2),
                      key=lambda word: word["x0"])

        def column_of(word, columns):
            hits = [col for col in columns
                    if any(cell.col_idx == col and cell.bbox and cell.bbox[0] - 1 <= word["x0"]
                           and word["x1"] <= cell.bbox[2] + 1 for cell in table.cells)]
            return hits[0] if len(hits) == 1 else None

        money = {m["source_col_idx"] for m in mappings}
        label_cols = {col for m in mappings for col in m.get("source_label_cols", [])}
        placed = {}
        for word in line:
            if normalize_text(word["text"]) in self._BILATERAL_TOTAL_LABELS:
                col = column_of(word, label_cols)
            elif self._parse_numeric(word["text"]) is not None:
                col = column_of(word, money)
            else:
                return table
            if col is None or col in placed:
                return table
            placed[col] = word
        if not any(col in money for col in placed):
            return table
        width = max(len(row) for row in table.rows)
        row_idx = len(table.rows)
        row = [placed[col]["text"] if col in placed else "" for col in range(width)]
        cells = [ParsedCell(row_idx, col, word["text"], normalize_text(word["text"]),
                            self._parse_numeric(word["text"]), table.page_number,
                            (word["x0"], word["top"], word["x1"], word["bottom"]), False,
                            table.confidence, table.unit_hint, "native_footer")
                 for col, word in sorted(placed.items())]
        bottom = max(word["bottom"] for word in placed.values())
        return replace(table, rows=[*table.rows, row], cells=[*table.cells, *cells],
                       bbox=(table.bbox[0], table.bbox[1], table.bbox[2], max(table.bbox[3], bottom)))

    def _extract_native_basic_footer(self, page, page_number: int) -> Optional[ParsedTable]:
        """Keep an exact native two-lane total line even without a closed grid."""
        words = page.extract_words()
        labels = [word for word in words if normalize_text(word["text"]) == "人员经费合计"]
        for label in labels:
            line = sorted([word for word in words if abs(word["top"] - label["top"]) <= 2],
                          key=lambda word: word["x0"])
            if (len(line) != 4 or normalize_text(line[0]["text"]) != "人员经费合计"
                    or normalize_text(line[2]["text"]) != "公用经费合计"
                    or any(self._parse_numeric(line[col]["text"]) is None for col in (1, 3))):
                continue
            rows = [[word["text"] for word in line]]
            cells = [ParsedCell(0, col, word["text"], normalize_text(word["text"]),
                                self._parse_numeric(word["text"]), page_number,
                                (word["x0"], word["top"], word["x1"], word["bottom"]),
                                False, .99, None, "native_footer") for col, word in enumerate(line)]
            return ParsedTable(f"UNKNOWN_P{page_number}_FOOTER", "续表", page_number, -1,
                               (line[0]["x0"], min(word["top"] for word in line),
                                line[-1]["x1"], max(word["bottom"] for word in line)),
                               0, .99, "native_footer", None, rows, cells,
                               _stable_fragment_uid("native-footer", page_number, rows=rows))
        return None

    def _align_native_continuation_footer(self, previous: ParsedTable, current: ParsedTable) -> ParsedTable:
        """合计格跨编码/名称列时，按原金额边界恢复维度占位，金额和矩形原样保留。"""
        supported = {'FIN_02_income', 'FIN_03_expenditure', 'FIN_05_general_public_expenditure',
                     'FIN_08_gov_fund', 'FIN_09_state_capital'}
        if (previous.table_code not in supported or current.extraction_method not in {'lines', 'lines_strict'}
                or current.page_number - self._last_page_number(previous) != 1
                or len(current.rows) != 1 or self._unit_hints_conflict(previous, current)):
            return current
        from src.services.fiscal_column_identity import canonical_column_mappings

        mappings = canonical_column_mappings(previous, NINE_TABLE_RULES[previous.table_code])
        width = max(len(row) for row in previous.rows)
        money = sorted({int(m['source_col_idx']) for m in mappings})
        if money != list(range(2, width)) or len(current.rows[0]) != len(money) + 1:
            return current
        positions = {(c.row_idx, c.col_idx): c for c in current.cells}
        for source_col, target_col in enumerate(money, 1):
            cell = positions.get((0, source_col))
            boxes = [c.bbox for c in previous.cells if c.col_idx == target_col and c.bbox]
            if cell is None or cell.bbox is None or not boxes:
                return current
            ref = min(boxes, key=lambda b: b[2] - b[0])
            if any(abs(cell.bbox[index] - ref[index]) > 1 for index in (0, 2)):
                return current
        label = positions.get((0, 0))
        if label is None or label.bbox is None:
            return current
        row = [current.rows[0][0], '', *current.rows[0][1:]]
        cells = [replace(cell, col_idx=cell.col_idx + (1 if cell.col_idx else 0), is_header=False)
                 for cell in current.cells]
        cells.append(ParsedCell(0, 1, '', '', None, current.page_number, None,
                                False, current.confidence, current.unit_hint, current.extraction_method))
        return replace(current, rows=[row], cells=sorted(cells, key=lambda c: c.col_idx),
                       header_row_count=0,
                       fragment_uid=_stable_fragment_uid('native-continuation-footer', current.fragment_uid, rows=[row]))

    def _recover_open_top_grid_row(self, page, table: ParsedTable,
                                    reference: Optional[ParsedTable] = None) -> ParsedTable:
        """Recover a native continuation row above a grid's missing top border.

        Use the existing physical column boundaries and native word positions;
        accept only one adjacent row with a literal functional code/name and
        separate numeric amounts. Never concatenate neighbouring amount fields.
        """
        if (table.extraction_method not in {"lines", "lines_strict"}
                or not table.table_code.startswith("UNKNOWN_") or not table.rows
                or len(table.rows[0]) < 3
                or not (str(table.rows[0][0] or '').isdigit()
                        or normalize_text(str(table.rows[0][0] or '')) in {'合计', '总计'})):
            return table
        width = len(table.rows[0])
        boxes = []
        for col in range(width):
            candidates = [c.bbox for c in table.cells if c.col_idx == col and c.bbox]
            if reference is not None and col < 2:
                candidates = [c.bbox for c in reference.cells if c.col_idx == col and c.bbox
                              and c.bbox[2] - c.bbox[0] < table.bbox[2] - table.bbox[0]] or candidates
            if not candidates:
                return table
            boxes.append(min(candidates, key=lambda b: b[2] - b[0]))
        height = min(b[3] - b[1] for b in boxes)
        if height <= 0:
            return table
        words = [w for w in page.extract_words()
                 if table.bbox[0] <= w["x0"] < w["x1"] <= table.bbox[2]
                 and table.bbox[1] - 2 * height <= w["top"] < w["bottom"] <= table.bbox[1] + .5]
        if not words:
            return table
        values = [[] for _ in range(width)]
        for word in sorted(words, key=lambda w: w["x0"]):
            center = (word["x0"] + word["x1"]) / 2
            columns = [col for col, box in enumerate(boxes) if box[0] <= center < box[2]]
            if len(columns) != 1:
                return table
            values[columns[0]].append(word["text"])
        # Wrapped subject names may occupy two lines. Codes and amounts must
        # remain separate literal words on the same baseline.
        numeric_words = [w for w in words if self._parse_numeric(w["text"]) is not None]
        if (not numeric_words or max(w["top"] for w in numeric_words) - min(w["top"] for w in numeric_words) > 3
                or any(len(values[col]) > 1 for col in [0, *range(2, width)])):
            return table
        row = ["".join(parts) for parts in values]
        if (not row[0].isdigit() or len(row[0]) not in {3, 5, 7}
                or not row[1] or self._parse_numeric(row[1]) is not None
                or not any(self._parse_numeric(v) is not None for v in row[2:])
                or any(v and self._parse_numeric(v) is None for v in row[2:])):
            return table
        top = min(w["top"] for w in words)
        recovered = [ParsedCell(0, col, value, normalize_text(value), self._parse_numeric(value),
                                table.page_number, (box[0], top, box[2], table.bbox[1]), False,
                                table.confidence, table.unit_hint, table.extraction_method)
                     for col, (value, box) in enumerate(zip(row, boxes))]
        rows = [row, *table.rows]
        return replace(table, rows=rows, header_row_count=0,
                       cells=[*recovered, *[replace(c, row_idx=c.row_idx + 1, is_header=False) for c in table.cells]],
                       bbox=(table.bbox[0], top, table.bbox[2], table.bbox[3]),
                       fragment_uid=_stable_fragment_uid("native-top", table.fragment_uid, rows=rows),
                       confidence=max(table.confidence, self._score_table(rows)))

    def _recover_open_bottom_grid_row(self, page, table: ParsedTable) -> ParsedTable:
        """Include an adjacent native data/total row lacking a bottom border."""
        if table.table_code == 'FIN_06_basic_expenditure':
            return self._recover_economic_bottom_row(page, table)
        supported = {"FIN_02_income", "FIN_03_expenditure", "FIN_05_general_public_expenditure",
                     "FIN_08_gov_fund", "FIN_09_state_capital"}
        if (table.extraction_method not in {"lines", "lines_strict"} or not table.rows
                or (table.table_code not in supported and not table.table_code.startswith("UNKNOWN_"))):
            return table
        width = max(len(row) for row in table.rows)
        if width < 3:
            return table
        boxes = []
        for col in range(width):
            candidates = [c.bbox for c in table.cells if c.col_idx == col and c.bbox
                          and not c.is_header and (col != 0 or c.raw_text.isdigit())
                          and normalize_text(c.raw_text or "") not in {"类", "款", "项"}]
            # A merged full-code cell spans the unused 款/项 header lanes.
            # Empty padding cells do not establish another body column there.
            if col and not any(c.col_idx == col and not c.is_header and c.raw_text
                               and normalize_text(c.raw_text) not in {"类", "款", "项"}
                               for c in table.cells):
                candidates = []
            boxes.append(min(candidates, key=lambda b: b[2] - b[0]) if candidates else None)
        numeric_boxes = [c.bbox for c in table.cells if c.bbox and not c.is_header
                         and c.numeric_value is not None and c.col_idx >= 2]
        if not numeric_boxes:
            return table
        height = min(box[3] - box[1] for box in numeric_boxes)
        words = [w for w in page.extract_words()
                 if table.bbox[0] <= w["x0"] < w["x1"] <= table.bbox[2]
                 and table.bbox[3] - .5 <= w["top"] < w["bottom"] <= table.bbox[3] + height + 3]
        if not words or max(w["top"] for w in words) - min(w["top"] for w in words) > 3:
            return table
        values = [[] for _ in range(width)]
        for word in sorted(words, key=lambda w: w["x0"]):
            center = (word["x0"] + word["x1"]) / 2
            columns = [col for col, box in enumerate(boxes) if box and box[0] <= center < box[2]]
            if word["text"] in {"合计", "总计"}:
                columns = [0]
            if len(columns) != 1:
                return table
            values[columns[0]].append(word["text"])
        row = ["".join(parts) for parts in values]
        if not (row[0] in {"合计", "总计"} or row[0].isdigit() and len(row[0]) in {3, 5, 7}):
            return table
        text_cols = [col for col in range(1, width) if row[col] and self._parse_numeric(row[col]) is None]
        if row[0] not in {"合计", "总计"} and len(text_cols) != 1:
            return table
        name_col = text_cols[0] if text_cols else 1
        if not any(self._parse_numeric(value) is not None for value in row[name_col + 1:]):
            return table
        if any(value and self._parse_numeric(value) is None for value in row[name_col + 1:]):
            return table
        bottom = max(w["bottom"] for w in words)
        index = len(table.rows)
        cells = [ParsedCell(index, col, value, normalize_text(value), self._parse_numeric(value),
                            table.page_number, (box[0], table.bbox[3], box[2], bottom) if box else None,
                            False, table.confidence, table.unit_hint, table.extraction_method)
                 for col, (value, box) in enumerate(zip(row, boxes))]
        rows = [*table.rows, row]
        return replace(table, rows=rows, cells=[*table.cells, *cells],
                       bbox=(table.bbox[0], table.bbox[1], table.bbox[2], bottom),
                       fragment_uid=_stable_fragment_uid("native-bottom", table.fragment_uid, rows=rows))

    def _recover_economic_bottom_row(self, page, table: ParsedTable) -> ParsedTable:
        """双栏经济表末行以金额基线定位，编码允许在同一列内纵向折行。"""
        if table.extraction_method not in {'lines', 'lines_strict'} or not table.rows:
            return table
        width = max(map(len, table.rows))
        if width != 8:  # 两组 类/款/名称/金额，其他模板交给候选裁定。
            return table
        boxes = []
        for col in range(width):
            choices = [c.bbox for c in table.cells if c.col_idx == col and c.bbox and not c.is_header]
            if not choices:
                return table
            boxes.append(min(choices, key=lambda b: b[2] - b[0]))
        height = min(b[3] - b[1] for b in boxes)
        if height <= 0:
            return table
        words = page.extract_words()
        row, sources, bottom, found = [''] * width, [[] for _ in range(width)], table.bbox[3], False
        for lane in (0, 4):
            amount_box = boxes[lane + 3]
            amounts = [w for w in words if amount_box[0] <= (w['x0'] + w['x1']) / 2 < amount_box[2]
                       and table.bbox[3] - .5 <= w['top'] <= table.bbox[3] + height
                       and self._parse_numeric(w['text']) is not None]
            if not amounts:
                continue
            if len(amounts) != 1:
                return table
            baseline = amounts[0]['top']
            selected = []
            for col in range(lane, lane + 4):
                box = boxes[col]
                # 折行编码的下一行仍须位于本行高度内，不能借用相邻行的字。
                selected.append([w for w in words if box[0] <= (w['x0'] + w['x1']) / 2 < box[2]
                                 and table.bbox[3] - .5 <= w['top'] <= baseline + height - 2])
            values = [''.join(w['text'] for w in sorted(group, key=lambda w: (w['top'], w['x0'])))
                      for group in selected]
            if (not re.fullmatch(r'\d{3}', values[0]) or not re.fullmatch(r'\d{2}', values[1])
                    or not re.search(r'[\u4e00-\u9fff]', values[2])
                    or len(selected[3]) != 1 or values[3] != amounts[0]['text']):
                return table
            found = True
            for local, group in enumerate(selected):
                col = lane + local
                row[col] = values[local]
                sources[col] = [{'text': w['text'], 'bbox': [w['x0'], w['top'], w['x1'], w['bottom']],
                                 'basis': 'native_open_bottom_row'} for w in group]
                bottom = max(bottom, *(w['bottom'] for w in group))
        if not found:
            return table
        index = len(table.rows)
        cells = [ParsedCell(index, col, value, normalize_text(value), self._parse_numeric(value),
                    table.page_number, (boxes[col][0], table.bbox[3], boxes[col][2], bottom) if value else None,
                    False, table.confidence, table.unit_hint, table.extraction_method,
                    native_text_sources=sources[col]) for col, value in enumerate(row)]
        rows = [*table.rows, row]
        return replace(table, rows=rows, cells=[*table.cells, *cells],
                       bbox=(*table.bbox[:3], bottom),
                       fragment_uid=_stable_fragment_uid('native-economic-bottom', table.fragment_uid, rows=rows))

    #: 目录枚举行：一、收入支出决算总表 / 二、收入决算表
    _TOC_ENUM_PATTERN = re.compile(r"^[一二三四五六七八九十]{1,3}[、.．]\s*.{2,40}表$")
    #: 财政金额必带两位小数；目录页与章节扉页整页不出现
    _DECIMAL_AMOUNT_PATTERN = re.compile(r"\d+\.\d{2}")
    _TOC_NUMBER_PREFIX = re.compile(r"^\s*\d{1,2}\s*[.．、]\s*")

    def _is_contents_or_section_page(self, lines: Sequence[str]) -> bool:
        """判断是否为目录页/章节扉页（这类页面不是数据表）。

        背景：目录页会列出一串表名，``detect_table_code`` 能从其中命中某个
        九表别名，于是本方法的上游会为它生成 ``page_text_fallback`` 片段并
        赋予表码。后果是同一表种出现两个表实例，规则侧「表实例唯一」的
        取数判据随之失效，空白兜底也无法生效，检查被迫转人工。

        判据（须同时满足，避免误伤真实表）：
        1. 前 12 行中有 ≥2 行是「中文序号 + ……表」的纯枚举行，或首屏含「目录」；
        2. 整页不含任何两位小数金额。

        真实财政表的项目行以「支出/收入」结尾而非「表」，且必定带两位小数
        金额，因此不会被本条排除。
        """
        # A/B 对照与紧急回退开关：设为 1 时恢复修复前的取片段行为。
        # 仅供诊断与回归对比，生产环境不应设置。
        if os.getenv("PDF_DISABLE_TOC_GUARD", "").strip().lower() in {"1", "true", "yes"}:
            return False
        head_text = "".join(lines[:4]).replace(" ", "").replace("\u3000", "")
        has_toc_mark = "目录" in head_text
        enum_rows = sum(
            1 for line in lines[:12] if self._TOC_ENUM_PATTERN.match(line.strip())
        )
        if not has_toc_mark and enum_rows < 2:
            return False
        # 目录条目的阿拉伯序号紧贴年份（「10.2022年部门…预算表」），去掉序号
        # 再找金额，否则「10.20」会被当成两位小数金额而放过目录页。
        return not any(self._DECIMAL_AMOUNT_PATTERN.search(self._TOC_NUMBER_PREFIX.sub("", line))
                       for line in lines)

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

        # 目录页/章节扉页只有表名枚举、没有金额，不是数据表。
        # 若不在此拦截，它们会以 page_text_fallback 片段获得九表表码，
        # 使同一表种出现第二个实例，规则取数与空白兜底双双失效。
        if self._is_contents_or_section_page(lines):
            return None

        # 页面上已经抽到真实网格时不再造占位表。
        # 占位表只是一页纯文本，没有原格证据；它的表码来自标题/表头猜测，
        # 在一页多表的横版页上极易猜成另一张表（实测 2023 交通委 p17：
        # 真实表是 FIN_04，占位表却猜成 FIN_08），从而凭空制造第二个实例。
        if os.getenv("PDF_DISABLE_PLACEHOLDER_ON_TABLE_PAGE", "").strip().lower() not in {"1", "true", "yes"}:
            native = [
                item for item in candidates
                if item.extraction_method in {"lines", "lines_strict"}
                and len(item.rows) >= 3
                and max((len(row) for row in item.rows), default=0) >= 3
            ]
            page_area = float(getattr(page, "width", 0.0) or 0.0) * float(
                getattr(page, "height", 0.0) or 0.0
            )
            if native and page_area > 0:
                covered = max(
                    max(0.0, (item.bbox[2] - item.bbox[0])) * max(0.0, (item.bbox[3] - item.bbox[1]))
                    for item in native
                )
                if covered / page_area >= 0.15:
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
            fragment_uid=_stable_fragment_uid(
                "fallback", page_number, table_code, title_line, rows=rows
            ),
        )

    def _build_logical_instance(self, table: ParsedTable) -> LogicalTableInstance:
        """为（可能跨页合并后的）片段构建逻辑实例（AC-02 / SYS-16）。

        - ``page_end`` 取单元格真实物理页最大值——此前固定等于起始页，
          跨页实例的页跨度被写成 1；
        - 主体与年度从表题解析进入实例身份（识别不到保持 None，不猜）；
        - 合并过多个物理片段时 ``fragment_uids`` 覆盖全部来源。
        """
        cell_pages = [cell.page_number for cell in table.cells if cell.page_number]
        page_end = max(cell_pages) if cell_pages else table.page_number
        years = {int(y) for y in _TITLE_YEAR_RE.findall(table.title or "")}
        fiscal_year = next(iter(sorted(years)), None)
        labelled = _TITLE_ORG_LABEL_RE.search(table.title or "")
        orgs = [labelled.group(1)] if labelled else _TITLE_ORG_RE.findall(table.title or "")
        org_name = orgs[0].strip() if orgs else None
        return LogicalTableInstance(
            instance_uid=table.fragment_uid,
            table_code=table.table_code,
            title=table.title,
            fragment_uids=[
                *(table.source_fragment_uids or []),
                table.fragment_uid,
            ],
            page_start=table.page_number,
            page_end=page_end,
            org_name=org_name,
            fiscal_year=fiscal_year,
            row_count=len(table.rows),
            confidence=table.confidence,
        )

    def _coalesce_tables(self, tables: Sequence[ParsedTable]) -> List[ParsedTable]:
        merged: List[ParsedTable] = []
        hints = [table for table in tables if table.extraction_method == 'page_text_fallback']
        for table in tables:
            if table.extraction_method == 'page_text_fallback':
                continue  # Narrative/title hints cannot interrupt physical continuation.
            if not merged:
                merged.append(table)
                continue

            previous_index = len(merged) - 1
            if table.extraction_method == "native_footer":
                for index in range(len(merged) - 1, -1, -1):
                    candidate = merged[index]
                    if candidate.table_code == "FIN_06_basic_expenditure":
                        if table.page_number - self._last_page_number(candidate) == 1:
                            previous_index = index
                        break
                    if not candidate.table_code.startswith("UNKNOWN_"):
                        break
            previous = merged[previous_index]
            table = self._align_native_total_footer(previous, table)
            table = self._align_native_continuation_columns(previous, table)
            table = self._collapse_native_split_label_columns(previous, table)
            table = self._pad_native_trailing_columns(previous, table)
            if self._is_continuation_table(previous, table) or self._is_detached_body(previous, table):
                merged[previous_index] = self._append_table(previous, table)
                continue
            merged.append(table)
        return sorted([*merged, *hints], key=lambda table: (table.page_number, table.bbox[1], table.bbox[0]))

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
        header_row_count = self._detect_header_row_count(
            rows, framed_preamble=extraction_method in {"lines", "lines_strict"})
        flattened_headers = self._collect_headers(rows, header_row_count)
        normalized_title = normalize_text(title)
        if ("编制说明" in normalized_title or "决算情况说明" in normalized_title
                or is_auxiliary_grant_expenditure_title(title)):
            table_code, match_confidence = None, 0.0
        else:
            table_code, match_confidence = detect_table_code(
                title=title,
                headers=flattened_headers,
                source_hint=" ".join(flattened_headers),
            )
        if (len(rows) == 1 and len(rows[0]) == 4
                and normalize_text(str(rows[0][0] or "")) == "人员经费合计"
                and normalize_text(str(rows[0][2] or "")) == "公用经费合计"):
            # This is a footer awaiting its preceding table identity, not a
            # standalone 三公 table merely because its labels contain 经费.
            table_code, match_confidence = None, 0.0
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

        parsed = ParsedTable(
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
            # RC-10：片段 UID 由（页号×表序×表种×表题×内容）派生——同一份
            # PDF 重复解析得到同一 UID，事实 UID 与证据格 ID 随之稳定，
            # 人工校正在重解析后仍能命中目标。
            fragment_uid=_stable_fragment_uid(
                "t", page_number, table_index, final_code, title, rows=rows
            ),
        )
        from src.services.fiscal_column_identity import mixed_expenditure_header_cells

        headers = mixed_expenditure_header_cells(parsed)
        for cell in parsed.cells:
            if (cell.row_idx, cell.col_idx) in headers:
                cell.is_header = True
        return parsed

    @staticmethod
    def _certify_native_blank_cells(page, cells, extraction_method):
        """空白须有原页网格坐标证明；扫描图像、抽字失败和合并占位格不能认证。"""
        for cell in cells:
            cell.native_blank_verified = False
        if extraction_method not in {"lines", "lines_strict"}:
            return
        chars = getattr(page, "chars", None)
        images = getattr(page, "images", None)
        if not chars or images is None:
            return

        boxes = []
        # PDF 的排版空格没有可见字形，不应阻止对原件空白格的认证；未知字符仍保守阻塞。
        visible = [c for c in chars if 'text' not in c or str(c.get('text') or '').strip()]
        for item in [*visible, *images]:
            try:
                boxes.append(tuple(float(item[k]) for k in ("x0", "top", "x1", "bottom")))
            except (KeyError, TypeError, ValueError):
                return  # 坐标未确认时保留未识别，不能当作原件空白。

        # 向量字形和填充图形也可能是没有文本层的数字。表框线仅在边界，
        # 不把整张外框的bbox当作格内墨迹；未知曲线则保守拒绝认证。
        vector_boxes = []
        for item in getattr(page, 'curves', []) or []:
            try:
                vector_boxes.append(tuple(float(item[k]) for k in ('x0', 'top', 'x1', 'bottom')))
            except (KeyError, TypeError, ValueError):
                return
        for item in getattr(page, 'rects', []) or []:
            color = item.get('non_stroking_color')
            if item.get('fill') and color not in (1, 1.0, (1,1,1), [1,1,1]):
                try:
                    vector_boxes.append(tuple(float(item[k]) for k in ('x0', 'top', 'x1', 'bottom')))
                except (KeyError, TypeError, ValueError):
                    return

        for cell in cells:
            if (cell.raw_text or cell.numeric_value is not None
                    or not cell.bbox or cell.bbox[2] <= cell.bbox[0] or cell.bbox[3] <= cell.bbox[1]):
                continue
            inner = (cell.bbox[0]+.75, cell.bbox[1]+.75, cell.bbox[2]-.75, cell.bbox[3]-.75)
            if any(min(b[2],inner[2]) > max(b[0],inner[0])
                   and min(b[3],inner[3]) > max(b[1],inner[1]) for b in vector_boxes):
                continue
            if not any(min(box[2], cell.bbox[2]) > max(box[0], cell.bbox[0]) + .1
                       and min(box[3], cell.bbox[3]) > max(box[1], cell.bbox[1]) + .1 for box in boxes):
                cell.native_blank_verified = True

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
        boxes = [box for row in table.rows for box in row.cells if box]
        def edges(axis):
            values = sorted({float(box[index]) for box in boxes for index in axis})
            return [value for index, value in enumerate(values)
                    if index == 0 or value - values[index - 1] > .5]
        x_edges, y_edges = edges((0, 2)), edges((1, 3))

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
                        is_header=row_idx < header_row_count or is_index_header(row),
                        confidence=confidence if raw_text else max(0.5, confidence - 0.25),
                        unit_hint=unit_hint,
                        extraction_method=extraction_method,
                        # 以原页网格边界计跨度，不能把合并格的 None 占位当实际空白。
                        col_span=1 + sum(bbox[0] + .5 < edge < bbox[2] - .5 for edge in x_edges) if bbox else 1,
                        row_span=1 + sum(bbox[1] + .5 < edge < bbox[3] - .5 for edge in y_edges) if bbox else 1,
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
        from src.services.source_reconstruction import resolve_native_table_candidates
        return resolve_native_table_candidates(self, existing, incoming)

    def _text_grid_shadow(self, first: ParsedTable, second: ParsedTable) -> bool:
        """A partial text extraction cannot form a second instance of a grid.

        Require matching table identity, overlapping body, a ruled header, and
        no independently usable columns in the text candidate. Two real tables
        with their own valid headers remain separate.
        """
        text = first if first.extraction_method == "text" else second
        grid = second if text is first else first
        rule = NINE_TABLE_RULES.get(grid.table_code)
        if rule is None or grid.extraction_method not in {"lines", "lines_strict"}:
            return False
        from src.services.fiscal_column_identity import canonical_column_mappings

        grid_mappings = canonical_column_mappings(grid, rule)
        text_mappings = canonical_column_mappings(text, rule)
        if not grid_mappings:
            return False
        if not text_mappings:
            return True
        # 文字策略只是把同一网格的数字重排了一遍：金额全都能在网格里找到，列身份也只认出
        # 网格的一部分，没有一条独立内容。表头同样完整的文字候选仍按第二张表保留。
        grid_values = {c.numeric_value for c in grid.cells if c.numeric_value is not None and not c.is_header}
        text_values = [c.numeric_value for c in text.cells if c.numeric_value is not None and not c.is_header]
        if (any(text_values) and all(value in grid_values for value in text_values)
                and {m['canonical_measure'] for m in text_mappings} < {m['canonical_measure'] for m in grid_mappings}):
            return True
        if grid.table_code not in {'FIN_08_gov_fund', 'FIN_09_state_capital'}:
            # 文字策略把表题、单位、表头残片排成网格时，阶段词（「…经费预算数」）
            # 也能给它映射列，但映射列下没有任何金额：它是影子，不是第二张表。
            mapped = {m['source_col_idx'] for m in text_mappings}
            return not any(c.numeric_value is not None and not c.is_header and c.col_idx in mapped
                           for c in text.cells)
        # 空表说明可能把 text 候选框撑大，使同一网格被保留成第二实例。
        # 只有两个候选的金额格都无非零值、原页网格空格已经认证，且文本
        # 表头实际覆盖网格表头时才消除影子；真实第二表与金额冲突仍保留。
        if grid.table_code not in {'FIN_08_gov_fund', 'FIN_09_state_capital'}:
            return False
        money = {m['source_col_idx'] for m in grid_mappings}
        data = [c for c in grid.cells if not c.is_header and c.col_idx in money]
        if (not data or any(c.numeric_value not in (None, 0) for c in data)
                or any(not c.raw_text.strip() and not c.native_blank_verified for c in data)):
            return False
        if any(c.numeric_value not in (None, 0) for c in text.cells if not c.is_header):
            return False
        literal = normalize_text(''.join(str(v or '') for row in text.rows for v in row))
        if not any(note in literal for note in ('本表无数据', '本表为空表')):
            return False
        headers = [c for c in grid.cells if c.is_header and c.bbox and c.raw_text.strip()]
        shared = {normalize_text(c.raw_text) for c in text.cells if c.bbox and c.raw_text.strip()
                  and len(normalize_text(c.raw_text)) >= 4
                  and any(normalize_text(c.raw_text) in normalize_text(h.raw_text)
                          and self._containment_ratio(c.bbox, h.bbox) >= .3 for h in headers)}
        return len(shared) >= 2

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

    def _detect_header_row_count(self, rows: Sequence[Sequence[str]], framed_preamble: bool = False) -> int:
        if not rows:
            return 0

        # 格线表把表题/编制单位/空行框进表格时，这几行不占表头窗口，否则真正的表头
        # 会落在窗口外。文字策略候选本就从表题开始排行，沿用原判定。
        lead = 0
        while framed_preamble and lead < len(rows) - 1 and (
                not any(str(value or "").strip() for value in rows[lead]) or is_table_preamble_row(rows[lead])):
            lead += 1
        header_rows = lead
        amount_columns: set[int] = set()
        amount_headers = {
            normalize_text(alias)
            for rule in NINE_TABLE_RULES.values()
            for aliases in rule.measure_aliases.values() for alias in aliases
        }
        for row in rows[lead:lead + 4]:
            texts = [str(value).strip() for value in row if str(value).strip()]
            if not texts:
                header_rows += 1
                continue
            numeric_ratio = sum(1 for value in texts if self._looks_numeric(value)) / len(texts)
            merged_text = normalize_text("".join(texts))
            if is_index_header(texts):
                header_rows += 1
                continue
            if "决算表" in merged_text or "总表" in merged_text:
                header_rows += 1
                continue
            # A sparse data row may contain only one amount among several
            # dimension labels. Use an already identified monetary column;
            # unrelated codes, years and structural index rows cannot trigger it.
            if any(col < len(row) and self._looks_numeric(str(row[col] or ""))
                   for col in amount_columns):
                break
            # 空表仍有数据行。已有金额表头后出现明确业务行标签时，不能
            # 因金额全空而把头几条业务行误标为表头。
            literal_money_only = all(not str(row[col] or "").strip() or self._looks_numeric(str(row[col]))
                                     for col in amount_columns if col < len(row))
            if amount_columns and literal_money_only and any(re.match(
                r"^(?:[一二三四五六七八九十]+、|本年(?:收入|支出)合计|年[初末].*结转|总计$|合计$)",
                normalize_text(str(value or "")),
            ) for value in row):
                break
            if amount_columns and literal_money_only and row and re.fullmatch(r"(?:\d{3}|\d{5}|\d{7})", str(row[0] or "").strip()) and any(
                    value and re.search(r"[\u4e00-\u9fff]", str(value))
                    for col, value in enumerate(row[1:], 1) if col not in amount_columns):
                break
            dimension_headers = {"功能分类科目编码", "科目名称", "科目编码", "年度", "年份", "类", "款", "项"}
            if numeric_ratio <= 0.45 or any(normalize_text(value) in dimension_headers for value in texts):
                header_rows += 1
                amount_columns.update(
                    col for col, value in enumerate(row)
                    if any(alias and alias in normalize_text(str(value or ""))
                           for alias in amount_headers)
                )
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
        if FORM_NUMBER_PATTERN.fullmatch(normalized):
            score -= 5.0
        if any(normalized == normalize_text(alias) for rule in NINE_TABLE_RULES.values() for alias in rule.aliases):
            score += 10.0
        elif any(len(normalize_text(alias)) >= 5 and normalized.endswith(normalize_text(alias))
                 for rule in NINE_TABLE_RULES.values() for alias in rule.aliases):
            score += 6.0  # “2022 年度收入支出决算总表”：年度/主体前缀 + 规范表名
        if _PART_HEADING_RE.match(normalized):
            # “第二部分 ××2022 年度部门决算表”是全书分部标题，与首表同页时不能顶替表题。
            score -= 8.0
        score -= index * 0.1
        return score

    def _detect_unit_hint(
        self,
        page,
        title: str,
        rows: Sequence[Sequence[str]],
    ) -> Optional[str]:
        local = '\n'.join([title, *[' '.join(str(item or '') for item in row) for row in rows[:3]]])
        units = {next(group for group in reversed(match.groups()) if group)
                 for match in UNIT_PATTERN.finditer(local)}
        if len(units) == 1:
            return next(iter(units))
        if units:
            return None
        page_units = {next(group for group in reversed(match.groups()) if group)
                      for match in UNIT_PATTERN.finditer(page.extract_text() or '')}
        return next(iter(page_units)) if len(page_units) == 1 else None

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
        final_nonempty_row = next(
            (
                row
                for row in reversed(previous.rows)
                if any(str(cell or "").strip() for cell in row)
            ),
            [],
        )
        if (
            final_nonempty_row
            and str(final_nonempty_row[0] or "").strip().startswith(("合计", "总计"))
            and self._headers_look_repeated(previous, current)
        ):
            return False
        if not self._compatible_column_count(previous, current):
            return False
        # RC-02：单位口径冲突（万元 vs 元）的两段不是同一实例的续页——
        # 合并会让归一值出现数量级差错。
        if self._unit_hints_conflict(previous, current):
            return False
        if self._starts_new_table(previous, current):
            return False

        previous_code = previous.table_code
        current_code = current.table_code
        if (previous_code.startswith("UNKNOWN_") and current_code.startswith("UNKNOWN_")
                and is_auxiliary_grant_expenditure_title(previous.title)
                and self._headers_look_repeated(previous, current)):
            # 九表之外的附表续页同样重复表头、不带表题，接回附表而不是另立一张认不出的表。
            return True
        if previous_code == current_code and not previous_code.startswith("UNKNOWN_"):
            # AC-02（SYS-16）：同表种不等于同实例。相邻两页的表各有独立表题、
            # 且表题不同（主体/年度/口径不同）时，是两张表，不得合并。
            if self._fragment_titles_conflict(previous, current):
                return False
            return True

        if current_code.startswith("UNKNOWN_") and not previous_code.startswith("UNKNOWN_"):
            if self._fragment_titles_conflict(previous, current):
                return False
            if self._native_continuation_columns_match(previous, current):
                return True
            if current.extraction_method == "native_footer":
                return False  # Exact native footer proof must not fall back to ratio heuristics.
            if self._looks_like_data_continuation(current) and (
                current.header_row_count <= 1
                or self._headers_look_repeated(previous, current)
            ):
                return True

        return False

    def _is_detached_body(self, previous: ParsedTable, current: ParsedTable) -> bool:
        """同一页上表头框和表体框被一道空隙隔成两块：表头块没有数据行，表体紧贴其下且逐列对齐。"""
        if (current.page_number != previous.page_number or self._last_page_number(previous) != previous.page_number
                or not current.table_code.startswith("UNKNOWN_") or previous.table_code not in NINE_TABLE_RULES
                or len(previous.rows) > previous.header_row_count):
            return False
        # 原件常在两块之间空出一行（约 25pt）；逐列对齐才是主要证据，间距只排除远处的另一张表。
        return (-1 <= current.bbox[1] - previous.bbox[3] <= 40
                and self._native_continuation_columns_match(previous, current))

    def _starts_new_table(self, previous: ParsedTable, current: ParsedTable) -> bool:
        """自带表题、且有一套与前表不同的完整表头的片段是新表，不是续页。

        上海 2022 预算在功能分类表前后插印「部门财政拨款支出预算明细表」
        （行维度是预算单位）。它与前表列数相近、模糊表种相同，被当成续页后，
        前表的列身份会套到它身上（编码被读成金额、基本支出记成项目支出），
        真正的续页反而接不上。续页要么重复表头，要么没有表头。
        """
        if current.header_row_count < 2 or self._headers_look_repeated(previous, current):
            return False
        title = (current.title or "").strip()
        if not title or self._CONTINUATION_TITLE_PATTERN.search(title):
            return False
        first_row = current.rows[0] if current.rows else []
        fallback_title = " ".join(str(value).strip() for value in first_row if str(value).strip())
        if normalize_text(title) == normalize_text(fallback_title):
            return False  # 表上方没有标题时，_extract_title 回退为首行表头文字
        headers = self._collect_headers(current.rows, current.header_row_count)
        return bool(headers) and not any(self._looks_numeric(header) for header in headers)

    def _native_blank_basic_tail_matches(self, previous: ParsedTable, current: ParsedTable, mappings) -> bool:
        """空金额经济分类尾页仍须导入；明确留空，不能生成零值事实。"""
        if (previous.table_code != "FIN_06_basic_expenditure"
                or current.page_number != self._last_page_number(previous) + 1
                or not current.table_code.startswith("UNKNOWN_")
                or any(len(row) != 6 for row in current.rows)
                or not current.rows
                or {m["source_col_idx"] for m in mappings} != {2, 5}
                or [normalize_text(str(current.rows[-1][col] or "")) for col in (0, 3)]
                != ["人员经费合计", "公用经费合计"]):
            return False
        amounts = [c for c in current.cells if c.col_idx in {2, 5}]
        if (len(amounts) != 2 * len(current.rows)
                or any(c.raw_text.strip() or not c.bbox or not c.native_blank_verified for c in amounts)):
            return False
        codes_seen = 0
        for col in (0, 3):
            prior = [str(row[col] or "").strip() for row in previous.rows
                     if len(row) == 6 and re.fullmatch(r"\d{3}(?:\d{2}){0,2}", str(row[col] or "").strip())]
            lane = [c for c in current.cells if c.col_idx == col and c.row_idx < len(current.rows) - 1
                    and c.raw_text.strip()]
            for cell in lane:
                text = cell.raw_text.strip()
                name = str(current.rows[cell.row_idx][col + 1] or "")
                if (not prior or not re.fullmatch(r"\d{3}(?:\d{2}){0,2}", text)
                        or not re.search(r"[\u4e00-\u9fff]", name)
                        or text[:3] != prior[-1][:3] or int(text) <= int(prior[-1])
                        or not cell.bbox or not cell.native_text_sources
                        or "".join(p.get("text", "") for p in cell.native_text_sources) != text
                        or any(not p.get("bbox") or p["bbox"][0] < cell.bbox[0] - .5
                               or p["bbox"][2] > cell.bbox[2] + .5 for p in cell.native_text_sources)):
                    return False
                prior.append(text)
                codes_seen += 1
        return codes_seen > 0

    def _native_continuation_columns_match(self, previous: ParsedTable, current: ParsedTable) -> bool:
        """Bind a headerless native grid to its preceding literal column layout.

        A sparse continuation can start with many blank amounts; its text/numeric
        ratio and inferred header count say nothing about whether it is a table.
        Require every physical column boundary to match instead. This also lets
        native continuation cells inherit table identity without treating a low
        title-match score as uncertain character recognition.
        """
        if previous.table_code not in NINE_TABLE_RULES:
            return False
        if current.extraction_method == "native_footer":
            if (previous.table_code != "FIN_06_basic_expenditure" or len(current.rows) != 1
                    or self._unit_hints_conflict(previous, current)
                    or self._fragment_titles_conflict(previous, current)):
                return False
            from src.services.fiscal_column_identity import canonical_column_mappings

            mappings = sorted(canonical_column_mappings(previous, NINE_TABLE_RULES[previous.table_code]),
                              key=lambda mapping: mapping["source_col_idx"])
            labels = [normalize_text(cell.raw_text) for cell in current.cells if cell.numeric_value is None]
            if len(mappings) != 2 or labels != ["人员经费合计", "公用经费合计"]:
                return False
            amounts = [cell for cell in current.cells if cell.numeric_value is not None]
            if len(amounts) != 2:
                return False
            for cell, mapping in zip(amounts, mappings):
                boxes = [original.bbox for original in previous.cells if original.bbox
                         and original.col_idx == mapping["source_col_idx"]]
                if cell.col_idx != mapping["source_col_idx"] or not cell.bbox or not boxes:
                    return False
                box = min(boxes, key=lambda value: value[2] - value[0])
                if not (box[0] - .5 <= cell.bbox[0] < cell.bbox[2] <= box[2] + .5):
                    return False
            return True
        if previous.extraction_method not in {"lines", "lines_strict"} or current.extraction_method not in {"lines", "lines_strict"}:
            return False
        if self._fragment_titles_conflict(previous, current):
            return False
        if any(cell.extraction_method == "ocr" for cell in current.cells):
            return False
        widths = [{len(row) for row in table.rows} for table in (previous, current)]
        if len(widths[0]) != 1 or widths[0] != widths[1]:
            return False
        width = next(iter(widths[0]), 0)
        if width < 2:
            return False

        from src.services.fiscal_column_identity import canonical_column_mappings

        mappings = canonical_column_mappings(previous, NINE_TABLE_RULES[previous.table_code])
        money_columns = {m["source_col_idx"] for m in mappings}
        if not money_columns:
            return False

        has_amount = any(cell.col_idx in money_columns and self._parse_numeric(cell.raw_text) is not None
                         for cell in current.cells)
        if not has_amount and not self._native_blank_basic_tail_matches(previous, current, mappings):
            # 金额全空的续页也须导入。仅接受带连续序号的双栏决算总表，
            # 所有金额格必须为经原页网格确认的空白，不能凭列宽拼接另一张表。
            if previous.table_code not in {"FIN_01_income_expenditure_total", "FIN_04_fiscal_grant_total"}:
                return False
            label_columns = sorted({col for mapping in mappings for col in mapping.get("source_label_cols", [])})
            if not label_columns:
                return False
            label_col = label_columns[-1]
            serials = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三",
                       "十四", "十五", "十六", "十七", "十八", "十九", "二十", "二十一", "二十二", "二十三", "二十四"]
            def ordinal(row):
                text = normalize_text(str(row[label_col] or "")) if label_col < len(row) else ""
                prefix, sep, _ = text.partition("、")
                return serials.index(prefix) if sep and prefix in serials else None
            prior = next((ordinal(row) for row in reversed(previous.rows) if ordinal(row) is not None), None)
            first = ordinal(current.rows[0]) if current.rows else None
            blanks = [cell for cell in current.cells if cell.col_idx in money_columns]
            if (prior is None or first != prior + 1 or not blanks
                    or any(cell.raw_text.strip() or not cell.native_blank_verified for cell in blanks)):
                return False

        def boundaries(table: ParsedTable):
            if not table.bbox or table.bbox[2] <= table.bbox[0]:
                return None
            left, span = table.bbox[0], table.bbox[2] - table.bbox[0]
            result = []
            for column in sorted(money_columns):
                boxes = [cell.bbox for cell in table.cells if cell.col_idx == column
                         and cell.bbox and cell.bbox[2] > cell.bbox[0]]
                if not boxes:
                    return None
                box = min(boxes, key=lambda bbox: bbox[2] - bbox[0])
                result.append(((box[0] - left) / span, (box[2] - left) / span))
            return result

        left, right = boundaries(previous), boundaries(current)
        if left is None or right is None:
            return False
        return all(abs(a - b) <= .002 for first, second in zip(left, right)
                   for a, b in zip(first, second))

    def _pad_native_trailing_columns(self, previous: ParsedTable, current: ParsedTable) -> ParsedTable:
        """续页网格少画了最右侧的空列：其余各列左右边界与前页逐列一致时，补空位而不是判列宽漂移。

        前页这几列不能有任何金额；补的是「无此格」占位（None），不是零，也不生成单元格。
        """
        if (current.page_number - self._last_page_number(previous) != 1
                or previous.table_code not in NINE_TABLE_RULES
                or not current.table_code.startswith("UNKNOWN_")
                or previous.extraction_method not in {"lines", "lines_strict"}
                or current.extraction_method not in {"lines", "lines_strict"}
                or self._unit_hints_conflict(previous, current)
                or self._fragment_titles_conflict(previous, current)):
            return current
        widths = [{len(row) for row in table.rows} for table in (previous, current)]
        if any(len(values) != 1 for values in widths):
            return current
        width, narrow = next(iter(widths[0])), next(iter(widths[1]))
        if width - narrow not in {1, 2}:
            return current
        if any(cell.col_idx >= narrow and not cell.is_header and (cell.numeric_value is not None or cell.raw_text.strip())
               for cell in previous.cells):
            return current

        def bounds(table, col):
            boxes = [c.bbox for c in table.cells if c.col_idx == col and c.bbox and c.page_number == table.page_number]
            return min(boxes, key=lambda b: b[2] - b[0]) if boxes else None

        for col in range(narrow):
            target, source = bounds(previous, col), bounds(current, col)
            if target is None or source is None or abs(target[0] - source[0]) > 1 or abs(target[2] - source[2]) > 1:
                return current
        return replace(current, rows=[[*row, *([None] * (width - narrow))] for row in current.rows])

    def _collapse_native_split_label_columns(self, previous: ParsedTable, current: ParsedTable) -> ParsedTable:
        """续页的编码/科目名称区被个别行错位的竖线多切出一两列时，按原格位置并回前页标签列。

        上海 2022 农业农村委支出表续页：两行的编码格右线画在 131.4 而非 126.2，整页多出
        一列，金额事实全部记成跨页列宽漂移。只有金额区逐列与前页同界、每个标签格的中心
        都唯一落在前页某一标签列内、且同一行不会有两段文字落进同一列时才改列号；
        原行列号、页码、bbox 与原文都保留可追溯。
        """
        if (current.page_number - self._last_page_number(previous) != 1
                or previous.table_code not in NINE_TABLE_RULES
                or not current.table_code.startswith("UNKNOWN_")
                or previous.extraction_method not in {"lines", "lines_strict"}
                or current.extraction_method not in {"lines", "lines_strict"}
                or self._unit_hints_conflict(previous, current)
                or self._fragment_titles_conflict(previous, current)):
            return current
        widths = [{len(row) for row in table.rows} for table in (previous, current)]
        if any(len(values) != 1 for values in widths):
            return current
        width, wide = next(iter(widths[0])), next(iter(widths[1]))
        difference = wide - width
        if difference not in {1, 2}:
            return current
        from src.services.fiscal_column_identity import canonical_column_mappings

        mappings = canonical_column_mappings(previous, NINE_TABLE_RULES[previous.table_code])
        dimensions = {col for m in mappings for col in m.get("source_label_cols", [])}
        if not mappings or not dimensions:
            return current
        prefix = max(dimensions) + 1
        if min(m["source_col_idx"] for m in mappings) < prefix:
            return current

        def bounds(table, col):
            boxes = [c.bbox for c in table.cells
                     if c.col_idx == col and c.bbox and c.page_number == table.page_number]
            return min(boxes, key=lambda b: b[2] - b[0]) if boxes else None

        for col in range(prefix, width):
            target, source = bounds(previous, col), bounds(current, col + difference)
            if target is None or source is None or abs(target[0] - source[0]) > 1 or abs(target[2] - source[2]) > 1:
                return current
            for cell in current.cells:
                if cell.col_idx == col + difference and cell.raw_text.strip() and (
                        not cell.bbox or cell.bbox[0] < target[0] - 1 or cell.bbox[2] > target[2] + 1):
                    return current
        lanes = [bounds(previous, col) for col in range(prefix)]
        if any(lane is None for lane in lanes):
            return current
        columns: Dict[Tuple[int, int], int] = {}
        for cell in current.cells:
            if cell.col_idx >= prefix + difference:
                columns[(cell.row_idx, cell.col_idx)] = cell.col_idx - difference
                continue
            if not cell.raw_text.strip():
                continue
            if not cell.bbox:
                return current
            center = (cell.bbox[0] + cell.bbox[2]) / 2
            hits = [col for col, lane in enumerate(lanes) if lane[0] - 1 <= center <= lane[2] + 1]
            if len(hits) != 1 or hits[0] in {target for (row, _), target in columns.items() if row == cell.row_idx}:
                return current
            columns[(cell.row_idx, cell.col_idx)] = hits[0]
        rows = []
        for row_idx, row in enumerate(current.rows):
            aligned = [None] * width
            for col, value in enumerate(row):
                target = columns.get((row_idx, col))
                if target is not None:
                    aligned[target] = value
                elif col >= prefix + difference:
                    aligned[col - difference] = value
            rows.append(aligned)
        cells = [replace(cell, col_idx=columns[(cell.row_idx, cell.col_idx)],
                         origin_fragment_uid=cell.origin_fragment_uid or current.fragment_uid,
                         origin_row_idx=cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx,
                         origin_col_idx=cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx)
                 for cell in current.cells if (cell.row_idx, cell.col_idx) in columns]
        return replace(current, rows=rows, cells=cells)

    def _align_native_continuation_columns(self, previous: ParsedTable, current: ParsedTable) -> ParsedTable:
        """Expand only the dimension columns when the money grid is unchanged.

        Native PDFs can draw separate 类/款/项 header cells on page one and one
        code column on continuation pages. Align literal code/name columns to
        the first header, checking every monetary boundary before moving any
        cell. Original column, row, page, bbox and raw text remain recoverable.
        """
        if (current.page_number - self._last_page_number(previous) != 1
                or previous.table_code not in {"FIN_02_income", "FIN_03_expenditure", "FIN_05_general_public_expenditure", "FIN_08_gov_fund", "FIN_09_state_capital"}
                or not current.table_code.startswith("UNKNOWN_")
                or previous.extraction_method not in {"lines", "lines_strict"}
                or current.extraction_method not in {"lines", "lines_strict"}
                or self._unit_hints_conflict(previous, current)
                or self._fragment_titles_conflict(previous, current)):
            return current
        widths = [{len(row) for row in table.rows} for table in (previous, current)]
        if any(len(values) != 1 for values in widths):
            return current
        reference_width, source_width = next(iter(widths[0])), next(iter(widths[1]))
        difference = reference_width - source_width
        if difference not in {1, 2}:
            return current
        from src.services.fiscal_column_identity import canonical_column_mappings

        mappings = canonical_column_mappings(previous, NINE_TABLE_RULES[previous.table_code])
        dimensions = {col for m in mappings for col in m.get("source_label_cols", [])}
        if not dimensions:
            return current
        reference_prefix = max(dimensions) + 1
        source_prefix = reference_prefix - difference
        if source_prefix != 2 or reference_prefix < 3:
            return current
        for row_idx, row in enumerate(current.rows):
            if self._is_full_width_note_row(current, row_idx):
                continue
            # 窄编码列里的 7 位编码常在格内折行（"21011\n00"），折行不改变编码本身。
            code = re.sub(r"\s+", "", str(row[0] or ""))
            if code and not (code.isdigit() or code in {"合计", "总计"}):
                return current
            if str(row[1] or "").strip() and self._parse_numeric(str(row[1])) is not None:
                return current

        def bounds(table, col):
            boxes = [c.bbox for c in table.cells if c.col_idx == col and c.bbox]
            if not boxes or table.bbox[2] <= table.bbox[0]:
                return None
            box = min(boxes, key=lambda b: b[2] - b[0])
            span = table.bbox[2] - table.bbox[0]
            return ((box[0] - table.bbox[0]) / span, (box[2] - table.bbox[0]) / span)

        for target_col in range(reference_prefix, reference_width):
            target, source = bounds(previous, target_col), bounds(current, target_col - difference)
            if target is None or source is None or any(abs(a - b) > .002 for a, b in zip(target, source)):
                return current
            # 最窄格匹配还不够：逐个金额格及其原字符都必须落在同一物理车道。
            for cell in current.cells:
                if cell.col_idx != target_col - difference or cell.numeric_value is None:
                    continue
                if not cell.bbox or not cell.native_text_sources:
                    return current
                span = current.bbox[2] - current.bbox[0]
                actual = ((cell.bbox[0] - current.bbox[0]) / span,
                          (cell.bbox[2] - current.bbox[0]) / span)
                if any(abs(a - b) > .002 for a, b in zip(target, actual, strict=True)):
                    return current
                if any(not proof.get("bbox") or proof["bbox"][0] < cell.bbox[0] - .5
                       or proof["bbox"][2] > cell.bbox[2] + .5 for proof in cell.native_text_sources):
                    return current
        columns = {0: 0, 1: reference_prefix - 1,
                   **{col: col + difference for col in range(source_prefix, source_width)}}
        rows = []
        for row in current.rows:
            aligned = [None] * reference_width
            for col, value in enumerate(row):
                aligned[columns[col]] = value
            rows.append(aligned)
        cells = [replace(cell, col_idx=columns[cell.col_idx],
                         origin_fragment_uid=cell.origin_fragment_uid or current.fragment_uid,
                         origin_row_idx=cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx,
                         origin_col_idx=cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx)
                 for cell in current.cells]
        return replace(current, rows=rows, cells=cells)

    def _align_native_total_footer(self, previous: ParsedTable, current: ParsedTable) -> ParsedTable:
        if (previous.table_code != "FIN_06_basic_expenditure"
                or current.page_number - self._last_page_number(previous) != 1
                or current.extraction_method not in {"lines", "lines_strict", "native_footer"}
                or len(current.rows) != 1 or len(current.rows[0]) != 4
                or normalize_text(str(current.rows[0][0] or "")) != "人员经费合计"
                or normalize_text(str(current.rows[0][2] or "")) != "公用经费合计"
                or any(self._parse_numeric(str(current.rows[0][col] or "")) is None for col in (1, 3))
                or self._unit_hints_conflict(previous, current)):
            return self._align_native_total_row(previous, current)
        from src.services.fiscal_column_identity import canonical_column_mappings

        mappings = sorted(canonical_column_mappings(previous, NINE_TABLE_RULES[previous.table_code]),
                          key=lambda m: m["source_col_idx"])
        if len(mappings) != 2 or not all(m.get("source_label_cols") for m in mappings):
            return current
        columns = {0: min(mappings[0]["source_label_cols"]), 1: mappings[0]["source_col_idx"],
                   2: min(mappings[1]["source_label_cols"]), 3: mappings[1]["source_col_idx"]}
        width = max(len(row) for row in previous.rows)
        row = [None] * width
        cells = []
        for cell in current.cells:
            row[columns[cell.col_idx]] = cell.raw_text
            cells.append(replace(cell, col_idx=columns[cell.col_idx],
                                 origin_fragment_uid=current.fragment_uid,
                                 origin_row_idx=cell.row_idx, origin_col_idx=cell.col_idx))
        aligned = replace(current, rows=[row], cells=cells)
        return aligned if self._native_continuation_columns_match(previous, aligned) else current

    @staticmethod
    def _is_full_width_note_row(table: ParsedTable, row_idx: int) -> bool:
        """原页末尾横跨全表的「注：」格：不是科目行，也不参与列宽证明。

        只豁免唯一有文字且物理矩形覆盖全表的注释，不能跳过异常业务行。
        """
        nonempty = [c for c in table.cells if c.row_idx == row_idx and c.raw_text.strip()]
        return (len(nonempty) == 1 and bool(re.match(r"^注[:：]", normalize_text(nonempty[0].raw_text)))
                and nonempty[0].bbox is not None and nonempty[0].numeric_value is None
                and abs(nonempty[0].bbox[0] - table.bbox[0]) <= .5
                and abs(nonempty[0].bbox[2] - table.bbox[2]) <= .5)

    def _align_native_total_row(self, previous: ParsedTable, current: ParsedTable) -> ParsedTable:
        """续页上单独一行的「合计」脚注：标签格横跨维度列，只剩 1+N 格。

        不对齐就以窄行并入，整表被判「跨页列宽漂移」而全部降为低置信度。
        每个金额格必须与前表对应金额列的物理左右边界一致才移动；原页、
        原行列、bbox 与字面值都保留。合计行之后只允许跟横跨全表的「注：」行。
        """
        rule = NINE_TABLE_RULES.get(previous.table_code)
        if (rule is None
                or current.page_number - self._last_page_number(previous) != 1
                or previous.extraction_method not in {"lines", "lines_strict"}
                or current.extraction_method not in {"lines", "lines_strict"}
                or not current.rows
                or not all(self._is_full_width_note_row(current, row_idx) for row_idx in range(1, len(current.rows)))
                or normalize_text(str(current.rows[0][0] or "")) not in {"合计", "总计"}
                or self._unit_hints_conflict(previous, current)):
            return current
        width = max((len(row) for row in previous.rows), default=0)
        if len(current.rows[0]) >= width:
            return current
        from src.services.fiscal_column_identity import canonical_column_mappings

        mappings = sorted(canonical_column_mappings(previous, rule), key=lambda m: m["source_col_idx"])
        amounts = sorted((cell for cell in current.cells if cell.col_idx > 0 and cell.row_idx == 0),
                         key=lambda cell: cell.col_idx)
        if not mappings or len(amounts) != len(mappings):
            return current
        columns = {0: 0}
        for cell, mapping in zip(amounts, mappings):
            if cell.raw_text.strip() and self._parse_numeric(cell.raw_text) is None:
                return current
            boxes = [original.bbox for original in previous.cells
                     if original.col_idx == mapping["source_col_idx"] and original.bbox
                     and original.page_number == previous.page_number]
            if not cell.bbox or not boxes:
                return current
            box = min(boxes, key=lambda value: value[2] - value[0])
            if abs(box[0] - cell.bbox[0]) > 1.0 or abs(box[2] - cell.bbox[2]) > 1.0:
                return current
            columns[cell.col_idx] = mapping["source_col_idx"]
        if set(columns) != {cell.col_idx for cell in current.cells if cell.row_idx == 0}:
            return current
        rows = [[None] * width for _ in current.rows]
        cells = []
        for cell in current.cells:
            column = columns[cell.col_idx] if cell.row_idx == 0 else cell.col_idx
            rows[cell.row_idx][column] = cell.raw_text
            cells.append(replace(cell, col_idx=column,
                                 origin_fragment_uid=cell.origin_fragment_uid or current.fragment_uid,
                                 origin_row_idx=cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx,
                                 origin_col_idx=cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx))
        return replace(current, rows=rows, cells=cells)

    #: 续表标记：表题正好是"续表"类短语的视为延续，不参与表题差异判定
    _CONTINUATION_TITLE_PATTERN = re.compile(r"^续?表?\s*(续)?[)）]?$|^\(?续\)?$|续表")

    def _recognized_table_alias(self, title: str) -> Optional[str]:
        """标题中识别出的规范表名（九表别名）；识别不到返回 None（视为噪声标题）。

        续页顶部常被 _extract_title 抓到残缺文本（如"项目 一般公共预算支出"），
        不能因噪声标题与主表题不同就拒绝续页合并。
        """
        normalized = normalize_text(title or "")
        if not normalized:
            return None
        from src.services.fiscal_table_rules import NINE_TABLE_RULES

        best: Optional[str] = None
        for rule in NINE_TABLE_RULES.values():
            for alias in rule.aliases:
                normalized_alias = normalize_text(alias)
                if normalized_alias and normalized_alias in normalized:
                    if best is None or len(normalized_alias) > len(best):
                        best = normalized_alias
        return best

    def _fragment_titles_conflict(self, previous: ParsedTable, current: ParsedTable) -> bool:
        """两片段表题是否指向不同实例（AC-02：主体/年度/标题不同）。

        - 续表标记只表示“可能延续”，不能盖过明示的主体、年度、表种或单位冲突；
          纯“续表”噪声标题在硬身份检查之后才按可延续处理；
        - 双方都是可识别表名但指向不同表种 → 冲突；
        - 双方同为一种表名且标题年度集合不相交 → 冲突
          （甲局2024收入表 vs 乙局2025收入表）；
        - 双方都不是规范表名（如自定义标题）时，去掉年度后归一化文本
          仍不同 → 冲突。
        """
        prev_title = (previous.title or "").strip()
        cur_title = (current.title or "").strip()
        if not prev_title or not cur_title:
            return False
        prev_alias = self._recognized_table_alias(prev_title)
        cur_alias = self._recognized_table_alias(cur_title)
        if prev_alias and cur_alias and prev_alias != cur_alias:
            return True
        prev_years = set(_TITLE_YEAR_RE.findall(prev_title))
        cur_years = set(_TITLE_YEAR_RE.findall(cur_title))
        if prev_years and cur_years and not (prev_years & cur_years):
            return True
        prev_orgs = set(_TITLE_ORG_RE.findall(prev_title))
        cur_orgs = set(_TITLE_ORG_RE.findall(cur_title))
        if prev_orgs and cur_orgs and not (prev_orgs & cur_orgs):
            return True
        if self._unit_hints_conflict(previous, current):
            return True

        prev_marked_continuation = bool(self._CONTINUATION_TITLE_PATTERN.search(prev_title))
        cur_marked_continuation = bool(self._CONTINUATION_TITLE_PATTERN.search(cur_title))
        if (
            prev_alias
            and cur_alias
            and prev_alias == cur_alias
            and normalize_text(prev_title) != normalize_text(cur_title)
            and not prev_marked_continuation
            and not cur_marked_continuation
            and self._headers_look_repeated(previous, current)
        ):
            # Same table kind with distinct complete titles and repeated headers
            # is a new instance. Identical titles on an unfinished table remain
            # eligible for continuation; a completed table is stopped above.
            return True

        # 显式身份边界已经排除；“续表”仅在缺少硬冲突时提供延续提示。
        if prev_marked_continuation or cur_marked_continuation:
            return False
        if prev_alias and cur_alias:
            # RC-02：同表种同年但主体不同（甲局 vs 乙局）是两张表，
            # 不得合并成一个逻辑实例。
            return False
        if (prev_alias or cur_alias) and not (prev_alias and cur_alias):
            # 一方是规范表名、另一方是噪声标题：按延续处理
            return False
        # 双方都不是规范表名：只在**主体/年度证据**冲突时才判不同实例。
        # 页标伪标题（"第一页/第二页"）与普通差异不构成身份冲突——
        # 否则真实续页会被拆散（既有 chained-continuation 契约）。
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

    #: 单位口径提取：从 unit_hint/表题中识别"单位：万元/元/亿元"。
    _UNIT_HINT_VALUE_RE = re.compile(r"(亿元|万元|千元|元)")

    def _unit_hints_conflict(self, previous: ParsedTable, current: ParsedTable) -> bool:
        """两片段的单位口径是否冲突（都是显式单位且不同时为冲突）。

        续页通常不带单位行，只有一方有单位时视为延续；两方都有且不同
        （万元 vs 元）才是冲突——不得自动换算，不得合并。
        """
        prev_unit = self._explicit_unit_hint(previous)
        cur_unit = self._explicit_unit_hint(current)
        if prev_unit and cur_unit:
            return prev_unit != cur_unit
        return False

    def _explicit_unit_hint(self, table: ParsedTable) -> Optional[str]:
        candidate = str(getattr(table, "unit_hint", None) or "").strip()
        match = self._UNIT_HINT_VALUE_RE.search(candidate)
        if match:
            return match.group(1)
        return None

    def _append_table(self, previous: ParsedTable, current: ParsedTable) -> ParsedTable:
        skip_rows = self._repeated_header_rows(previous, current)
        native_columns_bound = self._native_continuation_columns_match(previous, current)
        base_row_count = len(previous.rows)
        appended_rows = previous.rows + current.rows[skip_rows:]
        appended_cells: List[ParsedCell] = []
        from src.services.fiscal_column_identity import canonical_column_mappings

        rule = NINE_TABLE_RULES.get(previous.table_code)
        monetary_cols = {item['source_col_idx'] for item in canonical_column_mappings(previous, rule)} if rule else set()
        continuation_data_headers = {
            row_idx for row_idx, row in enumerate(current.rows[:current.header_row_count])
            if not is_index_header(row)
            and any(col < len(row) and self._parse_numeric(str(row[col] or '')) is not None for col in monetary_cols)
        }
        prev_default_origin = (
            previous.source_fragment_uids[0]
            if len(previous.source_fragment_uids or []) == 1
            else previous.fragment_uid
        )
        for cell in previous.cells:
            if cell.origin_fragment_uid is None:
                # 首段自身也可能已被并过一轮；无 origin 的格子属于当前视图自身
                appended_cells.append(
                    replace(
                        cell,
                        origin_fragment_uid=prev_default_origin,
                        origin_row_idx=cell.row_idx,
                    )
                )
            else:
                appended_cells.append(replace(cell))
        for cell in current.cells:
            if cell.row_idx < skip_rows:
                continue
            appended_cells.append(
                replace(
                    cell,
                    row_idx=base_row_count + (cell.row_idx - skip_rows),
                    is_header=(is_index_header(current.rows[cell.row_idx]) if native_columns_bound
                               else cell.is_header and cell.row_idx not in continuation_data_headers),
                    # The unknown continuation's score measures missing title/
                    # headers, not native glyph accuracy. Only an exact physical
                    # grid match permits inheriting the preceding table identity.
                    confidence=(max(cell.confidence, min(previous.confidence, .9))
                                if native_columns_bound and cell.raw_text.strip()
                                else cell.confidence),
                    unit_hint=cell.unit_hint or (previous.unit_hint if native_columns_bound else None),
                    # RC-02：合并视图的格子保留来源物理片段与原始行号，
                    # 证据格 ID 由此可以回到原页原格。
                    origin_fragment_uid=(
                        cell.origin_fragment_uid or current.fragment_uid
                    ),
                    origin_row_idx=(
                        cell.origin_row_idx
                        if cell.origin_row_idx is not None
                        else cell.row_idx
                    ),
                )
            )

        member_uids = sorted(
            {
                *(previous.source_fragment_uids or [previous.fragment_uid]),
                *(current.source_fragment_uids or [current.fragment_uid]),
            }
        )
        return ParsedTable(
            table_code=previous.table_code,
            title=previous.title if self._score_title_line(previous.title, 0) >= self._score_title_line(current.title, 0) else current.title,
            page_number=previous.page_number,
            source_fragment_uids=member_uids,
            table_index=previous.table_index,
            bbox=previous.bbox,
            header_row_count=previous.header_row_count,
            confidence=max(previous.confidence, current.confidence),
            extraction_method=previous.extraction_method,
            unit_hint=previous.unit_hint or current.unit_hint,
            rows=appended_rows,
            cells=appended_cells,
            # RC-10：合并视图 UID 由成员 UID 集合派生——成员 UID 已稳定，
            # 合并 UID 不再随解析次数漂移。
            fragment_uid=_stable_fragment_uid("merged", *member_uids),
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
        semantic_bonus = 0.0
        rule = NINE_TABLE_RULES.get(table.table_code)
        if rule is not None:
            from src.services.fiscal_column_identity import canonical_column_mappings

            monetary_cols = {item['source_col_idx'] for item in canonical_column_mappings(table, rule)}
            if any(cell.col_idx in monetary_cols and not cell.is_header and cell.numeric_value is not None for cell in table.cells):
                semantic_bonus = .5 + (.1 if table.extraction_method in {'lines', 'lines_strict'} else 0)
        return table.confidence + semantic_bonus + min(area / 1_000_000, 0.2)

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

    def _is_meaningful_table(self, rows: Sequence[Sequence[str]], *, native_grid: bool = False) -> bool:
        if native_grid:
            # 原生闭合网格就是原件结构。空表、稀疏名录、只有一行的续表
            # 均须完整保留；业务分类不确定与“丢掉原格”是两件事。
            return bool(rows and max(len(row) for row in rows) >= 2)
        if len(rows) < 2:
            # 单行也是原表数据：续页只有一条科目或表尾时不能按行数丢弃。
            # 无法确定业务身份的单行保留为 UNKNOWN，不臆造对应表种。
            return (len(rows) == 1 and len(rows[0]) >= 2
                    and sum(bool(str(value or "").strip()) for value in rows[0]) >= 2)
        width = max((len(row) for row in rows), default=0)
        if width < 2:
            return False
        non_empty = sum(1 for row in rows for value in row if str(value or "").strip())
        return non_empty >= max(4, len(rows))

    def _parse_numeric(self, text: str) -> Optional[float]:
        from src.engine.fiscal_lexeme import lex_numeric
        lexeme = lex_numeric(text)
        if lexeme.kind not in {'number', 'amount'} or lexeme.value is None:
            return None
        # numeric_value仅兼容旧几何代码；财政金额始终由raw_text转Decimal。
        return float(lexeme.value)


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
