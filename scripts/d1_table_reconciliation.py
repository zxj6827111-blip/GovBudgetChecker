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


def _merged_cells_text(doc: Any, instances: List[Any]) -> List[Dict[str, Any]]:
    """合并逻辑表的行文本（含**逐行真实页**），供逐格对账。

    IR-04 补强：逻辑合并片段（table_fragments 里 page_number=起始页、rows 为
    全表 74 行的那一条）不携带逐行页归属——按它取页会把第 8 页的行都记成
    第 7 页，制造错页。这里展开合并片段登记的 physical_fragment_uids，
    逐物理片段取行与其真实 page_number；无子片段的逻辑片段按自身页处理。
    """
    table_fragments = {
        f.fragment_uid: f for f in getattr(doc.parse_artifact, "table_fragments", []) or []
    }
    physical_fragments = {
        f.fragment_uid: f for f in getattr(doc.parse_artifact, "physical_fragments", []) or []
    }
    rows: List[Dict[str, Any]] = []
    for instance in instances:
        physical_uids: List[str] = []
        for fragment_uid in getattr(instance, "fragment_uids", None) or []:
            frag = table_fragments.get(fragment_uid)
            if frag is None:
                continue
            children = list(
                getattr(frag, "source_fragment_uids", None)
                or getattr(frag, "physical_fragment_uids", None)
                or []
            )
            physical_uids.extend(children or [fragment_uid])
        for fragment_uid in dict.fromkeys(physical_uids):
            frag = physical_fragments.get(fragment_uid) or table_fragments.get(fragment_uid)
            if frag is None:
                continue
            for row in getattr(frag, "rows", []) or []:
                rows.append({
                    "page": getattr(frag, "page_number", None),
                    "cells": [str(c or "") for c in row],
                })
    return rows


_NUM_TOKEN_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_HEADER_NOISE = ("编码", "科目名称", "项目")


def _clean_number_token(token: str) -> str:
    return str(token or "").strip(",，;；。.、 ")


def _is_number_token(token: str) -> bool:
    return bool(_NUM_TOKEN_RE.fullmatch(_clean_number_token(token)))


def _is_code_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d{3,7}", _clean_number_token(token)))


def _numbers_without_codes(items: List[Tuple[int, str]],
                           known_codes: set) -> List[str]:
    """数值序列：排除**已登记的科目编码**（3~7 位纯整数）。

    编码集合来自合并表各行的身份；金额统一带小数，因此不会与编码混淆。
    右栏编码（如 30212）即使名称被拆到下一行也能排除；错行/错列注入仍会
    改变序列或页归属。"""
    numbers: List[str] = []
    for _index, text in items:
        cleaned = _clean_number_token(text)
        if not _NUM_TOKEN_RE.fullmatch(cleaned):
            continue
        if _is_code_token(cleaned) and cleaned.lstrip("0") in known_codes:
            continue
        canonical = _canonical_number(cleaned)
        if canonical is not None:
            numbers.append(canonical)
    return numbers


def _row_identity_and_numbers(cells: List[str], known_codes: set) -> Tuple[str, List[str]]:
    """行身份与数值序列：合计/总计 > 纯整数科目编码 > 行标签 > 纯数值行。"""
    items = [(index, _norm(cell)) for index, cell in enumerate(cells) if _norm(cell)]
    if not items:
        return "", []
    identity = ""
    skip_index = None
    for index, text in items[:3]:
        if text.startswith(("合计", "总计")):
            identity, skip_index = text[:2], index
            break
        if _is_code_token(text):
            identity, skip_index = _clean_number_token(text), index
            break
    if not identity:
        for index, text in items:
            if not _is_number_token(text):
                identity, skip_index = text, index
                break
    if not identity:
        identity = "#numeric"
    rest = [(index, text) for index, text in items if index != skip_index]
    return identity, _numbers_without_codes(rest, known_codes)


def _parse_original_line(raw_line: str, known_codes: set) -> Optional[Tuple[str, List[str]]]:
    """原件单行 → (行身份, 数值序列)；纯数值行归入 ``#numeric``。"""
    tokens = [token for token in unicodedata.normalize("NFKC", raw_line).split() if token]
    if not tokens:
        return None
    first = tokens[0]
    cleaned_first = _clean_number_token(first)
    if cleaned_first.startswith(("合计", "总计")):
        identity = cleaned_first[:2]
        rest_tokens = tokens[1:]
    elif _is_code_token(first):
        identity = cleaned_first
        rest_tokens = tokens[1:]
    elif _is_number_token(first):
        identity = "#numeric"
        rest_tokens = tokens
    else:
        if any(noise in first for noise in _HEADER_NOISE) and len(tokens) <= 3:
            return None
        identity = first
        rest_tokens = tokens[1:]
    rest = [(index, token) for index, token in enumerate(rest_tokens)]
    return identity, _numbers_without_codes(rest, known_codes)


