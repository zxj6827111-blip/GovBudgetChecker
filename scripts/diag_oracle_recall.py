"""对账评测：解析产物的金额 vs oracle 逐页金额全集。

oracle 为 PyMuPDF 数据层提取（与生产解析器独立），其 amounts 是页面上
真实存在的金额字符串——用它衡量"解析是否漏数"最直接。

指标：
- 页级召回：oracle 金额中，被解析单元格覆盖的比例
- 漏数页：召回 < 阈值的页（说明表格结构或区域识别失效）

用法: .venv/bin/python scripts/diag_oracle_recall.py [doc_dir ...]
"""
from __future__ import annotations

import glob
import json
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.pdf_parser import PDFParser  # noqa: E402

AMOUNT = re.compile(r"^-?\d{1,3}(?:,\d{3})*\.\d{2}$|^-?\d+\.\d{2}$")


def dec(s: str) -> Decimal | None:
    t = s.replace(",", "").replace("，", "").strip()
    if not t:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def page_amounts_from_artifact(art) -> dict[int, set[Decimal]]:
    out: dict[int, set[Decimal]] = {}
    for cell in (c for t in art.table_fragments for c in t.cells):
        raw = str(cell.raw_text or "").strip().replace("，", ",")
        if not AMOUNT.match(raw):
            continue
        v = dec(raw)
        if v is not None:
            out.setdefault(cell.page_number, set()).add(v)
    return out


def evaluate(doc_dir: Path, threshold: float = 0.95) -> tuple | None:
    oracle_path = doc_dir / "source_oracle.json"
    pdf_path = doc_dir / "source.pdf"
    if not oracle_path.exists() or not pdf_path.exists():
        return None
    oracle = json.loads(oracle_path.read_text())
    art = PDFParser(None).build_parse_artifact(str(pdf_path.resolve()))
    parsed = page_amounts_from_artifact(art)

    rows = []
    total_truth = total_hit = 0
    for page in oracle.get("pages", []):
        no = int(page.get("page") or 0)
        # 只评测表格内金额：table_indices 非空表示该金额落在识别出的表格区域内。
        # 正文说明里的金额不属表格解析覆盖范围，混入会低估召回。
        truth = {dec(a.get("raw_text", "")) for a in (page.get("amounts") or [])
                 if a.get("table_indices") and dec(a.get("raw_text", "")) is not None}
        if not truth:
            continue
        got = parsed.get(no, set())
        hit = truth & got
        total_truth += len(truth)
        total_hit += len(hit)
        rows.append((no, len(truth), len(hit)))
    if total_truth == 0:
        return None
    recall = total_hit / total_truth
    bad = [r for r in rows if r[2] / r[1] < threshold]
    return doc_dir.name, total_truth, total_hit, recall, bad, len(rows)


def main(dirs: list[str]) -> None:
    targets: list[Path] = []
    for d in dirs:
        p = Path(d)
        if (p / "source_oracle.json").exists():
            targets.append(p)
        else:
            targets.extend(sorted(x for x in p.rglob("SH*") if x.is_dir()))
    if not targets:
        print("未找到含 source_oracle.json 的目录")
        return

    print(f"{'文档':<16}{'oracle金额':>10}{'命中':>8}{'召回':>9}{'问题页':>7}")
    print("-" * 55)
    agg_truth = agg_hit = 0
    all_bad = Counter()
    for d in targets:
        try:
            r = evaluate(d)
        except Exception as e:  # noqa: BLE001
            print(f"{d.name:<16} ERR {type(e).__name__}: {e}")
            continue
        if not r:
            continue
        name, truth, hit, recall, bad, npages = r
        agg_truth += truth
        agg_hit += hit
        for page, t, h in bad:
            all_bad[(name, page)] = (t, h)
        flag = f"{len(bad)}/{npages}" if bad else "0"
        print(f"{name:<16}{truth:>10}{hit:>8}{recall:>8.2%}{flag:>7}")
    print("-" * 55)
    print(f"总体召回 {agg_hit}/{agg_truth} = {agg_hit/max(agg_truth,1):.2%}")
    if all_bad:
        print(f"\n问题页（召回 <95%），共 {len(all_bad)} 页：")
        for (name, page), (t, h) in sorted(all_bad.items())[:20]:
            print(f"   {name} p{page}: {h}/{t}")


if __name__ == "__main__":
    args = sys.argv[1:] or ["outputs/shanghai_2024_new9_acceptance_20261016/documents"]
    main(args)
