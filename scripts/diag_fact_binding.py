"""事实级入库体检：表格里读到的金额格，是否真正变成了带身份的事实。

既有 diag_oracle_recall / diag_batch_quality 只看"解析产物里有没有这个数"，
看不到"读到了但没归属到任何列/表"的缺口，也看不到错列、编码当名称等错数。
本脚本走与生产相同的 artifact -> 画像 -> FactContext 链路（不访问数据库），
按物理格逐一核对金额格是否被事实绑定，并统计可疑事实。

用法:
    .venv/bin/python scripts/diag_fact_binding.py <dir_or_pdf> [...] --out result.json [--workers 8]
    .venv/bin/python scripts/diag_fact_binding.py --compare before.json after.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NINE = (
    "FIN_01_income_expenditure_total", "FIN_02_income", "FIN_03_expenditure",
    "FIN_04_fiscal_grant_total", "FIN_05_general_public_expenditure",
    "FIN_06_basic_expenditure", "FIN_07_three_public", "FIN_08_gov_fund",
    "FIN_09_state_capital",
)
# 千分位金额或带小数的金额；纯短整数（编码、序号、行次）不计入金额格。
AMOUNT_RE = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$|^-?\d+\.\d+$")
GRID_METHODS = {"lines", "lines_strict", "text", "native_footer"}


def _is_amount(text: str) -> bool:
    return bool(AMOUNT_RE.match(re.sub(r"\s+", "", text or "")))


def _center_in(bbox, cell_bbox, pad=1.0) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return (cell_bbox[0] - pad <= cx <= cell_bbox[2] + pad
            and cell_bbox[1] - pad <= cy <= cell_bbox[3] + pad)


def evaluate(pdf_path: str) -> dict:
    import pdfplumber

    from src.engine.fact_context import build_fact_context_from_artifact
    from src.services.document_profile_resolver import resolve_document_profile
    from src.services.pdf_parser import PDFParser

    art = PDFParser(None).build_parse_artifact(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        page_texts = [(page.extract_text() or "") for page in pdf.pages]
    profile = resolve_document_profile(filename=Path(pdf_path).name, page_texts=page_texts)
    org = profile.organization_name.value
    year = profile.year
    kind = profile.kind
    ctx = build_fact_context_from_artifact(
        art, org_name=org, fiscal_year=year,
        report_kind=kind if kind in {"budget", "final"} else None)
    facts = list(ctx.facts) if ctx else []

    code_by_fragment = {}
    for instance in art.logical_tables:
        for uid in instance.fragment_uids:
            code_by_fragment[uid] = instance.table_code
        code_by_fragment.setdefault(instance.instance_uid, instance.table_code)

    evidence = defaultdict(list)  # page -> [(bbox, fact)]
    for fact in facts:
        for ref in fact.extra_dims.get("evidence_refs") or []:
            if ref.get("bbox") and ref.get("physical_page"):
                evidence[int(ref["physical_page"])].append((ref["bbox"], fact))

    cells_total = Counter()
    cells_bound = Counter()
    unbound_samples = []
    multi_bound = 0
    for fragment in art.physical_fragments:
        if fragment.extraction_method not in GRID_METHODS:
            continue
        code = code_by_fragment.get(fragment.fragment_uid, fragment.table_code)
        bucket = code if code in NINE else ("EXTRA:" + code if not code.startswith("UNKNOWN") else "UNKNOWN")
        amount_cells = [cell for cell in fragment.cells
                        if cell.bbox and not cell.is_header and _is_amount(cell.raw_text)]
        if bucket == "UNKNOWN" and len(amount_cells) < 4:
            continue  # 正文页零星数字不是表格格
        for cell in amount_cells:
            cells_total[bucket] += 1
            bound = [f for bbox, f in evidence.get(cell.page_number, []) if _center_in(bbox, cell.bbox)]
            if bound:
                cells_bound[bucket] += 1
                if len({f.indicator for f in bound}) > 1:
                    multi_bound += 1
            elif len(unbound_samples) < 40:
                unbound_samples.append({
                    "table": bucket, "page": cell.page_number, "row": cell.row_idx,
                    "col": cell.col_idx, "raw": cell.raw_text, "title": (fragment.title or "")[:30],
                })

    suspicious = Counter()
    fact_rows = []
    for fact in facts:
        raw = (fact.raw_value or "").strip()
        name = (fact.subject_name or "").strip()
        if re.fullmatch(r"\d{1,2}", raw):
            suspicious["raw_value_code_like"] += 1
        if name and name.replace(" ", "").isdigit():
            suspicious["subject_name_is_code"] += 1
        if not fact.org_name:
            suspicious["org_name_null"] += 1
        if fact.recognition_status != "unverified":
            suspicious["status_" + fact.recognition_status] += 1
        ref = (fact.extra_dims.get("evidence_refs") or [{}])[0]
        bbox = ref.get("bbox") or [0, 0, 0, 0]
        fact_rows.append([
            fact.table_code, ref.get("physical_page"),
            round((bbox[0] + bbox[2]) / 2), round((bbox[1] + bbox[3]) / 2),
            fact.indicator, fact.subject_code, fact.subject_name, raw, fact.recognition_status,
        ])

    instances = Counter(instance.table_code if instance.table_code in NINE else
                        ("UNKNOWN" if instance.table_code.startswith("UNKNOWN") else "EXTRA:" + instance.table_code)
                        for instance in art.logical_tables)
    facts_by_table = Counter(f.table_code for f in facts)
    return {
        "file": pdf_path,
        "kind": kind, "year": year, "org": org,
        "instances": dict(instances),
        "facts": len(facts),
        "facts_by_table": dict(facts_by_table),
        "cells_total": dict(cells_total),
        "cells_bound": dict(cells_bound),
        "multi_bound_cells": multi_bound,
        "unbound_samples": unbound_samples,
        "suspicious": dict(suspicious),
        "fact_rows": fact_rows,
    }


def _safe_evaluate(path: str) -> dict:
    try:
        return evaluate(path)
    except Exception as exc:  # 单份失败不影响整批
        return {"file": path, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-2000:]}


def summarize(results: list[dict]) -> None:
    groups = defaultdict(list)
    for item in results:
        groups[Path(item["file"]).parent.name].append(item)
    for group, items in sorted(groups.items()):
        total, bound = Counter(), Counter()
        susp = Counter()
        errors = [i for i in items if "error" in i]
        for item in items:
            total.update(item.get("cells_total", {}))
            bound.update(item.get("cells_bound", {}))
            susp.update(item.get("suspicious", {}))
        all_total, all_bound = sum(total.values()), sum(bound.values())
        print(f"\n=== {group}: {len(items)} 份, 错误 {len(errors)}, 事实 {sum(i.get('facts', 0) for i in items)}")
        print(f"金额格 {all_total}, 已绑定 {all_bound}, 未绑定 {all_total - all_bound}")
        for key in sorted(total, key=lambda k: -(total[k] - bound[k])):
            gap = total[key] - bound[key]
            if gap:
                print(f"   {key:<40} 未绑定 {gap:>5} / {total[key]}")
        print("可疑事实:", dict(susp))
        worst = sorted(items, key=lambda i: -(sum(i.get("cells_total", {}).values()) - sum(i.get("cells_bound", {}).values())))[:8]
        for item in worst:
            gap = sum(item.get("cells_total", {}).values()) - sum(item.get("cells_bound", {}).values())
            if gap:
                print(f"   {Path(item['file']).name[:30]:<32} 未绑定 {gap}")
        for item in errors:
            print("   ERROR", Path(item["file"]).name, item["error"])


def compare(before_path: str, after_path: str) -> None:
    before = {Path(i["file"]).name + "|" + Path(i["file"]).parent.name: i for i in json.load(open(before_path))}
    after = {Path(i["file"]).name + "|" + Path(i["file"]).parent.name: i for i in json.load(open(after_path))}
    lost_total = changed_total = gained_total = 0
    for key in sorted(before):
        if key not in after or "error" in before[key] or "error" in after[key]:
            continue
        index = lambda rows: {tuple(r[:4]): r for r in rows}  # noqa: E731
        b, a = index(before[key]["fact_rows"]), index(after[key]["fact_rows"])
        lost = [b[k] for k in b if k not in a]
        gained = [a[k] for k in a if k not in b]
        changed = [(b[k], a[k]) for k in b if k in a and b[k][4:8] != a[k][4:8]]
        lost_total += len(lost)
        gained_total += len(gained)
        changed_total += len(changed)
        if lost or changed:
            print(f"\n{key}: 丢失 {len(lost)}, 变化 {len(changed)}, 新增 {len(gained)}")
            for row in lost[:6]:
                print("   LOST   ", row)
            for old, new in changed[:6]:
                print("   CHANGED", old[4:8], "->", new[4:8], "@", old[:2])
    print(f"\n合计: 丢失 {lost_total}, 变化 {changed_total}, 新增 {gained_total}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--out")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--compare", nargs=2)
    parser.add_argument("--summary")
    args = parser.parse_args()
    if args.compare:
        compare(*args.compare)
        return
    if args.summary:
        summarize(json.load(open(args.summary)))
        return
    pdfs = []
    for raw in args.paths:
        path = Path(os.path.expanduser(raw))
        pdfs.extend(sorted(path.glob("*.pdf")) if path.is_dir() else [path])
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_safe_evaluate, str(p)): p for p in pdfs}
        for done, future in enumerate(as_completed(futures), start=1):
            results.append(future.result())
            print(f"[{done}/{len(pdfs)}] {futures[future].name}", file=sys.stderr, flush=True)
    results.sort(key=lambda item: item["file"])
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, default=str))
    summarize(results)


if __name__ == "__main__":
    main()