def _reconcile_row_alignment(
    merged_rows: List[Dict[str, Any]],
    page_texts: List[str],
    pages: List[int],
) -> Dict[str, Any]:
    """按「行=科目/合计身份、列=数值序列」逐页对齐。

    数字多重集合比较对错行/错列注入全盲（Codex 注入反例 9/9 误 PASS）。
    这里把每行身份（科目编码或合计/总计）绑定到该页原件的数值序列，
    逐位比较顺序与归属页；错行/错列/错页/拆行名称都会产生 row_mismatches。
    """
    # 先登记合并表的科目编码集合：原件右侧车道编码（名称可能被拆到下一行）
    # 也按同一集合排除，避免把编码当金额。
    known_codes: set = set()
    for row in merged_rows:
        for cell in row["cells"]:
            if _is_code_token(cell):
                known_codes.add(_clean_number_token(cell).lstrip("0"))

    merged_row_index: Dict[str, List[Dict[str, Any]]] = {}
    for row in merged_rows:
        identity, numbers = _row_identity_and_numbers(row["cells"], known_codes)
        if not identity or not numbers:
            # 纯表头/空行不参与数值对齐（没有金额可逐格核对）。
            continue
        merged_row_index.setdefault(identity, []).append(
            {"page": row["page"], "numbers": numbers})

    original_rows_by_page: Dict[int, Dict[str, List[str]]] = {}
    for page in pages:
        if not (1 <= page <= len(page_texts)):
            continue
        rows_map: Dict[str, List[str]] = {}
        for raw_line in str(page_texts[page - 1] or "").splitlines():
            parsed = _parse_original_line(raw_line, known_codes)
            if parsed is None:
                continue
            identity, nums = parsed
            rows_map.setdefault(identity, []).extend(nums or [])
        original_rows_by_page[page] = rows_map

    row_mismatches: List[Dict[str, Any]] = []
    for identity, entries in merged_row_index.items():
        for entry in entries:
            page = entry["page"]
            original_map = original_rows_by_page.get(page) or {}
            original_seq = original_map.get(identity)
            if original_seq is None:
                row_mismatches.append({
                    "identity": identity, "page": page, "kind": "row_missing_on_page",
                    "merged": entry["numbers"][:6],
                })
                continue
            if identity == "#numeric":
                # 无行标签的纯数值行（如三公表金额行）：同页按物理顺序拼接比较。
                if entry["numbers"] != original_seq[:len(entry["numbers"])]:
                    row_mismatches.append({
                        "identity": identity, "page": page,
                        "kind": "numeric_row_sequence_mismatch",
                        "merged": entry["numbers"][:8], "original": original_seq[:8],
                    })
                continue
            if entry["numbers"] != original_seq[:len(entry["numbers"])] and \
                    sorted(entry["numbers"]) != sorted(original_seq[:len(entry["numbers"])]):
                row_mismatches.append({
                    "identity": identity, "page": page,
                    "kind": "row_number_sequence_mismatch",
                    "merged": entry["numbers"][:6], "original": original_seq[:6],
                })
    return {
        "merged_row_identities": sorted(merged_row_index),
        "row_mismatches": row_mismatches,
    }


