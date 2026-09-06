#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Golden Corpus 评测脚本：用人工标注计算 TP/FP/FN 与质量指标。

输入：
- corpus/<DOC-ID>/golden.json   人工标注（defect / rounding_hint /
  manual_review / acceptable 四类，含 rule_id、页码、expected_severity）
- outputs/golden_replay/<DOC-ID>-<mode>-<ts>.json  重放产物
  （scripts/replay_golden_corpus.py 生成）

指标（供 P0/P1 门禁使用）：
- TP：标注缺陷（defect）有对应 finding 命中（规则匹配 + 页码匹配）；
- FP：正式 finding 中未命中任何 defect 标注的条目；
- FN：defect 标注未被任何 finding 命中；
- 严重度准确率：TP 中 severity 与 expected_severity 一致的比例；
- 页码准确率：TP 中页码一致的比例；
- 证据可定位率：正式 finding 中证据含页码的比例（门禁要求 100%）；
- acceptable 违规：acceptable 负例页上不得出现任何 finding（如 T7/P28）。

只写 `outputs/`，绝不修改 corpus/ 与历史任务目录。

用法:
    python scripts/evaluate_golden_corpus.py --doc DOC-20260905-001 \
        --replay outputs/golden_replay/DOC-20260905-001-legacy-xxx.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS_DIR = ROOT / "corpus"
OUTPUT_DIR = ROOT / "outputs" / "golden_eval"

# severity 归一：finding 的 severity 词表 → 标注词表
_SEVERITY_ALIASES = {
    "high": "high",
    "error": "high",
    "critical": "high",
    "medium": "medium",
    "warn": "medium",
    "warning": "medium",
    "info": "info",
    "hint": "info",
    "low": "info",
    "manual_review": "manual_review",
}


def _norm_severity(value: Any) -> str:
    return _SEVERITY_ALIASES.get(str(value or "").strip().lower(), str(value or "").lower())


def _page_of(finding: Dict[str, Any]) -> Optional[int]:
    page = finding.get("page")
    if page is None:
        location = finding.get("location")
        if isinstance(location, dict):
            page = location.get("page")
    try:
        return int(page) if page is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 内容重叠校验（GPT5.6 P1-4 + review 🟡1 修正）
#
# 此前匹配只看规则+页码，"规则和页码正确但正文完全无关、evidence 为空"
# 的 finding 也会被判为命中。此后 TP 判定还需 evidence 内容重叠：
# 标注 evidence 与 finding 的 evidence_text/message 至少共享一个
# 归一化数字 token，或一个足够长的归一化文本片段。片段窗口随标注长度
# 退化（min(6, len)）——短于 6 字的标注按全串比对，避免"空表说明"这类
# 短证据永远匹配不上的盲区（review 🟡1）。
# ---------------------------------------------------------------------------

