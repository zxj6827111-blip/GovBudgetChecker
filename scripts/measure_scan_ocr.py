"""IR-16 扫描件 OCR 实测：用产品 v3 tile 客户端对无文本页做真实推理。

不重新跑规则、不改产品逻辑；输出每页裁图、请求/响应、耗时、置信度和金额 token。
扫描页与真实空白页按图像/墨迹分类，报告明确标注样本覆盖。
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_CORPUS = Path.home() / "Desktop/决算测试上海市"
DEFAULT_INTAKE = ROOT / "outputs/intake_remediation_20261024/batch2_final/intake.jsonl.gz"
DEFAULT_OUT = ROOT / "outputs/intake_remediation_20261024/ir16_ocr_measurement"
AMOUNT = re.compile(r"^-?\d{1,3}(?:,\d{3})*(?:\.\d+)?$")


def load_materials(path: Path) -> tuple[list[dict], list[dict]]:
    full, mixed, seen = [], [], set()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            sha = str(row.get("sha256") or "")
            if sha and sha in seen:
                continue
            if sha:
                seen.add(sha)
            pages = int(row.get("pages") or 0)
            no_text = int(row.get("no_text_pages") or 0)
            if pages and no_text >= pages and row.get("first_page_has_text") is False:
                full.append(row)
            elif 0 < no_text < pages:
                mixed.append(row)
    return full, mixed


def choose_page(pdf_path: Path, row: dict, is_full: bool) -> int:
    with pymupdf.open(pdf_path) as pdf:
        count = len(pdf)
        if is_full:
            return min(6, count)
        for page_no in range(1, count + 1):
            if not pdf[page_no - 1].get_text().strip():
                return page_no
    return 1


def classify_page(page, pix) -> str:
    images = page.get_images(full=True)
    if images:
        return "scan"
    # 没有内嵌图但页面上有可见墨迹，仍按扫描/图像页处理；纯空白页不送 OCR。
    samples = pix.samples
    if not samples:
        return "blank"
    step = max(1, len(samples) // 200_000)
    non_white = sum(1 for value in samples[::step] if value < 245)
    ratio = non_white / max(1, len(samples[::step]))
    return "scan" if ratio > 0.01 else "blank"


def amount_tokens(result) -> tuple[list[str], list[float]]:
    texts, scores = [], []
    for block in getattr(result, "blocks", []) or []:
        text = str(getattr(block, "text", "") or "").strip().replace("，", ",")
        if not text:
            continue
        texts.append(text)
        score = getattr(block, "score", None)
        if score is not None:
            scores.append(float(score))
    for table in getattr(result, "tables", []) or []:
        for row in (table.get("rows") or []) if isinstance(table, dict) else []:
            for value in row:
                text = str(value or "").strip().replace("，", ",")
                if AMOUNT.fullmatch(text):
                    texts.append(text)
    return [text for text in texts if AMOUNT.fullmatch(text)], scores


async def measure_tile(*, pdf_path: Path, row: dict, page_no: int, dpi: int, out_dir: Path, is_full: bool, text_only: bool, crop_height: int):
    from src.services.input_geometry import Affine
    from src.services.ocr_client import check_ocr_health, ocr_page
    from src.services.ocr_tiles import make_tile

    health = await check_ocr_health()
    if not health.get("ok"):
        raise RuntimeError(f"OCR service not ready: {health}")
    os.environ["OCR_MODEL_FINGERPRINT"] = str(health.get("model_fingerprint") or "")
    file_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    with pymupdf.open(pdf_path) as pdf:
        page = pdf[page_no - 1]
        pix = page.get_pixmap(dpi=dpi)
        page_kind = classify_page(page, pix)
        png = pix.tobytes("png")
        image = Image.open(io.BytesIO(png)).convert("RGB")
        box = (0, 0, image.width, min(image.height, crop_height))
        parent_sha = hashlib.sha256(png).hexdigest()
        request = make_tile(
            image, box,
            parent_sha=parent_sha, file_hash=file_hash, page=page_no,
            pixel_to_pdf=Affine(a=72.0 / dpi, d=72.0 / dpi),
            inference_mode="text" if text_only else "structure",
        )
    directory = out_dir / f"{file_hash[:12]}-p{page_no}-{dpi}dpi"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "crop.png").write_bytes(request.image)
    (directory / "request.json").write_text(
        json.dumps(request.context, ensure_ascii=False, indent=2), encoding="utf-8")
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=360) as client:
        result = await ocr_page(
            client, image_png=request.image, page_number=page_no,
            timeout_seconds=360, tile_context=request.context, region_only=text_only,
        )
    elapsed = round((time.monotonic() - started) * 1000, 1)
    payload = result.to_dict() if hasattr(result, "to_dict") else {}
    (directory / "response.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    amounts, scores = amount_tokens(result)
    record = {
        "file": row.get("file"),
        "sha256": file_hash,
        "physical_page": page_no,
        "is_full_scan": is_full,
        "page_kind": page_kind,
        "inference_mode": "text" if text_only else "structure",
        "dpi": dpi,
        "crop_box": list(box),
        "crop_size": [image.width, min(image.height, crop_height)],
        "service_ready": health,
        "ok": bool(getattr(result, "ok", False)),
        "error": getattr(result, "error", None),
        "blocks": len(getattr(result, "blocks", []) or []),
        "tables": len(getattr(result, "tables", []) or []),
        "amount_tokens": len(amounts),
        "amount_examples": amounts[:20],
        "avg_block_score": round(sum(scores) / len(scores), 4) if scores else None,
        "elapsed_ms": elapsed,
        "result_elapsed_ms": getattr(result, "elapsed_ms", None),
        "crop_path": str(directory / "crop.png"),
        "response_path": str(directory / "response.json"),
    }
    (directory / "result.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--intake", type=Path, default=DEFAULT_INTAKE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dpi", type=int, default=150, choices=[150, 200])
    parser.add_argument("--limit", type=int, default=0, help="0 = all; otherwise first N tiles")
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--crop-height", type=int, default=700)
    args = parser.parse_args()
    full, mixed = load_materials(args.intake)
    selected = []
    for row in full:
        pdf_path = args.corpus / row["file"]
        if pdf_path.is_file():
            selected.append((row, choose_page(pdf_path, row, True), True))
    for row in mixed:
        pdf_path = args.corpus / row["file"]
        if pdf_path.is_file():
            selected.append((row, choose_page(pdf_path, row, False), False))
    if args.limit:
        selected = selected[: args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    records = []
    for row, page_no, is_full in selected:
        try:
            record = await measure_tile(
                pdf_path=args.corpus / row["file"], row=row, page_no=page_no,
                dpi=args.dpi, out_dir=args.out, is_full=is_full,
                text_only=args.text_only, crop_height=args.crop_height,
            )
        except Exception as exc:
            record = {"file": row.get("file"), "physical_page": page_no,
                      "is_full_scan": is_full, "ok": False, "error": repr(exc)[:500]}
        records.append(record)
        print(json.dumps({k: record.get(k) for k in (
            "file", "physical_page", "page_kind", "ok", "blocks", "tables",
            "amount_tokens", "elapsed_ms", "error")}, ensure_ascii=False))
    (args.out / "results.json").write_text(
        json.dumps({"dpi": args.dpi, "materials": len(selected), "records": records},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "dpi": args.dpi,
        "inference_mode": "text" if args.text_only else "structure",
        "crop_height": args.crop_height,
        "materials": len(selected),
        "full_scans": sum(1 for r in records if r.get("is_full_scan")),
        "mixed": sum(1 for r in records if not r.get("is_full_scan")),
        "scan_pages": sum(1 for r in records if r.get("page_kind") == "scan"),
        "blank_pages": sum(1 for r in records if r.get("page_kind") == "blank"),
        "ok": sum(1 for r in records if r.get("ok")),
        "errors": sum(1 for r in records if not r.get("ok")),
        "elapsed_ms_total": round(sum(float(r.get("elapsed_ms") or 0) for r in records), 1),
        "limits": [
            "每份材料只抽 1 页（整份扫描取第 6 页，混合材料取首个无文本页）",
            f"裁图上限 {args.crop_height}px 高；未做全页/多页 OCR",
            "真实错误率需要人工逐格真值；本脚本只提供 OCR 输出、金额 token、置信度与耗时",
        ],
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
