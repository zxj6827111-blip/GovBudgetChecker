"""冻结的真实高分截断反例：仅按原图位置扩区重读，保留前后字面。"""

import argparse
import asyncio
import hashlib
import io
import json
import os
import time
from pathlib import Path
import fitz
import httpx
from PIL import Image
from src.services.ocr_client import OcrPageResult, OcrBlock, ocr_page
from src.services.ocr_tiles import seam_regions, make_tile, png_bytes
from src.services.input_geometry import Affine


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("previous")
    parser.add_argument("output")
    args = parser.parse_args()
    previous = Path(args.previous)
    out = Path(args.output)
    out.mkdir(exist_ok=False, parents=True)
    old = json.loads(previous.read_text())
    source = Path(old["source"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == old["source_sha256"]
    data = old["result"]
    result = OcrPageResult(data["page_number"], blocks=[OcrBlock(**b) for b in data["blocks"]])
    context = old["tile_context"]
    page_image = Image.open(previous.parent / "original-page-300dpi.png")
    proposals = seam_regions(context, result, page_image.size)
    target = next(
        b for b in result.blocks if b.text == "0.0" and b.bbox[2] >= context["size_px"][0] - 8
    )
    page_box = [
        target.bbox[0] + context["crop_box_px"][0],
        target.bbox[1] + context["crop_box_px"][1],
        target.bbox[2] + context["crop_box_px"][0],
        target.bbox[3] + context["crop_box_px"][1],
    ]
    box = next(
        b
        for b in proposals
        if b[0] < page_box[0] and b[1] < page_box[1] and b[2] > page_box[2] and b[3] > page_box[3]
    )
    with fitz.open(source) as pdf:
        zoom = 450 / 72
        page = pdf[data["page_number"] - 1]
        pix = page.get_pixmap(
            matrix=fitz.Matrix(zoom, zoom), clip=fitz.Rect(Affine(a=0.24, d=0.24).box(box))
        )
        image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        affine = Affine(a=1 / zoom, d=1 / zoom, e=pix.x / zoom, f=pix.y / zoom)
    tile = make_tile(
        image,
        (0, 0, image.width, image.height),
        parent_sha=hashlib.sha256(png_bytes(image)).hexdigest(),
        file_hash=old["source_sha256"],
        page=data["page_number"],
        pixel_to_pdf=affine,
        attempt=1,
    )
    (out / "expanded-450dpi.png").write_bytes(tile.image)
    start = time.monotonic()
    async with httpx.AsyncClient() as client:
        url = os.environ["OCR_SERVICE_URL"]
        assert url == "http://127.0.0.1:19017"
        health = (await client.get(url + "/health")).json()
        os.environ["OCR_MODEL_FINGERPRINT"] = health["model_fingerprint"]
        recovered = await ocr_page(
            client,
            image_png=tile.image,
            page_number=data["page_number"],
            tile_context=tile.context,
            region_only=True,
            timeout_seconds=30,
        )
    original_bbox = Affine(*context["pixel_to_pdf"]).box(target.bbox)
    candidates = [
        b
        for b in recovered.blocks
        if b.bbox
        and abs(sum(affine.box(b.bbox)[1::2]) / 2 - sum(original_bbox[1::2]) / 2) < 3
        and max(affine.box(b.bbox)[0], original_bbox[0])
        < min(affine.box(b.bbox)[2], original_bbox[2])
    ]
    receipt = {
        "source": str(source),
        "source_sha256": old["source_sha256"],
        "page": data["page_number"],
        "scope": "real_original_seam_counterexample_not_blind_acceptance",
        "before": target.to_dict(),
        "before_pdf_bbox": original_bbox,
        "proposed_region_px": box,
        "tile_context": tile.context,
        "elapsed_seconds": time.monotonic() - start,
        "result": recovered.to_dict(),
        "aligned_candidates": [b.to_dict() for b in candidates],
        "restored_literal": len(candidates) == 1 and candidates[0].text == "0.00",
    }
    (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {k: receipt[k] for k in ("restored_literal", "elapsed_seconds", "aligned_candidates")}
        )
    )
    assert recovered.ok and receipt["restored_literal"], (
        recovered.error or receipt["aligned_candidates"]
    )


if __name__ == "__main__":
    asyncio.run(main())
