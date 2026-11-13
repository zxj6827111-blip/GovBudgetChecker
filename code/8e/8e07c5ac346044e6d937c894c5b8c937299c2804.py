#!/usr/bin/env python3
"""非金标回归：验收包 v1-regression-r4 的 160 份材料，新旧代码逐条比对。

- 用与金标相同的条目提取口径（gold_baseline_match.load_system），只把 docs 换成样本身份；
- 新旧条目按规则 + 页码 + 原文/引文对齐，不按展示序号；
- 减少条目必须能归入六类之一；新增正式条目一律列出；
- 抽样 20 条减少条目，附 PDF 页码与原文引文，供回 PDF 复核。

用法：.venv/bin/python outputs/fp_batch1_20261103/scripts/nongold_compare.py \
        <old_run> <new_run> <out_dir>
"""
from __future__ import annotations

import csv
import gzip
import json
import random
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))
import gold_baseline_match as gm  # noqa: E402


def read_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def norm(text: str) -> str:
    text = re.sub(r"\s+", "", text or "")
    return re.sub(r"^[一二三四五六七八九十百]+、", "", text)


def message_equal(left: str, right: str) -> bool:
    """去展示序号后相等；消息在 600 字处截断，序号长度差 1 会挪动截断尾，允许短串是长串前缀。"""
    a, b = norm(left), norm(right)
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return len(long) - len(short) <= 2 and long.startswith(short)


def similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    if a["doc"] != b["doc"] or a["tier"] != b["tier"] or a["rule"] != b["rule"]:
        return 0.0
    if a["pages"] != b["pages"]:
        return 0.0
    message = SequenceMatcher(None, norm(a["message"]), norm(b["message"])).ratio()
    quote_a, quote_b = norm("".join(a.get("quotes") or [])), norm("".join(b.get("quotes") or []))
    quote = SequenceMatcher(None, quote_a, quote_b).ratio() if (quote_a or quote_b) else 1.0
    if message == 1.0 and quote == 1.0:
        return 1.0
    return 0.85 * message + 0.15 * quote


def align(old: List[Dict[str, Any]], new: List[Dict[str, Any]]):
    pool = {item["id"]: item for item in new}
    pairs, old_only, pending = [], [], []
    for o in old:
        exact = [n for n in pool.values() if similarity(o, n) == 1.0]
        if len(exact) == 1:
            pairs.append((o, exact[0], "exact"))
            pool.pop(exact[0]["id"])
        else:
            pending.append(o)
    for o in pending:
        scored = sorted(((similarity(o, n), n) for n in pool.values()), key=lambda x: -x[0])
        scored = [(s, n) for s, n in scored if s >= 0.90]
        if scored:
            s, n = scored[0]
            pairs.append((o, n, f"similar:{s:.3f}"))
            pool.pop(n["id"])
        else:
            old_only.append(o)
    return pairs, old_only, list(pool.values())


def classify(item: Dict[str, Any], status: Dict[str, Any]) -> str:
    """把减少条目归入六类；归不进去返回空串。"""
    rule, message = item["rule"], item.get("message") or ""
    year = status.get("report_year")
    scope = ((status.get("document_profile") or {}).get("writing_scope") or {}).get("value")
    if rule == "CMM-013" and "正文标题缺少“第二部分”" in message:
        return "D2-正文第二部分" if year and year >= 2023 and scope == "department" else ""
    if rule == "CMM-021" and "一般公共预算财政拨款三公经费支出决算表" in message:
        return "D2-2022三公表名" if year == 2022 else ""
    if rule == "CMM-013" and "目录与正文标题不一致" in message:
        return "R3-标题措辞"
    if rule == "CMM-007" and "同比零基数百分比无定义" in message:
        return "R26-零比零"
    if rule == "V33-TREND-COMPLETION-RATE" and ("分母为0" in message or "没有数学定义" in message):
        return "R26-零比零"
    if rule == "CMM-007" and "增减额与同比百分比复算不一致" in message:
        return "R16-舍入区间"
    if rule == "V33-234" and ("未说明原因" in message or "差异原因仅重述结果" in message):
        return "R30-泛泛原因"
    return ""


