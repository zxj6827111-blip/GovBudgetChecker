# -*- coding: utf-8 -*-
"""从160份非金标回归记录中提取“表内/表间/说明”失败线索。

只作为候选定位，不是金标结论。输出 candidate_leads.csv。
"""
import csv, glob, gzip, hashlib, json, os, re, sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "/Volumes/workSSD/software development/GovBudgetChecker"
OUT = sys.argv[2] if len(sys.argv) > 2 else "."
REC = os.path.join(SRC, "outputs/fp_batch1_20261103/nongold/new/records")
TARGETS = {
    "T01": ["V33-214", "V33-115"],
    "T02": ["V33-240"],
    "T03": ["V33-241", "V33-233"],
    "T04": ["V33-119"],
    "T05": ["V33-005", "V33-120"],
    "T06": ["V33-243", "V33-117"],
    "T07": ["V33-244"],
    "T08": ["V33-117", "V33-243", "V33-203"],
    "X01": ["V33-200", "V33-201"],
    "X02": ["V33-252"],
    "N02": ["V33-103", "V33-226"],
    "N06": ["V33-107", "V33-223"],
    "N08": ["V33-248", "V33-TXT-FUND-DETAIL"],
}
ALL = {c: t for t, cs in TARGETS.items() for c in cs}

# sha -> uploads pdf
sha_to_pdf = {}
for f in glob.glob(os.path.join(SRC, "uploads/*/*.pdf")):
    h = hashlib.sha256(open(f, "rb").read()).hexdigest()
    sha_to_pdf.setdefault(h, f)

rows = []
for p in sorted(glob.glob(os.path.join(REC, "*.json.gz"))):
    d = json.load(gzip.open(p, "rt", encoding="utf-8"))
    sid, sha = d.get("sample_id"), d.get("sha256")
    hits = {}
    for src in ("coverage_items", "obligations"):
        for oid, item in (d.get(src) or {}).items():
            detail = item.get("detail") or ""
            for chk, status in re.findall(r"([A-Z][A-Z0-9-]+)=([a-z_]+)", detail):
                if status == "fail" and chk in ALL:
                    hits.setdefault(ALL[chk], set()).add((chk, oid, detail[:200]))
    for target, hs in hits.items():
        for chk, oid, detail in sorted(hs):
            rows.append({
                "target": target, "sample_id": sid, "sha256": sha,
                "pdf_path": sha_to_pdf.get(sha, ""),
                "checker": chk, "obligation": oid, "detail": detail,
            })

# dedupe by target+sha
seen = set(); out = []
for r in rows:
    k = (r["target"], r["sha256"])
    if k in seen: continue
    seen.add(k); out.append(r)

fields = ["target", "sample_id", "sha256", "pdf_path", "checker", "obligation", "detail"]
with open(os.path.join(OUT, "candidate_leads.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)

from collections import Counter
print("leads", len(out), "with_pdf", sum(1 for r in out if r["pdf_path"]))
print("by target", Counter(r["target"] for r in out))
print("unique docs", len({r["sha256"] for r in out}))
print("missing pdf", sum(1 for r in out if not r["pdf_path"]))
