"""真实新扫描原件的故障注入：删除首轮一格返回，必须从原图补读恢复。"""

import argparse
import asyncio
import copy
import hashlib
import io
import json
import os
import time
from pathlib import Path
import fitz
import httpx
from PIL import Image
from src.services import ocr_client
from src.services.ocr_client import OcrBlock
from src.services.ocr_tiles import recognize_tiles, png_bytes
from src.services.input_geometry import Affine


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("truth")
    parser.add_argument("output")
    args = parser.parse_args()
    truth_path = Path(args.truth)
    truth = json.loads(truth_path.read_text())
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    source = Path(truth["source"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == truth["source_sha256"]
    region = truth["region_px"]
    page_number = truth["physical_page"]
    affine = Affine(a=0.24, d=0.24, e=region[0] * 0.24, f=region[1] * 0.24)
    with fitz.open(source) as pdf:
        page = pdf[page_number - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
        original = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        image = original.crop(region)
        image.save(out / "region-original-300dpi.png")
        parent = hashlib.sha256(png_bytes(original)).hexdigest()

        def high_resolution(box, attempt):
            zoom = (450 if attempt == 1 else 600) / 72
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=fitz.Rect(affine.box(box)))
            crop = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            return (
                crop,
                (0, 0, crop.width, crop.height),
                Affine(a=1 / zoom, d=1 / zoom, e=pix.x / zoom, f=pix.y / zoom),
            )

        infer = ocr_client.ocr_page
        requests = []
        deleted = []

        async def injected(client, **kwargs):
            response = await infer(client, **kwargs)
            context = kwargs["tile_context"]
            requests.append({"context": context, "response": response.to_dict()})
            if context["attempt"] == 0 and response.ok:
                matrix = Affine(*context["pixel_to_pdf"])
                target = Affine(a=0.24, d=0.24).box(truth["cells"][0]["bbox_px"])
                retained = []
                for b in response.blocks:
                    box = matrix.box(b.bbox) if b.bbox else None
                    if (
                        box
                        and target[0] < (box[0] + box[2]) / 2 < target[2]
                        and target[1] < (box[1] + box[3]) / 2 < target[3]
                    ):
                        deleted.append(
                            {"tile_id": context["tile_id"], "block": b.to_dict(), "pdf_bbox": box}
                        )
                    else:
                        retained.append(b)
                response = copy.deepcopy(response)
                response.blocks = retained
            return response

        ocr_client.ocr_page = injected
        start = time.monotonic()
        async with httpx.AsyncClient() as client:
            url = os.environ["OCR_SERVICE_URL"]
            assert url == "http://127.0.0.1:19017"
            health = (await client.get(url + "/health")).json()
            os.environ["OCR_MODEL_FINGERPRINT"] = health["model_fingerprint"]
            initial, rechecks, state = await recognize_tiles(
                image,
                file_hash=truth["source_sha256"],
                page=page_number,
                pixel_to_pdf=affine,
                client=client,
                model=health["model_fingerprint"],
                pipeline=health["pipeline_fingerprint"],
                original_parent_sha=parent,
                checkpoint_path=out / "checkpoint.json",
                high_resolution=high_resolution,
            )
        actual = []
        for context, response in initial + rechecks:
            matrix = Affine(*context["pixel_to_pdf"])
            for block in response.blocks:
                if block.bbox:
                    actual.append(
                        {
                            "text": block.text,
                            "bbox": matrix.box(block.bbox),
                            "attempt": context["attempt"],
                            "score": block.score,
                        }
                    )
        comparisons = []
        for expected in truth["cells"]:
            box = Affine(a=0.24, d=0.24).box(expected["bbox_px"])
            aligned = [
                b
                for b in actual
                if box[0] < (b["bbox"][0] + b["bbox"][2]) / 2 < box[2]
                and box[1] < (b["bbox"][1] + b["bbox"][3]) / 2 < box[3]
            ]
            comparisons.append(
                {
                    "expected": expected,
                    "aligned": aligned,
                    "exact": any(b["text"] == expected["raw_text"] for b in aligned),
                }
            )
        receipt = {
            "scope": "real_new_scan_fault_injection_partial_literal_recovery_not_document_acceptance",
            "truth_sha256": hashlib.sha256(truth_path.read_bytes()).hexdigest(),
            "deleted": deleted,
            "state": state,
            "requests": requests,
            "comparisons": comparisons,
            "elapsed_seconds": time.monotonic() - start,
            "restored_deleted_cell": bool(deleted)
            and any(
                b["attempt"] > 0 and b["text"] == truth["cells"][0]["raw_text"]
                for b in comparisons[0]["aligned"]
            ),
            "literal_exact": sum(c["exact"] for c in comparisons),
            "denominator": len(comparisons),
        }
        (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    k: receipt[k]
                    for k in [
                        "restored_deleted_cell",
                        "literal_exact",
                        "denominator",
                        "elapsed_seconds",
                    ]
                }
            ),
            flush=True,
        )
        assert (
            receipt["restored_deleted_cell"] and receipt["literal_exact"] == receipt["denominator"]
        )


if __name__ == "__main__":
    asyncio.run(main())