def load_items(run: Path, sample_ids: List[str]) -> List[Dict[str, Any]]:
    """与 gold_baseline_match.load_system 相同的条目提取，但 todo 取自 records（验收包不含 queue_counts）。"""
    items: List[Dict[str, Any]] = []
    for sid in sample_ids:
        status = read_gz(run / "status" / f"{sid}.status.json.gz")
        todo = read_gz(run / "records" / f"{sid}.json.gz")["todo"]
        report = status["review_report"]
        for bucket in ("issues", "tips"):
            for it in report.get(bucket) or []:
                sev = str(it.get("severity") or "").strip().lower()
                if str(it.get("evidence_status") or "").strip() == gm.DEGRADED:
                    tier = "D"
                elif sev in gm.FORMAL_SEVERITIES:
                    tier = "F"
                elif it.get("review_kind") == "review_notice" or it.get("source") == "structured_ingest":
                    tier = "N"
                elif it.get("review_kind") != "manual_review" and sev != "manual_review":
                    tier = "R"
                else:
                    tier = "P"
                items.append(gm._row(sid, tier, it.get("id"), it.get("rule_id") or "", sev, it, bucket))
        for rec in report.get("manual_review") or []:
            rtype = rec.get("record_type")
            if rtype == "unfinished_check":
                continue
            if rtype in {"finding_review_candidate", "ai_candidate"}:
                finding = rec.get("finding") or {}
                tier = "D" if str(finding.get("evidence_status") or "").strip() == gm.DEGRADED else "P"
                row = gm._row(sid, tier, finding.get("id"), rec.get("code") or finding.get("rule_id") or "",
                              str(finding.get("severity") or ""), finding, "manual_review")
                row["title"] = rec.get("display_title") or row["title"]
                items.append(row)
            else:
                items.append({"id": f"{sid}|N|{rec.get('code')}", "doc": sid, "tier": "N", "rule": rec.get("code"),
                              "sev": "", "pages": [], "text": rec.get("message") or "",
                              "title": rec.get("display_title") or "", "message": (rec.get("message") or "")[:400],
                              "quotes": [], "bucket": "manual_review"})
        ledger = {i["obligation_id"]: i for i in status["check_coverage"]["instances"]}
        for oid in sorted(set(todo["blocking_ids"])):
            inst = ledger[oid]
            items.append({"id": f"{sid}|O|{oid}", "doc": sid, "tier": "O", "rule": oid, "sev": "", "pages": [],
                          "text": f"{inst.get('title')} {inst.get('detail')}", "title": inst.get("title") or "",
                          "message": (inst.get("detail") or "")[:400], "quotes": [], "bucket": "obligation",
                          "reason": inst.get("reason_label") or inst.get("reason") or "", "ob_status": inst.get("status")})
    return items


