"""OCR 精度实测：把原生页渲染为图片，走本地 OCR 服务，比对数字还原率。

用于回答"如果 PDF 是扫描件，现有 OCR 链路还能不能支撑规则检查"。

用法: .venv/bin/python scripts/diag_ocr_precision.py <pdf> <page> [dpi]
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import httpx
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OCR_URL = "http://127.0.0.1:9010/ocr"
AMOUNT = re.compile(r"^-?\d{1,3}(?:,\d{3})*\.\d{2}$")


def native_amounts(pdf: str, page_no: int) -> list[str]:
    doc = pymupdf.open(pdf)
    words = doc[page_no - 1].get_text("words")
    out = []
    for w in words:
        t = w[4].strip().replace("，", ",")
        if AMOUNT.match(t):
            out.append(t)
    return out


def ocr_amounts(pdf: str, page_no: int, dpi: int) -> tuple[list[str], list[float], float]:
    doc = pymupdf.open(pdf)
    page = doc[page_no - 1]
    pix = page.get_pixmap(dpi=dpi)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        path = fh.name
        pix.save(path)
    try:
        with httpx.Client(timeout=180) as client:
            with open(path, "rb") as f:
                resp = client.post(OCR_URL, files={"image": ("page.png", f, "image/png")})
        resp.raise_for_status()
        payload = resp.json()
    finally:
        Path(path).unlink(missing_ok=True)

    blocks = payload.get("blocks") or payload.get("text_blocks") or []
    texts, scores = [], []
    for b in blocks:
        t = str(b.get("text") or "").strip().replace("，", ",")
        if t:
            texts.append(t)
            if b.get("score") is not None:
                scores.append(float(b["score"]))
    amounts = [t for t in texts if AMOUNT.match(t)]
    avg = sum(scores) / len(scores) if scores else 0.0
    return amounts, scores, avg


def main(pdf: str, page_no: int, dpi: int) -> None:
    truth = native_amounts(pdf, page_no)
    got, scores, avg = ocr_amounts(pdf, page_no, dpi)
    tset, gset = set(truth), set(got)
    hit = tset & gset
    print(f"页 {page_no} @ {dpi}DPI")
    print(f"  原生数字 {len(truth)}（去重 {len(tset)}）")
    print(f"  OCR 数字  {len(got)}（去重 {len(gset)}），平均置信度 {avg:.3f}")
    print(f"  完全命中 {len(hit)}/{len(tset)} = {len(hit)/max(len(tset),1):.1%}")
    miss = sorted(tset - gset)
    extra = sorted(gset - tset)
    if miss:
        print(f"  漏识别 {len(miss)}: {miss[:8]}")
    if extra:
        print(f"  多识别 {len(extra)}: {extra[:8]}")
    # 数值级容错：把 OCR 结果按数值匹配（允许逗号/小数点识别偏差）
    def norm(s):
        try:
            return Decimal(s.replace(",", ""))
        except Exception:  # noqa: BLE001
            return None
    tn = {norm(t) for t in tset if norm(t) is not None}
    gn = {norm(g) for g in gset if norm(g) is not None}
    print(f"  数值级命中 {len(tn & gn)}/{len(tn)} = {len(tn & gn)/max(len(tn),1):.1%}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 200)
