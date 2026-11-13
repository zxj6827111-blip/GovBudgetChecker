"""Finite synthetic qualification probe. No OCR/HTTP/database/model calls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ.pop('OCR_EVIDENCE_ATTESTATIONS_PATH', None)
os.environ.pop('OCR_EVIDENCE_ATTESTATIONS_SHA256', None)
import fitz
from tests.remediation.test_round11_ocr import fixture
from src.schemas.parse_artifact import ParsedTextBlock, EvidenceRef
from src.services.ocr_pipeline import _render_pages_png, _pdf_bbox_from_render_bbox
from src.services.ocr_qualification import qualification
from src.services.ocr_fact_promotion import promote_ocr_candidates
from src.engine.fact_context import build_fact_context_from_artifact
from src.services.evidence_guard import evaluate_finding_evidence

def digest(value):
    return hashlib.sha256(value).hexdigest()

sources = [
    'src/services/ocr_qualification.py', 'src/services/ocr_fact_promotion.py',
    'src/services/ocr_pipeline.py', 'src/services/evidence_guard.py',
    'src/services/fiscal_fact_materializer.py', 'src/services/scan_evidence_certification.py',
    'src/engine/fact_context.py', 'tests/remediation/test_round11_ocr.py',
    'tests/remediation/test_round13_ocr_trust.py',
]
before = {p: digest((ROOT / p).read_bytes()) for p in sources}
observations = []

# Verify both original R12-02 reproducers now reject, and canonical values survive.
for change in ('control', 'rows', 'bbox'):
    artifact, candidate = fixture()
    if change == 'rows':
        candidate.rows[1][2] = '900'
    elif change == 'bbox':
        candidate.cells[6]['page_bbox'] = [1.0, 1.0, 2.0, 2.0]
    q = qualification(candidate, artifact, organization='测试局', year=2025, report_kind='final')
    promote_ocr_candidates(artifact, organization='测试局', year=2025, report_kind='final')
    context = build_fact_context_from_artifact(artifact, org_name='测试局', fiscal_year=2025, report_kind='final')
    expected = 'qualified' if change == 'control' else 'rejected'
    assert q['status'] == expected
    assert len(context.definite_facts()) == (2 if change == 'control' else 0)
    assert not any(str(f.raw_value) in {'900','900.0'} for f in context.facts)
    observations.append({'case': change, 'expected': expected, 'actual': q,
        'definite_count': len(context.definite_facts()),
        'materialized_values': [f.raw_value for f in context.facts]})

# Native text cannot substitute for scan certification, with or without PDF path.
ref = EvidenceRef(file_hash='a'*64, physical_page=1, source_cell_id='ocr-cell',
    quote='100', bbox=(1,1,2,2), source_method='ocr', verification_status='unverified',
    location_level='exact_cell').to_dict()
ref['source_identity_status'] = 'verified'
for text in ('', '100'):
    result = evaluate_finding_evidence({'evidence':[copy.deepcopy(ref)]},
        document_page_count=1, page_texts=[text], source_file_hash='a'*64)
    assert result[2]['status'] == 'unverified'
    observations.append({'case':'uncertified-scan', 'native_text':text,
        'expected':'unverified','actual':result})

# Real 300dpi transforms and distinct two-page hashes; OCR responses are synthetic.
# Both page identities are present in the control before changing one field.
with tempfile.TemporaryDirectory(prefix='r14-pages-') as directory:
    path = Path(directory) / 'two-scanned-pages.pdf'
    with fitz.open() as pdf:
        for color in ((1,0,0),(0,0,1)):
            page=pdf.new_page(width=400,height=100)
            page.draw_rect(fitz.Rect(10,10,20,20),fill=color)
        pdf.save(path)
    images, transforms = _render_pages_png(str(path), [1,2])
    artifact, candidate = fixture()
    artifact.source_file_hash = digest(path.read_bytes())
    journal = artifact.coordinate_transforms['render']['region_requests']
    candidate.coordinate_transform_id = 'ocr_render_page_1'
    candidate.provenance.update(source_sha256=artifact.source_file_hash,
        render_sha256=transforms[1]['render_sha256'], input_sha256=transforms[1]['render_sha256'],
        source_size_px=transforms[1]['render_size_px'])
    for cell in candidate.cells:
        cell['page_bbox'] = _pdf_bbox_from_render_bbox(cell['render_bbox'], transforms[1])
    second = copy.deepcopy(candidate.provenance)
    second.update(render_sha256=transforms[2]['render_sha256'],
        input_sha256=transforms[2]['render_sha256'])
    table = second['raw_prediction']['table_res_list'][0]
    table['pred_html'] = table['pred_html'].replace('>100<','>900<')
    table['table_ocr_pred']['rec_texts'] = ['900' if t=='100' else t
        for t in table['table_ocr_pred']['rec_texts']]
    second['raw_prediction_sha256'] = digest(json.dumps(second['raw_prediction'],
        ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
    artifact.coordinate_transforms = {
        'ocr_render_page_1': {**transforms[1], 'ocr_response':copy.deepcopy(candidate.provenance), 'region_requests':journal},
        'ocr_render_page_2': {**transforms[2], 'ocr_response':second},
    }
    artifact.text_blocks.append(ParsedTextBlock(block_uid='page2-identity',page_number=2,
        text='测试局2025年决算 单位：万元',confidence=.995,extraction_method='ocr'))
    for label, number in (('physical-page-control',1),('physical-page-relocated',2)):
        a, c = copy.deepcopy(artifact), copy.deepcopy(candidate)
        prior = c.to_dict()
        c.page_number = number
        changes = {k: [prior[k],v] for k,v in c.to_dict().items() if prior[k] != v}
        a.ocr_table_candidates=[c]
        q=qualification(c,a,organization='测试局',year=2025,report_kind='final')
        promote_ocr_candidates(a,organization='测试局',year=2025,report_kind='final')
        context=build_fact_context_from_artifact(a,org_name='测试局',fiscal_year=2025,report_kind='final')
        facts=[{'indicator':f.indicator,'value':str(f.normalized_value),
            'recognition_status':f.recognition_status,
            'physical_page':f.extra_dims['evidence_refs'][0]['physical_page'],
            'bbox':f.extra_dims['evidence_refs'][0]['bbox'],
            'verification_status':f.extra_dims['evidence_refs'][0]['verification_status'],
            'input_sha256':f.extra_dims['evidence_refs'][0]['source_provenance']['source']['input_sha256']}
            for f in context.definite_facts()]
        observations.append({'case':label,'only_changes':changes,
            'expected':'qualified' if number==1 else 'rejected', 'actual':q,
            'coordinate_transform_id':c.coordinate_transform_id,
            'render_dpi':transforms[1]['dpi'],'page1_render_sha256':transforms[1]['render_sha256'],
            'page2_render_sha256':transforms[2]['render_sha256'],'page2_raw_table_values':['900','900'],
            'definite_facts':facts})
        assert q['status'] == 'qualified' and len(facts)==2
        assert all(f['physical_page']==number for f in facts)
        if number==2:
            assert changes=={'page_number':[1,2]}
            assert all(f['input_sha256']==transforms[1]['render_sha256'] for f in facts)

after={p:digest((ROOT/p).read_bytes()) for p in sources}
result={'source_sha256_before':before,'source_sha256_after':after,
    'source_unchanged':before==after,'observations':observations,
    'boundary':'Synthetic recognizer contracts plus actual local PDF rendering; no model/HTTP/DB runs or independent human certification.'}
output=Path(__file__).with_name('probe.json')
output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'output':str(output),'observations':len(observations),
    'residual_page_binding_reproduced':True,'source_unchanged':before==after},ensure_ascii=False))
