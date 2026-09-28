#!/usr/bin/env python
"""Benchmark 批量重放（WP4-I S1 工具 2）。

遍历已登记语料（corpus/manifest.csv），逐份以**当前代码**纯规则模式执行
（与生产 legacy 完全同源：build_document + run_rules_with_outcomes，禁 AI、
确定性），产出 ``outputs/benchmark/<ts>/DOC-ID.json``：

- 顶层键 ``doc_id / sha256 / findings``（方案 §5 工具 2 兼容口径）；
- 同时落 ``legacy`` 子结构（findings/rule_counts/rule_execution_summary/
  elapsed_ms），使 ``evaluate_golden_corpus.evaluate(doc_id, replay_path)``
  可以直接消费（R8 P1 的 doc_id+SHA 双绑定在评测侧继续生效）；
- 逐份记录 resolved report_kind（文种识别准确率的数据源）、六态摘要与
  运行时义务账本（10 组分层完成率）。

输入装载复用 replay_golden_corpus 的 fail-closed 链：
有 PDF → 校验 SHA 与 manifest 登记值一致后解析；无 PDF（干净 checkout）
→ 走 golden.json 同源的 fixture 回退（拒绝静默错源）。

用法：
    python scripts/run_benchmark.py                       # 全部登记语料
    python scripts/run_benchmark.py --doc DOC-B1-001      # 指定材料
    python scripts/run_benchmark.py --corpus corpus --out-root outputs/benchmark
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.bench_register import engine_fingerprint, load_manifest  # noqa: E402
from scripts.replay_golden_corpus import (  # noqa: E402
    _load_corpus_inputs,
    load_page_tables,
    load_page_texts,
    sha256_file,
    _serialize_finding,
)


def _replay_one(
    doc_id: str,
    page_texts: List[str],
    page_tables: List[Any],
    expected_sha: str,
) -> Dict[str, Any]:
    """单份材料纯规则重放（生产 legacy 同源入口，禁 AI）。

    report_kind 交由引擎按 path/page_texts 自解析——resolved kind 要如实
    记录，作为「文种识别准确率」的观测数据，不由调用方钦定。
    """
    from src.engine.check_obligations import build_obligation_ledger
    from src.engine.pipeline import build_document, run_rules_with_outcomes
    from src.engine.rule_outcome import summarize_rule_outcomes
    from src.services.document_profile_resolver import resolve_document_profile

    doc = build_document(
        path=f"{doc_id}.pdf",
        page_texts=page_texts,
        page_tables=json.loads(json.dumps(page_tables)),
        filesize=0,
    )
    started = time.time()
    issues, outcomes = run_rules_with_outcomes(doc, use_ai_assist=False)
    elapsed_ms = int((time.time() - started) * 1000)

    findings = [_serialize_finding(issue) for issue in issues]
    summary = summarize_rule_outcomes(outcomes)
    resolved_kind = str(getattr(doc, "report_kind", "") or "")

    profile = resolve_document_profile(
        path=f"{doc_id}.pdf",
        page_texts=page_texts,
    )
    # 纯规则模式：AI 未请求（not_run），义务账本里 AI 背书义务如实记未执行
    ledger = build_obligation_ledger(
        profile,
        report_kind=resolved_kind or None,
        rule_execution_summary=summary,
        ai_execution={"status": "not_run", "requested": False},
        ai_required=False,
    )

    return {
        # —— 方案 §5 工具 2 的顶层兼容键 ——
        "doc_id": doc_id,
        "sha256": expected_sha,
        "findings": findings,
        # —— evaluator 兼容子结构（evaluate() 消费 replay["legacy"]）——
        "legacy": {
            "mode": "legacy",
            "finding_total": len(findings),
            "rule_counts": dict(Counter(f["rule"] for f in findings)),
            "findings": findings,
            "rule_execution_summary": summary,
            "elapsed_ms": elapsed_ms,
        },
        # —— WP4-I 特有观测面 ——
        # §6.7 引擎指纹：与 doc_id + sha256 一起构成可归因的最小绑定
        "engine_fingerprint": engine_fingerprint(),
        "report_kind_resolved": resolved_kind,
        "rule_execution_summary": summary,
        "obligation_ledger": ledger,
        "replayed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _load_doc_inputs(
    doc_dir: Path, expected_sha: str
) -> tuple:
    """语料输入装载：有 PDF 按 manifest SHA 校验后解析；无 PDF 走
    replay_golden_corpus 的 golden 同源 fixture 回退（其内部做 golden
    SHA 绑定，两路都拒绝静默错源）。"""
    pdf_candidates = sorted(
        (p for p in doc_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"),
        key=lambda item: item.name,
    ) if doc_dir.exists() else []
    if pdf_candidates:
        matches = [p for p in pdf_candidates if sha256_file(p) == expected_sha]
        if len(matches) != 1:
            raise SystemExit(
                f"{doc_dir.name}: PDF 候选 {len(pdf_candidates)} 个，按 manifest SHA "
                f"唯一匹配到 {len(matches)} 个——材料被替换或 manifest 过期（fail-closed）"
            )
        pdf_path = matches[0]
        return pdf_path, load_page_texts(pdf_path), load_page_tables(pdf_path)
    # 干净 checkout：无 PDF，走 golden 同源 fixture 回退（有 PDF 必有 golden
    # 的校验在该路径内部；无 PDF 时同样要求 golden 存在才允许回退）
    return _load_corpus_inputs(doc_dir)


def discover_corpus(manifest_path: Path) -> List[dict]:
    rows = load_manifest(manifest_path)
    if not rows:
        raise SystemExit(
            f"manifest 无登记行（{manifest_path}）——先运行 bench_register.py 登记语料"
        )
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="corpus", help="语料根目录（默认 corpus）")
    parser.add_argument("--doc", action="append", help="只重放指定 DOC-ID（可重复）")
    parser.add_argument(
        "--out-root", default="outputs/benchmark", help="重放产物根目录"
    )
    args = parser.parse_args(argv)

    corpus_dir = Path(args.corpus)
    if not corpus_dir.is_absolute():
        corpus_dir = _REPO_ROOT / corpus_dir
    manifest_path = corpus_dir / "manifest.csv"
    rows = discover_corpus(manifest_path)
    if args.doc:
        wanted = set(args.doc)
        rows = [row for row in rows if row.get("doc_id") in wanted]
        if not rows:
            raise SystemExit(f"--doc 未匹配到任何登记行: {sorted(wanted)}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_root = Path(args.out_root)
    if not out_root.is_absolute():
        out_root = _REPO_ROOT / out_root
    out_dir = out_root / timestamp
    out_dir.mkdir(parents=True, exist_ok=False)

    results = []
    failures: List[str] = []
    for row in rows:
        doc_id = str(row.get("doc_id") or "")
        doc_dir = corpus_dir / doc_id
        try:
            _, page_texts, page_tables = _load_doc_inputs(
                doc_dir, str(row.get("sha256") or "")
            )
            payload = _replay_one(
                doc_id, page_texts, page_tables, str(row.get("sha256") or "")
            )
        except SystemExit as exc:
            failures.append(f"{doc_id}: {exc}")
            continue
        except FileNotFoundError as exc:
            failures.append(f"{doc_id}: {exc}")
            continue
        out_path = out_dir / f"{doc_id}.json"
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            display_path = str(out_path.relative_to(_REPO_ROOT))
        except ValueError:  # 输出根在仓库外（测试/自定义目录）
            display_path = str(out_path)
        results.append(
            {
                "doc_id": doc_id,
                "replay": display_path,
                "findings": len(payload["findings"]),
                "report_kind_resolved": payload["report_kind_resolved"],
                "report_kind_true": row.get("report_kind_true"),
                "unresolved": payload["rule_execution_summary"]["unresolved_total"],
            }
        )
        print(
            f"[{doc_id}] findings={len(payload['findings'])} "
            f"kind={payload['report_kind_resolved'] or 'unknown'}/{row.get('report_kind_true')} "
            f"unresolved={payload['rule_execution_summary']['unresolved_total']}"
        )

    manifest_out = out_dir / "_run_manifest.json"
    manifest_out.write_text(
        json.dumps(
            {
                "timestamp": timestamp,
                "mode": "pure-rules (no AI, deterministic)",
                "docs": results,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    fp = engine_fingerprint()
    print(f"engine: rules_sha256={fp['rules_sha256'][:12]}… "
          f"head={fp['git_head'] or 'n/a'} dirty={fp['git_dirty']}")
    print(f"written: {out_dir} ({len(results)} docs, {len(failures)} failures)")
    return 1 if failures and not results else 0


if __name__ == "__main__":
    raise SystemExit(main())
