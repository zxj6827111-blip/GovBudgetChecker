#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Golden Corpus 重放脚本：用当前解析器和规则真正重跑受保护语料 PDF。

与历史回放 `scripts/replay_analysis.py` 的分工：
- replay_analysis.py 只统计**旧产物**（历史任务的结果 JSON），做结构性指标校验；
- 本脚本用**当前代码**真实执行解析 + 规则（可选 AI），输出逐规则数量，
  用于整改后的样张验收与历史抽检对比。

铁律：只写 `outputs/`，绝不修改 `corpus/` 与历史任务目录（uploads/）。

用法:
    python scripts/replay_golden_corpus.py                      # 全部语料
    python scripts/replay_golden_corpus.py --doc DOC-20260905-001
    python scripts/replay_golden_corpus.py --parse-mode shadow  # legacy+structured 对比

解析三态开关（与生产一致）：
    legacy     现有规则路径（run_rules_with_outcomes）
    structured 结构化解析输入路径（阶段3 迁移规则，未迁移时与 legacy 等价并在
               结果里标注 structured_ready=false）
    shadow     两者都跑并输出逐规则差异对比
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS_DIR = ROOT / "corpus"
OUTPUT_DIR = ROOT / "outputs" / "golden_replay"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_page_texts(pdf_path: Path) -> List[str]:
    """与生产管线一致的页面文本抽取（api/main.py 同源函数）。"""
    import pdfplumber

    from src.services.pdf_page_extract import extract_visible_text_from_page

    texts: List[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            texts.append(extract_visible_text_from_page(page))
    return texts


def load_page_tables(pdf_path: Path) -> List[List[Any]]:
    import pdfplumber

    from src.services.pdf_page_extract import extract_tables_from_page

    tables: List[List[Any]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            tables.append(extract_tables_from_page(page))
    return tables


def run_legacy_rules(page_texts: List[str], page_tables: List[Any], report_kind: str) -> Dict[str, Any]:
    """当前规则路径（与生产 legacy 完全同源）。"""
    from src.engine.pipeline import build_document, run_rules_with_outcomes
    from src.engine.rule_outcome import summarize_rule_outcomes

    doc = build_document(
        path="",
        page_texts=page_texts,
        page_tables=page_tables,
        filesize=0,
    )
    started = time.time()
    issues, outcomes = run_rules_with_outcomes(doc, False, report_kind=report_kind)
    elapsed_ms = int((time.time() - started) * 1000)

    findings = []
    for issue in issues:
        findings.append(
            {
                "rule": str(getattr(issue, "rule", "")),
                "severity": str(getattr(issue, "severity", "")),
                "message": str(getattr(issue, "message", ""))[:200],
                "page": (getattr(issue, "location", {}) or {}).get("page"),
            }
        )
    rule_counts = dict(Counter(item["rule"] for item in findings))
    return {
        "mode": "legacy",
        "elapsed_ms": elapsed_ms,
        "finding_total": len(findings),
        "rule_counts": rule_counts,
        "findings": findings,
        "rule_execution_summary": summarize_rule_outcomes(outcomes),
    }


def run_structured_rules(page_texts: List[str], page_tables: List[Any], report_kind: str) -> Dict[str, Any]:
    """结构化解析输入路径。

    阶段3 的迁移规则（V33-115/117/120/202/203/220/241/243/244 首批）通过
    `src.engine.structured_rules` 提供的结构化输入执行；未迁移部分与
    legacy 同源。当前迁移批次尚未全部落地时如实标注 structured_ready=false。
    """
    from src.engine.pipeline import build_document
    from src.engine.rule_outcome import summarize_rule_outcomes
    from src.engine.structured_rules import (
        STRUCTURED_MIGRATED_RULES,
        run_structured_rules as _run,
    )

    doc = build_document(path="", page_texts=page_texts, page_tables=page_tables, filesize=0)
    started = time.time()
    issues, outcomes = _run(doc, report_kind=report_kind)
    elapsed_ms = int((time.time() - started) * 1000)
    findings = []
    for issue in issues:
        findings.append(
            {
                "rule": str(getattr(issue, "rule", "")),
                "severity": str(getattr(issue, "severity", "")),
                "message": str(getattr(issue, "message", ""))[:200],
                "page": (getattr(issue, "location", {}) or {}).get("page"),
            }
        )
    return {
        "mode": "structured",
        "structured_ready": bool(STRUCTURED_MIGRATED_RULES),
        "migrated_rules": sorted(STRUCTURED_MIGRATED_RULES),
        "elapsed_ms": elapsed_ms,
        "finding_total": len(findings),
        "rule_counts": dict(Counter(item["rule"] for item in findings)),
        "findings": findings,
        "rule_execution_summary": summarize_rule_outcomes(outcomes),
    }


def infer_report_kind(page_texts: List[str], doc_id: str) -> str:
    from src.engine.pipeline import _resolve_report_kind
    from src.engine.pipeline import build_document as _bd

    doc = _bd(path=f"{doc_id}.pdf", page_texts=page_texts, page_tables=[], filesize=0)
    return _resolve_report_kind(doc)


def normalize_rule_key(rule: str) -> str:
    """规则键归一化：legacy 与 structured 共用同一规则编号。

    规则编号本身形如 V33-001 / CMM-004（尾部数字是编号的一部分，
    不可截断），这里只做去空白与大写归一。
    """
    return unicodedata.normalize("NFKC", str(rule or "")).strip().upper()


def diff_legacy_structured(legacy: Dict[str, Any], structured: Dict[str, Any]) -> Dict[str, Any]:
    legacy_counts = {normalize_rule_key(k): v for k, v in legacy["rule_counts"].items()}
    structured_counts = {normalize_rule_key(k): v for k, v in structured["rule_counts"].items()}
    all_rules = sorted(set(legacy_counts) | set(structured_counts))
    return {
        "rules_changed": [
            {
                "rule": rule,
                "legacy": legacy_counts.get(rule, 0),
                "structured": structured_counts.get(rule, 0),
                "delta": structured_counts.get(rule, 0) - legacy_counts.get(rule, 0),
            }
            for rule in all_rules
            if legacy_counts.get(rule, 0) != structured_counts.get(rule, 0)
        ],
        "legacy_total": legacy["finding_total"],
        "structured_total": structured["finding_total"],
    }


def replay_doc(doc_dir: Path, parse_mode: str) -> Dict[str, Any]:
    pdf_path = next(
        (candidate for candidate in sorted(doc_dir.glob("*.pdf"))), None
    )
    if pdf_path is None:
        raise FileNotFoundError(f"no pdf in corpus doc dir: {doc_dir}")

    page_texts = load_page_texts(pdf_path)
    page_tables = load_page_tables(pdf_path)
    report_kind = infer_report_kind(page_texts, doc_dir.name)

    result: Dict[str, Any] = {
        "doc_id": doc_dir.name,
        "pdf": pdf_path.name,
        "sha256": sha256_file(pdf_path),
        "pages": len(page_texts),
        "report_kind": report_kind,
        "replay_ts": time.time(),
        "parse_mode": parse_mode,
    }

    if parse_mode == "legacy":
        result["legacy"] = run_legacy_rules(page_texts, page_tables, report_kind)
    elif parse_mode == "structured":
        result["structured"] = run_structured_rules(page_texts, page_tables, report_kind)
    elif parse_mode == "shadow":
        result["legacy"] = run_legacy_rules(page_texts, page_tables, report_kind)
        result["structured"] = run_structured_rules(page_texts, page_tables, report_kind)
        result["shadow_diff"] = diff_legacy_structured(
            result["legacy"], result["structured"]
        )
    else:
        raise ValueError(f"unknown parse mode: {parse_mode}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", default=None, help="只重放指定 DOC-ID（默认全部）")
    parser.add_argument(
        "--parse-mode",
        default="legacy",
        choices=["legacy", "structured", "shadow"],
        help="解析三态开关：legacy / structured / shadow",
    )
    args = parser.parse_args()

    if not CORPUS_DIR.exists():
        print(f"corpus dir not found: {CORPUS_DIR}", file=sys.stderr)
        return 1

    doc_dirs = (
        [CORPUS_DIR / args.doc]
        if args.doc
        else sorted(path for path in CORPUS_DIR.iterdir() if path.is_dir())
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    for doc_dir in doc_dirs:
        if not doc_dir.is_dir():
            continue
        try:
            result = replay_doc(doc_dir, args.parse_mode)
        except Exception as exc:  # noqa: BLE001 - 回放失败要留痕继续下一份
            result = {"doc_id": doc_dir.name, "error": f"{type(exc).__name__}: {exc}"}
            exit_code = 2
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_path = OUTPUT_DIR / f"{doc_dir.name}-{args.parse_mode}-{stamp}.json"
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary = result.get("legacy") or result.get("structured") or {}
        print(
            f"{doc_dir.name}: mode={args.parse_mode} "
            f"findings={summary.get('finding_total', '?')} -> {out_path.relative_to(ROOT)}"
        )
        if result.get("shadow_diff"):
            changed = result["shadow_diff"]["rules_changed"]
            print(f"  shadow 差异规则 {len(changed)} 条")
            for item in changed[:20]:
                print(
                    f"  {item['rule']}: legacy={item['legacy']} "
                    f"structured={item['structured']} (Δ{item['delta']})"
                )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
