#!/usr/bin/env python3
"""R1 数据表生成：只读 R1 输入副本，生成原子缺陷/粗单位/系统发现映射与 2A 范围。"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "inputs"
OUT = ROOT


def read(name):
    return json.loads((INPUTS / name).read_text(encoding="utf-8"))


docs = read("round6_gold_docs.json")
unit_status = read("round6_gold_unit_status.json")
rows = read("round6_final_rows.json")
items = {item["id"]: item for item in read("round6_system_items.json")}
review = read("review_result.json")
candidates = read("batch2a_candidate_targets.json")

status_by_unit = {u["unit_id"]: u for u in unit_status}


def norm(text):
    return re.sub(r"\s+", "", text or "")


def unit_key(unit):
    return unit["check"] if unit["kind"] == "CU" else f"free:{unit['cls']}"


F_RULES = {}
for row in rows:
    if row["tier"] == "F":
        F_RULES.setdefault(row["rule"], []).append(row)
    if row["tier"] == "P":
        pass

# 规则 -> 其误报声称的 r2 粗检查（用于 FP 映射；NOGOLD 表示金标无对应）
FP_TARGET_CHECK = {
    "V33-251": "GS-N05", "V33-110": "GS-N09", "V33-202": "NOGOLD", "V33-117": "GS-T06",
    "V33-234": "GS-N09", "CMM-017": "GS-S04", "V33-SG-COMPLETION": "GS-N07",
    "CMM-018": "free:R21", "CMM-019": "free:R21",
    "V33-TREND-COMPLETION-RATE": "GS-N07", "V33-224": "GS-N07",
}


def units_for_key(sid, key):
    return [u for u in docs[sid]["units"] if unit_key(u) == key]


def w01_units(sid):
    return [u for u in docs[sid]["units"] if unit_key(u) == "GS-W01"]


def unit_case(unit):
    # 只看该缺陷的首个出现点；同 unit 的其他 quote 可能是对照句（带万元），不能混用。
    first = (unit.get("values") or [{}])[0]
    quote = str(first.get("quote") or "")
    if re.search(r"(?<![\d.])0(?:\.0+)?\s*元", quote):
        return "zero_value_unit_text"
    if "元" in quote:
        return "explicit_unit_conflict"
    return "missing_unit"


def item_rows_for_unit(unit_id):
    out = {"F": [], "P": [], "R": [], "O": []}
    for row in rows:
        if row["tier"] in {"F", "P", "R"} and unit_id in (row.get("links") or []):
            out[row["tier"]].append(row["id"])
    status = status_by_unit.get(unit_id) or {}
    out["O"] = list(status.get("hit_O") or [])
    return out


def occurrence_coverage(occ, finding_ids):
    page = occ.get("page")
    quote = norm(occ.get("quote"))
    hits = []
    for fid in finding_ids:
        item = items.get(fid)
        if not item or page not in item.get("pages", []):
            continue
        if len(quote) >= 3 and (quote in norm(item.get("text")) or
                                any(norm(q) in quote for q in item.get("quotes") or [] if len(norm(q)) >= 3)):
            hits.append(fid)
    return hits


atomic_rows = []
unit_rows = []
for sid, doc in docs.items():
    for unit in doc["units"]:
        uid = unit["unit_id"]
        status = status_by_unit.get(uid) or {}
        linked = item_rows_for_unit(uid)
        occurrences = unit.get("values") or [{}]
        covered = 0
        for index, occ in enumerate(occurrences, start=1):
            f_hits = occurrence_coverage(occ, linked["F"])
            if f_hits:
                coverage = "F_hit"
                evidence = ";".join(f_hits)
                covered += 1
            elif linked["F"]:
                coverage = "F_link_no_occurrence"
                evidence = ";".join(linked["F"])
            elif linked["P"]:
                coverage = "P_candidate"
                evidence = ";".join(linked["P"])
            elif linked["O"]:
                coverage = "O_pending"
                evidence = ";".join(linked["O"])
            elif linked["R"]:
                coverage = "R_reference"
                evidence = ";".join(linked["R"])
            else:
                coverage = "uncovered"
                evidence = ""
            atomic_rows.append({
                "atomic_id": f"{uid}|occ{index}",
                "sample_id": sid, "year": doc["year"], "scope": doc["scope"],
                "unit_id": uid, "unit_key": unit_key(unit), "kind": unit["kind"],
                "exhaustive": "Y" if unit["kind"] == "CU" or unit.get("exhaustive") else "N",
                "unit_state": status.get("state", ""), "unit_diag": status.get("diag", ""),
                "occurrence_index": index, "page": occ.get("page", ""),
                "quote": occ.get("quote", ""), "gold_label": unit.get("label") or "",
                "gold_note": unit.get("note") or "",
                "system_finding_ids": ";".join(linked["F"] + linked["P"] + linked["R"] + linked["O"]),
                "coverage": coverage, "coverage_evidence": evidence,
            })
        unit_rows.append({
            "unit_id": uid, "sample_id": sid, "year": doc["year"], "scope": doc["scope"],
            "unit_key": unit_key(unit), "kind": unit["kind"],
            "exhaustive": "Y" if unit["kind"] == "CU" or unit.get("exhaustive") else "N",
            "state": status.get("state", ""), "diag": status.get("diag", ""),
            "pages": " ".join(map(str, unit.get("pages") or [])),
            "occurrence_count": len(occurrences),
            "covered_occurrences": covered,
            "uncovered_occurrences": len(occurrences) - covered,
            "f_hits": ";".join(linked["F"]), "p_hits": ";".join(linked["P"]),
            "o_hits": ";".join(linked["O"]), "r_hits": ";".join(linked["R"]),
            "unit_case": unit_case(unit) if unit_key(unit) == "GS-W01" else "",
        })

# R1 误报表：增加目标粗单元/原子引用/路由；不设总数硬门槛
fp_r1 = []
for row in rows:
    if row["tier"] != "F" or row["final"] not in {"FP_GOLD", "ADJ_FALSE"}:
        continue
    sid, rule = row["doc"], row["rule"]
    target_units = []
    mapping_status = "mapped"
    if row.get("cause") == "CASCADE":
        target_units = [u["unit_id"] for u in w01_units(sid)]
        mapping_status = "cascade_w01"
    else:
        key = FP_TARGET_CHECK.get(rule)
        if key == "NOGOLD":
            mapping_status = "no_gold_check"
        elif key:
            target_units = [u["unit_id"] for u in units_for_key(sid, key)]
            mapping_status = "mapped" if target_units else "no_unit_in_doc"
        else:
            mapping_status = "unmapped_rule"
    target_ref = ";".join(target_units) if target_units else mapping_status.upper()
    atomic = [a["atomic_id"] for a in atomic_rows if a["unit_id"] in target_units]
    route = "batch2a" if row.get("cause") == "CASCADE" else "later_batch"
    acceptance = ("作为下游回执/cascade_of 关闭；W01 主问题与金额子检查保留；"
                  "逐条证据复核，不设总数门槛" if row.get("cause") == "CASCADE"
                  else "逐条证据复核后关闭；不设总数门槛；未确定口径保持 open")
    fp_r1.append({
        "priority": row.get("cause") == "CASCADE" and "P0" or "P1",
        "sample_id": sid, "year": docs[sid]["year"], "scope": docs[sid]["scope"],
        "rule": rule, "system_item_id": row.get("raw_id"), "final": row["final"],
        "pages": " ".join(map(str, row["pages"])), "cause": row.get("cause") or "",
        "root_cause": row.get("why") or "", "target_units": target_ref,
        "target_mapping_status": mapping_status,
        "atomic_count": len(atomic), "atomic_ids": ";".join(atomic),
        "route_r1": route, "acceptance_r1": acceptance,
        "message": row["message"],
    })

# R1 漏报表：W01 三类；N05 候选路由；原子引用
miss_r1 = []
for unit in unit_status:
    if not unit["exhaustive"] or unit["state"] == "F":
        continue
    uid = unit["unit_id"]
    doc_unit = next(u for u in docs[unit["doc"]]["units"] if u["unit_id"] == uid)
    key = unit_key(doc_unit)
    atomic = [a["atomic_id"] for a in atomic_rows if a["unit_id"] == uid]
    case = unit_case(doc_unit) if key == "GS-W01" else ""
    route = "deterministic"
    action = "确定性规则；按原子缺陷逐点核验"
    acceptance = "原子缺陷逐点命中；多候选/歧义保持待核；不以单个位置覆盖整项"
    if key == "GS-N05":
        note = (unit.get("note") or "")
        if any(word in note for word in ("医疗健康", "文化教育与传媒", "医疗保障（款）", "旧称", "近义")):
            route = "candidate_review"
            action = "候选路由：实体/旧称/近义歧义；先候选复核，不硬编码 AI"
            acceptance = "候选正确且人工确认；未确认保持 PEND；不自动升正式"
        else:
            action = "确定性：唯一绑定后按原文逐字比对；后缀/其他/及与差异可 low，不豁免"
            acceptance = "唯一绑定成立；原文逐字差异命中；多候选保持待核"
    elif key == "GS-W01":
        route = "batch2a"
        action = "确定性：按 unit_case 分显式冲突/缺单位/零值单位文字"
        acceptance = "对应 unit_case 确认；主问题成立；下游回执不关闭金额子检查"
    miss_r1.append({
        "priority": "P0" if (key == "GS-W01" or (key == "GS-N05" and unit["state"] == "SILENT")) else "P1",
        "unit_id": uid, "sample_id": unit["doc"], "year": unit["year"], "scope": unit["scope"],
        "kind": unit["kind"], "key": key, "state": unit["state"], "diag": unit["diag"],
        "pages": " ".join(map(str, unit["pages"])), "unit_case": case,
        "atomic_count": len(atomic), "atomic_ids": ";".join(atomic),
        "route_r1": route, "action_r1": action, "acceptance_r1": acceptance,
        "note": unit.get("note") or "",
    })

# 2A 范围：11 条下游 FP + 3 条 W01 缺失；按 W01 物理错误分组，不按 14 个对象合并
cascade_fps = [r for r in fp_r1 if r["route_r1"] == "batch2a"]
missing_w01 = [u for u in miss_r1 if u["route_r1"] == "batch2a"]
two_a = []
for row in cascade_fps:
    target = row["target_units"].split(";")[0]
    doc_unit = next(u for u in docs[row["sample_id"]]["units"] if u["unit_id"] == target)
    two_a.append({
        "object_type": "downstream_fp", "object_id": row["system_item_id"],
        "sample_id": row["sample_id"], "page": row["pages"], "w01_unit_id": target,
        "unit_case": unit_case(doc_unit), "w01_state": status_by_unit.get(target, {}).get("state", ""),
        "w01_verdict": "TP" if (status_by_unit.get(target, {}).get("hit_F") or []) else "MISSING",
        "downstream_rule": row["rule"], "expected_receipt": "cascade_of + explained_by_unit_error；金额子检查 executed",
        "acceptance": "该下游回执不再作为独立正式问题；主 W01 问题保留；无其他变化",
    })
for row in missing_w01:
    doc_unit = next(u for u in docs[row["sample_id"]]["units"] if u["unit_id"] == row["unit_id"])
    two_a.append({
        "object_type": "w01_missing", "object_id": row["unit_id"],
        "sample_id": row["sample_id"], "page": row["pages"], "w01_unit_id": row["unit_id"],
        "unit_case": row["unit_case"], "w01_state": row["state"],
        "w01_verdict": "MISSING",
        "downstream_rule": "", "expected_receipt": "W01 主问题 F；按 unit_case 记录字面单位/值",
        "acceptance": "该 W01 原子缺陷命中；不补写原始事实；无下游误报",
    })


def write(name, data, fields=None):
    data = list(data)
    fields = fields or list(data[0].keys())
    with (OUT / name).open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(data)


write("batch2_r1_atomic_defects.csv", atomic_rows)
write("batch2_r1_unit_index.csv", unit_rows)
write("batch2_r1_fp28.csv", fp_r1)
write("batch2_r1_miss86.csv", miss_r1)
write("batch2_r1_2a_scope.csv", two_a)

summary = Counter()
for row in atomic_rows:
    summary[(row["kind"], row["coverage"])] += 1
with (OUT / "batch2_r1_atomic_summary.csv").open("w", encoding="utf-8-sig", newline="") as fh:
    writer = csv.writer(fh)
    writer.writerow(["kind", "coverage", "count"])
    for (kind, coverage), count in sorted(summary.items()):
        writer.writerow([kind, coverage, count])

print(json.dumps({
    "atomic_rows": len(atomic_rows), "units": len(unit_rows), "fp_r1": len(fp_r1),
    "miss_r1": len(miss_r1), "two_a": len(two_a),
    "two_a_types": Counter(r["object_type"] for r in two_a),
}, ensure_ascii=False, indent=1))