def main(argv: List[str]) -> int:
    old_run, new_run, out_dir = (Path(a).resolve() for a in argv[:3])
    out_dir.mkdir(parents=True, exist_ok=True)
    sample_ids = sorted(p.name.split(".")[0] for p in (old_run / "status").glob("*.status.json.gz"))
    old_items = load_items(old_run, sample_ids)
    new_items = load_items(new_run, sample_ids)
    statuses = {sid: read_gz(new_run / "status" / f"{sid}.status.json.gz") for sid in sample_ids}

    by_doc_old: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_doc_new: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    old_by_id = {item["id"]: item for item in old_items}
    for item in old_items:
        by_doc_old[item["doc"]].append(item)
    for item in new_items:
        by_doc_new[item["doc"]].append(item)

    removed, added, substantive, pairs_total = [], [], [], 0
    for sid in sample_ids:
        pairs, old_only, new_only = align(by_doc_old[sid], by_doc_new[sid])
        pairs_total += len(pairs)
        for o, n, how in pairs:
            diffs = [f for f in ("sev", "title", "quotes", "pages", "bucket", "obligation_ids")
                     if o.get(f) != n.get(f)]
            if not message_equal(o.get("message"), n.get("message")):
                diffs.append("message")
            if diffs:
                substantive.append({"sample_id": sid, "old_id": o["id"], "new_id": n["id"], "fields": diffs})
        for o in old_only:
            kind = classify(o, statuses[sid])
            removed.append({"sample_id": sid, "tier": o["tier"], "rule": o["rule"], "raw_id": o.get("raw_id"),
                            "pages": o["pages"], "message": o.get("message"), "class": kind,
                            "quotes": " ／ ".join((o.get("quotes") or [])[:2])})
        for n in new_only:
            added.append({"sample_id": sid, "tier": n["tier"], "rule": n["rule"], "raw_id": n.get("raw_id"),
                          "pages": n["pages"], "message": n.get("message")})

    removed_formal = [r for r in removed if r["tier"] in {"F", "P", "O", "R", "D"}]
    removed_notice = [r for r in removed if r["tier"] == "N"]
    added_formal = [r for r in added if r["tier"] in {"F", "P", "O", "R", "D"}]
    added_notice = [r for r in added if r["tier"] == "N"]
    unclassified = [r for r in removed_formal if not r["class"]]

    # 2022 年第七张表规范名本来就带“一般公共预算”（D2）：同年份条目的“应为…”
    # 文案随年度事实更新属预期变化，单列；其余文字变化仍视为需核查。
    expected_message_changes, other_substantive = [], []
    for entry in substantive:
        old_item = old_by_id.get(entry["old_id"]) or {}
        status = statuses[entry["sample_id"]]
        if (entry["fields"] == ["message"] and old_item.get("rule") == "CMM-021"
                and status.get("report_year") == 2022):
            expected_message_changes.append({**entry, "message_old": old_item.get("message"),
                                             "message_new": next(
                                                 (n["message"] for n in by_doc_new[entry["sample_id"]]
                                                  if n["id"] == entry["new_id"]), "")})
        else:
            other_substantive.append(entry)

    by_class = Counter(r["class"] or "UNCLASSIFIED" for r in removed_formal)
    by_tier = Counter(r["tier"] for r in removed_formal)

    # 义务账本：只记录 detail（规则 fail→pass 等）变化，与条目分开记账。
    ledger_detail, ledger_other = [], []
    for sid in sample_ids:
        old_status = read_gz(old_run / "status" / f"{sid}.status.json.gz")
        new_status = read_gz(new_run / "status" / f"{sid}.status.json.gz")
        old_inst = {i["obligation_id"]: i for i in old_status["check_coverage"]["instances"]}
        new_inst = {i["obligation_id"]: i for i in new_status["check_coverage"]["instances"]}
        for oid in sorted(set(old_inst) | set(new_inst)):
            left, right = old_inst.get(oid), new_inst.get(oid)
            if left == right:
                continue
            if left and right and {k: v for k, v in left.items() if k != "detail"} == \
                    {k: v for k, v in right.items() if k != "detail"}:
                ledger_detail.append({"sample_id": sid, "obligation": oid,
                                      "old_detail": left.get("detail"), "new_detail": right.get("detail")})
            else:
                ledger_other.append({"sample_id": sid, "obligation": oid, "old": left, "new": right})

    summary = {
        "samples": len(sample_ids), "items_old": len(old_items), "items_new": len(new_items),
        "pairs": pairs_total, "removed_formal": len(removed_formal), "removed_notice": len(removed_notice),
        "removed_by_tier": dict(by_tier),
        "added_formal": len(added_formal), "added_notice": len(added_notice),
        "by_class": dict(by_class), "unclassified": unclassified,
        "substantive_changed": substantive,
        "expected_message_changes": expected_message_changes,
        "other_substantive": other_substantive,
        "ledger_detail_changes": ledger_detail,
        "ledger_other_changes": ledger_other,
        "ok": (not added_formal and not unclassified and not other_substantive and not ledger_other),
    }
    (out_dir / "nongold_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=dict) + "\n", encoding="utf-8")
    with (out_dir / "nongold_item_diff.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["kind", "sample_id", "tier", "rule", "raw_id", "pages", "class", "message", "quotes"])
        for r in removed:
            writer.writerow(["removed", r["sample_id"], r["tier"], r["rule"], r["raw_id"],
                             " ".join(map(str, r["pages"])), r["class"], r["message"], r["quotes"]])
        for r in added:
            writer.writerow(["added", r["sample_id"], r["tier"], r["rule"], r["raw_id"],
                             " ".join(map(str, r["pages"])), "", r["message"], ""])
    # 抽样 20 条减少的正式条目（固定种子，可复现），附页码与原文引文。
    random.seed(20261103)
    sample = random.sample(removed_formal, min(20, len(removed_formal)))
    with (out_dir / "nongold_sample20.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_id", "rule", "class", "pages", "pdf_quote", "message"])
        for r in sorted(sample, key=lambda x: (x["sample_id"], str(x["raw_id"]))):
            writer.writerow([r["sample_id"], r["rule"], r["class"], " ".join(map(str, r["pages"])),
                             r["quotes"], r["message"]])
    print(json.dumps({k: summary[k] for k in ("samples", "items_old", "items_new", "pairs", "removed_formal",
                                             "removed_by_tier", "removed_notice", "added_formal", "added_notice",
                                             "by_class", "unclassified", "substantive_changed",
                                             "ledger_detail_changes", "ledger_other_changes", "ok")},
                     ensure_ascii=False, indent=1, default=dict)[:6000])
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
