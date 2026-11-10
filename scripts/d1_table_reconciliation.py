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
import json
import re
import sys
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
    """合并逻辑表的行文本（含页归属），供逐格对账。"""
    fragments = {f.fragment_uid: f for f in getattr(doc.parse_artifact, "table_fragments", []) or []}
    rows: List[Dict[str, Any]] = []
    for instance in instances:
        for fragment_uid in getattr(instance, "fragment_uids", None) or []:
            frag = fragments.get(fragment_uid)
            if frag is None:
                continue
            for row in getattr(frag, "rows", []) or []:
                rows.append({
                    "page": getattr(frag, "page_number", None),
                    "cells": [str(c or "") for c in row],
                })
    return rows


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

    merged_numbers: Dict[str, int] = {}
    merged_codes: set = set()
    merged_pages_by_code: Dict[str, set] = {}
    for row in merged_rows:
        page = row["page"]
        for cell_text in row["cells"]:
            canonical = _canonical_number(str(cell_text))
            if canonical is not None and re.search(r"\d", str(cell_text)):
                merged_numbers[canonical] = merged_numbers.get(canonical, 0) + 1
            for code_match in _CODE_RE.finditer(_norm(cell_text)):
                code = code_match.group(1)
                if len(code) >= 3:
                    merged_codes.add(code)
                    merged_pages_by_code.setdefault(code, set()).add(page)

    missing_numbers = {
        value: pages_found
        for value, pages_found in original_numbers.items()
        if merged_numbers.get(value, 0) < len(pages_found)
        # 原件出现次数多于合并表覆盖次数 → 至少一格未进入合并表
    }
    extra_numbers = {
        value: count
        for value, count in merged_numbers.items()
        if value not in original_numbers
    }
    missing_codes = {
        code: pages_found
        for code, pages_found in original_codes.items()
        if code not in merged_codes
    }
    return {
        "input": {"pdf": pdf, "anchor": anchor, "pages": pages},
        "merged_instances": len(instances),
        "merged_rows": len(merged_rows),
        "original_number_kinds": len(original_numbers),
        "missing_numbers": missing_numbers,
        "extra_numbers": extra_numbers,
        "missing_codes": missing_codes,
        "code_pages_in_merged": {
            code: sorted(pages) for code, pages in sorted(merged_pages_by_code.items())
        },
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--anchor", default=None, help="表名锚点（如 收入决算表）；缺省配 --auto")
    ap.add_argument("--pages", default=None, help="如 7-11 或 12,13；缺省用实例自身页跨度")
    ap.add_argument("--auto", action="store_true", help="对全部逻辑表实例逐个对账")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()
    if args.auto:
        report = reconcile_auto(args.pdf)
    else:
        if not args.anchor or not args.pages:
            ap.error("需要 --anchor + --pages，或使用 --auto")
        report = reconcile(args.pdf, args.anchor, args.pages)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
    print(text[:8000])


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
        results.append({
            "table_code": getattr(instance, "table_code", None),
            "title": str(getattr(instance, "title", "") or ""),
            "pages": sorted(pages_set),
            "rows": len(merged_rows),
            "missing_numbers": missing,
            "extra_numbers": extra,
            "missing_codes": sorted(original_codes - merged_codes),
            "verdict": "PASS" if not (missing or extra or (original_codes - merged_codes)) else "GAP",
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
