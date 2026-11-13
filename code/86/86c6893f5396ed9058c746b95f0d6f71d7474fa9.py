"""只访问专属隔离 OCR；原件按哈希读取，输出完整真实响应与资源证据。"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import time
import fitz
import httpx
from PIL import Image
from src.services.ocr_tiles import make_tile
from src.services.input_geometry import Affine
from src.services.ocr_client import ocr_page


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("output")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--width", type=int, default=2048)
    ap.add_argument("--height", type=int, default=1536)
    ap.add_argument("--x",type=int,default=0)
    ap.add_argument("--y",type=int,default=0)
    args = ap.parse_args()
    source = Path(args.pdf)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    with fitz.open(source) as pdf:
        page = pdf[args.page - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    # 原图上限内直接裁剪；不缩字。完整页另保留用于肉眼复核。
    image.save(out / "original-page-300dpi.png")
    box = (args.x, args.y, min(image.width, args.x+args.width), min(image.height, args.y+args.height))
    parent = hashlib.sha256((out / "original-page-300dpi.png").read_bytes()).hexdigest()
    tile = make_tile(
        image,
        box,
        parent_sha=parent,
        file_hash=file_hash,
        page=args.page,
        pixel_to_pdf=Affine(a=72 / 300, d=72 / 300),
    )
    (out / "tile.png").write_bytes(tile.image)
    started = time.monotonic()
    async with httpx.AsyncClient() as client:
        health = (await client.get(os.environ["OCR_SERVICE_URL"] + "/health")).json()
        os.environ["OCR_MODEL_FINGERPRINT"] = health["model_fingerprint"]
        result = await ocr_page(
            client, image_png=tile.image, page_number=args.page, tile_context=tile.context
        )
    duration = time.monotonic() - started
    data = {
        "source": str(source),
        "source_sha256": file_hash,
        "health": health,
        "tile_context": tile.context,
        "elapsed_seconds": duration,
        "result": result.to_dict(),
        "scope": "real_original_partial_page_inference_not_independent_acceptance",
    }
    (out / "result.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == file_hash
    print(
        json.dumps(
            {
                "ok": result.ok,
                "error": result.error,
                "blocks": len(result.blocks),
                "tables": len(result.tables),
                "seconds": duration,
                "output": str(out),
            }
        )
    )
    assert result.ok, result.error


asyncio.run(main())
