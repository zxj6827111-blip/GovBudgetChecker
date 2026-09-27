#!/usr/bin/env python
"""Benchmark 多文档聚合评测（WP4-I S1 工具 3）。

对 ``run_benchmark`` 的产物目录逐份调用 ``evaluate_golden_corpus.evaluate``
（命中判定口径完全复用：规则一致 + 页码 + 证据重叠 + 锚点约束 + 真值组
计分，R4–R9 语义零迁移），聚合输出 §4.2/§4.3 全部指标：

- Precision / Recall / FPR（操作性定义 FPR = 1 − Precision）+ FP 密度 +
  acceptable 负例违规；
- Rule Hit Rate、零触发规则清单、规则级 precision；
- 文种识别准确率（resolved vs manifest 登记真值）；
- 运行时义务完成率（按 10 个义务组分层，来自重放导出的 obligation ledger）；
- 规则六态分布；
- 分层下钻：report_kind × 子集 × 地区。

无 golden.json 的登记材料自动跳过（未标注 ≠ 失败），但 SHA 绑定失败等
评测硬错误会如实计入 failures，不做静默降级。指标快照默认落
``docs/baselines/bench1_metrics_<date>.json``（新增文件，不改旧 baseline）。

用法：
    python scripts/eval_benchmark.py --replay-dir outputs/benchmark/<ts>
    python scripts/eval_benchmark.py --replay-dir outputs/benchmark/<ts> \
        --markdown outputs/benchmark/<ts>/summary.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.bench_register import load_manifest  # noqa: E402
from scripts.evaluate_golden_corpus import evaluate  # noqa: E402


def _registry_codes() -> Dict[str, List[str]]:
    """按文种登记的规则码表（零触发清单与适用分母的依据）。"""
    from src.engine.budget_rules import ALL_BUDGET_RULES
    from src.engine.common_rules import ALL_COMMON_RULES
    from src.engine.rules_v33 import ALL_RULES as FINAL_ALL_RULES

    def _codes(rules: List[Any]) -> List[str]:
        codes = []
        for obj in rules:
            code = str(getattr(obj, "code", "") or "").strip()
            if code:
                codes.append(code)
        return codes

    return {
        "final": _codes(FINAL_ALL_RULES),
        "budget": _codes(ALL_BUDGET_RULES),
        "common": _codes(ALL_COMMON_RULES),
    }


def _load_replay_meta(replay_path: Path) -> Dict[str, Any]:
    payload = json.loads(replay_path.read_text(encoding="utf-8"))
    legacy = payload.get("legacy") or {}
    return {
        "doc_id": str(payload.get("doc_id") or replay_path.stem),
        "sha256": str(payload.get("sha256") or ""),
        "report_kind_resolved": str(payload.get("report_kind_resolved") or ""),
        "rule_execution_summary": payload.get("rule_execution_summary") or {},
        "obligation_ledger": payload.get("obligation_ledger") or {},
        # 无标注依赖的触发观测（零触发清单用；不等同于评测面的 rule_counts）
        "rule_counts": legacy.get("rule_counts") or {},
        "finding_total": int(legacy.get("finding_total")
                             or len(payload.get("findings") or [])),
    }


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def aggregate(doc_entries: List[Dict[str, Any]], registry: Dict[str, List[str]]) -> Dict[str, Any]:
    """把逐份评测报告聚合为 §4.2/§4.3 指标（纯函数，单测反例直接打这里）。"""
    evaluated = [e for e in doc_entries if e.get("eval_report")]
    unannotated = [e["doc_id"] for e in doc_entries if not e.get("eval_report")]

    tp = sum(r["tp"] for r in (e["eval_report"] for e in evaluated))
    fp = sum(r["fp"] for r in (e["eval_report"] for e in evaluated))
    fn = sum(r["fn"] for r in (e["eval_report"] for e in evaluated))
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    fpr = round(1 - precision, 4) if precision is not None else None
    fp_density = _safe_div(fp, len(evaluated))

    acceptable_violation_count = sum(
        len(r["acceptable_violations"]) for r in (e["eval_report"] for e in evaluated)
    )
    acceptable_annotation_count = 0
    for e in evaluated:
        golden_path = e.get("golden_path")
        if golden_path and Path(golden_path).exists():
            golden = json.loads(Path(golden_path).read_text(encoding="utf-8"))
            acceptable_annotation_count += sum(
                1 for lb in golden.get("labels", []) if lb.get("label") == "acceptable"
            )
    negative_violation_rate = (
        round(acceptable_violation_count / acceptable_annotation_count, 4)
        if acceptable_annotation_count
        else None
    )

    # —— 规则级：findings / TP（按 matched_rule）/ FP ——
    rule_stats: Dict[str, Dict[str, int]] = {}
    for e in evaluated:
        report = e["eval_report"]
        matched_rules = [
            str(item.get("matched_rule") or "")
            for item in report.get("matched", [])
        ] + [
            str(item.get("matched_rule") or "")
            for item in report.get("hint_hits", [])
        ]
        fp_rules = [
            str(item.get("rule") or "")
            for item in report.get("false_positive_details", [])
        ]
        for rule in matched_rules:
            rule_stats.setdefault(rule, {"findings": 0, "tp": 0, "fp": 0})["tp"] += 1
        for rule in fp_rules:
            rule_stats.setdefault(rule, {"findings": 0, "tp": 0, "fp": 0})["fp"] += 1
        for rule, count in (report.get("rule_counts") or {}).items():
            rule_stats.setdefault(rule, {"findings": 0, "tp": 0, "fp": 0})["findings"] += int(count)
    rule_level = {}
    for rule, stat in sorted(rule_stats.items()):
        rule_level[rule] = {
            **stat,
            "precision": _safe_div(stat["tp"], stat["tp"] + stat["fp"]),
        }

    # —— 零触发清单：按文种适用分母，重放 findings 里从未出现的规则码 ——
    # 触发观测不依赖标注：分母必须是**全部重放材料**。S1 首版误用 rule_level
    # （只统计已标注面），1/7 已标注时把「未标注材料上的触发」全记成零触发
    # （final 52 条），与本节语义相反。
    triggered: set = set()
    rule_trigger_counts: Dict[str, int] = {}
    for e in doc_entries:
        for rule, count in (e.get("replay_meta", {}).get("rule_counts") or {}).items():
            triggered.add(rule)
            rule_trigger_counts[rule] = rule_trigger_counts.get(rule, 0) + int(count)
    zero_trigger: Dict[str, List[str]] = {}
    kind_docs: Dict[str, int] = {}
    for e in doc_entries:
        kind = str(e.get("manifest", {}).get("report_kind_true") or "")
        kind_docs[kind] = kind_docs.get(kind, 0) + 1
    for kind, codes in registry.items():
        if kind == "common":
            applicable = sum(kind_docs.get(k, 0) for k in ("final", "budget", "unknown"))
        else:
            applicable = kind_docs.get(kind, 0)
        if applicable == 0:
            continue
        zero_trigger[kind] = [
            code for code in codes if code not in triggered
        ]

    # —— 文种识别准确率（resolved vs 登记真值；重放面独立于标注） ——
    kind_total = 0
    kind_correct = 0
    kind_mismatches = []
    for e in doc_entries:
        true_kind = str(e.get("manifest", {}).get("report_kind_true") or "")
        if not true_kind:
            continue
        kind_total += 1
        resolved = str(e.get("replay_meta", {}).get("report_kind_resolved") or "")
        if resolved == true_kind:
            kind_correct += 1
        else:
            kind_mismatches.append(
                {"doc_id": e["doc_id"], "resolved": resolved, "true": true_kind}
            )

    # —— 六态分布合计 ——
    status_distribution: Dict[str, int] = {}
    for e in doc_entries:
        summary = e.get("replay_meta", {}).get("rule_execution_summary") or {}
        for key in ("pass", "fail", "not_applicable", "insufficient_data",
                    "parse_error", "execution_error"):
            status_distribution[key] = status_distribution.get(key, 0) + int(summary.get(key) or 0)

    # —— 运行时义务完成率（按义务组分层合计） ——
    ledger_groups: Dict[str, Dict[str, int]] = {}
    ledger_totals = {"applicable": 0, "completed": 0, "unresolved": 0}
    for e in doc_entries:
        for group in (e.get("replay_meta", {}).get("obligation_ledger", {}).get("by_group") or []):
            gid = str(group.get("group_id") or "unknown")
            bucket = ledger_groups.setdefault(
                gid, {"applicable": 0, "completed": 0, "unresolved": 0}
            )
            bucket["applicable"] += int(group.get("applicable") or 0)
            bucket["completed"] += int(group.get("completed") or 0)
            bucket["unresolved"] += int(group.get("unresolved") or 0)
            ledger_totals["applicable"] += int(group.get("applicable") or 0)
            ledger_totals["completed"] += int(group.get("completed") or 0)
            ledger_totals["unresolved"] += int(group.get("unresolved") or 0)
    for gid, bucket in ledger_groups.items():
        bucket["completion_rate"] = _safe_div(bucket["completed"], bucket["applicable"])
    ledger_completion_rate = _safe_div(
        ledger_totals["completed"], ledger_totals["applicable"]
    )

    # —— 分层：subset / region（总体指标必须能下钻） ——
    def _stratify(key: str) -> Dict[str, Dict[str, Any]]:
        buckets: Dict[str, Dict[str, int]] = {}
        for e in evaluated:
            value = str(e.get("manifest", {}).get(key) or "unset")
            report = e["eval_report"]
            bucket = buckets.setdefault(value, {"docs": 0, "tp": 0, "fp": 0, "fn": 0})
            bucket["docs"] += 1
            bucket["tp"] += report["tp"]
            bucket["fp"] += report["fp"]
            bucket["fn"] += report["fn"]
        for bucket in buckets.values():
            bucket["precision"] = _safe_div(bucket["tp"], bucket["tp"] + bucket["fp"])
            bucket["recall"] = _safe_div(bucket["tp"], bucket["tp"] + bucket["fn"])
        return buckets

    return {
        "docs_total": len(doc_entries),
        "docs_evaluated": len(evaluated),
        "docs_unannotated": unannotated,
        "evaluated_doc_ids": [e["doc_id"] for e in evaluated],
        "failures": [e for e in doc_entries if e.get("error")],
        "totals": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "fpr_operational": fpr,
            "fp_density": fp_density,
            "acceptable_violations": acceptable_violation_count,
            "negative_violation_rate": negative_violation_rate,
            "hint_groups_hit": sum(
                int(r["hint_groups_hit"]) for r in (e["eval_report"] for e in evaluated)
            ),
            "hint_groups_total": sum(r["hint_groups_total"] for r in (e["eval_report"] for e in evaluated)),
        },
        "rule_level": rule_level,
        "zero_trigger_scope": "全部重放材料（无标注依赖）",
        "rule_trigger_counts": dict(sorted(rule_trigger_counts.items())),
        "zero_trigger_rules": zero_trigger,
        "kind_accuracy": {
            "total": kind_total,
            "correct": kind_correct,
            "rate": _safe_div(kind_correct, kind_total),
            "mismatches": kind_mismatches,
        },
        "status_distribution": status_distribution,
        "obligation_ledger": {
            "completion_rate": ledger_completion_rate,
            "totals": ledger_totals,
            "by_group": ledger_groups,
        },
        "stratified": {
            "by_subset": _stratify("subset"),
            "by_region": _stratify("region"),
            "by_report_kind_true": _stratify("report_kind_true"),
        },
    }


def build_doc_entries(replay_dir: Path, corpus_dir: Path, mode: str) -> List[Dict[str, Any]]:
    """装载重放产物 → 逐份评测（无 golden 跳过；SHA 绑定失败记 failures）。

    evaluate() 的 golden 路径取自其模块常量 CORPUS_DIR；为支持自定义语料根，
    调用期间把它对齐到本工具的 corpus_dir（不改 evaluator 文件、调用外
    恢复原值）。
    """
    from scripts import evaluate_golden_corpus as _egc

    manifest_rows = {row["doc_id"]: row for row in load_manifest(corpus_dir / "manifest.csv")}
    entries: List[Dict[str, Any]] = []
    original_corpus = _egc.CORPUS_DIR
    try:
        _egc.CORPUS_DIR = corpus_dir
        for replay_path in sorted(replay_dir.glob("DOC-*.json")):
            meta = _load_replay_meta(replay_path)
            doc_id = meta["doc_id"]
            entry: Dict[str, Any] = {
                "doc_id": doc_id,
                "replay_path": str(replay_path),
                "replay_meta": meta,
                "manifest": manifest_rows.get(doc_id, {}),
                "eval_report": None,
            }
            golden_path = corpus_dir / doc_id / "golden.json"
            if not golden_path.exists():
                entry["skipped"] = "未标注（无 golden.json）"
                entries.append(entry)
                continue
            entry["golden_path"] = str(golden_path)
            try:
                entry["eval_report"] = evaluate(doc_id, replay_path, mode=mode)
            except ValueError as exc:
                entry["error"] = str(exc)
            entries.append(entry)
    finally:
        _egc.CORPUS_DIR = original_corpus
    return entries


def render_markdown(report: Dict[str, Any]) -> str:
    """人读摘要（总体指标 + 义务组 + 规则级 precision 前列）。"""
    totals = report["totals"]
    lines = [
        "# WP4-I Benchmark 聚合报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 材料：{report['docs_evaluated']}/{report['docs_total']} 份已评测"
        f"（未标注 {len(report['docs_unannotated'])} 份）",
        f"- Precision：{totals['precision']}  Recall：{totals['recall']}  "
        f"FPR(1-P)：{totals['fpr_operational']}  FP密度：{totals['fp_density']}",
        f"- TP/FP/FN：{totals['tp']}/{totals['fp']}/{totals['fn']}",
        f"- 文种识别准确率：{report['kind_accuracy']['rate']}",
        f"- 义务完成率（运行时）：{report['obligation_ledger']['completion_rate']}",
        "",
        "## 义务组完成率",
        "",
        "| 组 | applicable | completed | 完成率 |",
        "|---|---|---|---|",
    ]
    for gid, bucket in sorted(report["obligation_ledger"]["by_group"].items()):
        lines.append(
            f"| {gid} | {bucket['applicable']} | {bucket['completed']} | "
            f"{bucket['completion_rate']} |"
        )
    lines += ["", "## 规则级 precision（有 TP/FP 记录的规则）", "", "| 规则 | findings | TP | FP | precision |", "|---|---|---|---|---|"]
    for rule, stat in report["rule_level"].items():
        lines.append(
            f"| {rule} | {stat['findings']} | {stat['tp']} | {stat['fp']} | {stat['precision']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-dir", required=True, help="run_benchmark 产物目录")
    parser.add_argument("--corpus", default="corpus", help="语料根目录")
    parser.add_argument("--mode", choices=("auto", "legacy", "structured"), default="auto")
    parser.add_argument(
        "--output",
        default=None,
        help="指标快照路径（默认 docs/baselines/bench1_metrics_<date>.json）",
    )
    parser.add_argument("--markdown", default=None, help="同时输出 Markdown 摘要")
    args = parser.parse_args()

    replay_dir = Path(args.replay_dir)
    if not replay_dir.is_absolute():
        replay_dir = _REPO_ROOT / replay_dir
    corpus_dir = Path(args.corpus)
    if not corpus_dir.is_absolute():
        corpus_dir = _REPO_ROOT / corpus_dir
    if not replay_dir.exists():
        raise SystemExit(f"重放产物目录不存在: {replay_dir}")

    entries = build_doc_entries(replay_dir, corpus_dir, args.mode)
    registry = _registry_codes()
    report = aggregate(entries, registry)
    report["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report["replay_dir"] = str(replay_dir)
    report["mode"] = args.mode

    output = Path(args.output) if args.output else (
        _REPO_ROOT / "docs" / "baselines"
        / f"bench1_metrics_{datetime.now().strftime('%Y%m%d')}.json"
    )
    if not output.is_absolute():
        output = _REPO_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"metrics -> {output}")

    if args.markdown:
        md_path = Path(args.markdown)
        if not md_path.is_absolute():
            md_path = _REPO_ROOT / md_path
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(report), encoding="utf-8")
        print(f"markdown -> {md_path}")

    totals = report["totals"]
    print(
        f"SUMMARY docs={report['docs_evaluated']}/{report['docs_total']} "
        f"P={totals['precision']} R={totals['recall']} FPR={totals['fpr_operational']} "
        f"FP密度={totals['fp_density']}"
    )
    # §4.5 门禁策略：第一期只观测不拦截——报警锚点仅打印
    if totals["precision"] is not None and totals["precision"] < 0.60:
        print("报警锚点：Precision < 0.60（L2 参考线）")
    if totals["recall"] is not None and totals["recall"] < 0.50:
        print("报警锚点：Recall < 0.50（L2 参考线）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
