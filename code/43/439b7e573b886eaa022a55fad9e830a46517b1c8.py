"""先冻结原页证据；本阶段不导入被测项目的解析器或财政事实模块。"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import time

import pymupdf

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
INPUT = Path('/Users/zhangxingjin/Desktop/决算测试上海市/2024')
TITLES = {
    '01': '收入支出决算总表',
    '02': '收入决算表',
    '03': '支出决算表',
    '04': '财政拨款收入支出决算总表',
    '05': '一般公共预算财政拨款支出决算表',
    '06': '一般公共预算财政拨款基本支出决算表',
    '07': '财政拨款“三公”经费支出决算表',
    '08': '政府性基金预算财政拨款收入支出决算表',
    '09': '国有资本经营预算财政拨款收入支出决算表',
}
MONEY = re.compile(r'-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{1,4}')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def compact(s):
    return re.sub(r'\s+', '', str(s or '')).replace('，', ',')


def write(p, v):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n')


def contains(b, w):
    x, y = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
    return b[0] - .5 <= x <= b[2] + .5 and b[1] - .5 <= y <= b[3] + .5


def main():
    if (BASE / 'source_freeze.json').exists():
        raise RuntimeError('冻结记录已存在，不覆盖；需要新一轮时创建新的验收目录。')
    files = sorted(INPUT.glob('*.pdf'))
    code = {str(p.relative_to(ROOT)): sha(p) for name in ('src', 'api') for p in (ROOT / name).rglob('*.py')}
    write(BASE / 'code_before.json', code)
    previous = json.loads((ROOT / 'outputs/native_structure_repair_20261015/final_summary.json').read_text())
    baseline = {name: {'expected': digest, 'actual': sha(ROOT / name), 'matches': sha(ROOT / name) == digest}
                for name, digest in previous['changed_files'].items()}
    write(BASE / 'v23_identity.json', baseline)
    known = set()
    for path in [ROOT / 'outputs/native_structure_repair_20261015/original_verification.json',
                 ROOT / 'outputs/native_structure_repair_20261015/frozen_corpus/results.json',
                 ROOT / 'outputs/reading_recovery_20261015/service/service_receipt.json']:
        payload = json.loads(path.read_text())
        def collect(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    if k in ('sha256', 'source_file_hash', 'file_hash') and isinstance(v, str):
                        known.add(v)
                    collect(v)
            elif isinstance(value, list):
                for v in value:
                    collect(v)
        collect(payload)
    inventory = []
    start = time.monotonic()
    for idx, src in enumerate(files, 1):
        doc_id = f'SH2024_{idx:02}'
        out = BASE / 'documents' / doc_id
        out.mkdir(parents=True)
        dest = out / 'source.pdf'
        shutil.copy2(src, dest)
        digest = sha(src)
        assert sha(dest) == digest
        pages = []
        active = None
        with pymupdf.open(dest) as pdf:
            for page_idx, page in enumerate(pdf, 1):
                text = page.get_text()
                words = [list(w) for w in page.get_text('words')]
                top = compact(text[:260])
                hits = [(len(title), code) for code, title in TITLES.items() if title in top]
                if '目录' in top[:30] or len(hits) > 2 or '第三部分' in top[:100] or '决算情况说明' in top[:150]:
                    active = None
                elif hits:
                    active = max(hits)[1]
                tables = []
                try:
                    found = page.find_tables().tables
                    for table_idx, table in enumerate(found):
                        rows = table.extract()
                        tables.append(dict(index=table_idx, bbox=list(table.bbox), row_count=table.row_count,
                                           col_count=table.col_count, rows=rows,
                                           cell_boxes=[[list(b) if b else None for b in r.cells] for r in table.rows]))
                except Exception as exc:
                    tables.append({'error': f'{type(exc).__name__}: {exc}'})
                # 核心表正文结束后不把随后绩效表错误继承成第九张表。
                if active and not hits and not any(re.fullmatch(r'\d{3,7}', str(w[4])) for w in words):
                    if not any(contains(t.get('bbox', [0, 0, 0, 0]), w) for t in tables for w in words if MONEY.fullmatch(compact(w[4]))):
                        active = None
                amounts = []
                for word_idx, w in enumerate(words):
                    raw = compact(w[4])
                    if not MONEY.fullmatch(raw):
                        continue
                    region = [t['index'] for t in tables if 'bbox' in t and contains(t['bbox'], w)]
                    amounts.append(dict(word_idx=word_idx, raw_text=raw, bbox=w[:4], table_indices=region))
                pages.append(dict(page=page_idx, width=page.rect.width, height=page.rect.height,
                                  rotation=page.rotation, text=text, words=words, tables=tables,
                                  source_core_code=active if tables else None, title_hit=max(hits)[1] if hits else None,
                                  amounts=amounts, image_count=len(page.get_images())))
        write(out / 'source_oracle.json', {'doc_id': doc_id, 'engine': f'PyMuPDF {pymupdf.VersionBind}',
              'source_sha256': digest, 'independent_of_production_parser': True,
              'human_full_cell_truth': False, 'pages': pages})
        entry = dict(doc_id=doc_id, filename=src.name, source_pdf=str(src), frozen_pdf=str(dest), sha256=digest,
                     pages=len(pages), overlap_with_v23_known_sources=digest in known,
                     source_core_pages=[p['page'] for p in pages if p['source_core_code']],
                     source_table_count=sum(len(p['tables']) for p in pages),
                     native_amount_tokens=sum(len(p['amounts']) for p in pages),
                     core_amount_tokens=sum(len(p['amounts']) for p in pages if p['source_core_code']),
                     textless_pages=[p['page'] for p in pages if not p['text'].strip()])
        inventory.append(entry)
        print(doc_id, src.name, len(pages), 'core pages', len(entry['source_core_pages']), 'tokens', entry['core_amount_tokens'], flush=True)
    write(BASE / 'inventory.json', inventory)
    write(BASE / 'source_freeze.json', dict(frozen_at=datetime.now(timezone.utc).isoformat(),
          source_engine='PyMuPDF', source_only_before_production_parse=True,
          inventory_sha256=sha(BASE / 'inventory.json'),
          oracles={d['doc_id']: sha(BASE / 'documents' / d['doc_id'] / 'source_oracle.json') for d in inventory},
          code_sha256=sha(BASE / 'code_before.json'), pdfs=len(inventory), pages=sum(d['pages'] for d in inventory),
          no_human_independent_signoff=True, elapsed_seconds=round(time.monotonic() - start, 2)))
    print('SOURCE_FROZEN', len(inventory), sum(d['pages'] for d in inventory), flush=True)


if __name__ == '__main__':
    main()
