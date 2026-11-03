# -*- coding: utf-8 -*-
"""独立复算：只读上一步生成的 CSV/JSON + 源标注，校验汇总数字可回溯。

运行：
    python3 scripts/validate_outputs.py \
        --src "/Volumes/workSSD/software development/GovBudgetChecker" \
        --out "docs/gold_coverage_20261103"
"""

import argparse
import collections
import csv
import json
import os
import re
import sys

CHECKS = [
    "GS-S01", "GS-S02", "GS-S03", "GS-S04",
    "GS-T01", "GS-T02", "GS-T03", "GS-T04", "GS-T05", "GS-T06", "GS-T07", "GS-T08",
    "GS-X01", "GS-X02",
    "GS-N01", "GS-N02", "GS-N03", "GS-N04", "GS-N05", "GS-N06", "GS-N07", "GS-N08", "GS-N09",
    "GS-W01",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    src, out = os.path.abspath(args.src), os.path.abspath(args.out)
    ok = []
    fail = []

    def check(name, cond, detail=""):
        (ok if cond else fail).append((name, detail))

    with open(os.path.join(out, "source_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    inv = list(csv.DictReader(open(os.path.join(out, "coverage_inventory.csv"), encoding="utf-8-sig")))
    cases = list(csv.DictReader(open(os.path.join(out, "case_evidence.csv"), encoding="utf-8-sig")))

    # 1. 108 项
    keys = {r["item_key"] for r in inv}
    final_keys = {r["item_key"] for r in inv if r["report_kind"] == "final"}
    budget_keys = {r["item_key"] for r in inv if r["report_kind"] == "budget"}
    check("108 检查点齐全", len(keys) == 108 and len(final_keys) == 54 and len(budget_keys) == 54,
          "all=%d final=%d budget=%d" % (len(keys), len(final_keys), len(budget_keys)))

    # 2. 每个 final 子场景四态相加=33
    bad = [(r["sub_id"], sum(int(r[k]) for k in
            ("pass_docs", "defect_docs", "na_docs", "cd_docs", "unconfirmed_docs")))
           for r in inv if r["report_kind"] == "final"
           and sum(int(r[k]) for k in ("pass_docs", "defect_docs", "na_docs", "cd_docs", "unconfirmed_docs")) != 33]
    check("final 子场景四态合计=33", not bad, str(bad[:5]))

    # 3. 预算行全为 0
    bad = [r["sub_id"] for r in inv if r["report_kind"] == "budget"
           and any(r[k] != "0" for k in ("pass_docs", "defect_docs", "na_docs", "cd_docs", "unconfirmed_docs"))]
    check("预算行无案例（计数为 0）", not bad, str(bad[:5]))

    # 4. 案例 sample_id 均属 33 份
    sample_ids = {s["sample_id"] for s in manifest["samples"]}
    check("案例 sample_id 均在 33 份内", all(c["sample_id"] in sample_ids for c in cases),
          str(sorted({c["sample_id"] for c in cases} - sample_ids)))

    # 5. 检查项缺陷单位去重=78；自由发现=105
    cu = {c["unit_key"] for c in cases if c["counted_as"] == "defect_unit" and c["evidence_type"] == "check_item"}
    fu = {c["unit_key"] for c in cases if c["counted_as"] == "defect_unit" and c["evidence_type"] == "free_finding"}
    check("检查项缺陷单位去重=78", len(cu) == 78, str(len(cu)))
    check("自由发现去重=105", len(fu) == 105, str(len(fu)))

    # 6. 每个子场景 defect_units = 该子场景去重缺陷行数
    per_sub = collections.Counter()
    for c in cases:
        if c["counted_as"] == "defect_unit":
            per_sub[c["sub_id"]] += 1
    bad = []
    for r in inv:
        if r["report_kind"] != "final":
            continue
        if int(r["defect_units"]) != per_sub.get(r["sub_id"], 0):
            bad.append((r["sub_id"], r["defect_units"], per_sub.get(r["sub_id"], 0)))
    check("子场景 defect_units 与明细一致", not bad, str(bad[:5]))

    # 6b. 每个子场景 pass/defect/NA 文档数与明细去重一致
    pass_docs = collections.defaultdict(set)
    defect_docs = collections.defaultdict(set)
    na_docs = collections.defaultdict(set)
    for c in cases:
        if c["counted_as"] == "pass_doc":
            pass_docs[c["sub_id"]].add(c["sample_id"])
        elif c["counted_as"] == "defect_unit":
            defect_docs[c["sub_id"]].add(c["sample_id"])
        elif c["counted_as"] == "na_doc":
            na_docs[c["sub_id"]].add(c["sample_id"])
    bad = []
    for r in inv:
        if r["report_kind"] != "final":
            continue
        sid = r["sub_id"]
        if int(r["pass_docs"]) != len(pass_docs.get(sid, set())):
            bad.append((sid, "pass", r["pass_docs"], len(pass_docs.get(sid, set()))))
        if int(r["defect_docs"]) != len(defect_docs.get(sid, set())):
            bad.append((sid, "defect", r["defect_docs"], len(defect_docs.get(sid, set()))))
        if int(r["na_docs"]) != len(na_docs.get(sid, set())):
            bad.append((sid, "na", r["na_docs"], len(na_docs.get(sid, set()))))
    check("子场景 pass/defect/NA 份数与明细去重一致", not bad, str(bad[:5]))

    # 7. 792 条四态记录
    total = 0
    for sid in sample_ids:
        with open(os.path.join(src, "gold", sid, "annotation.json"), encoding="utf-8") as f:
            ann = json.load(f)
        ids = [c["check_id"] for c in ann["checks"]]
        if len(ids) != 24 or set(ids) != set(CHECKS):
            fail.append(("24 项检查齐全 " + sid, str(ids)))
        total += len(ids)
    check("33×24=792 条四态记录", total == 792, str(total))

    # 8. 冻结与 PDF 指纹
    check("33 份 annotation 与 FROZEN 一致", manifest["checks"]["annotation_frozen_match"] == 33,
          str(manifest["checks"]["annotation_frozen_match"]))
    check("33 份 PDF 与样本清单一致", manifest["checks"]["pdf_sha_match"] == 33,
          str(manifest["checks"]["pdf_sha_match"]))

    # 9. 穷尽/尽力类拆分
    eff = manifest["checks"]["free_findings_by_class"]
    exhaustive = sum(eff.get(k, 0) for k in
                     ("R9-千分位", "R9-小数位", "R9-双小数点", "R9-标点连用", "R9-半角冒号",
                      "R22", "R24", "R28", "R21"))
    check("穷尽类=68、尽力类=37", exhaustive == 68 and sum(eff.values()) - exhaustive == 37,
          "exh=%d best=%d" % (exhaustive, sum(eff.values()) - exhaustive))

    # 10. 页码可解析
    bad = [c["case_id"] for c in cases if c["page"] and not re.match(r"^\d+$", c["page"])]
    check("页码均为整数或空", not bad, str(bad[:5]))

    print("OK %d" % len(ok))
    for n, d in ok:
        print("  PASS", n, ("(" + d + ")") if d else "")
    if fail:
        print("FAIL %d" % len(fail))
        for n, d in fail:
            print("  FAIL", n, d)
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