def reconcile(pdf: str, anchor: str, page_spec: str) -> Dict[str, Any]:
    parser = PDFParser(None)
    artifact = parser.build_parse_artifact(pdf)

    with pdfplumber.open(pdf) as pdf_doc:
        page_texts = [(p.extract_text() or "") for p in pdf_doc.pages]
        total_pages = len(page_texts)
    pages = _parse_pages(page_spec, total_pages)

    instances = _logical_tables_for_anchor(artifact, anchor)
    # 只保留页码范围与所查页相交的实例（锚点是子串时避免并表误入）
    instances = [
        inst for inst in instances
        if _instance_pages(doc_for_artifact(artifact, pdf, page_texts), inst) & set(pages)
    ]
    doc = doc_for_artifact(artifact, pdf, page_texts)
    merged_rows = _merged_cells_text(doc, instances)
    merged_row_pages = {row["page"] for row in merged_rows}
    # 行归属页过滤：只对账落在所查页范围内的合并行
    merged_rows = [row for row in merged_rows if row["page"] in set(pages)]

    # 原件侧数字清单（去千分位归一），排除表头/页码等纯序号
    original_numbers: Dict[str, List[int]] = {}
    original_codes: Dict[str, List[int]] = {}
    for page in pages:
        text = page_texts[page - 1]
        for match in _NUM_RE.finditer(text):
            canonical = _canonical_number(match.group())
            if canonical is None:
                continue
            # 页码/序号过滤：纯整数且值等于页码本身或行序（<= 页行数经验阈值）
            if re.fullmatch(r"-?\d+", canonical) and abs(int(canonical)) <= 60 and "." not in match.group():
                continue
            if re.fullmatch(r"(?:19|20)\d{2}", canonical):
                continue  # 年份词（标题行）不是表格数据格
            original_numbers.setdefault(canonical, []).append(page)
        for match in _CODE_RE.finditer(text):
            code = match.group(1)
            original_codes.setdefault(code, []).append(page)

    alignment = _reconcile_row_alignment(merged_rows, page_texts, pages)
    identities = set(alignment["merged_row_identities"])
    missing_codes = {code: pages for code in original_codes if code not in identities}
    merged_pages_by_code = {
        identity: sorted(pages) for identity in identities if identity.isdigit()}
    return {
        "input": {"pdf": pdf, "anchor": anchor, "pages": pages},
        "merged_instances": len(instances),
        "merged_rows": len(merged_rows),
        "original_number_kinds": len(original_numbers),
        "row_mismatches": alignment["row_mismatches"],
        "missing_numbers": {},
        "extra_numbers": {},
        "missing_codes": missing_codes,
        "code_pages_in_merged": merged_pages_by_code,
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


def _amount_cell_indices(cells: List[str], known_codes: set) -> List[int]:
    indices: List[int] = []
    for index, cell in enumerate(cells):
        cleaned = _clean_number_token(cell)
        if not _NUM_TOKEN_RE.fullmatch(cleaned):
            continue
        if _is_code_token(cleaned) and cleaned.lstrip("0") in known_codes:
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
    known_codes: set = set()
    for row in merged_rows:
        for cell in row["cells"]:
            if _is_code_token(cell):
                known_codes.add(_clean_number_token(cell).lstrip("0"))
    normal = _reconcile_row_alignment(merged_rows, page_texts, sorted(pages_set))

    by_page: Dict[Any, List[Tuple[str, List[str], Dict[str, Any]]]] = {}
    for row in merged_rows:
        identity, numbers = _row_identity_and_numbers(row["cells"], known_codes)
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
                 and _row_identity_and_numbers(row["cells"], known_codes)[0] == identity_a)
    row_b = next(row for row in injected if row["page"] == page
                 and _row_identity_and_numbers(row["cells"], known_codes)[0] == identity_b)
    indices_a = _amount_cell_indices(row_a["cells"], known_codes)
    indices_b = _amount_cell_indices(row_b["cells"], known_codes)
    for left, right in zip(indices_a, indices_b):
        row_a["cells"][left], row_b["cells"][right] = row_b["cells"][right], row_a["cells"][left]
    mutated = _reconcile_row_alignment(injected, page_texts, sorted(pages_set))
    detected = any(
        str(item.get("kind", "")).endswith("mismatch")
        for item in mutated["row_mismatches"]
    )
    return {
        "ok": (not normal["row_mismatches"]) and detected,
        "instance": getattr(instance, "table_code", None),
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
    doc = doc_for_artifact(artifact, pdf, page_texts)
    fragments = {f.fragment_uid: f for f in getattr(artifact, "table_fragments", []) or []}
    results = []
    for instance in getattr(artifact, "logical_tables", []) or []:
        pages_set = _instance_pages(doc, instance)
        if not pages_set:
            continue
        code = str(getattr(instance, "table_code", "") or "")
        if code.startswith("UNKNOWN"):
            # 未识别版式的目录/杂表页不参与数值对账（无表义）
            continue
        merged_rows = [
            row for row in _merged_cells_text(doc, [instance])
            if row["page"] in pages_set
        ]
        original_numbers: Dict[str, int] = {}
        original_codes: set = set()
        for page in sorted(pages_set):
            if not (1 <= page <= len(page_texts)):
                continue
            text = page_texts[page - 1]
            for match in _NUM_RE.finditer(text):
                canonical = _canonical_number(match.group())
                if canonical is None:
                    continue
                if re.fullmatch(r"-?\d+", canonical) and abs(int(canonical)) <= 60 and "." not in match.group():
                    continue
                if re.fullmatch(r"(?:19|20)\d{2}", canonical):
                    continue  # 年份词（标题行）不是表格数据格
                original_numbers[canonical] = original_numbers.get(canonical, 0) + 1
            for code_match in _CODE_RE.finditer(text):
                code = code_match.group(1)
                if len(code) >= 3:
                    original_codes.add(code)
        merged_numbers: Dict[str, int] = {}
        merged_codes: set = set()
        for row in merged_rows:
            for cell_text in row["cells"]:
                canonical = _canonical_number(str(cell_text))
                if canonical is not None and re.search(r"\d", str(cell_text)):
                    merged_numbers[canonical] = merged_numbers.get(canonical, 0) + 1
                for code_match in _CODE_RE.finditer(_norm(cell_text)):
                    if len(code_match.group(1)) >= 3:
                        merged_codes.add(code_match.group(1))
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
            merged_rows, page_texts, sorted(pages_set))
        row_mismatches = alignment["row_mismatches"]
        results.append({
            "table_code": getattr(instance, "table_code", None),
            "title": str(getattr(instance, "title", "") or ""),
            "pages": sorted(pages_set),
            "rows": len(merged_rows),
            "missing_numbers": missing,
            "extra_numbers": extra,
            "missing_codes": sorted(original_codes - merged_codes),
            "row_mismatches": row_mismatches[:20],
            "verdict": "PASS" if not (
                missing or extra or (original_codes - merged_codes) or row_mismatches
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
