#!/usr/bin/env python3
"""R-05：其余 4 份 B1 标注的原件回核（全 defect/自由发现 + 全部 pass 引文）。

对每条 annotation 的 page+quote 做原 PDF 文本层逐字核验（空白归一），
并渲染 defect/自由发现所在页供目视复核。任何 quote 不命中都记录，不静默放过。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import fitz

B1 = Path("/Volumes/workSSD/software development/GovBudgetChecker/outputs/gold_b1_20261103/run_20261103T150928")
OUT = Path("/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_closeout_20261104_work/b1_verify_all")
PDFS = json.loads((B1 / "b1_pdfs.json").read_text(encoding="utf-8"))


def compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def segments(quote: str):
    parts = re.split(r"[（(][^）)]*[）)]|\d[\d,.]*\s*(?:万元|亿元|元|%|％)?", compact(quote))
    return [part for part in parts if len(re.findall(r"[\u4e00-\u9fff]", part)) >= 4]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pages").mkdir(exist_ok=True)
    report = {}
    for sample_id, pdf_path in PDFS.items():
        annotation = json.loads((B1 / "annotations" / sample_id / "annotation.json").read_text(encoding="utf-8"))
        doc = fitz.open(pdf_path)
        pages = [compact(doc[index].get_text()) for index in range(doc.page_count)]
        entries, render_pages = [], set()

        def verify(kind, check_id, label, value):
            page, quote = value.get("page"), str(value.get("quote") or "")
            ok = False
            mode = "missing"
            if isinstance(page, int) and 1 <= page <= len(pages) and quote:
                needle = compact(quote)
                if needle and needle in pages[page - 1]:
                    ok, mode = True, "exact_compact"
                else:
                    parts = segments(quote)
                    if parts and all(part in pages[page - 1] for part in parts):
                        ok, mode = True, "segments"
                    else:
                        mode = "not_found"
                render_pages.add(page)
            entries.append({"kind": kind, "check_id": check_id, "label": label,
                            "page": page, "quote": quote[:160], "ok": ok, "mode": mode})

        for check in annotation.get("checks") or []:
            for item in check.get("items") or []:
                for value in item.get("values") or []:
                    if isinstance(value, dict):
                        verify(check.get("verdict"), check.get("check_id"),
                               str(item.get("label") or "")[:80], value)
        for index, finding in enumerate(annotation.get("free_findings") or []):
            verify("free", f"free#{index}", str(finding.get("expected") or "")[:80],
                   {"page": finding.get("page"), "quote": finding.get("quote")})
        # 渲染 defect/free 页
        for page in sorted(render_pages):
            pix = doc[page - 1].get_pixmap(dpi=110)
            pix.save(OUT / "pages" / f"{sample_id}_p{page:02d}.png")
        bad = [entry for entry in entries if not entry["ok"]]
        report[sample_id] = {
            "pdf": pdf_path,
            "pdf_sha256": __import__("hashlib").sha256(Path(pdf_path).read_bytes()).hexdigest(),
            "pages": doc.page_count,
            "quotes_total": len(entries),
            "quotes_ok": len(entries) - len(bad),
            "quotes_bad": bad,
            "rendered_pages": sorted(render_pages),
            "defect_checks": [c["check_id"] for c in annotation["checks"] if c["verdict"] == "defect"],
            "free_findings": len(annotation.get("free_findings") or []),
        }
        doc.close()
    (OUT / "b1_verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({sid: {"quotes": row["quotes_total"], "ok": row["quotes_ok"],
                            "bad": len(row["quotes_bad"]), "pages": row["rendered_pages"]}
                      for sid, row in report.items()}, ensure_ascii=False, indent=2))
    return 0 if not any(row["quotes_bad"] for row in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
