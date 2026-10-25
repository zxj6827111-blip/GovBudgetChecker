"""IR-16 结构模式探针：对指定扫描页顶部 700px 裁图跑 structure OCR。

只用于确认文本框模式之外，结构模式在当前服务上是否能返回表/行/列；
不做准确率统计（准确率需要人工真值，见 IR-18）。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT / "outputs" / "intake_remediation_20261024" / "ir16_structure_probe"


async def probe(pdf_path: Path, page_no: int, dpi: int, crop_height: int, out_dir: Path) -> dict:
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
        png = pix.tobytes("png")
        image = Image.open(io.BytesIO(png)).convert("RGB")
        box = (0, 0, image.width, min(image.height, crop_height))
        request = make_tile(
            image, box,
            parent_sha=hashlib.sha256(png).hexdigest(),
            file_hash=file_hash,
            page=page_no,
            pixel_to_pdf=Affine(a=72.0 / dpi, d=72.0 / dpi),
            inference_mode="structure",
        )

    directory = out_dir / f"{file_hash[:12]}-p{page_no}-{dpi}dpi-structure"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "crop.png").write_bytes(request.image)
    (directory / "request.json").write_text(
        json.dumps(request.context, ensure_ascii=False, indent=2), encoding="utf-8")

    started = time.monotonic()
    async with httpx.AsyncClient(timeout=360) as client:
        result = await ocr_page(
            client, image_png=request.image, page_number=page_no,
            timeout_seconds=360, tile_context=request.context, region_only=False,
        )
    elapsed = round((time.monotonic() - started) * 1000, 1)
    payload = result.to_dict() if hasattr(result, "to_dict") else {}
    (directory / "response.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    tables = getattr(result, "tables", []) or []
    table_shapes = []
    for table in tables:
        rows = table.get("rows") if isinstance(table, dict) else None
        table_shapes.append({
            "rows": len(rows or []),
            "widths": [len(row or []) for row in (rows or [])][:10],
        })
    record = {
        "file": str(pdf_path),
        "sha256": file_hash,
        "physical_page": page_no,
        "dpi": dpi,
        "crop_height": crop_height,
        "inference_mode": "structure",
        "service_ready": health,
        "ok": bool(getattr(result, "ok", False)),
        "error": getattr(result, "error", None),
        "blocks": len(getattr(result, "blocks", []) or []),
        "tables": len(tables),
        "table_shapes": table_shapes,
        "elapsed_ms": elapsed,
        "crop_path": str(directory / "crop.png"),
        "response_path": str(directory / "response.json"),
    }
    (directory / "result.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, action="append", required=True)
    parser.add_argument("--page", type=int, action="append", required=True)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--crop-height", type=int, default=700)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if len(args.pdf) != len(args.page):
        parser.error("--pdf and --page must be given in pairs")
    args.out.mkdir(parents=True, exist_ok=True)
    records = []
    for pdf_path, page_no in zip(args.pdf, args.page):
        record = await probe(pdf_path, page_no, args.dpi, args.crop_height, args.out)
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    (args.out / "results.json").write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(record["ok"] for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
