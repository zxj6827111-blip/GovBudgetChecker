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
import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gold_build import scan_r21, scan_r22, scan_r23, scan_r24, scan_r28  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = ROOT / "docs" / "GOLD_STANDARD_SAMPLE_20261101.csv"
SOURCE_ROOT = Path.home() / "Desktop" / "预决算信息收集" / "决算测试上海市"
FROZEN_CSV = ROOT / "gold" / "FROZEN.csv"

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
        self.path = path
        self.doc = pymupdf.open(path)
        self._cache: dict[int, str] = {}
        self._plumber = None
        self._pl_cache: dict[int, str] = {}

    def text(self, page: int) -> str:
        if page not in self._cache:
            self._cache[page] = self.doc[page - 1].get_text()
        return self._cache[page]

    def plumber_text(self, page: int) -> str:
        if page not in self._pl_cache:
            if self._plumber is None:
                self._plumber = pdfplumber.open(self.path)
            self._pl_cache[page] = self._plumber.pages[page - 1].extract_text() or ""
        return self._pl_cache[page]


def norm_text(s: str, source: str | None = None) -> str:
    if source == "pdfplumber":
        s = s.replace("(cid:1)", "")
    return norm_ws(s)


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

    def need_quote(q: str, page: int, where: str, source: str | None = None) -> None:
        if not q:
            errs.append(f"{where}：quote 为空")
            return
        if not (1 <= page <= pdf.doc.page_count):
            errs.append(f"{where}：页码越界 {page}")
            return
        text = pdf.plumber_text(page) if source == "pdfplumber" else pdf.text(page)
        if norm_ws(q) not in norm_text(text, source):
            tag = f"（{source}）" if source else ""
            errs.append(f"{where}：quote 未在第 {page} 页原文{tag}中找到 → {q!r}")

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
                need_quote(v.get("quote", ""), v.get("page"), where, v.get("source"))
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
        need_quote(f.get("quote", ""), f.get("page"), f"free.{f.get('id')}", f.get("source"))
        if not f.get("expected"):
            errs.append(f"free.{f.get('id')}：缺 expected")
        for k, oc in enumerate(f.get("occurrences", [])):
            need_quote(oc.get("quote", ""), oc.get("page"), f"free.{f.get('id')}.occ[{k}]",
                       oc.get("source") or f.get("source"))

    checks_by_id = {c.get("check_id"): c for c in ann.get("checks", [])}

    # R21/R22/R24：occurrences 必须与程序全文扫描逐条一致（§三.4、§六.5）
    scanners = {"R21": scan_r21, "R22": scan_r22, "R24": scan_r24, "R28": scan_r28}
    tagged = {f.get("scan"): f for f in ann.get("free_findings", []) if f.get("scan")}
    for rule, fn in scanners.items():
        got = fn(sid)
        have = tagged.get(rule)
        if not got:
            if have:
                errs.append(f"{rule}：程序扫描无命中，但标注有该条自由发现")
            continue
        if not have:
            errs.append(f"{rule}：程序扫描命中 {len(got)} 处，但标注没有 scan={rule} 的自由发现")
            continue
        want = have.get("occurrences", [])
        if len(want) != len(got):
            errs.append(f"{rule}：occurrences {len(want)} 条，程序扫描 {len(got)} 条")
        for k, (w, g) in enumerate(zip(want, got)):
            if w.get("page") != g["page"] or norm_ws(w.get("quote", "")) != norm_ws(g["quote"]):
                errs.append(f"{rule}.occ[{k}]：标注 p{w.get('page')} {w.get('quote','')!r} "
                            f"≠ 扫描 p{g['page']} {g['quote']!r}")
                break

    # R23：程序发现目录与正文标题不一致时，GS-S03 必须是 defect
    r23 = scan_r23(sid).get("problems") or []
    s03 = checks_by_id.get("GS-S03")
    if r23 and (s03 is None or s03.get("verdict") != "defect"):
        errs.append(f"R23：目录/正文标题不一致 {len(r23)} 处，但 GS-S03 不是 defect")

    # D2=A：2022 年模板三公表名含“一般公共预算”、2023 年起部门决算正文无“第二部分”均不报
    year = ann.get("basic", {}).get("fiscal_year")
    scope = ann.get("basic", {}).get("material_scope")
    s01 = checks_by_id.get("GS-S01")
    if year == 2022 and s01 and s01.get("verdict") == "defect":
        if "一般公共预算财政拨款“三公”经费" in json.dumps(s01, ensure_ascii=False):
            errs.append("D2=A：2022 年模板三公表名含“一般公共预算”，不得记 GS-S01 defect")
    s02 = checks_by_id.get("GS-S02")
    if year and year >= 2023 and scope == "department" and s02 and s02.get("verdict") == "defect":
        txt = json.dumps(s02, ensure_ascii=False)
        if "第二部分" in txt and "缺" in txt:
            errs.append("D2=A：2023 年起部门决算正文表格部分不写“第二部分”，不得记 GS-S02 defect")

    # R12：正文金额漏单位一律进 GS-W01，不得另写自由发现
    w01 = checks_by_id.get("GS-W01")
    for f in ann.get("free_findings", []):
        q = norm_ws(f.get("quote", ""))
        if re.search(r"\d[\d,]*\.?\d*\s*元", q) and "万元" not in q and "万" in (f.get("expected") or ""):
            errs.append(f"R12：{f.get('id')} 为正文金额漏单位，应记 GS-W01，不得写自由发现"
                        + ("（且 W01 现为 pass）" if w01 and w01.get("verdict") == "pass" else ""))

    # R16：增长率/完成率判 defect 必须在区间外且差 > 0.1 个百分点
    def rate_error(cid: str, it: dict) -> str | None:
        if it.get("verdict") != "defect":
            return None
        label = it.get("label", "")
        if not any(k in label for k in ("同比", "增长", "下降", "完成率", "增幅", "降幅")):
            return None
        rc = it.get("rate_check")
        if not rc:
            return f"{cid}：增长率/完成率判 defect 须提供 rate_check（区间与差值）"
        if rc.get("undefined_base"):
            return None
        v, lo, hi, diff = rc.get("value"), rc.get("low"), rc.get("high"), rc.get("diff")
        if None in (v, lo, hi, diff):
            return f"{cid}：rate_check 字段不完整"
        if lo <= v <= hi:
            return f"{cid}：正文值 {v} 在 R16 区间 [{lo}, {hi}] 内，不得判 defect"
        if diff <= 0.1:
            return f"{cid}：与复算值差 {diff} 未超过 0.1 个百分点，不得判 defect"
        return None

    for c in ann.get("checks", []):
        for k, it in enumerate(c.get("items", [])):
            e = rate_error(c.get("check_id"), it)
            if e:
                errs.append(f"{e}（items[{k}] {it.get('label')}）")

    # R27：标题里的单位名称/年度笔误，S03 与 S04 都要记，note 互相指明
    def title_typo(a: str, b: str) -> bool:
        if not a or not b:
            return False
        ya, yb = set(re.findall(r"20\d{1,3}", a)), set(re.findall(r"20\d{1,3}", b))
        if ya and yb and ya != yb:
            return True
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.9 and a != b

    if any(title_typo(p.get("toc", ""), p.get("body", "")) for p in r23):
        s04 = checks_by_id.get("GS-S04")
        if s04 is None or s04.get("verdict") != "defect":
            errs.append("R27：目录/正文标题为单位名称或年度笔误，GS-S04 也须记 defect")
        else:
            if "GS-S03" not in json.dumps(s03, ensure_ascii=False):
                errs.append("R27：GS-S04 的 note 须指明 GS-S03")
            if "GS-S04" not in json.dumps(s03, ensure_ascii=False):
                errs.append("R27：GS-S03 的 note 须指明 GS-S04")

    # R20：note/reason 里的 F 编号必须存在，“见 GS-XXX”所指检查项必须是 defect
    free_ids = {f.get("id") for f in ann.get("free_findings", [])}
    checks_by_id = {c.get("check_id"): c for c in ann.get("checks", [])}

    def check_note(text: str, where: str) -> None:
        for m in re.finditer(r"F\d{2}", text or ""):
            if m.group(0) not in free_ids:
                errs.append(f"{where}：note 引用不存在的自由发现 {m.group(0)}")
        for m in re.finditer(r"见\s*(GS-[A-Z]\d{2})", text or ""):
            cid = m.group(1)
            target = checks_by_id.get(cid)
            if not target or target.get("verdict") != "defect":
                errs.append(f"{where}：note 写“见 {cid}”，但该项不是 defect")

    for c in ann.get("checks", []):
        check_note(c.get("reason", ""), f"{c.get('check_id')}.reason")
        for k, it in enumerate(c.get("items", [])):
            check_note(it.get("note", ""), f"{c.get('check_id')}.items[{k}].note")
    for f in ann.get("free_findings", []):
        check_note(f.get("note", ""), f"free.{f.get('id')}.note")

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
    # 批次 3：建 pages → judge 落盘的墙钟分钟数（含通读与判定）
    "GS-2022-06": 12,
    "GS-2022-07": 5,
    "GS-2023-06": 5,
    "GS-2023-07": 8,
    "GS-2024-06": 15,
    "GS-2024-07": 8,
    "GS-2025-06": 10,
    "GS-2025-07": 5,
    # 批次 4：五份批量处理，总墙钟约 21 分钟（按份分段落账）
    "GS-2022-08": 6,
    "GS-2023-08": 3,
    "GS-2024-08": 4,
    "GS-2025-08": 3,
    "GS-2025-09": 4,
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
    "GS-2022-06": "批次3实测墙钟（脚本+通读+判定）",
    "GS-2022-07": "批次3实测墙钟（脚本+通读+判定）",
    "GS-2023-06": "批次3实测墙钟（脚本+通读+判定）",
    "GS-2023-07": "批次3实测墙钟（脚本+通读+判定）",
    "GS-2024-06": "批次3实测墙钟（脚本+通读+判定）",
    "GS-2024-07": "批次3实测墙钟（脚本+通读+判定）",
    "GS-2025-06": "批次3实测墙钟（脚本+通读+判定）",
    "GS-2025-07": "批次3实测墙钟（脚本+通读+判定）",
    "GS-2022-08": "批次4实测（五份总墙钟约21分钟，按份分段落账）",
    "GS-2023-08": "批次4实测（五份总墙钟约21分钟，按份分段落账）",
    "GS-2024-08": "批次4实测（五份总墙钟约21分钟，按份分段落账）",
    "GS-2025-08": "批次4实测（五份总墙钟约21分钟，按份分段落账）",
    "GS-2025-09": "批次4实测（五份总墙钟约21分钟，按份分段落账）",
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


