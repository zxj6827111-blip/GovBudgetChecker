#!/usr/bin/env python3
"""R1 定点修正 R1-01/02/03：证据摘录 vs 缺陷位置、W01 精确位置、2A 映射。

只读 inputs/ 与 R1 v1 数据；生成 v2 修正版，不改 v1/金标/r5/第一批。
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "inputs"


def read(name):
    return json.loads((INPUTS / name).read_text(encoding="utf-8"))


docs = read("round6_gold_docs.json")
status = {u["unit_id"]: u for u in read("round6_gold_unit_status.json")}
rows = read("round6_final_rows.json")
items = {i["id"]: i for i in read("round6_system_items.json")}


def norm(text):
    return re.sub(r"\s+", "", text or "")


def unit_key(unit):
    return unit["check"] if unit["kind"] == "CU" else f"free:{unit['cls']}"


def links_for(uid):
    out = {"F": [], "P": [], "R": [], "O": list((status.get(uid) or {}).get("hit_O") or [])}
    for row in rows:
        if row["tier"] in {"F", "P", "R"} and uid in (row.get("links") or []):
            out[row["tier"]].append(row["id"])
    return out


def covers(occ, fids):
    hits = []
    for fid in fids:
        item = items.get(fid)
        if not item or occ.get("page") not in item.get("pages", []):
            continue
        q = norm(occ.get("quote"))
        if len(q) >= 3 and (q in norm(item.get("text")) or
                            any(norm(x) in q for x in item.get("quotes") or [] if len(norm(x)) >= 3)):
            hits.append(fid)
    return hits


# 2A 选定 11 个粗 W01 单元；每个出现点显式标注：defect_location / supporting_evidence。
# key=(unit_id, occurrence_index)
W01_ROWS = {
    ("GS-2022-02|C|GS-W01|0", 1): ("defect_location", "explicit_unit_conflict", False),
    ("GS-2022-04|C|GS-W01|0", 1): ("defect_location", "explicit_unit_conflict", False),
    ("GS-2022-08|C|GS-W01|0", 1): ("defect_location", "explicit_unit_conflict", False),
    ("GS-2022-08|C|GS-W01|0", 2): ("supporting_evidence", "", False),
    ("GS-2023-04|C|GS-W01|0", 1): ("defect_location", "explicit_unit_conflict", False),
    ("GS-2023-05|C|GS-W01|0", 1): ("defect_location", "explicit_unit_conflict", False),
    ("GS-2023-05|C|GS-W01|0", 2): ("supporting_evidence", "", False),
    ("GS-2024-01|C|GS-W01|0", 1): ("defect_location", "explicit_unit_conflict", True),
    ("GS-2024-02|C|GS-W01|1", 1): ("defect_location", "missing_unit", True),
    ("GS-2024-04|C|GS-W01|0", 1): ("defect_location", "explicit_unit_conflict", True),
    ("GS-2024-04|C|GS-W01|0", 2): ("defect_location", "explicit_unit_conflict", True),
    ("GS-2024-04|C|GS-W01|0", 3): ("defect_location", "explicit_unit_conflict", True),
    ("GS-2024-06|C|GS-W01|0", 1): ("defect_location", "missing_unit", True),
    ("GS-2024-06|C|GS-W01|0", 2): ("supporting_evidence", "", False),
    ("GS-2024-06|C|GS-W01|1", 1): ("defect_location", "zero_value_unit_text", True),
    ("GS-2024-06|C|GS-W01|1", 2): ("supporting_evidence", "", False),
    ("GS-2025-04|C|GS-W01|0", 1): ("defect_location", "explicit_unit_conflict", False),
    # GS-2025-09 W01 不在 2A 选中范围，但证据行也明确区分；映射留 later_batch
    ("GS-2025-09|C|GS-W01|0", 1): ("defect_location", "explicit_unit_conflict", False),
    ("GS-2025-09|C|GS-W01|0", 2): ("supporting_evidence", "", False),
}
TWO_A_W01_UNITS = {
    "GS-2022-02|C|GS-W01|0", "GS-2022-04|C|GS-W01|0", "GS-2022-08|C|GS-W01|0",
    "GS-2023-04|C|GS-W01|0", "GS-2023-05|C|GS-W01|0", "GS-2024-01|C|GS-W01|0",
    "GS-2024-02|C|GS-W01|1", "GS-2024-04|C|GS-W01|0", "GS-2024-06|C|GS-W01|0",
    "GS-2024-06|C|GS-W01|1", "GS-2025-04|C|GS-W01|0",
}

evidence_rows = []
defect_rows = []
for sid, doc in docs.items():
    for unit in doc["units"]:
        uid = unit["unit_id"]
        linked = links_for(uid)
        occurrences = unit.get("values") or [{}]
        for index, occ in enumerate(occurrences, start=1):
            kind, case, needs_pdf = W01_ROWS.get((uid, index), ("unresolved", "", False))
            defect_id = ""
            if kind == "defect_location":
                defect_id = f"W01DEF-{uid.split('|')[0]}-{uid.rsplit('|', 1)[-1]}-{index}"
                defect_rows.append({
                    "defect_id": defect_id, "sample_id": sid, "scope": doc["scope"],
                    "w01_unit_id": uid, "occurrence_index": index, "page": occ.get("page", ""),
                    "quote": occ.get("quote", ""), "unit_case": case,
                    "needs_pdf_verification": "Y" if (needs_pdf or uid in TWO_A_W01_UNITS) else "N",
                    "in_2a_scope": "Y" if uid in TWO_A_W01_UNITS else "N",
                    "mapping_status": "2A" if uid in TWO_A_W01_UNITS else "later_batch",
                    "w01_verdict": "TP" if linked["F"] else ("MISSING" if status.get(uid, {}).get("state") == "SILENT" else "OTHER"),
                    "literal_note": unit.get("note") or "",
                })
            if kind == "defect_location":
                f_hits = covers(occ, linked["F"])
                coverage = "F_hit" if f_hits else ("F_link_no_occurrence" if linked["F"] else "uncovered")
                coverage_evidence = ";".join(f_hits or linked["F"])
            elif kind == "supporting_evidence":
                coverage, coverage_evidence = "supporting_evidence", ""
            else:
                coverage, coverage_evidence = "unresolved", ""
            evidence_rows.append({
                "evidence_id": f"{uid}|ev{index}", "sample_id": sid, "year": doc["year"],
                "scope": doc["scope"], "unit_id": uid, "unit_key": unit_key(unit),
                "kind": unit["kind"], "occurrence_index": index, "page": occ.get("page", ""),
                "quote": occ.get("quote", ""), "row_type": kind, "unit_case": case,
                "defect_id": defect_id,
                "defect_scope": ("2A" if uid in TWO_A_W01_UNITS else
                                 ("W01_later" if unit_key(unit) == "GS-W01" else "later_batch")),
                "needs_pdf_verification": "Y" if (needs_pdf or uid in TWO_A_W01_UNITS) else "N",
                "coverage": coverage, "coverage_evidence": coverage_evidence,
                "gold_label": unit.get("label") or "", "gold_note": unit.get("note") or "",
            })

# 粗单位索引：证据行/缺陷位置/未解析分别计数，不把证据行当缺陷
unit_rows = []
for sid, doc in docs.items():
    for unit in doc["units"]:
        uid = unit["unit_id"]
        evs = [r for r in evidence_rows if r["unit_id"] == uid]
        unit_rows.append({
            "unit_id": uid, "sample_id": sid, "year": doc["year"], "scope": doc["scope"],
            "unit_key": unit_key(unit), "kind": unit["kind"],
            "exhaustive": "Y" if unit["kind"] == "CU" or unit.get("exhaustive") else "N",
            "state": status.get(uid, {}).get("state", ""),
            "evidence_rows": len(evs),
            "defect_locations": sum(1 for r in evs if r["row_type"] == "defect_location"),
            "supporting_evidence": sum(1 for r in evs if r["row_type"] == "supporting_evidence"),
            "unresolved_rows": sum(1 for r in evs if r["row_type"] == "unresolved"),
            "mapping_status": ("2A_complete" if uid in TWO_A_W01_UNITS else
                               ("W01_later" if unit_key(unit) == "GS-W01" else "later_batch")),
        })

# 下游 FP -> 精确 defect_id（同文档 W01 缺陷位置）
defect_by_unit = {}
for r in defect_rows:
    if r["in_2a_scope"] == "Y":
        defect_by_unit.setdefault(r["w01_unit_id"], []).append(r["defect_id"])

fp_rows = []
for row in rows:
    if row["tier"] != "F" or row["final"] not in {"FP_GOLD", "ADJ_FALSE"}:
        continue
    sid = row["doc"]
    if row.get("cause") == "CASCADE":
        w01 = [u for u in docs[sid]["units"] if unit_key(u) == "GS-W01" and u["unit_id"] in TWO_A_W01_UNITS]
        defect_ids = defect_by_unit.get(w01[0]["unit_id"], []) if w01 else []
        route = "batch2a"
        map_status = "2A"
    else:
        defect_ids, route, map_status = [], "later_batch", "later_batch"
    fp_rows.append({
        "sample_id": sid, "rule": row["rule"], "system_item_id": row.get("raw_id"),
        "final": row["final"], "pages": " ".join(map(str, row["pages"])),
        "cause": row.get("cause") or "", "route_r1": route,
        "w01_unit_id": (defect_rows[0]["w01_unit_id"] if route == "batch2a" and defect_rows else ""),
        "defect_ids": ";".join(defect_ids), "defect_count": len(defect_ids),
        "mapping_status": map_status,
    })
# 修正 cascade 的 w01_unit_id（上面取错，按行重算）
for r in fp_rows:
    if r["route_r1"] != "batch2a":
        continue
    target = next(uid for uid in TWO_A_W01_UNITS if uid.startswith(r["sample_id"] + "|"))
    r["w01_unit_id"] = target
    r["defect_ids"] = ";".join(defect_by_unit.get(target, []))
    r["defect_count"] = len(defect_by_unit.get(target, []))

# 2A 范围：14 对象；下游 FP 可映射多个缺陷（GS-2024-04 3 处）
two_a = []
for r in fp_rows:
    if r["route_r1"] != "batch2a":
        continue
    defects = [d for d in defect_rows if d["defect_id"] in (r["defect_ids"].split(";") if r["defect_ids"] else [])]
    two_a.append({
        "object_type": "downstream_fp", "object_id": r["system_item_id"], "sample_id": r["sample_id"],
        "page": r["pages"], "w01_unit_id": r["w01_unit_id"],
        "w01_case": defects[0]["unit_case"] if defects else "",
        "w01_verdict": "TP" if status.get(r["w01_unit_id"], {}).get("hit_F") else "MISSING",
        "defect_ids": r["defect_ids"], "defect_location_count": r["defect_count"],
        "needs_pdf_verification": "Y" if any(d["needs_pdf_verification"] == "Y" for d in defects) else "N",
        "downstream_rule": r["rule"],
        "expected_receipt": "每个 defect_id 独立 cascade_of + explained_by_unit_error；金额子检查 executed",
        "acceptance": "下游 FP 不再独立正式；主 W01 按 defect_id 保留；GS-2024-04 三处分别定位",
    })
for uid in sorted(TWO_A_W01_UNITS):
    if status.get(uid, {}).get("state") != "SILENT":
        continue
    defects = [d for d in defect_rows if d["w01_unit_id"] == uid]
    for d in defects:
        two_a.append({
            "object_type": "w01_missing", "object_id": d["defect_id"], "sample_id": d["sample_id"],
            "page": d["page"], "w01_unit_id": uid, "w01_case": d["unit_case"],
            "w01_verdict": "MISSING", "defect_ids": d["defect_id"], "defect_location_count": 1,
            "needs_pdf_verification": d["needs_pdf_verification"],
            "downstream_rule": "",
            "expected_receipt": "W01 主问题 F；保留字面值/单位；不补写万元",
            "acceptance": "该 defect_id 命中；不新增同点重复；无下游误报",
        })


def write(name, data):
    if not data:
        return
    with (ROOT / name).open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)


write("batch2_r1_evidence_index_v2.csv", evidence_rows)
write("batch2_r1_defect_locations_v2.csv", defect_rows)
write("batch2_r1_unit_index_v2.csv", unit_rows)
write("batch2_r1_fp28_v2.csv", fp_rows)
write("batch2_r1_2a_scope_v2.csv", two_a)

summary = Counter((r["row_type"], r["defect_scope"]) for r in evidence_rows)
with (ROOT / "batch2_r1_evidence_summary_v2.csv").open("w", encoding="utf-8-sig", newline="") as fh:
    writer = csv.writer(fh)
    writer.writerow(["row_type", "defect_scope", "count"])
    for (row_type, scope), count in sorted(summary.items()):
        writer.writerow([row_type, scope, count])

print(json.dumps({
    "evidence_rows": len(evidence_rows),
    "row_types": dict(Counter(r["row_type"] for r in evidence_rows)),
    "defect_locations_all": len(defect_rows),
    "defect_locations_2a": sum(1 for r in defect_rows if r["in_2a_scope"] == "Y"),
    "2a_objects": len(two_a),
    "2a_downstream": sum(1 for r in two_a if r["object_type"] == "downstream_fp"),
    "2a_missing": sum(1 for r in two_a if r["object_type"] == "w01_missing"),
    "2a_defect_total": sum(r["defect_location_count"] for r in two_a if r["object_type"] == "downstream_fp"),
}, ensure_ascii=False, indent=1))
