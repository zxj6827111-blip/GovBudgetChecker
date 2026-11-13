#!/usr/bin/env python3
"""金标准标注的机器核验（规范 §六.1）。

用法：
  python scripts/gold_verify.py gold/GS-2022-01/annotation.json
  python scripts/gold_verify.py --all

核验内容：
  1. sha256 与样本清单一致；
  2. checks 恰好包含 23 个 check_id；
  3. 每条 values[].quote 能在该页 PDF 文本中逐字找到；
  4. 每个 formula 可复算；
  5. 检查项整体结论与子项结论的聚合规则一致；
  6. free_findings 的 quote 能在该页找到。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = ROOT / "docs" / "GOLD_STANDARD_SAMPLE_20261101.csv"
SOURCE_ROOT = Path.home() / "Desktop" / "预决算信息收集" / "决算测试上海市"

CHECK_IDS = [
    "GS-S01", "GS-S02", "GS-S03", "GS-S04",
    "GS-T01", "GS-T02", "GS-T03", "GS-T04", "GS-T05", "GS-T06", "GS-T07", "GS-T08",
    "GS-X01", "GS-X02",
    "GS-N01", "GS-N02", "GS-N03", "GS-N04", "GS-N05", "GS-N06", "GS-N07",
    "GS-N08", "GS-N09",
    "GS-W01",
]
VERDICTS = {"pass", "defect", "not_applicable", "cannot_determine"}

NUM = r"-?\d[\d,]*(?:\.\d+)?"
SUM_RE = re.compile(rf"({NUM}(?:\s*\+\s*{NUM})+)\s*=\s*({NUM})")
RATIO_RE = re.compile(rf"({NUM})\s*/\s*({NUM})\s*=\s*({NUM})\s*%")


def to_float(s: str) -> float:
    return float(s.replace(",", ""))


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", "", s)


def load_samples() -> dict[str, dict]:
    with SAMPLE_CSV.open(encoding="utf-8-sig") as f:
        return {r["sample_id"]: r for r in csv.DictReader(f)}


def num_tokens(expr: str) -> list[str]:
    return [t.replace(",", "") for t in re.findall(r"-?\d[\d,]*(?:\.\d+)?", expr)]


def check_r10(item: dict) -> list[str]:
    """R10：算式左侧每个运算数都要在 values 里有原文出处。"""
    f = item.get("formula", "")
    if not f:
        return []
    quoted = [re.sub(r"\s+", "", v.get("quote", "")).replace(",", "") for v in item.get("values", [])]
    derived = re.sub(r"\s+", "", item.get("note", "")).replace(",", "")
    bad = []
    for eq in re.split(r"[；;]", f):
        lhs = eq.split("=")[0]
        for tok in num_tokens(lhs):
            if not any(tok in q for q in quoted) and tok not in derived:
                bad.append(tok)
    return sorted(set(bad))


class Pdf:
    def __init__(self, path: Path):
        self.doc = pymupdf.open(path)
        self._cache: dict[int, str] = {}

    def text(self, page: int) -> str:
        if page not in self._cache:
            self._cache[page] = self.doc[page - 1].get_text()
        return self._cache[page]


def check_formula(formula: str) -> list[str]:
    errs = []
    for lhs, rhs in SUM_RE.findall(formula):
        terms = [to_float(t) for t in lhs.split("+")]
        total = to_float(rhs)
        if abs(sum(terms) - total) > 0.005:
            errs.append(f"加总算式不成立：{lhs} = {rhs}（复算 {sum(terms):.2f}）")
    for a, b, p in RATIO_RE.findall(formula):
        av, bv, pv = to_float(a), to_float(b), to_float(p)
        if bv == 0:
            errs.append(f"占比分母为 0：{a} / {b}")
            continue
        got = av / bv * 100
        if abs(got - pv) > 0.01:
            errs.append(f"占比不成立：{a} / {b} = {p}%（复算 {got:.4f}%）")
    return errs


def verify(path: Path, samples: dict[str, dict]) -> list[str]:
    errs: list[str] = []
    ann = json.loads(path.read_text(encoding="utf-8"))
    sid = ann.get("sample_id")
    if sid not in samples:
        return [f"未知 sample_id：{sid}"]
    row = samples[sid]

    if ann.get("sha256") != row["sha256"]:
        errs.append("sha256 与样本清单不一致")

    pdf_path = SOURCE_ROOT / row["path"]
    digest = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != row["sha256"]:
        errs.append("源 PDF 的 sha256 与清单不一致（文件被替换？）")

    pdf = Pdf(pdf_path)
    basic = ann.get("basic", {})
    if basic.get("pages") != pdf.doc.page_count:
        errs.append(f"页数不符：ann={basic.get('pages')} pdf={pdf.doc.page_count}")

    ids = [c.get("check_id") for c in ann.get("checks", [])]
    if sorted(ids) != sorted(CHECK_IDS):
        missing = set(CHECK_IDS) - set(ids)
        extra = set(ids) - set(CHECK_IDS)
        if missing:
            errs.append(f"缺检查项：{sorted(missing)}")
        if extra:
            errs.append(f"多余检查项：{sorted(extra)}")
        if len(ids) != len(set(ids)):
            errs.append("检查项有重复")

    def need_quote(q: str, page: int, where: str) -> None:
        if not q:
            errs.append(f"{where}：quote 为空")
            return
        if not (1 <= page <= pdf.doc.page_count):
            errs.append(f"{where}：页码越界 {page}")
            return
        if norm_ws(q) not in norm_ws(pdf.text(page)):
            errs.append(f"{where}：quote 未在第 {page} 页原文中找到 → {q!r}")

    for c in ann.get("checks", []):
        cid = c.get("check_id")
        if c.get("verdict") not in VERDICTS:
            errs.append(f"{cid}：verdict 非法 {c.get('verdict')!r}")
        items = c.get("items", [])
        sub = [i.get("verdict") for i in items if i.get("verdict")]
        if sub:
            if "defect" in sub:
                agg = "defect"
            elif "cannot_determine" in sub:
                agg = "cannot_determine"
            elif all(v == "pass" for v in sub):
                agg = "pass"
            else:
                agg = "not_applicable"
            if c.get("verdict") != agg:
                errs.append(f"{cid}：整体结论 {c.get('verdict')} 与子项聚合 {agg} 不一致")
        if c.get("verdict") in ("defect", "cannot_determine", "not_applicable") and not c.get("reason") and not items:
            errs.append(f"{cid}：{c.get('verdict')} 但未写 reason")
        for k, it in enumerate(items):
            where = f"{cid}.items[{k}]"
            for v in it.get("values", []):
                need_quote(v.get("quote", ""), v.get("page"), where)
            f = it.get("formula", "")
            for e in check_formula(f):
                errs.append(f"{where}：{e}")
            miss = check_r10(it)
            if miss:
                errs.append(f"{where}：R10 算式运算数未在 values 中取原文 → {miss[:8]}")
        if not items:
            f = c.get("formula", "")
            for e in check_formula(f):
                errs.append(f"{cid}：{e}")

    for f in ann.get("free_findings", []):
        need_quote(f.get("quote", ""), f.get("page"), f"free.{f.get('id')}")
        if not f.get("expected"):
            errs.append(f"free.{f.get('id')}：缺 expected")
        for k, oc in enumerate(f.get("occurrences", [])):
            need_quote(oc.get("quote", ""), oc.get("page"), f"free.{f.get('id')}.occ[{k}]")

    return errs


# 每份标注耗时的**估算值**（分钟）。初标轮未逐份秒表计时，放量轮将改为实测。
ELAPSED_ESTIMATE = {
    "GS-2022-01": 25,
    "GS-2023-01": 40,
    "GS-2024-01": 35,
    "GS-2025-01": 25,
    "GS-2022-02": 3,
    "GS-2022-04": 3,
    "GS-2023-02": 3,
    "GS-2023-04": 2,
    "GS-2024-02": 3,
    "GS-2024-04": 5,
    "GS-2025-02": 3,
    "GS-2025-04": 2,
    # 批次 2：建 pages → judge 落盘的墙钟分钟数（含通读与判定）
    "GS-2022-03": 11,
    "GS-2022-05": 4,
    "GS-2023-03": 3,
    "GS-2023-05": 8,
    "GS-2024-03": 6,
    "GS-2024-05": 3,
    "GS-2025-03": 3,
    "GS-2025-05": 3,
}

NOTE = {
    "GS-2022-01": "初标轮估算；第4轮返工后更新",
    "GS-2023-01": "初标轮估算；第4轮返工后更新",
    "GS-2024-01": "初标轮估算；第4轮返工后更新",
    "GS-2025-01": "初标轮估算；第4轮返工后更新",
    "GS-2022-03": "批次2实测墙钟（脚本+通读+判定）",
    "GS-2022-05": "批次2实测墙钟（脚本+通读+判定）",
    "GS-2023-03": "批次2实测墙钟（脚本+通读+判定）",
    "GS-2023-05": "批次2实测墙钟（脚本+通读+判定）",
    "GS-2024-03": "批次2实测墙钟（脚本+通读+判定）",
    "GS-2024-05": "批次2实测墙钟（脚本+通读+判定）",
    "GS-2025-03": "批次2实测墙钟（脚本+通读+判定）",
    "GS-2025-05": "批次2实测墙钟（脚本+通读+判定）",
}


def write_summary(paths: list[Path], out: Path) -> None:
    import collections

    rows = []
    for p in paths:
        ann = json.loads(p.read_text(encoding="utf-8"))
        sid = ann.get("sample_id")
        counts = collections.Counter(c.get("verdict") for c in ann.get("checks", []))
        rows.append(
            {
                "sample_id": sid,
                "pages": ann.get("basic", {}).get("pages"),
                "checks": len(ann.get("checks", [])),
                "pass": counts.get("pass", 0),
                "defect": counts.get("defect", 0),
                "not_applicable": counts.get("not_applicable", 0),
                "cannot_determine": counts.get("cannot_determine", 0),
                "free_findings": len(ann.get("free_findings", [])),
                "elapsed_min": ELAPSED_ESTIMATE.get(sid, ""),
                "note": NOTE.get(sid, "第4轮返工实测/估算"),
            }
        )
    fields = list(rows[0].keys()) if rows else []
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nSUMMARY → {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    samples = load_samples()
    paths = [Path(p) for p in args.paths]
    if args.all:
        paths = sorted((ROOT / "gold").glob("GS-*/annotation.json"))
    if not paths:
        raise SystemExit("no annotation.json given")

    total_bad = 0
    for p in paths:
        errs = verify(p, samples)
        if errs:
            total_bad += 1
            print(f"FAIL {p.parent.name}")
            for e in errs:
                print(f"   - {e}")
        else:
            print(f"PASS {p.parent.name}")
    print(f"\n{len(paths) - total_bad}/{len(paths)} 通过")
    if args.summary and total_bad == 0:
        write_summary(paths, ROOT / "gold" / "SUMMARY.csv")
    return 1 if total_bad else 0


if __name__ == "__main__":
    sys.exit(main())