def load_frozen() -> dict[str, str]:
    if not FROZEN_CSV.exists():
        return {}
    with FROZEN_CSV.open(encoding="utf-8-sig") as f:
        return {r["sample_id"]: r["annotation_sha256"] for r in csv.DictReader(f)}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def frozen_error(path: Path, frozen: dict[str, str]) -> str:
    ann = json.loads(path.read_text(encoding="utf-8"))
    sid = ann.get("sample_id")
    want = frozen.get(sid)
    if not want:
        return f"{sid}：已冻结但不在 FROZEN.csv"
    got = sha256_file(path)
    if got != want:
        return f"{sid}：冻结后 annotation 被改动（sha256 {got[:12]}… != {want[:12]}…）"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    samples = load_samples()
    frozen = load_frozen()
    paths = [Path(p) for p in args.paths]
    if args.all:
        paths = sorted((ROOT / "gold").glob("GS-*/annotation.json"))
    if not paths:
        raise SystemExit("no annotation.json given")

    total_bad = 0
    verify_bad = 0
    n_verify = 0
    n_frozen = 0
    for p in paths:
        ann = json.loads(p.read_text(encoding="utf-8"))
        if ann.get("truth_frozen_at"):
            n_frozen += 1
            err = frozen_error(p, frozen)
            if err:
                total_bad += 1
                print(f"FAIL FROZEN {p.parent.name}")
                print(f"   - {err}")
            else:
                print(f"FROZEN OK {p.parent.name}")
            continue
        n_verify += 1
        errs = verify(p, samples)
        if errs:
            total_bad += 1
            verify_bad += 1
            print(f"FAIL {p.parent.name}")
            for e in errs:
                print(f"   - {e}")
        else:
            print(f"PASS {p.parent.name}")
    print(f"\n未冻结 {n_verify - verify_bad}/{n_verify} 通过；冻结 {n_frozen} 份哈希核对")
    if args.summary and total_bad == 0:
        write_summary(paths, ROOT / "gold" / "SUMMARY.csv")
    return 1 if total_bad else 0


if __name__ == "__main__":
    sys.exit(main())
