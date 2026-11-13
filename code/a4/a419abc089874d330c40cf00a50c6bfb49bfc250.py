import sys, os, json, re, warnings, logging, functools
warnings.filterwarnings('ignore'); logging.disable(logging.CRITICAL)
from concurrent.futures import ProcessPoolExecutor
MODE = None

CELLVIS_STATS = {}
def cell_visible(art, path):
    """Batch-1 prototype: grid and amount cells untouched; only non-numeric label cells of
    nine-table fragments get their text replaced by the clip-visible text, and only when
    the pdfplumber text equals PyMuPDF's full (unclipped) text for the same cell."""
    import pymupdf as fitz, dataclasses, unicodedata
    from src.services.fiscal_table_rules import NINE_TABLE_RULES
    doc = fitz.open(path); cache = {}
    def chars(pg):
        if pg not in cache:
            page = doc[pg - 1]
            def collect(flags):
                return [(ch['c'], ch['bbox']) for bl in page.get_text('rawdict', flags=flags)['blocks']
                        for ln in bl.get('lines', []) for sp in ln['spans'] for ch in sp['chars'] if ch['c'].strip()]
            cache[pg] = (collect(0), collect(fitz.TEXT_CLIP), page.rotation == 0 and page.cropbox == page.mediabox)
        return cache[pg]
    def inside(b, cb):
        mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return cb[0] <= mx < cb[2] and cb[1] <= my < cb[3]
    norm = lambda s: re.sub(r'\s+', '', unicodedata.normalize('NFKC', s or ''))
    st = {'replaced': 0, 'skipped_alignment': 0, 'code_conflict': 0, 'geom_skip': 0}
    def fix(table):
        if table.table_code not in NINE_TABLE_RULES: return table
        new = []
        for c in table.cells:
            if c.numeric_value is not None or not c.bbox or not (c.raw_text or '').strip():
                new.append(c); continue
            allc, visc, simple = chars(c.page_number)
            if not simple: st['geom_skip'] += 1; new.append(c); continue
            full = ''.join(ch for ch, b in allc if inside(b, c.bbox))
            vis = ''.join(ch for ch, b in visc if inside(b, c.bbox))
            if norm(full) == norm(vis): new.append(c); continue
            if sorted(norm(full)) != sorted(norm(c.raw_text)):
                st['skipped_alignment'] += 1; new.append(c); continue   # cannot prove same characters
            if re.fullmatch(r'[\d\s]+', c.raw_text or ''):
                st['code_conflict'] += 1; new.append(c); continue       # codes: report only, never rewrite
            if not norm(vis):
                st['skipped_alignment'] += 1; new.append(c); continue
            st['replaced'] += 1
            new.append(dataclasses.replace(c, raw_text=vis, normalized_text=vis,
                       grid_raw_text=c.grid_raw_text if c.grid_raw_text is not None else c.raw_text))
        return dataclasses.replace(table, cells=new)
    art.table_fragments = [fix(t) for t in art.table_fragments]
    art.physical_fragments = [fix(t) for t in art.physical_fragments]
    return art, st

def setup(mode):
    import logging, warnings; logging.disable(logging.CRITICAL); warnings.filterwarnings('ignore')
    if mode != 'visible': return
    import pymupdf as fitz, pdfplumber
    @functools.lru_cache(maxsize=None)
    def hidden_for(path):
        doc = fitz.open(path); res = {}
        for i, page in enumerate(doc):
            def chars(f):
                return [(ch['c'], round(ch['bbox'][0], 1), round(ch['bbox'][1], 1)) for bl in page.get_text('rawdict', flags=f)['blocks']
                        for ln in bl.get('lines', []) for sp in ln['spans'] for ch in sp['chars']]
            vis = set(chars(fitz.TEXT_CLIP)); h = [c for c in chars(0) if c not in vis and c[0].strip()]
            if h: res[i + 1] = tuple(h)
        return res
    orig = pdfplumber.pdf.PDF.pages
    def pages(self):
        name = getattr(self.stream, 'name', None)
        hid = hidden_for(os.path.abspath(name)) if isinstance(name, str) else {}
        return [p.filter(lambda o, h=hid.get(p.page_number): o.get('object_type') != 'char' or not any(
            o['text'] == c and abs(o['x0'] - x) < .6 and abs(o['top'] - y) < 1.5 for c, x, y in h)) if hid.get(p.page_number) else p
            for p in orig.fget(self)]
    pdfplumber.pdf.PDF.pages = property(pages)
def one(path):
    try:
        from src.services.pdf_parser import PDFParser
        from src.engine.fact_context import build_fact_context_from_artifact
        art = PDFParser(None).build_parse_artifact(path)
        st = None
        if os.environ.get('CELLVIS'):
            art, st = cell_visible(art, path)
        ctx = build_fact_context_from_artifact(art, report_kind='final', doc_type='dept_final')
        out = {}
        for f in (ctx.facts if ctx else []):
            e = f.extra_dims['evidence_refs'][0]
            pos = (e.get('source_cell_id') or ':?').split(':')[-1]
            key = f"{e['physical_page']}|{f.table_code}|{pos}|{f.indicator}|{f.extra_dims.get('source_header')}"
            out[key] = [f.raw_value, f.unit, str(f.normalized_value), [round(x, 1) for x in (e.get('bbox') or [])], f.row_order, f.subject_code, f.subject_name]
        if st is not None: out['__stats__'] = st
        return path, out
    except Exception as ex:
        return path, {'__error__': repr(ex)[:100]}
if __name__ == '__main__':
    mode, plist, outp = sys.argv[1:4]
    paths = json.load(open(plist))
    with ProcessPoolExecutor(8, initializer=setup, initargs=(mode,)) as ex:
        res = dict(ex.map(one, paths, chunksize=2))
    json.dump(res, open(outp, 'w'), ensure_ascii=False); print('done', len(res))
