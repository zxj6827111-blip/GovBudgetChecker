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
from copy import deepcopy
from typing import Any, Dict, List, Optional

from src.schemas.parse_artifact import (
    LogicalTableInstance,
    OcrTableCandidate,
    ParsedCell,
    ParsedTable,
)
from src.services.pdf_parser import _stable_fragment_uid
from src.services.fiscal_row_roles import is_index_header
from src.services.fiscal_table_rules import normalize_text

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


def _source_title(artifact,candidate,table_code):
    from src.services.fiscal_table_rules import detect_table_code
    from src.services.ocr_qualification import bound_ocr_block
    page_box=candidate.page_bbox
    if not page_box:return None
    blocks=[b for b in artifact.text_blocks if b.page_number==candidate.page_number and b.bbox
        and b.bbox[1]<page_box[1] and b.bbox[2]>page_box[0]and b.bbox[0]<page_box[2]
        and (b.extraction_method=='native_text'or b.extraction_method=='ocr'
             and(b.confidence or 0)>=.98 and bound_ocr_block(b,artifact))]
    blocks.sort(key=lambda b:(b.bbox[1],b.bbox[0]));matches=[]
    for start in range(len(blocks)):
        group=[]
        for block in blocks[start:start+3]:
            if group and block.bbox[1]-group[-1].bbox[3]>24:break
            group.append(block);text=''.join(b.text for b in group)
            detected,score=detect_table_code(text,[])
            if detected==table_code and score>=.8:matches.append((max(b.bbox[3]for b in group),text))
    if not matches:return None
    closest=max(y for y,_ in matches)
    titles={text for y,text in matches if y==closest}
    # 同一标题可由单块或带相邻编制信息的组合得到；只选被其他匹配包含的最短原文。
    titles={t for t in titles if not any(other!=t and other in t for other in titles)}
    return next(iter(titles))if len(titles)==1 else None


def _numeric_parseable(text: str) -> bool:
    return _ocr_number(text) is not None


