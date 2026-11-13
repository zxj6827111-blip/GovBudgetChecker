#!/usr/bin/env python
"""D1 跨页合并逐格对账工具（D0 RC-9 / 计划 D1 验收依据）。

目的：验证"多页逻辑表合成一张正确的表"——以**原件应有页、行、格**为准，
不用解析器自产数量作为唯一标准。对指定表格锚点：
1. 用独立提取通道（pdfplumber）读原件各页文本层的数字与科目编码；
2. 与系统的合并逻辑表（parse_artifact.logical_tables）逐格对账；
3. 报告三类偏差：原件有而合并表缺（漏格/漏行）、合并表有而原件无（幻觉/
   错值）、科目行归属页与合并实例不符（跨页归并错误）。

不落盘、不改分析结果；输出 JSON/文本报告供验收与防回归。

用法:
    .venv/bin/python scripts/d1_table_reconciliation.py <pdf> \
        --anchor 收入决算表 --pages 7-11 [--org 单位名 --year 2025] [--json out]
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pdfplumber  # noqa: E402

from src.services.pdf_parser import PDFParser  # noqa: E402
from src.services.pdf_page_extract import extract_tables_from_page  # noqa: E402

_NUM_RE = re.compile(r"(?<![\d.,-])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")
_CODE_RE = re.compile(r"(?<!\d)([1-9]\d{2}(?:\d{2}){0,2})(?!\d)")


def _norm(value: Any) -> str:
    return "".join(str(value or "").split())


def _canonical_number(text: str) -> Optional[str]:
    cleaned = text.replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return str(value.normalize())


def _parse_pages(spec: str, total: int) -> List[int]:
    pages: List[int] = []
    for part in spec.split(","):
        if "-" in part:
            left, right = part.split("-")
            pages.extend(range(int(left), int(right) + 1))
        elif part.strip():
            pages.append(int(part))
    return [p for p in pages if 1 <= p <= total]


def _logical_tables_for_anchor(artifact: Any, anchor: str) -> List[Any]:
    result: List[Any] = []
    for instance in getattr(artifact, "logical_tables", []) or []:
        title = str(getattr(instance, "title", "") or "")
        if anchor in _norm(title):
            result.append(instance)
    return result


def _normalized_row(cells: List[str]) -> List[str]:
    return [_norm(cell) for cell in cells]


def _logical_view_with_provenance(
    doc: Any,
    instances: List[Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """读取**实际交给规则/持久化的逻辑合并行**，并对照来源物理片段。

    R2-02：物理片段只作来源依据。验收对象是逻辑合并片段
    （``table_fragments`` 中登记了 ``source_fragment_uids`` 的那一条）的
    rows/cells；逐行与来源物理片段的行做一致性核对（逻辑合并损坏 → 
    merged_view_source_mismatch），再由调用方与原件页逐行对齐。没有逻辑
    合并片段的实例，物理片段本身即有效视图。
    """
    table_fragments = {
        f.fragment_uid: f for f in getattr(doc.parse_artifact, "table_fragments", []) or []
    }
    physical_fragments = {
        f.fragment_uid: f for f in getattr(doc.parse_artifact, "physical_fragments", []) or []
    }
    rows: List[Dict[str, Any]] = []
    view_errors: List[Dict[str, Any]] = []
    for instance in instances:
        logical_fragments = []
        for fragment_uid in getattr(instance, "fragment_uids", None) or []:
            frag = table_fragments.get(fragment_uid)
            if frag is None:
                continue
            children = list(getattr(frag, "source_fragment_uids", None) or [])
            if children:
                logical_fragments.append((frag, children))
        if logical_fragments:
            for frag, children in logical_fragments:
                provenance: List[Tuple[Any, List[str]]] = []
                # source_fragment_uids 的登记顺序不保证页序（实测按后页在前）；
                # 逻辑合并行按物理页升序拼接，来源核对必须先按 page_number 排序。
                ordered_children = sorted(
                    children,
                    key=lambda uid: (
                        getattr(physical_fragments.get(uid), "page_number", None) or 0,
                        str(uid),
                    ),
                )
                for child_uid in ordered_children:
                    child = physical_fragments.get(child_uid)
                    if child is None:
                        view_errors.append({
                            "kind": "merged_view_source_missing",
                            "fragment_uid": getattr(frag, "fragment_uid", None),
                            "source_fragment_uid": child_uid,
                        })
                        continue
                    for source_row in getattr(child, "rows", []) or []:
                        provenance.append((
                            getattr(child, "page_number", None),
                            [str(cell or "") for cell in source_row],
                        ))
                logical_rows = [[str(cell or "") for cell in row]
                                for row in getattr(frag, "rows", []) or []]
                if len(logical_rows) != len(provenance):
                    view_errors.append({
                        "kind": "merged_view_row_count_mismatch",
                        "table_code": getattr(instance, "table_code", None),
                        "logical_rows": len(logical_rows),
                        "source_rows": len(provenance),
                    })
                for index in range(min(len(logical_rows), len(provenance))):
                    page, source_cells = provenance[index]
                    logical_cells = logical_rows[index]
                    if _normalized_row(logical_cells) != _normalized_row(source_cells):
                        view_errors.append({
                            "kind": "merged_view_source_mismatch",
                            "table_code": getattr(instance, "table_code", None),
                            "page": page,
                            "row_index": index,
                            "logical": logical_cells[:6],
                            "source": source_cells[:6],
                        })
                    rows.append({"page": page, "cells": logical_cells})
            continue
        for fragment_uid in getattr(instance, "fragment_uids", None) or []:
            frag = physical_fragments.get(fragment_uid) or table_fragments.get(fragment_uid)
            if frag is None:
                continue
            for row in getattr(frag, "rows", []) or []:
                rows.append({
                    "page": getattr(frag, "page_number", None),
                    "cells": [str(cell or "") for cell in row],
                })
    return rows, view_errors


def _merged_cells_text(doc: Any, instances: List[Any]) -> List[Dict[str, Any]]:
    """兼容入口：只返回逻辑合并行（供自测注入使用），不暴露来源错误。"""
    rows, _errors = _logical_view_with_provenance(doc, instances)
    return rows


_NUM_TOKEN_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


def _clean_number_token(token: str) -> str:
    return str(token or "").strip(",，;；。.、 ")


def _is_number_token(token: str) -> bool:
    return bool(_NUM_TOKEN_RE.fullmatch(_clean_number_token(token)))


def _is_code_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d{3,7}", _clean_number_token(token)))


_YEAR_TOKEN_RE = re.compile(r"(?:19|20)\d{2}")
_CN_NUMERAL_LABEL_RE = re.compile(r"^[一二三四五六七八九十百]+、")
_ROW_LABEL_PREFIXES = (
    "合计", "总计", "本年", "年初", "年末", "使用非财政",
    "人员经费", "公用经费", "其中", "其他",
)


def _is_year_token(token: str) -> bool:
    return bool(_YEAR_TOKEN_RE.fullmatch(_clean_number_token(token)))


def _is_two_digit_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}", _clean_number_token(token)))


def _looks_like_row_label(text: str) -> bool:
    """续行名称与真正行标签的区分依据（中文序号/合计/本年等）。"""
    value = str(text or "").strip()
    if not value:
        return False
    if _CN_NUMERAL_LABEL_RE.match(value):
        return True
    return value.startswith(_ROW_LABEL_PREFIXES)


def _looks_like_name_continuation(text: str) -> bool:
    """换行名称片段：短、无数字、非注/说明、非新行标签。"""
    value = str(text or "").strip()
    if not value or len(value) > 8:
        return False
    if any(char.isdigit() for char in value):
        return False
    if value[0] in "注说表单第" or value[-1] in "。：；;:":
        return False
    if _looks_like_row_label(value):
        return False
    return True


def _is_table_header_text(text: str) -> bool:
    value = str(text or "")
    return any(noise in value for noise in (
        "科目编码", "科目名称", "经济分类", "功能分类", "决算数", "预算数",
        "单位：", "金额单位", "年初预算", "调整预算", "编码", "科目",
    ))


def _header_column_roles(merged_rows: List[Dict[str, Any]]) -> Dict[int, str]:
    """从表头行学习列角色（R7-02）：栏目身份优先于整数比例统计。

    表头行 = 全部非空格都是文本、且含表头词的合并行；只有与数据行
    **列数一致**的表头才参与（拆码/合并格会改变表头格数，避免错位）。
    映射：编码 → code；金额/决算数/预算数 → amount；项目/名称/科目 →
    label。同列多行表头取最强证据（code > amount > label）。
    """
    lengths: Dict[int, int] = {}
    for row in merged_rows:
        if any(_is_number_token(_clean_number_token(_norm(cell)))
               for cell in row["cells"] if _norm(cell)):
            lengths[len(row["cells"])] = lengths.get(len(row["cells"]), 0) + 1
    if not lengths:
        return {}
    modal_length = max(lengths, key=lengths.get)
    rank = {"code": 3, "amount": 2, "label": 1}
    roles: Dict[int, str] = {}
    for row in merged_rows:
        cells = [_norm(cell) for cell in row["cells"]]
        if len(cells) != modal_length:
            continue
        nonempty = [cell for cell in cells if cell]
        if not nonempty:
            continue
        if any(_is_number_token(_clean_number_token(cell)) for cell in nonempty):
            continue  # 数据行
        if not any(word in "".join(nonempty) for word in
                   ("编码", "金额", "决算数", "预算数", "项目", "名称", "科目")):
            continue
        for index, cell in enumerate(cells):
            if not cell:
                continue
            role = None
            if "编码" in cell:
                role = "code"
            elif any(word in cell for word in ("金额", "决算数", "预算数")):
                role = "amount"
            elif any(word in cell for word in ("项目", "名称", "科目")):
                role = "label"
            if role is None:
                continue
            if index not in roles or rank[role] > rank[roles[index]]:
                roles[index] = role
    return roles


def _header_fragment_for_instance(doc: Any, instance: Any) -> Any:
    """实例页序最早的物理片段——表头几何与栏目映射的来源。"""
    physical = {f.fragment_uid: f
                for f in getattr(doc.parse_artifact, "physical_fragments", []) or []}
    table_frags = {f.fragment_uid: f
                   for f in getattr(doc.parse_artifact, "table_fragments", []) or []}
    candidates: List[Any] = []
    for fragment_uid in getattr(instance, "fragment_uids", None) or []:
        frag = table_frags.get(fragment_uid)
        if frag is not None:
            children = list(getattr(frag, "source_fragment_uids", None) or [])
            if children:
                candidates.extend(physical[child] for child in children
                                  if child in physical)
            else:
                candidates.append(frag)
        elif fragment_uid in physical:
            candidates.append(physical[fragment_uid])
    if not candidates:
        return None
    return min(candidates, key=lambda f: (getattr(f, "page_number", 0) or 0,
                                          str(getattr(f, "fragment_uid", ""))))


_TABLE_SCOPE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "intersection_tolerance": 3,
    "min_words_vertical": 1,
    "min_words_horizontal": 1,
}
_SCOPE_TOL = 1.0


def _scope_data_line(text: str) -> bool:
    """表范围延伸的数据行判据：多数值格，或行首为 3~7 位科目编码。

    章节标题（单个年度数字嵌在长文本里）、表题、注释、页码都不满足，
    延伸遇到它们即停——不把表外年度借进金额清单。单独"2025 年"形态
    的年度元信息也不是数据行。
    """
    tokens = unicodedata.normalize("NFKC", str(text or "")).split()
    cleaned = [_clean_number_token(token) for token in tokens]
    cleaned = [token for token in cleaned if token]
    if not cleaned:
        return False
    nums = [token for token in cleaned if _is_number_token(token)]
    if len(nums) >= 2:
        return True
    if cleaned and _is_code_token(cleaned[0]):
        if (len(nums) == 1 and _is_year_token(nums[0])
                and any("年" in token for token in cleaned)):
            return False
        return True
    return False


_SCOPE_METADATA_WORDS = ("决算表", "预算表", "单位：", "年度", "部分")


def _scope_name_fragment(text: str) -> bool:
    """表范围延伸的名称续行判据：跨行名称的纯文本片段。

    续页名称与编码金额行逐行交错（文旅局/上海大学实证），延伸必须
    穿过名称片段才能保住编码行身份；章节标题片段（含决算表/部分等
    元信息词、超长、注/表开头）不在此列。
    """
    value = "".join(str(text or "").split())
    if not value or len(value) > 15:
        return False
    if any(char.isdigit() for char in value):
        return False
    if value[0] in "注说表第":
        return False
    return not any(word in value for word in _SCOPE_METADATA_WORDS)


def _table_scoped_texts(pdf_doc: Any) -> List[str]:
    """R11-01：原件侧对账文本限定独立表格通道的实际表范围。

    每页用 find_tables 的表格 bbox 过滤文本行：只保留与表格纵向范围
    重叠的行——表外章节标题/年度/目录/页脚不进入金额与行身份推断
    （老干部活动室第5页"2025 年/度决算表"实证）。续页网格顶/底边框
    缺失时，首末数据行可能落在 bbox 外：向上下延伸到相邻的数据行或
    名称续行（横向须在表宽内），遇标题/注释即停。无表格的页退回整页
    文本（保持既有行为）。表内跨行名称、续行与跨页合计仍在各自页表
    范围内保留。全文文本另供 V1/V2 标题/栏目核验，不受此过滤影响。
    """
    scoped: List[str] = []
    for page in pdf_doc.pages:
        try:
            tables = page.find_tables(table_settings=_TABLE_SCOPE_SETTINGS)
        except Exception:
            tables = []
        if not tables:
            try:
                tables = page.find_tables()
            except Exception:
                tables = []
        if not tables:
            scoped.append(page.extract_text() or "")
            continue
        boxes = [table.bbox for table in tables]
        lines = page.extract_text_lines()
        kept = [
            index for index, line in enumerate(lines)
            if any(line["bottom"] > box[1] - _SCOPE_TOL
                   and line["top"] < box[3] + _SCOPE_TOL for box in boxes)
        ]
        if kept:
            x0 = min(box[0] for box in boxes) - 2.0
            x1 = max(box[2] for box in boxes) + 2.0

            def extendable(index: int, _lines=lines, _x0=x0, _x1=x1) -> bool:
                line = _lines[index]
                return (line["x0"] >= _x0 and line["x1"] <= _x1
                        and (_scope_data_line(line["text"])
                             or _scope_name_fragment(line["text"])))

            start, stop = kept[0], kept[-1]
            while start > 0 and extendable(start - 1):
                start -= 1
            while stop + 1 < len(lines) and extendable(stop + 1):
                stop += 1
            kept = sorted(set(kept)
                          | set(range(start, kept[0]))
                          | set(range(kept[-1] + 1, stop + 1)))
        scoped.append("\n".join(lines[index]["text"] for index in kept))
    return scoped


def _original_header_rows(page_tables: List[Any], pages: List[int], rule: Any) -> List[List[Tuple[int, str]]]:
    """原件表头行（独立 pdfplumber 表格通道，R9-02 V4 用，保留列位）。

    按页序取**首个**含本表栏目词的页：该页选含必备栏目/指标别名最多的
    一张表，取首个数据行（含数值格）之前的表头行；每行展开为
    ``(列位, 归一文本)``，保留父子栏目的列几何。换行格在表格通道里仍是
    完整一格。只用于栏目身份对回，不参与金额解析，不使用合并侧角色。
    """
    words = [_norm(unicodedata.normalize("NFKC", header))
             for header in rule.required_headers]
    words += [_norm(unicodedata.normalize("NFKC", alias))
              for aliases in rule.measure_aliases.values() for alias in aliases]
    words = [word for word in words if word]
    for page in pages:
        if not (1 <= page <= len(page_tables)):
            continue
        best_rows: List[List[Tuple[int, str]]] = []
        best_score = 0
        for table in page_tables[page - 1] or []:
            rows: List[List[Tuple[int, str]]] = []
            for row in table:
                cells = [(col, _norm(unicodedata.normalize("NFKC", cell)))
                         for col, cell in enumerate(row)]
                if any(_is_number_token(_clean_number_token(text))
                       for _col, text in cells if text):
                    break
                rows.append([(col, text) for col, text in cells if text])
            flat = [text for row in rows for _col, text in row]
            score = sum(1 for word in words
                        if any(word in cell or (len(cell) >= 2 and cell in word)
                               for cell in flat))
            if score > best_score:
                best_score, best_rows = score, rows
        if best_rows:
            return best_rows
    return []


def _header_cell_hits(flat: List[Tuple[int, int, str]], name: str) -> List[Tuple[int, int, str]]:
    hits = [cell for cell in flat if cell[2] == name]
    if not hits and name:
        hits = [cell for cell in flat if name in cell[2]]
    return hits


def _semantic_amount_roles(
    doc: Any,
    instance: Any,
    merged_rows: List[Dict[str, Any]],
    page_texts: List[str],
    pages: List[int],
    page_tables: Optional[List[Any]] = None,
) -> Tuple[Optional[Dict[int, str]], Dict[str, Any]]:
    """R8-02/R9-02/R10-02：已知九表复用生产语义列映射确定金额列（候选侧假设）。

    采纳前必须通过独立核对门（原页文本/表格来自 pdfplumber 独立通道）：
    V1 实例标题归一后出现在原页文本；
    V2 该表 required_headers 归一后都出现在原页文本；
    V3 映射非空、每个映射列位在行宽内、映射列数据格（跳过表头）为数值
    或空白；
    V4 身份：叶栏目名在原件表头格中按同名候选列数配对命中，父栏目按
    列优先最近格归属，叶格原列位顺序须与来源列位一致——确定性矛盾留
    header_mismatches（GAP）；
    V4 覆盖：预期指标集合由原件表头独立建立（别名命中表头格），候选
    映射缺项（未知/重名表头）→ GAP，不能只验成功映射子集。
    V1-V3 任一不成立或表头不可判定 → 不采纳语义角色，退回表头关键词+
    统计逻辑，semantic_evidence.verification 显式记 incomplete/
    not_applicable（不静默）；确定性矛盾记 failed，采纳记 complete。
    原件侧身份不使用这里的角色——候选提供假设，真值由原页 token 序列
    逐行裁定。
    """
    from src.engine.fact_context import _column_mappings_for_instance
    from src.services.fiscal_table_rules import NINE_TABLE_RULES

    evidence: Dict[str, Any] = {"adopted": False, "verification": "incomplete"}
    rule = NINE_TABLE_RULES.get(str(getattr(instance, "table_code", "") or ""))
    if rule is None:
        evidence.update(reason="unknown_table", verification="not_applicable")
        return None, evidence
    fragment = _header_fragment_for_instance(doc, instance)
    if fragment is None:
        evidence["reason"] = "no_header_fragment"
        return None, evidence
    # 生产映射路径消费表头几何与提取方式；合成/最小片段缺这些属性时
    # 无语义证据可用，退回表头关键词+统计（不改判 GAP/PASS）。
    if not all(hasattr(fragment, attr) for attr in (
            "extraction_method", "rows", "cells",
            "header_row_count", "page_number", "table_code")):
        evidence["reason"] = "fragment_lacks_header_geometry"
        return None, evidence
    mappings = _column_mappings_for_instance(instance, fragment)
    if not mappings:
        evidence.update(reason="empty_mapping", gate="V3", verification="not_applicable")
        return None, evidence

    def nfkc(value: Any) -> str:
        return "".join(unicodedata.normalize("NFKC", str(value or "")).split())

    original_text = nfkc("".join(
        page_texts[page - 1] for page in pages if 1 <= page <= len(page_texts)))
    title = nfkc(getattr(instance, "title", ""))
    if not title or title not in original_text:
        evidence.update(reason="title_not_in_original", gate="V1")
        return None, evidence
    missing_headers = [header for header in rule.required_headers
                       if nfkc(header) not in original_text]
    if missing_headers:
        evidence.update(reason="required_headers_missing", gate="V2",
                        missing_headers=missing_headers)
        return None, evidence
    lengths: Dict[int, int] = {}
    for row in merged_rows:
        if any(_is_number_token(_clean_number_token(_norm(cell)))
               for cell in row["cells"] if _norm(cell)):
            lengths[len(row["cells"])] = lengths.get(len(row["cells"]), 0) + 1
    modal_width = max(lengths, key=lengths.get) if lengths else 0
    amount_cols: set = set()
    for item in mappings:
        try:
            col = int(item.get("source_col_idx"))
        except (TypeError, ValueError):
            continue
        if not 0 <= col < modal_width:
            evidence.update(reason="mapping_col_out_of_width", gate="V3", col=col)
            return None, evidence
        amount_cols.add(col)
    if not amount_cols:
        evidence.update(reason="no_valid_mapping_cols", gate="V3")
        return None, evidence
    for row in merged_rows:
        cells = [_norm(cell) for cell in row["cells"]]
        if not any(_is_number_token(_clean_number_token(cell))
                   for cell in cells if cell):
            continue  # 表头/纯文本行不参与数据格校验
        for col in sorted(amount_cols):
            value = cells[col] if col < len(cells) else ""
            if value and not _is_number_token(_clean_number_token(value)):
                evidence.update(reason="mapping_col_non_numeric_data", gate="V3",
                                col=col, page=row.get("page"))
                return None, evidence
    # V4（R9-02）：语义指标列身份对回原件表头格/列位。候选映射是语义
    # 解释，不能自证原件：
    # - 叶栏目名在原件表头格中的命中数须与同名候选列数一致，按列序逐个
    #   配对（双栏同名逐一核对；数量不符 → 不可判定，退回）；
    # - 父栏目须是叶格上方列位不超过叶格的最近表头格（列优先、同列取
    #   最近行），最近格不匹配而父词存在于原件表头 → 确定性身份矛盾，
    #   留 GAP；
    # - 各列叶格按行序的顺序须与来源列位顺序一致，顺序矛盾 → GAP。
    # 表头不可判定（无表头格/缺失/重名数不符）→ 不采纳语义、退回现有
    # 逻辑，不判 GAP。金额序列相同不能替代栏目身份核验。
    header_rows = _original_header_rows(page_tables or [], pages, rule)
    flat = [(ri, col, text) for ri, row in enumerate(header_rows)
            for col, text in row]
    names: List[Tuple[int, List[str]]] = []
    for item in sorted(mappings, key=lambda m: int(m.get("source_col_idx") or 0)):
        col = int(item.get("source_col_idx"))
        if col not in amount_cols:
            continue
        full = nfkc(item.get("source_col_name"))
        names.append((col, [part for part in full.split("/") if part]))
    leaf_counts: Dict[str, int] = {}
    for _col, parts in names:
        leaf = parts[-1] if parts else ""
        leaf_counts[leaf] = leaf_counts.get(leaf, 0) + 1
    leaf_hits: Dict[str, List[Tuple[int, int, str]]] = {}
    for leaf, count in leaf_counts.items():
        hits = _header_cell_hits(flat, leaf)
        if not leaf:
            evidence.update(reason="header_identity_inconclusive", gate="V4",
                            header_cells_found=bool(flat))
            return None, evidence
        if not hits:
            # 候选叶名在原件表头不存在：原件在该来源列位上有不同表头格
            # → 确定性改名损坏（含超串改名，如"基本支出"→"基本支出补充"），
            # GAP；原件该列无表头格（几何不可得）→ 不可判定，退回。
            rename_errors = []
            for col, parts in names:
                if not parts or parts[-1] != leaf:
                    continue
                at_col = [cell[2] for cell in flat if cell[1] == col]
                if at_col and not any(leaf == text or leaf in text for text in at_col):
                    rename_errors.append({
                        "col": col, "candidate_name": leaf,
                        "original_header_at_col": at_col,
                    })
            if rename_errors:
                evidence.update(
                    adopted=False, verification="failed",
                    reason="header_semantic_identity_mismatch", gate="V4",
                    header_mismatches=[{
                        "kind": "header_semantic_identity_mismatch",
                        "table_code": getattr(instance, "table_code", None),
                        "rename_errors": rename_errors,
                    }])
                return None, evidence
            evidence.update(reason="header_identity_inconclusive", gate="V4",
                            header_cells_found=bool(flat))
            return None, evidence
        if len(hits) != count:
            evidence.update(reason="header_identity_inconclusive", gate="V4",
                            header_cells_found=bool(flat))
            return None, evidence
        # 同名叶格按原列位排序配对（混合多行表头里同名格可能跨行，
        # 行序不等于列序）。
        leaf_hits[leaf] = sorted(hits, key=lambda cell: (cell[1], cell[0]))
    used: Dict[str, int] = {}
    positions: List[Tuple[int, Tuple[int, int, str]]] = []
    identity_errors: List[Dict[str, Any]] = []
    for col, parts in names:
        leaf = parts[-1]
        index = used.get(leaf, 0)
        used[leaf] = index + 1
        leaf_cell = leaf_hits[leaf][index]
        positions.append((col, leaf_cell))
        anchor = leaf_cell
        for parent in reversed(parts[:-1]):
            # 父格归属：叶格上方、列位不超过叶格的表头格中取列位最大者
            # （同列取最近行）。九表版式中维度列连续居左、父栏目与叶格
            # 同列或自左跨列，列优先可避开下一行维度格（如"科目名称"）。
            candidates = [cell for cell in flat
                          if cell[0] < anchor[0] and cell[1] <= anchor[1]]
            if not candidates:
                evidence.update(reason="header_identity_inconclusive", gate="V4",
                                header_cells_found=bool(flat))
                return None, evidence
            nearest = max(candidates, key=lambda cell: (cell[1], cell[0]))
            if parent not in nearest[2] and nearest[2] not in parent:
                if _header_cell_hits(flat, parent):
                    identity_errors.append({
                        "col": col, "parent": parent,
                        "nearest_original_header": nearest[2],
                    })
                else:
                    evidence.update(reason="header_identity_inconclusive", gate="V4",
                                    header_cells_found=bool(flat))
                    return None, evidence
            anchor = nearest
    # 列序不变量：来源列位顺序须与叶格**原列位**顺序一致（混合多行
    # 表头里叶格跨行，行序优先会把行0的年末列排到行1的基本/项目之前，
    # 造成误杀——上海大学/市政水务 FIN_08 实证）。
    order_errors = [
        {"col_a": left[0], "col_b": right[0],
         "name_a": left[1][2], "name_b": right[1][2]}
        for left, right in zip(positions, positions[1:], strict=False)
        if (left[1][1], left[1][0]) > (right[1][1], right[1][0])
    ]
    if identity_errors or order_errors:
        evidence.update(
            adopted=False, verification="failed",
            reason="header_semantic_identity_mismatch", gate="V4",
            header_mismatches=[{
                "kind": "header_semantic_identity_mismatch",
                "table_code": getattr(instance, "table_code", None),
                "candidate_order": [{"col": col, "name": parts[-1]}
                                    for col, parts in names],
                "original_order": [cell[2] for _col, cell in
                                   sorted(positions, key=lambda item: (item[1][1], item[1][0]))],
                "parent_errors": identity_errors,
                "order_errors": order_errors,
            }])
        return None, evidence
    # V4 覆盖核验（R10-02）：预期集合由**原件表头**独立建立，不由候选
    # 成功映射反向定义。原件表头格含某指标别名 → 该指标属于应核对集合；
    # 候选映射缺项（未知/重名表头使映射器放弃）→ 确定性身份损坏，GAP。
    # 双向对账：原件应有 / 候选已识别 / 缺失 全部留痕。
    # 通用阶段词（预算数/决算数）是阶段包装而非独立栏目身份——生产矩阵
    # 分支同样跳过，阶段信息由父路径携带，不计入预期指标集合。
    expected_measures = set()
    for measure, aliases in rule.measure_aliases.items():
        if measure in {"budget", "actual"}:
            continue
        if any(nfkc(alias) and nfkc(alias) in cell[2]
               for alias in aliases for cell in flat):
            expected_measures.add(measure)
    mapped_measures = {str(item.get("canonical_measure")) for item in mappings}
    missing_measures = sorted(expected_measures - mapped_measures)
    if missing_measures:
        evidence.update(
            adopted=False, verification="failed",
            reason="header_semantic_measure_missing", gate="V4",
            header_mismatches=[{
                "kind": "header_semantic_measure_missing",
                "table_code": getattr(instance, "table_code", None),
                "expected_measures": sorted(expected_measures),
                "mapped_measures": sorted(mapped_measures),
                "missing_measures": missing_measures,
            }])
        return None, evidence
    evidence.update(
        adopted=True, verification="complete",
        amount_cols=sorted(amount_cols),
        expected_measures=sorted(expected_measures),
        measures={str(item.get("canonical_measure")): item.get("source_col_idx")
                  for item in mappings})
    return {col: "amount" for col in sorted(amount_cols)}, evidence


def _combined_semantic_roles(
    doc: Any,
    instances: List[Any],
    merged_rows: List[Dict[str, Any]],
    page_texts: List[str],
    pages: List[int],
    page_tables: Optional[List[Any]] = None,
) -> Tuple[Optional[Dict[int, str]], Dict[str, Any]]:
    """多实例锚点：每实例单独算语义角色；全部采纳且一致才共用，冲突退回。"""
    if not instances:
        return None, {"adopted": False, "reason": "no_instance"}
    per_instance = []
    for instance in instances:
        instance_pages = sorted(_instance_pages(doc, instance) & set(pages))
        per_instance.append(_semantic_amount_roles(
            doc, instance, merged_rows, page_texts, instance_pages or pages,
            page_tables))
    header_mismatches = [item for _roles, evidence in per_instance
                         for item in evidence.get("header_mismatches") or []]
    if len(per_instance) == 1:
        return per_instance[0]
    first_roles, _first_evidence = per_instance[0]
    if first_roles is not None and all(
            roles == first_roles for roles, _evidence in per_instance[1:]):
        return first_roles, {"adopted": True, "instances": len(per_instance),
                             "amount_cols": sorted(first_roles),
                             "header_mismatches": header_mismatches}
    return None, {"adopted": False, "reason": "multi_instance_conflict",
                  "instances": [evidence for _roles, evidence in per_instance],
                  "header_mismatches": header_mismatches}


def _learn_column_roles(
    merged_rows: List[Dict[str, Any]],
    semantic_roles: Optional[Dict[int, str]] = None,
) -> Tuple[Optional[Dict[int, str]], set]:
    """列角色学习（R6-02/R7-02/R8-02）：角色跟**列**走，栏目身份优先。

    返回 ``(roles, codes)``：
    - ``roles``：``{列位: 'code'|'subcode'|'amount'|'label'}``；学习失败
      （金额守卫触发）时为 ``None``，两侧共同退回车道形状规则；
    - ``codes``：编码列中的编码值集合（原件侧身份依据之一）。

    R7-02：先用表头栏目确认角色——表头明确写“金额/决算数/预算数”的列
    一律是金额（整数金额不得因占比被认成编码），写“编码”的列才是编码；
    统计只用于无表头证据的列。金额守卫保留：候选编码列会把全表数字列
    吃光时判学习失败。同一个数值在编码列是编码、在金额列是金额。
    R8-02：已知九表经 V1-V3 核对门采纳的语义金额列一律是 amount，优先
    于表头关键词与统计；统计候选不得把语义金额列改判 code，只填充语义
    未覆盖的列。
    """
    semantic_amounts = {index for index, role in (semantic_roles or {}).items()
                        if role == "amount"}
    header_roles = _header_column_roles(merged_rows)
    stats: Dict[int, Dict[str, int]] = {}
    for row in merged_rows:
        for index, cell in enumerate(row["cells"]):
            token = _norm(cell)
            if not token:
                continue
            stat = stats.setdefault(index, {"code": 0, "sub": 0, "num": 0,
                                            "total": 0})
            stat["total"] += 1
            if _is_code_token(token):
                stat["code"] += 1
            if re.fullmatch(r"\d{1,2}", token):
                stat["sub"] += 1
            if _is_number_token(token):
                stat["num"] += 1
    candidates = {
        index for index, stat in stats.items()
        if stat["total"] and stat["code"] * 2 >= stat["total"]
    }
    # 表头明确的金额列与语义金额列不得成为候选编码列（栏目身份优先于统计）。
    candidates = {index for index in candidates
                  if header_roles.get(index) != "amount"
                  and index not in semantic_amounts}
    if candidates and not any(
            index not in candidates and stat["num"] > 0
            for index, stat in stats.items()):
        # 金额守卫：所有数字列都会被当成编码 → 角色学习不可信。此时
        # 两侧共同退回车道形状规则（同一套解析，保持对称，R5 行为）。
        return None, set()
    roles: Dict[int, str] = {}
    for index in sorted(stats):
        header = header_roles.get(index)
        if index in semantic_amounts:
            roles[index] = "amount"
        elif header == "code":
            roles[index] = "code"
        elif header == "amount":
            roles[index] = "amount"
        elif index in candidates:
            roles[index] = "code"
        elif (stats[index]["sub"] * 2 >= stats[index]["total"]
              and roles.get(index - 1) == "code"):
            roles[index] = "subcode"
        elif stats[index]["num"] > 0 or header == "label":
            roles[index] = "amount" if stats[index]["num"] > 0 else "label"
        else:
            roles[index] = "label"
    codes: set = set()
    for row in merged_rows:
        for index, cell in enumerate(row["cells"]):
            token = _norm(cell)
            if not token:
                continue
            if roles.get(index) == "code" and _is_code_token(token):
                codes.add(token)
    return roles, codes


def _parse_lanes(
    tokens: List[str],
    known_codes: Optional[set] = None,
    token_roles: Optional[List[Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    """一行 → 语义车道序列：每条车道含 (类码, 款/项码, 名称, 金额序列)。

    R3-03：完整科目身份必须包含拆开的款/项码（301 01 ≠ 301 02），
    编码不得进入金额统计；多车道按出现顺序保留。
    R4-02/R5-02：原件侧（无列位）3~7 位整数只有位于**车道起点**且后随
    名称/两位款/项码、或在已学习的编码集合中并按车道状态确认，才是
    类码；"合计 100"的 100、"201 教育支出 100" 的 100 都是金额。
    R6-02/R7-02：合并侧传入 ``token_roles``（按列位学习到的角色）时，
    角色跟列走——code/subcode 列按位取身份，amount 列的数值（含年份
    形态）一律是金额；原件侧只有"年份数字 + 年度单元"（"2025 年度…"）
    是日期短语，"2025 年末结转和结余"的 2025 仍是金额。
    """
    known_codes = known_codes or set()
    lanes: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {"cls": None, "item": None,
                               "label": [], "numbers": []}
    seen_number = False

    def flush() -> None:
        nonlocal current, seen_number
        if current["cls"] or current["label"] or current["numbers"]:
            lanes.append(current)
        current = {"cls": None, "item": None, "label": [], "numbers": []}
        seen_number = False

    index = 0
    while index < len(tokens):
        cleaned = _clean_number_token(tokens[index])
        if not cleaned:
            index += 1
            continue
        role = token_roles[index] if token_roles and index < len(token_roles) else None
        following = ""
        if index + 1 < len(tokens):
            following = _clean_number_token(tokens[index + 1])
        followed_by_label = bool(following) and not _NUM_TOKEN_RE.fullmatch(following)
        followed_by_subcode = bool(following) and _is_two_digit_token(following)
        if role == "code":
            # R6-02：合并侧按列位取角色——编码列的 3~7 位整数是类码；
            # 编码列里的其他数值仍按金额保留，不丢验收对象。
            if _is_code_token(cleaned):
                flush()
                current["cls"] = cleaned
                index += 1
                continue
            if _NUM_TOKEN_RE.fullmatch(cleaned):
                canonical = _canonical_number(cleaned)
                if canonical is not None:
                    current["numbers"].append(canonical)
                    seen_number = True
                index += 1
                continue
        elif role == "subcode":
            if (re.fullmatch(r"\d{1,2}", cleaned) and current["cls"]
                    and current["item"] is None and not current["numbers"]
                    and not current["label"]):
                current["item"] = cleaned
                index += 1
                continue
        elif role == "amount":
            if _NUM_TOKEN_RE.fullmatch(cleaned):
                canonical = _canonical_number(cleaned)
                if canonical is not None:
                    current["numbers"].append(canonical)
                    seen_number = True
                index += 1
                continue
        else:
            if _is_code_token(cleaned):
                at_lane_start = not (current["cls"] or current["label"]
                                     or current["numbers"])
                if cleaned in known_codes:
                    # R5-02：已知编码按车道状态定角色，不做全表词表。
                    # 金额只出现在"车道已有身份且尚未记数"的位置：
                    #   合计行 `合计 301`（无编码身份、有标签）→ 金额；
                    #   空名称车道 `301 … 100`（编码后第一个数值）→ 金额；
                    #   同值金额在行尾 `301 工资 301` → 金额；
                    # 其余（车道起点、上一车道已记数、编码身份后还有后续格）
                    # 都是下一车道的编码，如 `30113 … 431.39 30212 13.24`、
                    # `30310 个人农业生产补贴 30231 81.11`。
                    lane_has_cls = current["cls"] is not None
                    lane_has_label = bool(current["label"])
                    lane_has_numbers = bool(current["numbers"])
                    if not lane_has_cls:
                        is_amount = lane_has_label and not lane_has_numbers
                    elif not lane_has_label and not lane_has_numbers:
                        # 编码后的第一个数值通常是金额（空名称车道）；但它本身
                        # 是已知编码且后随名称时，是下一车道的编码
                        # （`30399 30240 税金及附加费用 20.81`）。
                        is_amount = not (cleaned in known_codes
                                         and (followed_by_label or followed_by_subcode))
                    elif lane_has_label and not lane_has_numbers:
                        is_amount = not (following or followed_by_label
                                         or followed_by_subcode)
                    else:
                        is_amount = False
                    if not is_amount:
                        flush()
                        current["cls"] = cleaned
                        index += 1
                        continue
                elif at_lane_start and (followed_by_label or followed_by_subcode):
                    # 未学习的 3~7 位整数只有在车道起点且后随名称/两位款/项码
                    # 时才当编码（编码列被守卫拒绝后保留单行表的编码身份）。
                    flush()
                    current["cls"] = cleaned
                    index += 1
                    continue
            if (_is_two_digit_token(cleaned) and current["cls"]
                    and current["item"] is None and not current["numbers"]
                    and not current["label"]):
                # 两位款/项码后必须是名称或金额（行尾单独两位数仍按金额处理）。
                if following:
                    current["item"] = cleaned
                    index += 1
                    continue
            if _NUM_TOKEN_RE.fullmatch(cleaned):
                if _is_year_token(cleaned) and following.startswith("年度"):
                    # 唯一日期形态："2025 年度…"元信息不是金额
                    # （任务书：真实"2025年度…"元信息仍不能算金额）；
                    # "2025 年末结转和结余"的 2025 是金额。
                    index += 1
                    continue
                canonical = _canonical_number(cleaned)
                if canonical is not None:
                    current["numbers"].append(canonical)
                    seen_number = True
                index += 1
                continue
        if seen_number:
            flush()
        current["label"].append(cleaned)
        index += 1
    flush()
    return lanes


def _lanes_identity_and_numbers(
    lanes: List[Dict[str, Any]],
) -> Tuple[str, List[str], str]:
    """车道序列 → (完整身份, 金额序列, 名称摘要)。

    有编码的车道用「类码[:款/项码]」按序拼接作为身份；纯标签行用
    车道名称拼接作为身份（金额顺序仍逐位比较）。
    """
    codes = [
        f"{lane['cls']}:{lane['item']}" if lane.get("item") else str(lane["cls"])
        for lane in lanes if lane.get("cls")
    ]
    labels = ["".join(lane.get("label") or []) for lane in lanes if lane.get("label")]
    numbers = [number for lane in lanes for number in lane.get("numbers") or []]
    if codes:
        identity = "|".join(codes)
    elif labels:
        identity = "|".join(labels)
    elif numbers:
        identity = "#numeric"
    else:
        identity = ""
    return identity, numbers, "|".join(labels)


def _codes_in_identity(identity: str) -> List[str]:
    """身份串中的 3~7 位类级编码（``301:01|302:01`` → ``301``/``302``）。"""
    codes: List[str] = []
    for part in str(identity or "").split("|"):
        cls = part.split(":", 1)[0]
        if _is_code_token(cls):
            codes.append(cls)
    return codes


def _row_identity_and_numbers(cells: List[str],
                              roles: Optional[Dict[int, str]] = None) -> Tuple[str, List[str]]:
    """合并行 → (完整科目身份, 金额序列)。

    R6-02：角色按**原列位**附加到 token，角色跟列走——code/subcode 列
    按位取身份，amount 列的数值（含年份形态）一律是金额；不再按数值
    形态猜测。去空列只影响 token 顺序，列位角色不丢。
    """
    tokens: List[str] = []
    token_roles: List[Optional[str]] = []
    for index, cell in enumerate(cells):
        token = _norm(cell)
        if not token:
            continue
        tokens.append(token)
        token_roles.append(roles.get(index) if roles else None)
    if not tokens:
        return "", []
    lanes = _parse_lanes(tokens, None, token_roles)
    identity, numbers, _label = _lanes_identity_and_numbers(lanes)
    return identity, numbers


def _parse_original_page(page_text: str, page_number: int,
                         known_codes: Optional[set] = None) -> List[Dict[str, Any]]:
    """原件单页文本 → 行记录（身份、金额序列、名称）。

    处理真实版式的三类跨行：
    1. 名称在金额行之前（`二十二、…支` / `0.00 0.00 …` / `出`），
       名称续行并回已生成的行；
    2. 名称在金额行之后（`机关事业单位基本养老` / `2080505 518.69 …`），
       前缀只作车道名称，身份仍按编码；
    3. 纯金额行（三公表、总表）没有标签时身份为 ``#numeric``。

    年份形态：只有"2025 年度…"日期短语单元不算金额（R7-02 收窄为
    唯一形态），其余年份形态数值按金额核验。
    """
    known_codes = known_codes or set()
    rows: List[Dict[str, Any]] = []
    pending: List[str] = []

    def append_continuation(fragment: str) -> None:
        if not rows:
            return
        row = rows[-1]
        if row.get("basis") == "label":
            row["label"] = str(row.get("label") or "") + fragment
            row["identity"] = row["label"]

    for raw_line in str(page_text or "").splitlines():
        tokens = [token for token in unicodedata.normalize("NFKC", raw_line).split()
                  if token]
        cleaned = [_clean_number_token(token) for token in tokens]
        cleaned = [token for token in cleaned if token]
        if not cleaned:
            continue
        if (all(_is_number_token(token) for token in cleaned)
                and not any(_is_code_token(token) for token in cleaned)):
            # 页码/孤立序号行（如页脚 "12"）不是表格金额。
            if len(cleaned) == 1 and re.fullmatch(r"\d{1,4}", cleaned[0]):
                if int(cleaned[0]) == int(page_number):
                    pending = []
                    continue
            numbers = []
            for token in cleaned:
                if _is_year_token(token):
                    # 孤立年份行（标题/页脚年份）不是金额；表格数据行
                    # 由 _parse_lanes 的"年度单元"规则处理。
                    continue
                canonical = _canonical_number(token)
                if canonical is not None:
                    numbers.append(canonical)
            if not numbers:
                pending = []
                continue
            if pending and _looks_like_row_label("".join(pending)):
                label = "".join(pending)
                rows.append({"identity": label, "label": label,
                             "numbers": numbers, "basis": "label"})
                pending = []
            elif pending:
                # 表头/标题残留不构成数据行：按纯金额行登记。
                pending = []
                rows.append({"identity": "#numeric", "label": "",
                             "numbers": numbers, "basis": "numeric"})
            elif rows:
                # 同一行的金额换行续写。
                rows[-1]["numbers"].extend(numbers)
            else:
                rows.append({"identity": "#numeric", "label": "",
                             "numbers": numbers, "basis": "numeric"})
            continue
        lanes = _parse_lanes(tokens, known_codes)
        numbers = [number for lane in lanes for number in lane.get("numbers") or []]
        if numbers:
            identity, sequence, label = _lanes_identity_and_numbers(lanes)
            if not identity:
                continue
            basis = "code" if any(lane.get("cls") for lane in lanes) else "label"
            # 前置名称片段（换行名称的前半句）并入首车道名称，不改编码身份。
            if pending and basis == "code":
                label = "".join(pending) + label
            rows.append({"identity": identity, "label": label,
                         "numbers": sequence, "basis": basis})
            pending = []
            continue
        line_text = "".join(cleaned)
        if _is_table_header_text(line_text):
            pending = []
            continue
        if _looks_like_row_label(line_text):
            # 真正的行标签（中文序号/合计/本年…）覆盖此前的标题/表头残留。
            pending = [line_text]
            continue
        if pending:
            pending.append(line_text)
        elif rows and _looks_like_name_continuation(line_text):
            append_continuation(line_text)
        else:
            pending = [line_text]
    return [row for row in rows if row["numbers"]]


def _reconcile_row_alignment(
    merged_rows: List[Dict[str, Any]],
    page_texts: List[str],
    pages: List[int],
    semantic_roles: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """按「语义车道身份 + 逐位金额序列」逐页对齐。

    R2-02 的严格性保留：错列/错行/合并视图损坏都会改变该身份下的金额
    顺序；R3-03 修正身份归一：拆码（301 01/301 02）是不同身份，编码与
    两位款/项码不再当作金额，换行名称与续页行按原件阅读顺序归并。
    R4-02/R5-02：编码列学习 + 金额守卫，整数金额不凭形态吃成编码。
    R6-02/R7-02：列角色学习一次、贯穿两侧——表头栏目身份优先（金额列
    不因整数占比被认成编码），统计只辅助无表头列；合并侧按列位取角色，
    原件侧编码集合与日期单元同源。逐行对象由身份+序列比对承担，数值
    出现在同页其他行不满足本行；原件有、合并缺失的数据行反向报告。
    R8-02：语义金额列（经 V1-V3 核对门）参与同一次角色学习，两侧共用
    同一 roles；原件侧解析仍独立按 token 顺序，不使用合并侧列角色。
    """
    roles, known_codes = _learn_column_roles(merged_rows, semantic_roles)
    merged_by_page: Dict[int, Dict[str, List[List[str]]]] = {}
    for row in merged_rows:
        identity, numbers = _row_identity_and_numbers(row["cells"], roles)
        if not identity or not numbers:
            # 纯表头/空行不参与数值对齐（没有金额可逐格核对）。
            continue
        page = row["page"]
        merged_by_page.setdefault(page, {}).setdefault(identity, []).append(numbers)

    original_by_page: Dict[int, Dict[str, List[List[str]]]] = {}
    for page in pages:
        if not (1 <= page <= len(page_texts)):
            continue
        for record in _parse_original_page(page_texts[page - 1], page, known_codes):
            original_by_page.setdefault(page, {}).setdefault(
                record["identity"], []).append(record["numbers"])

    row_mismatches: List[Dict[str, Any]] = []
    for page, identities in merged_by_page.items():
        original_map = original_by_page.get(page) or {}
        for identity, sequences in identities.items():
            original_sequences = original_map.get(identity)
            if original_sequences is None:
                # 名称被原件换行拆开而合并侧截断时，按前缀唯一匹配回退。
                candidates = [
                    key for key in original_map
                    if identity and (key.startswith(identity) or identity.startswith(key))
                    and min(len(key), len(identity)) >= 6
                ]
                if len(candidates) == 1:
                    original_sequences = original_map[candidates[0]]
                else:
                    row_mismatches.append({
                        "identity": identity, "page": page,
                        "kind": "row_missing_on_page",
                        "merged": sequences[0][:6],
                    })
                    continue
            for index, numbers in enumerate(sequences):
                if index >= len(original_sequences):
                    row_mismatches.append({
                        "identity": identity, "page": page,
                        "kind": "row_extra_in_merged",
                        "merged": numbers[:6],
                    })
                    continue
                original_numbers = original_sequences[index]
                if numbers != original_numbers:
                    row_mismatches.append({
                        "identity": identity, "page": page,
                        "kind": ("numeric_row_sequence_mismatch" if identity == "#numeric"
                                 else "row_number_sequence_mismatch"),
                        "merged": numbers[:8], "original": original_numbers[:8],
                    })
            for extra in original_sequences[len(sequences):]:
                row_mismatches.append({
                    "identity": identity, "page": page,
                    "kind": "row_missing_in_merged",
                    "original": extra[:8],
                })
    # R7-02：原件有、合并视图没有的数据行也必须报告（反向覆盖）。
    # 逐行对象由身份+序列比对承担——数值出现在同页其他行不满足本行，
    # 不再使用"页级存在性"作为 grounding（同页别处的同值不能算本行正确）。
    for page, original_map in original_by_page.items():
        merged_identities = merged_by_page.get(page) or {}
        for identity, sequences in original_map.items():
            if identity in merged_identities:
                continue
            for numbers in sequences:
                row_mismatches.append({
                    "identity": identity, "page": page,
                    "kind": "row_missing_in_merged",
                    "original": numbers[:8],
                })
    return {
        "merged_row_identities": sorted({
            identity for identities in merged_by_page.values() for identity in identities
        }),
        "row_mismatches": row_mismatches,
    }


def reconcile(pdf: str, anchor: str, page_spec: str) -> Dict[str, Any]:
    parser = PDFParser(None)
    artifact = parser.build_parse_artifact(pdf)

    with pdfplumber.open(pdf) as pdf_doc:
        page_texts = [(p.extract_text() or "") for p in pdf_doc.pages]
        page_tables = [extract_tables_from_page(p) for p in pdf_doc.pages]
        scoped_texts = _table_scoped_texts(pdf_doc)
        total_pages = len(page_texts)
    pages = _parse_pages(page_spec, total_pages)

    instances = _logical_tables_for_anchor(artifact, anchor)
    # 只保留页码范围与所查页相交的实例（锚点是子串时避免并表误入）
    instances = [
        inst for inst in instances
        if _instance_pages(doc_for_artifact(artifact, pdf, page_texts), inst) & set(pages)
    ]
    doc = doc_for_artifact(artifact, pdf, page_texts)
    merged_rows, view_errors = _logical_view_with_provenance(doc, instances)
    # 行归属页过滤：只对账落在所查页范围内的合并行
    merged_rows = [row for row in merged_rows if row["page"] in set(pages)]

    # 原件侧数字清单：只取解析出的表格行（排除表头、页码、年份、编码）
    semantic_roles, semantic_evidence = _combined_semantic_roles(
        doc, instances, merged_rows, page_texts, pages, page_tables)
    roles, known_codes = _learn_column_roles(merged_rows, semantic_roles)
    original_numbers: Dict[str, List[int]] = {}
    original_codes: Dict[str, List[int]] = {}
    for page in pages:
        for record in _parse_original_page(scoped_texts[page - 1], page, known_codes):
            for number in record["numbers"]:
                original_numbers.setdefault(number, []).append(page)
            for code in _codes_in_identity(record["identity"]):
                original_codes.setdefault(code, []).append(page)

    alignment = _reconcile_row_alignment(merged_rows, scoped_texts, pages, semantic_roles)
    identities = set(alignment["merged_row_identities"])
    merged_codes = {code for identity in identities for code in _codes_in_identity(identity)}
    missing_codes = {code: pages for code in original_codes if code not in merged_codes}
    merged_pages_by_code = {
        code: sorted(pages) for code in merged_codes}
    return {
        "input": {"pdf": pdf, "anchor": anchor, "pages": pages},
        "merged_instances": len(instances),
        "merged_rows": len(merged_rows),
        "original_number_kinds": len(original_numbers),
        "row_mismatches": alignment["row_mismatches"],
        "merged_view_errors": view_errors[:50],
        "missing_numbers": {},
        "extra_numbers": {},
        "missing_codes": missing_codes,
        "code_pages_in_merged": merged_pages_by_code,
        "semantic_evidence": semantic_evidence,
        "header_mismatches": list(semantic_evidence.get("header_mismatches") or []),
    }


def _instance_pages(doc: Any, instance: Any) -> set:
    """实例页跨度：优先 page_start/page_end（合并实例的片段 uid 不逐页登记）。"""
    start = getattr(instance, "page_start", None)
    end = getattr(instance, "page_end", None)
    if type(start) is int and type(end) is int and start > 0 and end >= start:
        return set(range(start, end + 1))
    fragments = {f.fragment_uid: f for f in getattr(doc.parse_artifact, "table_fragments", []) or []}
    pages: set = set()
    for fragment_uid in getattr(instance, "fragment_uids", None) or []:
        frag = fragments.get(fragment_uid)
        if frag is not None:
            pages.add(getattr(frag, "page_number", None))
    pages.discard(None)
    return pages


def doc_for_artifact(artifact: Any, pdf: str, page_texts: List[str]) -> Any:
    from src.engine.rules_v33 import build_document

    doc = build_document(pdf, page_texts, [], 0)
    doc.parse_artifact = artifact
    return doc


def _amount_cell_indices(cells: List[str], roles: Optional[Dict[int, str]] = None) -> List[int]:
    """金额格：数值列（含年份形态）中的数值；编码/款项目码列除外。"""
    indices: List[int] = []
    for index, cell in enumerate(cells):
        cleaned = _clean_number_token(cell)
        if not _NUM_TOKEN_RE.fullmatch(cleaned):
            continue
        if roles and roles.get(index) in ("code", "subcode"):
            continue
        if not roles and _is_code_token(cleaned):
            # 无角色信息时按旧形状规则排除 3~7 位整数（兼容入口）。
            continue
        indices.append(index)
    return indices


def self_test(pdf: str) -> Dict[str, Any]:
    """故障注入反例：把合并表两行的金额互换，逐行对齐必须拒绝。

    Codex 原注入试验（互换合计行与 201 类本年收入）在旧工具上 9/9 误
    PASS。这里对**同内容**的合并行做同样互换，要求 row_mismatches 非空；
    不修改原件、不写回分析结果。
    """
    parser = PDFParser(None)
    artifact = parser.build_parse_artifact(pdf)
    with pdfplumber.open(pdf) as pdf_doc:
        page_texts = [(p.extract_text() or "") for p in pdf_doc.pages]
        page_tables = [extract_tables_from_page(p) for p in pdf_doc.pages]
        scoped_texts = _table_scoped_texts(pdf_doc)
    doc = doc_for_artifact(artifact, pdf, page_texts)
    instance = next(
        (item for item in getattr(artifact, "logical_tables", []) or []
         if getattr(item, "table_code", "") == "FIN_02_income"),
        None,
    )
    if instance is None:
        instance = next(
            (item for item in getattr(artifact, "logical_tables", []) or []
             if not str(getattr(item, "table_code", "")).startswith("UNKNOWN")),
            None,
        )
    if instance is None:
        return {"ok": False, "reason": "no logical table instance"}
    pages_set = _instance_pages(doc, instance)
    merged_rows = [
        row for row in _merged_cells_text(doc, [instance]) if row["page"] in pages_set
    ]
    semantic_roles, semantic_evidence = _semantic_amount_roles(
        doc, instance, merged_rows, page_texts, sorted(pages_set), page_tables)
    roles, _codes = _learn_column_roles(merged_rows, semantic_roles)
    normal = _reconcile_row_alignment(merged_rows, scoped_texts, sorted(pages_set),
                                      semantic_roles)

    by_page: Dict[Any, List[Tuple[str, List[str], Dict[str, Any]]]] = {}
    for row in merged_rows:
        identity, numbers = _row_identity_and_numbers(row["cells"], roles)
        if identity and numbers:
            by_page.setdefault(row["page"], []).append((identity, numbers, row))
    pair = None
    for page, entries in by_page.items():
        if len(entries) < 2:
            continue
        totals = [item for item in entries if item[0] in ("合计", "总计")]
        codes = [item for item in entries if item[0].isdigit()]
        first, second = (
            (totals[0], codes[0]) if totals and codes else (entries[0], entries[1]))
        if first[1] != second[1]:
            pair = (page, first, second)
            break
    if pair is None:
        return {"ok": False, "reason": "no injectable row pair"}
    page, (identity_a, _numbers_a, _row_a), (identity_b, _numbers_b, _row_b) = pair

    injected = copy.deepcopy(merged_rows)
    row_a = next(row for row in injected if row["page"] == page
                 and _row_identity_and_numbers(row["cells"], roles)[0] == identity_a)
    row_b = next(row for row in injected if row["page"] == page
                 and _row_identity_and_numbers(row["cells"], roles)[0] == identity_b)
    indices_a = _amount_cell_indices(row_a["cells"], roles)
    indices_b = _amount_cell_indices(row_b["cells"], roles)
    for left, right in zip(indices_a, indices_b, strict=True):
        row_a["cells"][left], row_b["cells"][right] = row_b["cells"][right], row_a["cells"][left]
    mutated = _reconcile_row_alignment(injected, scoped_texts, sorted(pages_set),
                                       semantic_roles)
    detected = any(
        str(item.get("kind", "")).endswith("mismatch")
        for item in mutated["row_mismatches"]
    )
    return {
        "ok": (not normal["row_mismatches"]) and detected,
        "instance": getattr(instance, "table_code", None),
        "semantic_evidence": semantic_evidence,
        "pages": sorted(pages_set),
        "swap": {"page": page, "identity_a": identity_a, "identity_b": identity_b},
        "normal_mismatches": normal["row_mismatches"][:5],
        "injected_mismatches": mutated["row_mismatches"][:8],
        "detected": detected,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--anchor", default=None, help="表名锚点（如 收入决算表）；缺省配 --auto")
    ap.add_argument("--pages", default=None, help="如 7-11 或 12,13；缺省用实例自身页跨度")
    ap.add_argument("--auto", action="store_true", help="对全部逻辑表实例逐个对账")
    ap.add_argument("--self-test", action="store_true", help="互换两行金额的注入反例")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()
    if args.self_test:
        report = self_test(args.pdf)
    elif args.auto:
        report = reconcile_auto(args.pdf)
    else:
        if not args.anchor or not args.pages:
            ap.error("需要 --anchor + --pages，或使用 --auto / --self-test")
        report = reconcile(args.pdf, args.anchor, args.pages)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
    print(text[:8000])
    if args.self_test and not report.get("ok"):
        raise SystemExit(1)


def reconcile_auto(pdf: str) -> Dict[str, Any]:
    """对全部逻辑表实例逐个对账（无需手工页码）。"""
    parser = PDFParser(None)
    artifact = parser.build_parse_artifact(pdf)
    with pdfplumber.open(pdf) as pdf_doc:
        page_texts = [(p.extract_text() or "") for p in pdf_doc.pages]
        page_tables = [extract_tables_from_page(p) for p in pdf_doc.pages]
        scoped_texts = _table_scoped_texts(pdf_doc)
    doc = doc_for_artifact(artifact, pdf, page_texts)
    results = []
    for instance in getattr(artifact, "logical_tables", []) or []:
        pages_set = _instance_pages(doc, instance)
        if not pages_set:
            continue
        code = str(getattr(instance, "table_code", "") or "")
        if code.startswith("UNKNOWN"):
            # 未识别版式的目录/杂表页不参与数值对账（无表义）
            continue
        merged_rows, view_errors = _logical_view_with_provenance(doc, [instance])
        merged_rows = [row for row in merged_rows if row["page"] in pages_set]
        semantic_roles, semantic_evidence = _semantic_amount_roles(
            doc, instance, merged_rows, page_texts, sorted(pages_set), page_tables)
        header_mismatches = list(semantic_evidence.get("header_mismatches") or [])
        roles, known_codes = _learn_column_roles(merged_rows, semantic_roles)
        original_numbers: Dict[str, int] = {}
        original_codes: set = set()
        for page in sorted(pages_set):
            if not (1 <= page <= len(page_texts)):
                continue
            for record in _parse_original_page(scoped_texts[page - 1], page,
                                               known_codes):
                for number in record["numbers"]:
                    original_numbers[number] = original_numbers.get(number, 0) + 1
                original_codes.update(_codes_in_identity(record["identity"]))
        merged_numbers: Dict[str, int] = {}
        merged_codes: set = set()
        for row in merged_rows:
            identity, numbers = _row_identity_and_numbers(row["cells"], roles)
            if not identity or not numbers:
                continue
            for number in numbers:
                merged_numbers[number] = merged_numbers.get(number, 0) + 1
            merged_codes.update(_codes_in_identity(identity))
        missing = {
            value: pages_count
            for value, pages_count in original_numbers.items()
            if merged_numbers.get(value, 0) < pages_count
        }
        extra = {
            value: count
            for value, count in merged_numbers.items()
            if value not in original_numbers
        }
        alignment = _reconcile_row_alignment(
            merged_rows, scoped_texts, sorted(pages_set), semantic_roles)
        row_mismatches = alignment["row_mismatches"]
        results.append({
            "table_code": getattr(instance, "table_code", None),
            "title": str(getattr(instance, "title", "") or ""),
            "pages": sorted(pages_set),
            "rows": len(merged_rows),
            "semantic_evidence": semantic_evidence,
            "missing_numbers": missing,
            "extra_numbers": extra,
            "missing_codes": sorted(original_codes - merged_codes),
            "row_mismatches": row_mismatches[:20],
            "header_mismatches": header_mismatches,
            "merged_view_errors": view_errors[:50],
            "verdict": "PASS" if not (
                missing or extra or (original_codes - merged_codes)
                or row_mismatches or view_errors or header_mismatches
            ) else "GAP",
        })
    failed = [r for r in results if r["verdict"] == "GAP"]
    return {
        "input": {"pdf": pdf, "mode": "auto"},
        "instances": len(results),
        "pass": len(results) - len(failed),
        "gap": len(failed),
        "results": results,
    }


if __name__ == "__main__":
    main()
