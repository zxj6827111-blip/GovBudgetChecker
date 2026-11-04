#!/usr/bin/env python3
"""第二批 160 份非金标回归比较器（记录级完整指纹 + 阶段允许变化契约）。

与冻结 r5 工具的分工：
- r5 ``verify_pending_review.py`` 继续负责样本/环境/代码身份与 M/I 不变量；
- 本工具只回答“相对冻结组合基线，哪些业务事实变了、是否都在本段契约内”。
  不做 M 检查，不替代 r5 工具。

比较字段（存在即比，缺失不算变化）：
run / obligation_summary / obligations / coverage_items / receipts / findings
+ finding_bodies / todo / reading_items / same_source_occurrences / ledger_sources_equal

契约（--contract，先于业务修改冻结）：
{
  "allowed_classes": [
    {"id": "...", "kind": "finding_added|finding_removed|receipt_field|obligation_field|run_field|todo_field",
     "match": {"sample_ids": ["*"], "rule_prefixes": [...], "fields": [...],
               "before": "*", "after": "*", "message_contains": [...], "quote_contains": [...]},
     "basis": "方案条目/阶段目标"}
  ],
  "forbidden": ["amount_subcheck_closed", "unfinished_check_hidden", "status_regression"],
  "resolved_earlier": [{"target_id": "...", "sample_id": "...", "evidence": "..."}]
}

用法：
    .venv/bin/python scripts/batch2_regression_compare.py \
        --baseline <collection dir> --current <collection dir> \
        --contract <stage contract.json> --stage S2 --out <dir>
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MISSING = "<absent>"
UNRESOLVED = {"insufficient_data", "parse_error", "execution_error", "ai_not_run", "ai_failed"}


def read_json(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def load_records(root: Path) -> Dict[str, Dict[str, Any]]:
    records = {}
    for path in sorted((root / "records").glob("*.json.gz")):
        rec = read_json(path)
        records[str(rec.get("sample_id"))] = rec
    return records


def load_meta(root: Path) -> Dict[str, Any]:
    return read_json(root / "meta.json")


def flatten(value: Any, prefix: str = "") -> Dict[str, Any]:
    """把嵌套结构摊平成 path->值，便于逐字段定位。"""
    flat: Dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            flat.update(flatten(item, f"{prefix}/{key}"))
    elif isinstance(value, list):
        flat[prefix] = value
    else:
        flat[prefix] = value
    return flat


def field_diff(before: Any, after: Any, fields: Optional[Sequence[str]] = None) -> List[Tuple[str, Any, Any]]:
    left, right = flatten(before), flatten(after)
    diffs = []
    for path in sorted(set(left) | set(right)):
        a, b = left.get(path, MISSING), right.get(path, MISSING)
        if canonical(a) == canonical(b):
            continue
        # 新增可选字段在旧记录中缺失、新记录为 null：视为等价，不算业务变化。
        if (a is MISSING and b is None) or (a is None and b is MISSING):
            continue
        if fields and not any(path == f or path.startswith(f + "/") for f in fields):
            continue
        diffs.append((path, a, b))
    return diffs


def receipt_key(receipt: Dict[str, Any]) -> str:
    return "|".join(str(receipt.get(part) or "") for part in ("obligation_id", "rule_id", "sub_check_id"))


def index_receipts(record: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for receipt in record.get("receipts") or []:
        if isinstance(receipt, dict):
            indexed[receipt_key(receipt)] = receipt
    return dict(indexed)


def findings_of(record: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    bodies = record.get("finding_bodies") or {}
    result = {}
    for fp in record.get("findings") or []:
        result[fp] = bodies.get(fp) or {}
    return result


def classify(diff: Dict[str, Any], classes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hits = []
    for entry in classes:
        match = entry.get("match") or {}
        if entry.get("kind") != diff["kind"]:
            continue
        samples = match.get("sample_ids")
        if samples and "*" not in samples and diff["sample_id"] not in samples:
            continue
        prefixes = match.get("rule_prefixes") or []
        if prefixes and not any(str(diff.get("rule") or "").startswith(p) for p in prefixes):
            continue
        fields = match.get("fields") or []
        if fields and not any(diff.get("field", "").endswith(f) or diff.get("field", "") == f for f in fields):
            continue
        needles = match.get("message_contains") or []
        text = str(diff.get("message") or "") + str(diff.get("quote") or "")
        if needles and not all(n in text for n in needles):
            continue
        hits.append(entry)
    return hits


def forbidden_hits(base: Dict[str, Any], cur: Dict[str, Any]) -> List[str]:
    """结构性禁止项：不因条目存在而把未完成金额检查关掉/藏起来。"""
    problems = []
    base_rec, cur_rec = base, cur
    cur_obligations = cur_rec.get("obligations") or {}
    cur_receipts = index_receipts(cur_rec)
    for key, receipt in cur_receipts.items():
        oid, rule, sub = key.split("|")
        obligation = cur_obligations.get(oid) or {}
        status = str(receipt.get("status") or "")
        base_receipt = (index_receipts(base_rec) or {}).get(key) or {}
        base_status = str(base_receipt.get("status") or "")
        amount = str(receipt.get("amount_subcheck_status") or receipt.get("amount_check_status") or "")
        if status == "pass" and base_status and base_status in UNRESOLVED:
            problems.append(f"receipt_status_regression:{key}:{base_status}->pass")
        if amount in {"closed", "pass"} and str(obligation.get("status") or "") in UNRESOLVED:
            problems.append(f"amount_subcheck_closed_while_unresolved:{key}")
        if amount == "closed":
            problems.append(f"amount_subcheck_closed:{key}")
    for oid, obligation in cur_obligations.items():
        base_obj = (base_rec.get("obligations") or {}).get(oid) or {}
        if (str(obligation.get("status") or "") == "completed"
                and str(base_obj.get("status") or "") in UNRESOLVED):
            problems.append(f"obligation_status_regression:{oid}")
        reason = str(obligation.get("detail") or "")
        if "未查完" in reason and str(obligation.get("status") or "") == "completed":
            problems.append(f"unfinished_marked_completed:{oid}")
    return problems


def compare_record(sample_id: str, base: Dict[str, Any], cur: Dict[str, Any]) -> List[Dict[str, Any]]:
    diffs: List[Dict[str, Any]] = []

    def add(kind, field="", before=None, after=None, **extra):
        diffs.append({"sample_id": sample_id, "kind": kind, "field": field,
                      "before": before, "after": after, **extra})

    for kind, key in (("run_field", "run"), ("obligation_field", "obligations"),
                      ("coverage_field", "coverage_items"), ("todo_field", "todo")):
        before, after = base.get(key) or {}, cur.get(key) or {}
        if key in {"run", "todo"}:
            for path, a, b in field_diff(before, after):
                add(kind, field=path, before=a, after=b)
        else:
            for item_key in sorted(set(before) | set(after)):
                for path, a, b in field_diff(before.get(item_key) or {}, after.get(item_key) or {}):
                    add(kind, field=f"{item_key}{path}", before=a, after=b,
                        obligation_id=item_key)

    base_receipts, cur_receipts = index_receipts(base), index_receipts(cur)
    for key in sorted(set(base_receipts) | set(cur_receipts)):
        b, c = base_receipts.get(key), cur_receipts.get(key)
        if b is None:
            body = c or {}
            add("receipt_added", field="", before=MISSING, after=canonical(body),
                rule=body.get("rule_id"), obligation_id=body.get("obligation_id"))
            continue
        if c is None:
            add("receipt_removed", field="", before=canonical(b), after=MISSING,
                rule=b.get("rule_id"), obligation_id=b.get("obligation_id"))
            continue
        for path, a, value in field_diff(b, c):
            add("receipt_field", field=f"{key}{path}", before=a, after=value,
                rule=b.get("rule_id"), obligation_id=b.get("obligation_id"))

    base_findings, cur_findings = findings_of(base), findings_of(cur)
    for fp in sorted(set(cur_findings) - set(base_findings)):
        body = cur_findings[fp]
        add("finding_added", field="", before=MISSING, after=fp,
            rule=body.get("rule_id") or body.get("rule"), message=body.get("message"),
            quote="".join(str(q) for q in (body.get("quotes") or body.get("evidence") or [])))
    for fp in sorted(set(base_findings) - set(cur_findings)):
        body = base_findings[fp]
        add("finding_removed", field="", before=fp, after=MISSING,
            rule=body.get("rule_id") or body.get("rule"), message=body.get("message"))
    for fp in sorted(set(base_findings) & set(cur_findings)):
        for path, a, b in field_diff(base_findings[fp], cur_findings[fp]):
            add("finding_body", field=f"{fp}{path}", before=a, after=b,
                rule=base_findings[fp].get("rule_id") or base_findings[fp].get("rule"))

    for path, a, b in field_diff({"items": base.get("reading_items") or []},
                                 {"items": cur.get("reading_items") or []}):
        add("reading_field", field=path, before=a, after=b)
    for path, a, b in field_diff({"items": base.get("same_source_occurrences") or []},
                                 {"items": cur.get("same_source_occurrences") or []}):
        add("same_source_field", field=path, before=a, after=b)
    if canonical(base.get("ledger_sources_equal")) != canonical(cur.get("ledger_sources_equal")):
        add("ledger_field", field="ledger_sources_equal",
            before=base.get("ledger_sources_equal"), after=cur.get("ledger_sources_equal"))
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--mode", choices=["self", "stage"], default="stage")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    base_dir, cur_dir, out = Path(args.baseline).resolve(), Path(args.current).resolve(), Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"输出目录已存在且非空：{out}")
    out.mkdir(parents=True, exist_ok=True)
    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    classes = contract.get("allowed_classes") or []

    base_records, cur_records = load_records(base_dir), load_records(cur_dir)
    checks: List[Dict[str, Any]] = []

    def add(check_id, title, ok, details=()):
        checks.append({"id": check_id, "title": title, "verdict": "PASS" if ok else "FAIL",
                       "count": len(details), "details": list(details)[:50]})

    sample_diffs: Dict[str, List[Dict[str, Any]]] = {}
    for sample in sorted(set(base_records) | set(cur_records)):
        if sample not in base_records or sample not in cur_records:
            sample_diffs[sample] = [{"sample_id": sample, "kind": "sample_missing",
                                     "field": "", "before": sample in base_records,
                                     "after": sample in cur_records}]
            continue
        sample_diffs[sample] = compare_record(sample, base_records[sample], cur_records[sample])

    add("C1", "样本集合与文档指纹一致",
        set(base_records) == set(cur_records)
        and all(base_records[s].get("sha256") == cur_records[s].get("sha256")
                for s in set(base_records) & set(cur_records)),
        [s for s in set(base_records) | set(cur_records)
         if s not in base_records or s not in cur_records
         or base_records[s].get("sha256") != cur_records[s].get("sha256")])

    all_diffs = [d for diffs in sample_diffs.values() for d in diffs]
    unexplained, explained, used_classes = [], [], defaultdict(int)
    for diff in all_diffs:
        if args.mode == "self":
            unexplained.append(f"{diff['sample_id']}:{diff['kind']}:{diff.get('field','')}")
            continue
        hits = classify(diff, classes)
        if hits:
            top_priority = max(hit.get("priority", 0) for hit in hits)
            top_hits = [hit for hit in hits if hit.get("priority", 0) == top_priority]
        else:
            top_hits = []
        if len(top_hits) == 1:
            explained.append({**diff, "class_id": top_hits[0]["id"]})
            used_classes[top_hits[0]["id"]] += 1
        elif len(top_hits) > 1:
            unexplained.append(f"{diff['sample_id']}:{diff['kind']}:{diff.get('field','')}:匹配多个允许类")
        else:
            unexplained.append(f"{diff['sample_id']}:{diff['kind']}:{diff.get('field','')}")

    add("C2", "所有变化都命中唯一允许类", not unexplained, unexplained)
    add("C3", "禁止项：状态回退/未完成金额检查被关闭或隐藏",
        not [p for p in (forbidden_hits(base_records[s], cur_records[s])
                         for s in set(base_records) & set(cur_records)) for p in p], [])
    forbidden = [p for s in sorted(set(base_records) & set(cur_records))
                 for p in forbidden_hits(base_records[s], cur_records[s])]
    checks[-1]["details"] = forbidden[:50]
    checks[-1]["count"] = len(forbidden)
    checks[-1]["verdict"] = "PASS" if not forbidden else "FAIL"

    unused = [c["id"] for c in classes if c.get("required", False) and used_classes.get(c["id"], 0) == 0]
    add("C4", "声明为必用的允许类均已出现", not unused, unused)
    add("C5", "自比模式零差异", not (args.mode == "self" and all_diffs),
        [f"{d['sample_id']}:{d['kind']}" for d in all_diffs[:50]])

    base_meta, cur_meta = load_meta(base_dir), load_meta(cur_dir)
    add("C6", "画像/输入清单身份一致（代码身份另由 r5 M 检查）",
        canonical(base_meta.get("profile")) == canonical(cur_meta.get("profile"))
        and canonical(base_meta.get("samples")) == canonical(cur_meta.get("samples")))

    overall = "PASS" if all(c["verdict"] == "PASS" for c in checks) else "FAIL"
    summary = {
        "stage": args.stage, "mode": args.mode,
        "baseline": str(base_dir), "current": str(cur_dir),
        "samples": {"baseline": len(base_records), "current": len(cur_records)},
        "diffs": {"total": len(all_diffs), "explained": len(explained), "unexplained": len(unexplained)},
        "class_usage": dict(used_classes),
        "checks": checks, "overall": overall,
        "unexplained": unexplained[:200],
    }
    (out / "batch2_regression_compare.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with (out / "changes.jsonl").open("w", encoding="utf-8") as handle:
        for item in explained + [{"sample_id": d.split(":")[0], "unexplained": d} for d in unexplained]:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(json.dumps({"overall": overall, "stage": args.stage, "mode": args.mode,
                      "diffs": summary["diffs"],
                      "checks": {c["id"]: c["verdict"] for c in checks}}, ensure_ascii=False, indent=1))
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
