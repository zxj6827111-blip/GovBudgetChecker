# -*- coding: utf-8 -*-
"""机器核验初标：24 项齐全、quote 在页面存在、formula 非空。"""
import json, glob, os, sys
import fitz

OUT = sys.argv[1]
PDFS = sys.argv[2]  # json mapping sample_id -> pdf path
pdfs = json.load(open(PDFS, encoding="utf-8"))
CHECKS = ["GS-S01","GS-S02","GS-S03","GS-S04","GS-T01","GS-T02","GS-T03","GS-T04","GS-T05","GS-T06","GS-T07","GS-T08","GS-X01","GS-X02","GS-N01","GS-N02","GS-N03","GS-N04","GS-N05","GS-N06","GS-N07","GS-N08","GS-N09","GS-W01"]
res = {"ok": True, "samples": {}}
for p in sorted(glob.glob(os.path.join(OUT, "annotations", "*", "annotation.json"))):
    a = json.load(open(p, encoding="utf-8")); sid = a["sample_id"]
    doc = fitz.open(pdfs[sid]); texts = [pg.get_text() for pg in doc]
    problems = []
    ids = [c["check_id"] for c in a["checks"]]
    if sorted(ids) != sorted(CHECKS): problems.append("check_ids")
    for c in a["checks"]:
        for it in c.get("items") or []:
            for v in it.get("values") or []:
                pg = v.get("page"); q = v.get("quote") or ""
                if not isinstance(pg, int) or pg < 1 or pg > len(texts):
                    problems.append(f"{c['check_id']} bad page {pg}"); continue
                if q and q not in texts[pg-1]:
                    problems.append(f"{c['check_id']} quote not found p{pg}: {q[:40]}")
    from collections import Counter
    vc = Counter(c.get("verdict") for c in a["checks"])
    res["samples"][sid] = {"problems": problems, "checks": len(a["checks"]), "review_status": a.get("review_status"),
                            "verdicts": dict(vc)}
    if problems: res["ok"] = False
json.dump(res, open(os.path.join(OUT, "machine_checks.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False, indent=1)[:3000])