def _ocr_number(text: str) -> Optional[float]:
    import math
    from src.engine.fiscal_lexeme import lex_numeric
    value=lex_numeric(text)
    if value.kind not in {'number','amount'}:return None
    number = float(value.value)
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
    while header_depth < min(len(rows), 8) and not any(
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


_TOTAL_ROW_LABELS = {"合计", "总计", "收入总计", "支出总计"}


def _ocr_continues(parser: Any, previous: ParsedTable, current: ParsedTable) -> bool:
    """扫描件续页：紧邻下一页、同表种、列数一致、表题不冲突，且前段还没到合计行。"""
    if (current.page_number - parser._last_page_number(previous) != 1
            or current.table_code != previous.table_code
            or {len(row) for row in previous.rows} != {len(row) for row in current.rows}):
        return False
    last = next((row for row in reversed(previous.rows) if any(str(v or "").strip() for v in row)), [])
    if any(normalize_text(str(v or "")) in _TOTAL_ROW_LABELS for v in last):
        return False
    titled = [t for t in (previous, current) if not t.title.startswith("OCR候选表:")]
    return len(titled) < 2 or not parser._fragment_titles_conflict(previous, current)


def _join_ocr_continuations(artifact: Any, fragments: List[ParsedTable]) -> None:
    """同一张表在扫描件里跨页时，按原生续表的拼接方式接成一个逻辑表。

    每页一个逻辑实例会让规则看到两张同种表而整体放弃核对。物理片段与来源索引
    保持逐页原样，只替换逻辑视图；拼接沿用原生续表的去重复表头与原格回溯。
    """
    from src.services.pdf_parser import PDFParser

    parser = PDFParser(None)
    groups: List[List[ParsedTable]] = []
    for fragment in sorted(fragments, key=lambda item: (item.page_number, item.bbox[1])):
        if groups and _ocr_continues(parser, groups[-1][-1], fragment):
            groups[-1].append(fragment)
        else:
            groups.append([fragment])
    for members in (group for group in groups if len(group) > 1):
        merged = members[0]
        for member in members[1:]:
            merged = parser._append_table(merged, member)
        uids = {member.fragment_uid for member in members}
        first = next(t for t in artifact.logical_tables if t.instance_uid == members[0].fragment_uid)
        index = next(i for i, t in enumerate(artifact.table_fragments) if t.fragment_uid in uids)
        artifact.table_fragments = [t for t in artifact.table_fragments if t.fragment_uid not in uids]
        artifact.table_fragments.insert(index, merged)
        artifact.logical_tables = [t for t in artifact.logical_tables if t.instance_uid not in uids]
        artifact.logical_tables.append(LogicalTableInstance(
            instance_uid=merged.fragment_uid, table_code=merged.table_code, title=merged.title,
            fragment_uids=[*merged.source_fragment_uids, merged.fragment_uid],
            page_start=members[0].page_number, page_end=members[-1].page_number,
            row_count=len(merged.rows), confidence=min(member.confidence for member in members),
            org_name=first.org_name, fiscal_year=first.fiscal_year, column_mappings=first.column_mappings))


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
        "fused_cells": [],
        "native_conflicts": [],
    }
    if artifact is None:
        return summary

    candidates: List[OcrTableCandidate] = list(getattr(artifact, "ocr_table_candidates", []) or [])
    promoted_uids: set[str] = set()
    promoted_fragments=[]
    from src.services.ocr_grid_reconstruction import GRID_PROTOCOL
    grid_candidates=[c for c in candidates if c.provenance.get('schema_version')==GRID_PROTOCOL]

    for candidate in candidates:
        if candidate.provenance.get('schema_version')!=GRID_PROTOCOL and candidate.page_bbox:
            area=(candidate.page_bbox[2]-candidate.page_bbox[0])*(candidate.page_bbox[3]-candidate.page_bbox[1])
            shadows=[g for g in grid_candidates if g.page_number==candidate.page_number and g.page_bbox
                and area>0 and max(0,min(g.page_bbox[2],candidate.page_bbox[2])-max(g.page_bbox[0],candidate.page_bbox[0]))
                *max(0,min(g.page_bbox[3],candidate.page_bbox[3])-max(g.page_bbox[1],candidate.page_bbox[1]))/area>=.95]
            if shadows:
                summary['rejected_tables'].append({'candidate_uid':candidate.candidate_uid,
                    'reason':'model_projection_of_independent_grid','grid_candidate_uids':[g.candidate_uid for g in shadows]})
                continue
        from src.services.table_reading_assessment import intersects, selected_tables
        overlaps_native = candidate.page_bbox and any(
            table.extraction_method != "ocr" and intersects(table.bbox, candidate.page_bbox)
            and any(cell.page_number == candidate.page_number for cell in table.cells)
            for table in selected_tables(artifact)
        )
        if overlaps_native and (candidate.mapping_status != 'mapped' or not candidate.cells):
            summary['rejected_tables'].append({'candidate_uid':candidate.candidate_uid,
                                               'reason':'native_table_overlap_requires_review'})
            continue
        # Work on one snapshot; qualification and materialisation consume the
        # same source-bound cell objects, including merged-cell geometry.
        candidate = deepcopy(candidate)
        from src.services.ocr_qualification import bound_table

        canonical, _ = bound_table(candidate, artifact)
        rows = [list(row) for row in candidate.rows or []]
        if len(rows) < 2:
            summary["rejected_tables"].append(
                {"candidate_uid": candidate.candidate_uid, "reason": "too_few_rows"}
            )
            continue

        # 合并表头只在身份视图传播，不修改原始 rows 或逐格字面。
        identity_rows=deepcopy(rows)
        first_data=next((i for i,row in enumerate(rows)if any(_numeric_parseable(c)for c in row)),1)
        for cell in candidate.cells:
            if cell.get('row_idx',0)>=first_data:continue
            for r in range(cell['row_idx'],min(first_data,cell['row_idx']+cell.get('row_span',1))):
                for c in range(cell['col_idx'],cell['col_idx']+cell.get('col_span',1)):
                    if r<len(identity_rows)and c<len(identity_rows[r])and not identity_rows[r][c]:
                        identity_rows[r][c]=cell.get('raw_text','')
        table_code, confidence, mappings = _detect_table_identity(identity_rows)
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

        if overlaps_native:
            from src.services.native_ocr_fusion import fuse_overlapping_cells
            fusion=fuse_overlapping_cells(artifact,candidate,table_code=table_code,mappings=mappings,
                organization=organization,year=year,report_kind=report_kind)
            summary['fused_cells'].extend(fusion['fused_cells'])
            summary['native_conflicts'].extend(fusion['conflicts'])
            summary['rejected_tables'].append({'candidate_uid':candidate.candidate_uid,
                'reason':'native_cells_fused'if fusion['fused_cells']else'native_table_overlap_requires_review',
                'cell_rejections':fusion['rejected']})
            continue

        # 构建物理片段（与原生表同构）并晋升进 artifact
        from src.services.ocr_qualification import page_binding_reasons, qualification

        qualified = qualification(
            candidate, artifact, organization=organization, year=year, report_kind=report_kind
        )
        identity_reasons = page_binding_reasons(candidate, artifact)
        if identity_reasons:
            summary["rejected_tables"].append({
                "candidate_uid": candidate.candidate_uid,
                "reason": "physical_page_identity_not_bound",
                "operand_qualification": qualified,
            })
            continue
        if canonical is not None:
            rows = canonical["rows"]
            source_cells = canonical["cells"]
        else:
            source_cells = candidate.cells
        # The physical page comes from the renderer/request identity. A mutable
        # candidate page must never relabel a saved source response.
        source_page = qualified.get("physical_page") or candidate.page_number
        header_row_count = next(
            (
                index for index, row in enumerate(rows)
                if not is_index_header(row) and any(_numeric_parseable(cell) for cell in row)
            ),
            1,
        )
        unit_hint = qualified["unit"] or _detect_unit_hint_from_blocks(
            artifact, candidate.page_number
        )
        original_cells = {(cell.get("row_idx"), cell.get("col_idx")): cell for cell in source_cells}
        cell_qualifications = {
            (cell["row_idx"], cell["col_idx"]): qualification(
                candidate, artifact, organization=organization, year=year,
                report_kind=report_kind, cell_position=(cell["row_idx"], cell["col_idx"]),
            )
            for cell in source_cells
            if cell.get("col_idx") in measure_cols and cell.get("row_idx", 0) >= header_row_count
            and not is_index_header(rows[cell["row_idx"]]) and _numeric_parseable(cell.get("raw_text", ""))
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
                        page_number=source_page,
                        bbox=tuple(original_cells[(row_idx, col_idx)]["page_bbox"])
                        if original_cells.get((row_idx, col_idx), {}).get("page_bbox")
                        else None,
                        is_header=row_idx < header_row_count or is_index_header(row),
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
                            "qualification": cell_qualifications.get((row_idx, col_idx), qualified),
                            "source": candidate.provenance,
                            "cell": original_cells.get((row_idx, col_idx), {}),
                        },
                    )
                )
        fragment = ParsedTable(
            table_code=table_code,
            title=_source_title(artifact,candidate,table_code)or f"OCR候选表:{table_code}",
            page_number=source_page,
            table_index=len(getattr(artifact, "table_fragments", []) or []),
            bbox=candidate.page_bbox or (0, 0, 0, 0),
            header_row_count=header_row_count,
            confidence=max([result["minimum_score"] for result in cell_qualifications.values() if result["status"] == "qualified"], default=qualified["minimum_score"] if qualified["status"] == "qualified" else 0),
            extraction_method="ocr",
            unit_hint=unit_hint,
            rows=rows,
            cells=cells,
            # RC-10：OCR 片段 UID 同样内容派生，重解析后校正目标不漂移
            fragment_uid=_stable_fragment_uid(
                "ocr",
                source_page,
                table_code,
                rows=rows,
            ),
        )
        artifact.table_fragments.append(fragment)
        promoted_fragments.append(fragment)
        # 混合页也须保存 OCR 的物理来源，不能只保存合并后的逻辑表。
        # 否则数据库回读时，金额虽在事实中却无法回到同一原始单元格。
        artifact.physical_fragments.append(deepcopy(fragment))
        artifact.logical_tables.append(
            LogicalTableInstance(
                instance_uid=fragment.fragment_uid,
                table_code=table_code,
                title=fragment.title,
                fragment_uids=[fragment.fragment_uid],
                page_start=source_page,
                page_end=source_page,
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
        # 来源索引和逻辑格也进入统一主干，不能只追加二维表和事实。
        from copy import copy
        from src.services.source_reconstruction import attach_reconstruction
        projection=copy(artifact);projection.table_fragments=promoted_fragments
        attach_reconstruction(projection,promoted_fragments)
        for field,key in [('source_tokens','token_uid'),('cell_candidates','candidate_uid'),('resolved_cells','logical_cell_uid')]:
            existing=getattr(artifact,field);known={getattr(item,key)for item in existing}
            existing.extend(item for item in getattr(projection,field)if getattr(item,key)not in known)
        _join_ocr_continuations(artifact, promoted_fragments)
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
    if summary['fused_cells']:
        summary['candidate_materialized']=True
        summary['usable_for_rules']=True
    if promoted_uids or summary['fused_cells'] or summary['native_conflicts']:
        from src.services.input_processing_identity import artifact_content_hash
        artifact.parse_run_uid=artifact_content_hash(artifact)[:32]
    return summary
