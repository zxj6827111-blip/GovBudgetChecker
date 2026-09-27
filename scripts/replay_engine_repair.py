#!/usr/bin/env python
"""引擎修复重放：4 份冻结决算样张上的 unresolved 与 findings 基线/回归取证。

「查得准」A 档改造（2026-09-27）的量化验收需要修复前后的可比数据：
- 每份样张跑全量规则（决算专用 + 通用），统计六态中未完成态
  （insufficient_data / parse_error / execution_error）的规则清单与原因；
- 统计正式 findings 总数（验收行：宜川 34 → ≤12、文旅局级联 4 → 0）；
- 输出 JSON 供 tests/test_engine_repair_20260927.py 的 _estimate_before/_after
  对照与 docs/baselines 快照使用。

只读冻结夹具（tests/fixtures/*_page_data.json），不解析 PDF、不调 AI、
不写任何既有产物目录；--output 必须显式指定。

用法：
    python scripts/replay_engine_repair.py --output outputs/engine_repair/baseline.json
    python scripts/replay_engine_repair.py --print-summary
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engine.pipeline import build_document, run_rules_with_outcomes  # noqa: E402
from src.engine.rule_outcome import summarize_rule_outcomes  # noqa: E402

#: 4 份冻结决算样张（A 档方案 MR-1 的回归语料）。
REPLAY_FIXTURES: List[tuple] = [
    ("yichuan", "tests/fixtures/cross_san_gong_truth_page_data.json"),
    ("shiquan", "tests/fixtures/shiquan_narrative_truth_page_data.json"),
    ("wenlv", "tests/fixtures/wenlv_narrative_truth_page_data.json"),
    ("shengtaihuanjing", "tests/fixtures/sample_page_data.json"),
]


def _load_fixture(rel_path: str) -> Dict[str, Any]:
    path = _REPO_ROOT / rel_path
    if not path.exists():
        raise SystemExit(f"冻结夹具缺失: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def replay_one(alias: str, rel_path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    doc = build_document(
        path=f"{payload.get('doc_id', alias)}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload["page_tables"])),
        filesize=0,
    )
    # 4 份样张均为决算公开材料；显式指定避免夹具 doc_id 影响文种判定
    issues, outcomes = run_rules_with_outcomes(doc, use_ai_assist=False, report_kind="final")
    summary = summarize_rule_outcomes(outcomes)

    sev_counts = {"error": 0, "warn": 0, "info": 0}
    for issue in issues:
        sev = str(getattr(issue, "severity", "") or "").lower()
        if sev in sev_counts:
            sev_counts[sev] += 1

    return {
        "doc_id": payload.get("doc_id"),
        "fixture": rel_path,
        "findings_total": len(issues),
        "findings_by_severity": sev_counts,
        "unresolved_total": summary["unresolved_total"],
        "unresolved_rules": summary["unresolved_rules"],
        "outcome_stats": {
            key: summary[key]
            for key in (
                "total_rules", "executed", "pass", "fail", "not_applicable",
                "insufficient_data", "parse_error", "execution_error",
                "partial_findings_total",
            )
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="输出 JSON 路径（必须显式指定）")
    parser.add_argument("--print-summary", action="store_true", help="同时打印摘要")
    args = parser.parse_args()

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = _REPO_ROOT / out_path
    resolved = out_path.resolve()
    # 度量产物不得写回夹具/语料目录，避免污染被度量数据
    for forbidden in ("tests/fixtures", "corpus"):
        if forbidden in str(resolved):
            raise SystemExit(f"输出路径不允许落在受保护目录: {resolved}")

    report: Dict[str, Any] = {"documents": [], "totals": {}}
    for alias, rel in REPLAY_FIXTURES:
        payload = _load_fixture(rel)
        report["documents"].append(replay_one(alias, rel, payload))

    report["totals"] = {
        "unresolved_total": sum(d["unresolved_total"] for d in report["documents"]),
        "findings_total": sum(d["findings_total"] for d in report["documents"]),
    }
    report["fixture_shas"] = {
        rel: hashlib.sha256((_REPO_ROOT / rel).read_bytes()).hexdigest()
        for _, rel in REPLAY_FIXTURES
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.print_summary:
        for d in report["documents"]:
            print(f"[{d['doc_id']}] findings={d['findings_total']} {d['findings_by_severity']} unresolved={d['unresolved_total']}")
            for u in d["unresolved_rules"]:
                reasons = "; ".join(str(r) for r in (u.get("unresolved_reasons") or [])[:2])
                print(f"    {u.get('rule_id', '?'):<32} {u.get('status', '?'):<18} {reasons[:90]}")
        print(f"TOTAL unresolved={report['totals']['unresolved_total']} findings={report['totals']['findings_total']}")
    else:
        print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
