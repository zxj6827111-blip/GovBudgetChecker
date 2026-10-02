"""OCR 候选表 → 可检查事实的晋升门（ZC-02 / AC-07）。

纪律（验收 AC-07 再次验收条件）：

- 晋升必须通过三道复验：**列身份**（表头与九表规则的 measure 别名匹配）、
  **关键数字**（数据行金额格可解析率）、**置信度**（表头匹配置信度门槛）；
- 通过晋升的 OCR 表进入统一解析对象（fragment + 逻辑实例），由与原生表
  **同一个**事实物化实现生成 FiscalFact——证据 source_method="ocr"、
  verification_status 保持 "unverified"，绝不因"通过晋升"而被标成 verified；
- 未通过晋升的候选保持 ``usable_for_rules=false``，继续待人工复核。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.schemas.parse_artifact import (
    LogicalTableInstance,
    OcrTableCandidate,
    ParsedCell,
    ParsedTable,
)

logger = logging.getLogger(__name__)

#: 表头匹配置信度门槛（detect_table_code 归一化分数）
_PROMOTE_HEADER_CONFIDENCE = 0.75
#: 数据行金额格可解析率门槛（关键数字复验）
_PROMOTE_NUMERIC_RATE = 0.5
#: 晋升表置信度（低于该值的事实会被识别为 low_confidence，不能作确定操作数）
_PROMOTED_CONFIDENCE = 0.85


#: OCR 页面上的单位声明（与原生解析同一口径，不猜口径）
_UNIT_HINT_PATTERN = None


def _unit_hint_pattern():
    global _UNIT_HINT_PATTERN
    if _UNIT_HINT_PATTERN is None:
        import re

        _UNIT_HINT_PATTERN = re.compile(r"单位[:：]\s*(万元|元|亿元|千元)")
    return _UNIT_HINT_PATTERN


def _detect_unit_hint_from_blocks(artifact: Any, page_number: int) -> Optional[str]:
    """从该页的 OCR 文本块中探测"单位：万元"类声明；探测不到不猜。"""
    pattern = _unit_hint_pattern()
    for block in getattr(artifact, "text_blocks", []) or []:
        if (
            getattr(block, "page_number", None) == page_number
            and str(getattr(block, "extraction_method", "") or "") == "ocr"
        ):
            match = pattern.search(str(block.text or ""))
            if match:
                return match.group(1)
    return None


def _numeric_parseable(text: str) -> bool:
    value = str(text or "").strip().replace(",", "").replace("，", "")
    for unit_token in ("万元", "亿元", "千元", "点", "元"):
        if value.endswith(unit_token):
            value = value[: -len(unit_token)]
    if not value:
        return False
    try:
        float(value)
        return True
    except ValueError:
        return False


def _detect_table_identity(rows: List[List[str]]) -> tuple[Optional[str], float, Dict[str, List[Dict[str, Any]]]]:
    """按表头行匹配九表规则，返回 (table_code, confidence, canonical mappings)。

    表头窗口按数据特征选择：若第 2 行已含数值单元格，说明只有 1 行表头，
    不得把数据行拼进表头（会把"合计"数据行拼成"项目合计"污染列身份）。
    """
    from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text

    if not rows:
        return None, 0.0, {}

    header_depth = 1
    if len(rows) > 1 and not any(_numeric_parseable(cell) for cell in rows[1]):
        header_depth = 2
    header_rows = [row for row in rows[:header_depth] if row]
    if not header_rows:
        return None, 0.0, {}
    col_count = max(len(row) for row in header_rows)
    headers = [
        "".join(str(row[col]) for row in header_rows if col < len(row)).strip()
        for col in range(col_count)
    ]
    normalized_headers = [normalize_text(header) for header in headers]
    # FIN_07（三公表）的 required_headers（合计/因公出国(境)费…）是**行标签**
    # 而非列头——身份匹配同时考察列头与首列数据行标签。
    row_labels = [normalize_text(str(row[0] or "")) for row in rows[1:] if row]

    best_code: Optional[str] = None
    best_score = 0.0
    best_mappings: List[Dict[str, Any]] = []
    for code, rule in NINE_TABLE_RULES.items():
        required = [normalize_text(token) for token in (rule.required_headers or [])]
        if not required:
            continue

        def _hit(token: str) -> bool:
            if any(token == header or token in header for header in normalized_headers):
                return True
            return any(token and token in label for label in row_labels)

        hit = sum(1 for token in required if _hit(token))
        score = hit / len(required)
        if score <= best_score and best_code is not None:
            continue
        mappings: List[Dict[str, Any]] = []
        for measure, aliases in (rule.measure_aliases or {}).items():
            # 精确匹配优先，避免"合计"命中"项目合计"这类拼接列
            best_col: Optional[int] = None
            best_exact = False
            for col, header in enumerate(headers):
                normalized = normalized_headers[col]
                exact = any(alias and normalize_text(str(alias)) == normalized for alias in aliases)
                substring = any(alias and str(alias) in header for alias in aliases)
                if exact and (best_col is None or not best_exact):
                    best_col, best_exact = col, True
                elif substring and best_col is None:
                    best_col, best_exact = col, False
            if best_col is not None:
                mappings.append(
                    {
                        "source_col_idx": best_col,
                        "canonical_measure": measure,
                        "source_col_name": headers[best_col],
                        "confidence": round(score, 4),
                    }
                )
        best_code, best_score, best_mappings = code, score, mappings
    return best_code, best_score, best_mappings


def promote_ocr_candidates(
    artifact: Any,
    *,
    file_hash: str = "",
) -> Dict[str, Any]:
    """把通过复验的 OCR 候选表晋升进统一解析对象（就地追加）。

    返回晋升摘要：``promoted_tables`` / ``rejected_tables``（含拒绝原因）/
    ``usable_for_rules``。晋升表与原生表共用同一事实物化路径（AC-01）。
    """
    summary: Dict[str, Any] = {
        "promoted_tables": [],
        "rejected_tables": [],
        "usable_for_rules": False,
    }
    if artifact is None:
        return summary

    candidates: List[OcrTableCandidate] = list(getattr(artifact, "ocr_table_candidates", []) or [])
    promoted_uids: set[str] = set()

    for candidate in candidates:
        rows = [list(row) for row in candidate.rows or []]
        if len(rows) < 2:
            summary["rejected_tables"].append(
                {"candidate_uid": candidate.candidate_uid, "reason": "too_few_rows"}
            )
            continue

        table_code, confidence, mappings = _detect_table_identity(rows)
        if not table_code or confidence < _PROMOTE_HEADER_CONFIDENCE:
            summary["rejected_tables"].append(
                {
                    "candidate_uid": candidate.candidate_uid,
                    "reason": "column_identity_below_threshold",
                    "table_code": table_code,
                    "confidence": round(confidence, 4),
                }
            )
            continue
        if not mappings:
            summary["rejected_tables"].append(
                {"candidate_uid": candidate.candidate_uid, "reason": "no_measure_column_mapping"}
            )
            continue

        # 关键数字复验：数据行的金额列必须可解析（OCR 乱码会在这里被拦下）
        measure_cols = {int(mapping["source_col_idx"]) for mapping in mappings}
        data_rows = rows[1:]
        numeric_cells = 0
        total_measure_cells = 0
        for row in data_rows:
            for col in measure_cols:
                if col < len(row) and str(row[col] or "").strip():
                    total_measure_cells += 1
                    if _numeric_parseable(row[col]):
                        numeric_cells += 1
        if total_measure_cells == 0 or numeric_cells / total_measure_cells < _PROMOTE_NUMERIC_RATE:
            summary["rejected_tables"].append(
                {
                    "candidate_uid": candidate.candidate_uid,
                    "reason": "key_number_recheck_failed",
                    "numeric_rate": round(numeric_cells / max(total_measure_cells, 1), 4),
                }
            )
            continue

        # 构建物理片段（与原生表同构）并晋升进 artifact
        unit_hint = _detect_unit_hint_from_blocks(artifact, candidate.page_number)
        cells: List[ParsedCell] = []
        for row_idx, row in enumerate(rows):
            for col_idx, raw in enumerate(row):
                text = str(raw or "").strip()
                numeric = None
                if _numeric_parseable(text):
                    try:
                        numeric = float(text.replace(",", "").replace("，", ""))
                    except ValueError:
                        numeric = None
                cells.append(
                    ParsedCell(
                        row_idx=row_idx,
                        col_idx=col_idx,
                        raw_text=text,
                        normalized_text=text,
                        numeric_value=numeric,
                        page_number=candidate.page_number,
                        bbox=candidate.page_bbox,
                        is_header=(row_idx == 0),
                        confidence=round(confidence, 4),
                        unit_hint=unit_hint,
                        extraction_method="ocr",
                    )
                )
        fragment = ParsedTable(
            table_code=table_code,
            title=f"OCR候选表:{table_code}",
            page_number=candidate.page_number,
            table_index=len(getattr(artifact, "table_fragments", []) or []),
            bbox=candidate.page_bbox or (0, 0, 0, 0),
            header_row_count=1,
            confidence=_PROMOTED_CONFIDENCE,
            extraction_method="ocr",
            unit_hint=unit_hint,
            rows=rows,
            cells=cells,
        )
        artifact.table_fragments.append(fragment)
        artifact.logical_tables.append(
            LogicalTableInstance(
                instance_uid=fragment.fragment_uid,
                table_code=table_code,
                title=fragment.title,
                fragment_uids=[fragment.fragment_uid],
                page_start=candidate.page_number,
                page_end=candidate.page_number,
                row_count=len(rows),
                confidence=_PROMOTED_CONFIDENCE,
                column_mappings=mappings,
            )
        )
        promoted_uids.add(candidate.candidate_uid)
        summary["promoted_tables"].append(
            {
                "candidate_uid": candidate.candidate_uid,
                "table_code": table_code,
                "confidence": round(confidence, 4),
                "fragment_uid": fragment.fragment_uid,
            }
        )

    if promoted_uids:
        summary["usable_for_rules"] = True
        logger.info(
            "OCR candidates promoted: %s",
            sorted(promoted_uids),
            extra={"stage": "ocr_fact_promotion"},
        )
    return summary
