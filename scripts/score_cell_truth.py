"""IR-18：按人工逐格真值统计整表、行、金额格召回与列身份正确率。

只统计 truth_source 为 human 或 t0_frozen 的样本；unannotated 样本不计入分母，
指标在分母为 0 时输出 null。系统候选来自当前解析器对同一年原件的重新解析，
解析快照缓存在 system/<sha12>.json，避免重复解析。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_TRUTH = ROOT / "outputs/intake_remediation_20261024/ir18_cell_truth/truth"
DEFAULT_CORPUS = Path.home() / "Desktop/决算测试上海市"
DEFAULT_T0_DIR = ROOT / "outputs/intake_remediation_20261024/t0"
DEFAULT_OUT = ROOT / "outputs/intake_remediation_20261024/ir18_cell_truth"
NON_NUMERIC = {"BLANK", "DASH", "NONE", "UNLABELED"}


def normalize(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def number(text: Any) -> Decimal | None:
    value = normalize(text).replace(",", "")
    if not re.fullmatch(r"-?(?:\d+(?:\.\d+)?)", value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_source(truth: dict[str, Any], corpus: Path, t0_dir: Path) -> Path | None:
    file = str(truth.get("file") or "")
    if truth.get("synthetic") or file.startswith("synthetic/"):
        path = t0_dir / file
    else:
        path = corpus / file
    return path if path.exists() else None


def build_snapshot(pdf_path: Path, cache_dir: Path) -> dict[str, Any]:
    cache = cache_dir / f"{sha256(pdf_path)[:12]}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    from src.services.manual_cell_overlay import _instance_mappings
    from src.services.pdf_parser import PDFParser

    artifact = PDFParser(None).build_parse_artifact(str(pdf_path))
    tables = []
    for table in artifact.table_fragments:
        instance = next(
            (item for item in artifact.logical_tables
             if table.fragment_uid in (getattr(item, "fragment_uids", []) or [])
             or table.fragment_uid == getattr(item, "instance_uid", None)),
            None,
        )
        mappings = _instance_mappings(artifact, table)
        tables.append({
            "fragment_uid": table.fragment_uid,
            "table_code": str(table.table_code or ""),
            "title": str(table.title or ""),
            "page_number": int(table.page_number),
            "page_start": int(getattr(instance, "page_start", table.page_number) or table.page_number),
            "page_end": int(getattr(instance, "page_end", table.page_number) or table.page_number),
            "rows": table.rows,
            "cells": [{
                "row_idx": cell.row_idx, "col_idx": cell.col_idx, "raw_text": cell.raw_text,
                "numeric_value": cell.numeric_value, "page_number": cell.page_number,
                "is_header": cell.is_header,
            } for cell in table.cells],
            "mappings": [{
                "source_col_idx": item.get("source_col_idx"),
                "canonical_measure": item.get("canonical_measure"),
            } for item in mappings],
        })
    snapshot = {"sha256": sha256(pdf_path), "file": str(pdf_path), "tables": tables}
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


def candidate_tables(snapshot: dict[str, Any], truth: dict[str, Any]) -> list[dict[str, Any]]:
    page = int(truth.get("physical_page") or 1)
    code = str(truth.get("table_code") or "")
    matches = []
    for table in snapshot.get("tables") or []:
        table_code = str(table.get("table_code") or "")
        # 续页常被系统标成 UNKNOWN；按同页片段评分，并在结果里记录系统表种。
        if table_code != code and not (truth.get("continuation") and table_code.startswith("UNKNOWN")):
            continue
        if not (int(table.get("page_start") or 0) <= page <= int(table.get("page_end") or 0)):
            continue
        matches.append(table)
    return matches


def find_row(table: dict[str, Any], code: str) -> tuple[int, list[Any]] | None:
    target = normalize(code)
    if target.lower() in {"total", "合计", "总计"}:
        for index, row in enumerate(table.get("rows") or []):
            if any(normalize(value) in {"合计", "总计"} for value in row):
                return index, row
        return None
    for index, row in enumerate(table.get("rows") or []):
        if any(normalize(value) == target for value in row):
            return index, row
    return None


def cell_at(table: dict[str, Any], row_idx: int, col_idx: int) -> dict[str, Any] | None:
    for cell in table.get("cells") or []:
        if int(cell.get("row_idx")) == row_idx and int(cell.get("col_idx")) == col_idx:
            return cell
    return None


def measure_column(table: dict[str, Any], measure: str) -> int | None:
    for mapping in table.get("mappings") or []:
        if str(mapping.get("canonical_measure")) == measure:
            try:
                return int(mapping.get("source_col_idx"))
            except (TypeError, ValueError):
                return None
    return None


def score_sample(truth: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    tables = candidate_tables(snapshot, truth)
    result = {
        "sample_id": truth.get("sample_id"), "t0_id": truth.get("t0_id"),
        "table_found": bool(tables),
        "system_table_code": tables[0].get("table_code") if tables else None,
        "row_total": 0, "row_hits": 0,
        "amount_total": 0, "amount_hits": 0, "blank_total": 0, "blank_hits": 0,
        "column_total": 0, "column_hits": 0, "notes": [],
    }
    if not tables:
        result["notes"].append("未找到同页同表种")
        return result
    table = tables[0]
    identity = truth.get("column_identity") or {}
    for measure, column in identity.items():
        result["column_total"] += 1
        if measure_column(table, str(measure)) == int(column):
            result["column_hits"] += 1
    for row in truth.get("rows") or []:
        code = str(row.get("code") or "")
        if not code:
            continue
        result["row_total"] += 1
        located = find_row(table, code)
        if located is None:
            continue
        result["row_hits"] += 1
        row_idx, _ = located
        for measure, value in (row.get("cells") or {}).items():
            text = str(value or "").strip()
            # UNLABELED 是“原件无表头的列”，没有列身份，不能按指标计入金额召回。
            if str(measure) == "UNLABELED" or text in {"NONE", "UNLABELED"}:
                continue
            column = measure_column(table, str(measure))
            cell = cell_at(table, row_idx, column) if column is not None else None
            if text in {"BLANK", "DASH"}:
                result["blank_total"] += 1
                if text == "BLANK" and (cell is None or not normalize(cell.get("raw_text"))):
                    result["blank_hits"] += 1
                elif text == "DASH" and cell is not None and "-" in normalize(cell.get("raw_text")):
                    result["blank_hits"] += 1
                continue
            result["amount_total"] += 1
            expected = number(text)
            actual = number(cell.get("raw_text")) if cell is not None else None
            if expected is not None and actual is not None and expected == actual:
                result["amount_hits"] += 1
    return result


def ratio(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--t0-dir", type=Path, default=DEFAULT_T0_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    truths = []
    for path in sorted(args.truth.glob("*.json")):
        truth = json.loads(path.read_text(encoding="utf-8"))
        if truth.get("truth_source") in {"human", "t0_frozen"}:
            truths.append(truth)
    cache_dir = args.out / "system"
    per_sample = []
    missing = []
    for truth in truths:
        source = resolve_source(truth, args.corpus, args.t0_dir)
        if source is None:
            missing.append(truth.get("sample_id"))
            continue
        snapshot = build_snapshot(source, cache_dir)
        per_sample.append(score_sample(truth, snapshot))
    totals = {
        "whole_table": (sum(1 for item in per_sample if item["table_found"]), len(per_sample)),
        "rows": (sum(item["row_hits"] for item in per_sample), sum(item["row_total"] for item in per_sample)),
        "amount_cells": (sum(item["amount_hits"] for item in per_sample), sum(item["amount_total"] for item in per_sample)),
        "blank_cells": (sum(item["blank_hits"] for item in per_sample), sum(item["blank_total"] for item in per_sample)),
        "column_identity": (sum(item["column_hits"] for item in per_sample), sum(item["column_total"] for item in per_sample)),
    }
    report = {
        "annotated_samples": len(truths),
        "scored_samples": len(per_sample),
        "missing_sources": missing,
        "metrics": {
            "whole_table_recall": ratio(*totals["whole_table"]),
            "row_recall": ratio(*totals["rows"]),
            "amount_cell_recall": ratio(*totals["amount_cells"]),
            "blank_dash_preservation": ratio(*totals["blank_cells"]),
            "column_identity_accuracy": ratio(*totals["column_identity"]),
        },
        "counts": {key: {"hits": value[0], "total": value[1]} for key, value in totals.items()},
        "per_sample": per_sample,
        "scope_note": (
            "只统计 truth_source=human/t0_frozen 的样本；unannotated 不计入分母。"
            "整表召回要求同页同表种存在；行召回按科目编码或合计标签匹配；"
            "金额格召回按列身份映射后比较数值；列身份只在真值提供列号时统计。"
        ),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "score.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("annotated_samples", "scored_samples", "metrics", "counts", "missing_sources")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
