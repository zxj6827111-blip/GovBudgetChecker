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
from src.services.pdf_parser import _stable_fragment_uid

logger = logging.getLogger(__name__)

#: 表头匹配置信度门槛（detect_table_code 归一化分数）
_PROMOTE_HEADER_CONFIDENCE = 0.75
#: 数据行金额格可解析率门槛（关键数字复验）
_PROMOTE_NUMERIC_RATE = 0.5
#: 晋升表置信度（RC-07：低于 0.8，物化事实全部 low_confidence——
#: 未经逐格识别置信度认证的 OCR 数据不进入确定性勾稽）
#: RC-07：表种上下文消歧——资金/口径关键词必须出现在该页 OCR 文本里，
#: 只凭通用"合计"表头不得唯一认证基金/国资等专项表。
_TABLE_CONTEXT_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "FIN_08_gov_fund": ("政府性基金", "基金预算"),
    "FIN_09_state_capital": ("国有资本",),
    "FIN_05_general_public_expenditure": ("一般公共预算",),
    "FIN_04_fiscal_grant_total": ("财政拨款",),
    "FIN_07_three_public": ("三公", "因公出国", "公务接待", "机关运行"),
}


def _page_context_supports_table(artifact: Any, page_number: int, table_code: str) -> bool:
    """该页 OCR 文本是否支持该表种身份（RC-07 上下文消歧）。

    - 表种未登记上下文关键词（如收支总表）→ 不拦截；
    - 登记了关键词但该页 OCR 文本完全不含 → 上下文不支持，拒绝晋升。
    """
    keywords = _TABLE_CONTEXT_KEYWORDS.get(table_code)
    if not keywords:
        return True
    page_text = "\n".join(
        str(getattr(block, "text", "") or "")
        for block in getattr(artifact, "text_blocks", []) or []
        if getattr(block, "page_number", None) == page_number
    )
    if not page_text:
        return False
    return any(token in page_text for token in keywords)


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
    return _ocr_number(text) is not None


def _ocr_number(text: str) -> Optional[float]:
    import math
    import re
    import unicodedata
    from decimal import Decimal

    value = unicodedata.normalize("NFKC", str(text or "").strip())
    value = "".join(value.split())
    for unit in ("万元", "亿元", "千元", "元"):
        if value.endswith(unit):
            value = value[: -len(unit)].strip()
            break
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?", value):
        return None
    number = float(Decimal(value.replace(",", "").replace("，", "")))
    return number if math.isfinite(number) else None