def _normalize_for_overlap(text: Any) -> str:
    """去掉空白与标点，只保留字母数字与 CJK 字符，统一小写。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "").lower())


def _numeric_tokens(text: Any) -> set:
    """提取数字 token（金额/年份/编码），归一化去千分位。"""
    raw = str(text or "").replace(",", "")
    return {t for t in re.findall(r"\d+(?:\.\d+)?", raw) if len(t) >= 2}


def evidence_overlaps(annotation_evidence: Any, finding: Dict[str, Any]) -> bool:
    """标注证据与 finding 证据是否内容重叠（数字或长文本片段）。"""
    ann_norm = _normalize_for_overlap(annotation_evidence)
    if not ann_norm:
        # 标注本身没写 evidence：退回规则+页码匹配（不惩罚标注侧的缺失）
        return True
    finding_text = " ".join(
        str(finding.get(key) or "") for key in ("evidence_text", "message")
    )
    find_norm = _normalize_for_overlap(finding_text)
    if not find_norm:
        return False
    # 1) 数字 token 交集（金额/页码位/编码是最直接的共同证据）
    ann_nums = _numeric_tokens(annotation_evidence)
    find_nums = _numeric_tokens(finding_text)
    if ann_nums and ann_nums & find_nums:
        return True
    # 2) 归一化片段重叠：窗口取 min(6, 标注长度)，短标注按全串比对
    window = min(6, len(ann_norm))
    if window <= 0:
        return False
    for i in range(0, len(ann_norm) - window + 1):
        if ann_norm[i : i + window] in find_norm:
            return True
    return False


def match_annotation(
    annotation: Dict[str, Any],
    findings: List[Dict[str, Any]],
    consumed: set,
) -> Optional[Dict[str, Any]]:
    """标注 ↔ finding 匹配：规则一致（或标注无规则时只看页码）+ 页码一致
    + evidence 内容重叠（GPT5.6 P1-4）。

    每条 finding 只能被一条 defect 标注消费（consumed 集合去重），
    避免同页同规则的多条标注命中同一条 finding。
    优先返回重叠度最高的 finding（数字交集 > 文本片段），使"最贴近
    原始数字的证据"优先被消费。
    """
    rule_id = str(annotation.get("rule_id") or "").strip().upper()
    page = annotation.get("page")
    annotation_evidence = annotation.get("evidence")
    candidates: List[Tuple[int, Dict[str, Any]]] = []
    for finding in findings:
        if id(finding) in consumed:
            continue
        if page is not None and _page_of(finding) != page:
            continue
        if rule_id:
            finding_rule = str(finding.get("rule") or "").strip().upper()
            if finding_rule != rule_id:
                continue
        if not evidence_overlaps(annotation_evidence, finding):
            continue
        # 重叠度打分：数字 token 交集数优先，其次共享文本片段长度
        finding_text = " ".join(
            str(finding.get(key) or "") for key in ("evidence_text", "message")
        )
        shared_nums = len(
            _numeric_tokens(annotation_evidence) & _numeric_tokens(finding_text)
        )
        ann_norm = _normalize_for_overlap(annotation_evidence)
        find_norm = _normalize_for_overlap(finding_text)
        shared_text = 0
        for size in range(min(len(ann_norm), len(find_norm)), 5, -1):
            if ann_norm[:size] in find_norm or any(
                ann_norm[i : i + size] in find_norm
                for i in range(0, len(ann_norm) - size + 1)
            ):
                shared_text = size
                break
        candidates.append((shared_nums * 100 + shared_text, finding))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def evaluate(doc_id: str, replay_path: Path) -> Dict[str, Any]:
    golden_path = CORPUS_DIR / doc_id / "golden.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))

    run = replay.get("legacy") or replay.get("structured") or {}
    findings: List[Dict[str, Any]] = run.get("findings") or []

    labels = golden.get("labels", [])
    defects = [lb for lb in labels if lb.get("label") == "defect"]
    hints = [
        lb for lb in labels
        if lb.get("label") in ("rounding_hint", "manual_review")
    ]
    acceptable = [lb for lb in labels if lb.get("label") == "acceptable"]

    matched: List[Dict[str, Any]] = []
    missed: List[Dict[str, Any]] = []
    hint_matched: List[Dict[str, Any]] = []
    hint_missed: List[Dict[str, Any]] = []
    matched_finding_ids: set = set()

    for annotation in defects:
        finding = match_annotation(annotation, findings, matched_finding_ids)
        if finding is None:
            missed.append(annotation)
        else:
            matched_finding_ids.add(id(finding))
            matched.append(
                {
                    "annotation_id": annotation.get("annotation_id"),
                    "rule_id": annotation.get("rule_id"),
                    "expected_page": annotation.get("page"),
                    "expected_severity": annotation.get("expected_severity"),
                    "actual_severity": _norm_severity(finding.get("severity")),
                    "severity_ok": _norm_severity(finding.get("severity"))
                    == _norm_severity(annotation.get("expected_severity")),
                    "page_ok": _page_of(finding) == annotation.get("page"),
                }
            )

    for annotation in hints:
        finding = match_annotation(annotation, findings, matched_finding_ids)
        if finding is None:
            hint_missed.append(annotation)
        else:
            matched_finding_ids.add(id(finding))
            hint_matched.append(
                {
                    "annotation_id": annotation.get("annotation_id"),
                    "rule_id": annotation.get("rule_id"),
                    "expected_page": annotation.get("page"),
                    "expected_severity": annotation.get("expected_severity"),
                    "actual_severity": _norm_severity(finding.get("severity")),
                    "page_ok": _page_of(finding) == annotation.get("page"),
                }
            )

    tp = len(matched)
    fn = len(missed)
    # FP：未被任何标注（缺陷或预期提示）消费的 finding——提示级预期输出
    # 不是误报（HANDOFF §2 三档真值模型）
    fp = sum(1 for finding in findings if id(finding) not in matched_finding_ids)

    severity_ok = [item for item in matched if item["severity_ok"]]
    page_ok = [item for item in matched if item["page_ok"]]

    # 证据可定位率：正式 finding 需带页码
    locatable = [f for f in findings if _page_of(f) is not None]
    locatable_rate = round(len(locatable) / len(findings), 4) if findings else None

    # acceptable 负例违规：负例页上不得出现任何 finding
    acceptable_violations = []
    for annotation in acceptable:
        page = annotation.get("page")
        hits = [f for f in findings if page is not None and _page_of(f) == page]
        if hits:
            acceptable_violations.append(
                {
                    "annotation_id": annotation.get("annotation_id"),
                    "page": page,
                    "finding_count": len(hits),
                    "rules": sorted({str(f.get("rule")) for f in hits}),
                }
            )

    precision = round(tp / (tp + fp), 4) if (tp + fp) else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) else None

    return {
        "doc_id": doc_id,
        "replay": str(replay_path.relative_to(ROOT)) if replay_path.is_relative_to(ROOT) else str(replay_path),
        "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "finding_total": len(findings),
        "rule_counts": run.get("rule_counts", {}),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "hint_total": len(hints),
        "hint_matched": len(hint_matched),
        "hint_missed_count": len(hint_missed),
        "severity_accuracy": round(len(severity_ok) / tp, 4) if tp else None,
        "page_accuracy": round(len(page_ok) / tp, 4) if tp else None,
        "locatable_evidence_rate": locatable_rate,
        "acceptable_violations": acceptable_violations,
        "matched": matched,
        "hint_hits": hint_matched,
        "missed": [
            {
                "annotation_id": item.get("annotation_id"),
                "rule_id": item.get("rule_id"),
                "page": item.get("page"),
                "evidence": item.get("evidence"),
            }
            for item in missed
        ],
        "hint_missed": [
            {
                "annotation_id": item.get("annotation_id"),
                "rule_id": item.get("rule_id"),
                "page": item.get("page"),
                "evidence": item.get("evidence"),
            }
            for item in hint_missed
        ],
        "false_positive_details": [
            {
                "rule": str(f.get("rule")),
                "page": _page_of(f),
                "message": str(f.get("message"))[:120],
            }
            for f in findings
            if id(f) not in matched_finding_ids
        ],
    }


def check_gates(report: Dict[str, Any]) -> List[str]:
    """门禁检查（整改验收口径，非 Golden Corpus 20 份正式门禁）。"""
    failures: List[str] = []
    if report["fp"] != 0:
        failures.append(f"FP={report['fp']}，要求 0（样张 52 条误报必须清零）")
    if report["fn"] != 0:
        failures.append(f"FN={report['fn']}，硬问题召回要求 3/3")
    if report["precision"] is not None and report["precision"] < 0.95:
        failures.append(f"精确率 {report['precision']} < 0.95")
    if report["recall"] is not None and report["recall"] < 0.98:
        failures.append(f"召回率 {report['recall']} < 0.98")
    if report["locatable_evidence_rate"] not in (None, 1.0):
        failures.append(f"证据可定位率 {report['locatable_evidence_rate']} < 1.0")
    if report["acceptable_violations"]:
        failures.append(
            f"acceptable 负例出现 {len(report['acceptable_violations'])} 处违规 finding"
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", required=True, help="语料 DOC-ID")
    parser.add_argument("--replay", required=True, help="重放产物 JSON 路径")
    args = parser.parse_args()

    replay_path = Path(args.replay)
    if not replay_path.is_absolute():
        replay_path = ROOT / replay_path
    if not replay_path.exists():
        print(f"replay file not found: {replay_path}", file=sys.stderr)
        return 1

    report = evaluate(args.doc, replay_path)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = OUTPUT_DIR / f"{args.doc}-eval-{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"TP={report['tp']} FP={report['fp']} FN={report['fn']} "
          f"precision={report['precision']} recall={report['recall']}")
    print(f"hint命中 {report['hint_matched']}/{report['hint_total']} "
          f"severity_accuracy={report['severity_accuracy']} "
          f"page_accuracy={report['page_accuracy']} "
          f"locatable_evidence_rate={report['locatable_evidence_rate']}")
    if report["hint_missed"]:
        print(f"hint_missed: {report['hint_missed']}")
    if report["acceptable_violations"]:
        print(f"acceptable violations: {report['acceptable_violations']}")
    if report["missed"]:
        print(f"missed: {report['missed']}")
    gate_failures = check_gates(report)
    if gate_failures:
        for failure in gate_failures:
            print(f"GATE-FAIL: {failure}")
        print(f"report -> {out_path.relative_to(ROOT)}")
        return 2
    print("GATE-PASS")
    print(f"report -> {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
