"""冻结真实PDF字符、矢量网格、页面渲染及指定基线解析器的产物。可断点续跑。"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline', type=Path, required=True)
    ap.add_argument('--render-dpi', type=int, default=150)
    args = ap.parse_args()
    base = args.baseline.resolve()
    sys.path.insert(0, str(base / 'code'))
    import pymupdf
    from src.services.pdf_parser import PDFParser
    records = json.loads((base / 'source_manifest.json').read_text())
    for index, record in enumerate(records, 1):
        sha = record['sha256']
        source = base / 'sources' / (sha + '.pdf')
        target = base / 'evidence' / sha
        if (target / 'complete.json').exists():
            continue
        if hashlib.sha256(source.read_bytes()).hexdigest() != sha:
            raise ValueError('frozen PDF hash mismatch')
        target.mkdir(parents=True, exist_ok=True)
        artifact = PDFParser(None).build_parse_artifact(str(source))
        with gzip.open(target / 'artifact.json.gz', 'wt', encoding='utf-8') as f:
            json.dump(artifact.to_dict(), f, ensure_ascii=False)
        doc = pymupdf.open(source)
        for no, page in enumerate(doc, 1):
            data = {'physical_page': no, 'rect': list(page.rect), 'rotation': page.rotation,
                    'cropbox': list(page.cropbox), 'raw_text': page.get_text('rawdict'),
                    'drawings': page.get_drawings()}
            with gzip.open(target / f'p{no:04}.json.gz', 'wt', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, default=list)
            png = target / f'p{no:04}.png'
            if not png.exists():
                page.get_pixmap(dpi=args.render_dpi).save(png)
        (target / 'complete.json').write_text(json.dumps({'sha256': sha, 'pages': len(doc),
            'render_dpi': args.render_dpi, 'parser_fingerprint': artifact.parser_fingerprint}))
        doc.close()
        print(f'{index}/{len(records)} {record["year"]} {record["file"]}', flush=True)

if __name__ == '__main__':
    main()