def _detect_table_identity(
    rows: List[List[str]],
) -> tuple[Optional[str], float, Dict[str, List[Dict[str, Any]]]]:
    """按表头行匹配九表规则，返回 (table_code, confidence, canonical mappings)。

    表头窗口按数据特征选择：若第 2 行已含数值单元格，说明只有 1 行表头，
    不得把数据行拼进表头（会把"合计"数据行拼成"项目合计"污染列身份）。
    """
    from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text

    if not rows:
        return None, 0.0, {}

    header_depth = 1
    while header_depth < min(len(rows), 3) and not any(
        _numeric_parseable(cell) for cell in rows[header_depth]
    ):
        header_depth += 1
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
    organization: Optional[str] = None,
    year: Optional[int] = None,
    report_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """把通过复验的 OCR 候选表晋升进统一解析对象（就地追加）。

    返回晋升摘要：``promoted_tables`` / ``rejected_tables``（含拒绝原因）/
    ``usable_for_rules``。晋升表与原生表共用同一事实物化路径（AC-01）。
    """
    summary: Dict[str, Any] = {
        "promoted_tables": [],
        "rejected_tables": [],
        "candidate_materialized": False,
        "usable_for_rules": False,
        "operand_count": 0,
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
        # RC-07：上下文消歧——通用"名称/合计"表头不得唯一认证专项表种；
        # 该页 OCR 文本必须出现该表种的资金/口径关键词。
        if not _page_context_supports_table(artifact, candidate.page_number, table_code):
            summary["rejected_tables"].append(
                {
                    "candidate_uid": candidate.candidate_uid,
                    "reason": "table_identity_context_missing",
                    "table_code": table_code,
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
        from src.services.ocr_qualification import qualification

        qualified = qualification(
            candidate, artifact, organization=organization, year=year, report_kind=report_kind
        )
        unit_hint = qualified["unit"] or _detect_unit_hint_from_blocks(
            artifact, candidate.page_number
        )
        original_cells = {
            (cell.get("row_idx"), cell.get("col_idx")): cell for cell in candidate.cells
        }
        cells: List[ParsedCell] = []
        for row_idx, row in enumerate(rows):
            for col_idx, raw in enumerate(row):
                text = str(raw or "").strip()
                numeric = _ocr_number(text)
                cells.append(
                    ParsedCell(
                        row_idx=row_idx,
                        col_idx=col_idx,
                        raw_text=text,
                        normalized_text=text,
                        numeric_value=numeric,
                        page_number=candidate.page_number,
                        bbox=tuple(original_cells[(row_idx, col_idx)]["page_bbox"])
                        if original_cells.get((row_idx, col_idx), {}).get("page_bbox")
                        else None,
                        is_header=(row_idx == 0),
                        confidence=float(
                            original_cells.get((row_idx, col_idx), {}).get("score") or 0
                        ),
                        unit_hint=unit_hint,
                        extraction_method="ocr",
                        row_span=int(
                            original_cells.get((row_idx, col_idx), {}).get("row_span") or 1
                        ),
                        col_span=int(
                            original_cells.get((row_idx, col_idx), {}).get("col_span") or 1
                        ),
                        ocr_provenance={
                            "qualification": qualified,
                            "source": candidate.provenance,
                            "cell": original_cells.get((row_idx, col_idx), {}),
                        },
                    )
                )
        fragment = ParsedTable(
            table_code=table_code,
            title=f"OCR候选表:{table_code}",
            page_number=candidate.page_number,
            table_index=len(getattr(artifact, "table_fragments", []) or []),
            bbox=candidate.page_bbox or (0, 0, 0, 0),
            header_row_count=next(
                (
                    index
                    for index, row in enumerate(rows)
                    if any(_numeric_parseable(cell) for cell in row)
                ),
                1,
            ),
            confidence=qualified["minimum_score"] if qualified["status"] == "qualified" else 0,
            extraction_method="ocr",
            unit_hint=unit_hint,
            rows=rows,
            cells=cells,
            # RC-10：OCR 片段 UID 同样内容派生，重解析后校正目标不漂移
            fragment_uid=_stable_fragment_uid(
                "ocr",
                candidate.page_number,
                table_code,
                rows=rows,
            ),
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
                confidence=fragment.confidence,
                org_name=organization,
                fiscal_year=year,
                column_mappings=[
                    {**mapping, "confidence": fragment.confidence} for mapping in mappings
                ],
            )
        )
        promoted_uids.add(candidate.candidate_uid)
        summary["promoted_tables"].append(
            {
                "candidate_uid": candidate.candidate_uid,
                "table_code": table_code,
                "confidence": round(confidence, 4),
                "fragment_uid": fragment.fragment_uid,
                "operand_qualification": qualified,
            }
        )

    if promoted_uids:
        # 候选物化与资格分开；完整逐格来源及复验通过才可作操作数。
        summary["candidate_materialized"] = True
        summary["usable_for_rules"] = any(
            item["operand_qualification"]["status"] == "qualified"
            for item in summary["promoted_tables"]
        )
        summary["operand_count"] = 0
        logger.info(
            "OCR candidates promoted: %s",
            sorted(promoted_uids),
            extra={"stage": "ocr_fact_promotion"},
        )
    return summary
