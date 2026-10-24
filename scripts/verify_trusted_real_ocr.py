"""在独立服务对真实扫描原件做保真图块推理，保留实际输入与原始响应。"""
from __future__ import annotations
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import argparse
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--box', default='760,180,2040,948')
    ap.add_argument('--text-only', action='store_true')
    args = ap.parse_args()
    import httpx
    import pymupdf
    from PIL import Image
    from src.services.input_geometry import Affine
    from src.services.ocr_tiles import make_tile
    from src.services.ocr_client import ocr_page
    directory = ROOT / 'outputs/pdf_trusted_ingest_20261020/real_scan'
    source = json.loads((directory / 'source.json').read_text())
    path = Path(source['frozen_path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == path.stem
    async with httpx.AsyncClient(timeout=10) as client:
        health = (await client.get('http://127.0.0.1:19010/health')).json()
        assert health['models_ready'],health
        assert health['model_fingerprint'] == hashlib.sha256((ROOT/'models/MANIFEST.sha256').read_bytes()).hexdigest()
        os.environ.update(OCR_SERVICE_URL='http://127.0.0.1:19010',OCR_MODEL_FINGERPRINT=health['model_fingerprint'])
        with pymupdf.open(path) as pdf:
            pix = pdf[4].get_pixmap(dpi=300)
            image = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
            parent_data = pix.tobytes('png')
            parent_sha = hashlib.sha256(parent_data).hexdigest()
            (directory / ('render-300dpi-' + parent_sha + '.png')).write_bytes(parent_data)
            request = make_tile(image,tuple(map(int,args.box.split(','))),parent_sha=parent_sha,
                file_hash=path.stem,page=5,pixel_to_pdf=Affine(a=.24,d=.24),
                inference_mode='text' if args.text_only else 'structure')
        directory = directory / ('probe-' + request.context['tile_id'][:16] + '-'
                                  + health['pipeline_fingerprint'][:12] + '-' + uuid.uuid4().hex[:8])
        directory.mkdir()
        (directory/'runtime_identity.json').write_text(json.dumps(health,ensure_ascii=False,indent=2))
        (directory/'actual_model_input.png').write_bytes(request.image)
        (directory/'request.json').write_text(json.dumps(request.context,ensure_ascii=False,indent=2))
        result = await ocr_page(client,image_png=request.image,page_number=5,tile_context=request.context,
                                region_only=args.text_only)
        (directory/'response.json').write_text(json.dumps(result.to_dict(),ensure_ascii=False,indent=2))
        record = {'source_sha256':path.stem,'physical_page':5,'real_scan':True,
                  'input_size':request.context['size_px'],'ok':result.ok,'error':result.error,
                  'blocks':len(result.blocks),'tables':len(result.tables),
                  'model_input_size_px':result.provenance.get('model_input_size_px'),
                  'detector_tensor_sizes_px':result.provenance.get('detector_tensor_sizes_px'),
                  'detector_input_verified':result.provenance.get('detector_input_verified'),
                  'inference_padding_px':result.provenance.get('inference_padding_px'),
                  'detector_requested_limit_px':result.provenance.get('detector_requested_limit_px'),
                  'input_resize_applied':result.provenance.get('input_resize_applied'),
                  'elapsed_ms':result.elapsed_ms,'scope':'real_original_crop_probe_not_full_document_acceptance'}
        (directory/'probe_result.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
        print(json.dumps(record,ensure_ascii=False))
        if not result.ok:raise RuntimeError(result.error)


if __name__ == '__main__': asyncio.run(main())
