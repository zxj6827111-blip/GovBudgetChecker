"""Scenario replay. Flags (comma list): visible, year, alias, caliber_unit, gate_audit."""
import sys, os, re, warnings, functools, json
warnings.filterwarnings('ignore')
flags = set(sys.argv.pop(1).split(','))
sys.path.insert(0, os.getcwd())
if 'visible' in flags:
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
    orig_pages = pdfplumber.pdf.PDF.pages
    def pages(self):
        name = getattr(self.stream, 'name', None)
        hid = hidden_for(os.path.abspath(name)) if isinstance(name, str) and name.lower().endswith('.pdf') else {}
        return [p.filter(lambda o, h=hid.get(p.page_number): o.get('object_type') != 'char' or not any(
            o['text'] == c and abs(o['x0'] - x) < .6 and abs(o['top'] - y) < 1.5 for c, x, y in h)) if hid.get(p.page_number) else p
            for p in orig_pages.fget(self)]
    pdfplumber.pdf.PDF.pages = property(pages)
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

if 'cellvis' in flags:
    from src.services import pdf_parser as PP
    _orig_h = PP.PDFParser.build_parse_artifact_from_handle
    def _h(self, pdf, *a, **k):
        art = _orig_h(self, pdf, *a, **k)
        name = getattr(pdf.stream, 'name', None)
        if isinstance(name, str):
            art, st = cell_visible(art, os.path.abspath(name)); print('CELLVIS', st, file=sys.stderr)
        return art
    PP.PDFParser.build_parse_artifact_from_handle = _h

if 'year' in flags:
    from src.engine import rules_v33 as R
    R._TXT_FUND_YEAR_RE = re.compile(r"(?<![\d.,])(?:19|20)\d{2}(?![\d.,])")
if 'alias' in flags:
    from src.engine import native_narrative_checks as N, native_table_text as T, native_no_activity as NA
    orig_fo = N._foreign_owner
    def fo(proof, owner):  # experiment: cover-declared subject of this PDF as the only extra alias
        return orig_fo(proof, owner) and orig_fo(proof, '上海市信访办公室')
    N._foreign_owner = fo; T._foreign_owner = fo
if 'caliber_unit' in flags:
    from src.services import fiscal_source_scope as FS
    FS.organization_scope = lambda artifact, local_title=None: 'unit'
if 'gate_audit' in flags:
    from src.engine import rule_identity as RI
    RI.INTERNAL_RELATION_RULES = frozenset(RI.INTERNAL_RELATION_RULES | {'CMM-005', 'CMM-006', 'CMM-007', 'V33-234', 'V33-245', 'V33-236'})
import runpy
sys.argv = ['reprocess_uploads.py'] + sys.argv[1:]
runpy.run_path('scripts/reprocess_uploads.py', run_name='__main__')
