"""用已存档的裁图坐标与 OCR 响应重算 IR-16b 结果，不再调用 OCR。

补一条判据：OCR 多出来的数字如果在本页文字层里能找到（只是落在像素带之外，
被裁图切掉了一两个像素而 OCR 仍读对），记为边缘词，不算幻觉；
整页都找不到的才叫真多识别。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from measure_ocr_textlayer_oracle import (  # noqa: E402
    compare_chars, compare_tokens, amount_tokens, words_in_band,
)

DEFAULT_CORPUS = Path.home() / "Desktop/决算测试上海市"


def rescore(run_dir: Path, corpus: Path, dpi: int) -> dict:
    records = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))["records"]
    pages = 0
    tot = Counter()
    zero_nz = {"zero": Counter(), "nonzero": Counter()}
    char_tot = Counter()
    spurious_log: list[dict] = []
    for rec in records:
        if not rec.get("ok") or not rec.get("band_px"):
            continue
        zoom = dpi / 72.0
        y0, y1 = rec["band_px"]
        with pymupdf.open(corpus / rec["file"]) as doc:
            page = doc[rec["physical_page"] - 1]
            oracle_text = " ".join(words_in_band(page, y0 / zoom, y1 / zoom))
            page_tokens = set(amount_tokens(page.get_text()))
        sub = [p for p in run_dir.iterdir() if p.is_dir() and p.name.startswith(rec["sha256"][:12])]
        ocr_text = json.loads((sub[0] / "response.json").read_text(encoding="utf-8"))["ocr_text"]
        leftover, stat = compare_tokens(amount_tokens(oracle_text), amount_tokens(ocr_text))
        total, hit, extra = compare_chars(oracle_text, ocr_text)
        remaining = {t: n for t, n in leftover.items() if n > 0}
        edge = sum(n for t, n in remaining.items() if t in page_tokens)
        true_extra = sum(n for t, n in remaining.items() if t not in page_tokens)
        pages += 1
        for key, value in (("oracle", stat.oracle_amounts), ("exact", stat.exact), ("soft", stat.soft),
                           ("delimiter", stat.delimiter), ("misread", stat.misread),
                           ("missing", stat.missing)):
            tot[key] += value
        tot["edge"] += edge
        tot["true_extra"] += true_extra
        for label, bucket in (("zero", stat.zero), ("nonzero", stat.nonzero)):
            for key in ("oracle", "exact", "delimiter", "misread", "missing"):
                zero_nz[label][key] += bucket.get(key, 0)
        char_tot["oracle"] += total
        char_tot["hit"] += hit
        char_tot["extra"] += extra
        if edge or true_extra:
            spurious_log.append({"file": Path(rec["file"]).name, "page": rec["physical_page"],
                                 "edge": edge, "true_extra": true_extra,
                                 "tokens": sorted(t for t in remaining if remaining[t] > 0)})
    value_wrong = tot["delimiter"] + tot["misread"]
    out = {
        "run_dir": str(run_dir),
        "dpi": dpi,
        "pages": pages,
        "amount_tokens": dict(tot),
        "exact_rate": round(tot["exact"] / max(1, tot["oracle"]), 4),
        "value_wrong": value_wrong,
        "value_wrong_rate": round(value_wrong / max(1, tot["oracle"]), 4),
        "delimiter_error": tot["delimiter"],
        "digit_misread": tot["misread"],
        "missing": tot["missing"],
        "soft_format_only": tot["soft"],
        "spurious_split": {"edge_outside_band_but_on_page": tot["edge"],
                           "not_found_anywhere_on_page": tot["true_extra"]},
        "nonzero_only": {**dict(zero_nz["nonzero"]),
                         "value_wrong_rate": round(
                             (zero_nz["nonzero"]["delimiter"] + zero_nz["nonzero"]["misread"])
                             / max(1, zero_nz["nonzero"]["oracle"]), 4)},
        "zero_only": {**dict(zero_nz["zero"]),
                      "value_wrong_rate": round(
                          (zero_nz["zero"]["delimiter"] + zero_nz["zero"]["misread"])
                          / max(1, zero_nz["zero"]["oracle"]), 4)},
        "chars": {**dict(char_tot), "hit_rate": round(char_tot["hit"] / max(1, char_tot["oracle"]), 4)},
        "spurious_pages": spurious_log,
    }
    (run_dir / "rescored.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()
    for run in args.runs:
        print(json.dumps(rescore(run, args.corpus, args.dpi), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
