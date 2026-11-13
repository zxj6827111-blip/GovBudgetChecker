from pathlib import Path
import copy,hashlib,json,os,sys
ROOT=Path(__file__).resolve().parents[2]; OUT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT))
os.environ.update(PYTHON_DOTENV_DISABLED='1',DATABASE_URL='',GOVBUDGET_TEST_DATABASE_URL='')
import fitz
from src.services.scan_evidence_certification import load_attestations,verify_scan_reference
from src.services.evidence_guard import evaluate_finding_evidence,apply_evidence_completeness
from scripts.run_zc07_acceptance import _authenticated_reference
FIX=OUT/'scan_guard_fixture';FIX.mkdir(exist_ok=True)
sha=lambda b:hashlib.sha256(b).hexdigest()
with fitz.open() as d:
 p=d.new_page(width=200,height=200);p.insert_text((20,40),'100');image=p.get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png')
path=FIX/'synthetic_scan.pdf'
with fitz.open() as d:p=d.new_page(width=200,height=200);p.insert_image(p.rect,stream=image);d.save(path)
box=[10.0,10.0,100.0,100.0]
with fitz.open(path) as d:
 assert not d[0].get_text();mat=fitz.Matrix(300/72,300/72);whole=d[0].get_pixmap(matrix=mat).tobytes('png');crop=d[0].get_pixmap(matrix=mat,clip=fitz.Rect(box)).tobytes('png')
source={'model_fingerprint':sha(b'synthetic model'),'raw_prediction_sha256':sha(b'synthetic raw'),'render_sha256':sha(whole)}
good={'source_method':'ocr','file_hash':sha(path.read_bytes()),'physical_page':1,'page':1,'bbox':box,'quote':'100','source_cell_id':'cell1','source_identity_status':'verified','location_level':'exact_cell','verification_status':'unverified','source_provenance':{'source':source}}
def add_id(ref):ref['evidence_id']=hashlib.sha1('|'.join([ref['file_hash'],str(ref['physical_page']),ref['source_cell_id'],ref['quote']]).encode()).hexdigest()[:16]
add_id(good);review=FIX/'review.record';review.write_text('synthetic trusted reviewer fixture only; not government truth')
record={**good,**source,'reviewed_by':'fixture-reviewer','render_dpi':300,'crop_sha256':sha(crop),'review_record_path':review.name,'review_record_sha256':sha(review.read_bytes())}
manifest_path=FIX/'attestations.json';manifest_path.write_text(json.dumps({'schema_version':'gbc.scan.attestations.v1','frozen_at':'fixture-date','reviewed_by':'fixture-reviewer','regions':[record]}))
expected=sha(manifest_path.read_bytes());manifest=load_attestations(manifest_path,expected)
os.environ.update(OCR_EVIDENCE_ATTESTATIONS_PATH=str(manifest_path),OCR_EVIDENCE_ATTESTATIONS_SHA256=expected)
bad=copy.deepcopy(good);bad.update(source_cell_id='unreviewed-cell2',bbox=[150.,150.,180.,180.]);add_id(bad)
results={'scope':'synthetic no-text PDF + trusted-registry fixture, no independent business certification','direct_certificate_good':verify_scan_reference(good,path,manifest),'direct_certificate_bad':verify_scan_reference(bad,path,manifest),'cases':{}}
for name,refs in [('good_only',[good]),('unreviewed_only',[bad]),('good_plus_unreviewed',[good,bad])]:
 finding={'id':'synthetic-'+name,'source':'rule','rule':'C-001','severity':'high','page':1,'bbox':refs[-1]['bbox'],'evidence':copy.deepcopy(refs)}
 complete,missing,verification=evaluate_finding_evidence(finding,document_page_count=1,page_texts=[''],source_file_hash=good['file_hash'],source_pdf_path=str(path))
 results['cases'][name]={'complete':complete,'missing':missing,'verification':verification,'references_after_guard':finding['evidence'],'per_reference_formal_gate':[ _authenticated_reference(r,good['file_hash'],path,manifest) for r in finding['evidence']]}
 if name=='good_plus_unreviewed':
  payload={'rule_findings':[finding]};summary=apply_evidence_completeness(payload,document_page_count=1,page_texts=[''],source_file_hash=good['file_hash'],source_pdf_path=str(path));results['cases'][name]['guard_summary']=summary
assert results['direct_certificate_good'] and not results['direct_certificate_bad']
assert results['cases']['good_only']['verification']['status']=='verified'
assert results['cases']['unreviewed_only']['verification']['status']=='unverified'
assert results['cases']['good_plus_unreviewed']['verification']['status']=='unverified'
assert results['cases']['good_plus_unreviewed']['per_reference_formal_gate']==[True,False]
(OUT/'scan_guard_probe.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'direct_certificate':[results['direct_certificate_good'],results['direct_certificate_bad']],'guard_statuses':{k:v['verification']['status'] for k,v in results['cases'].items()},'mixed_reference_gate':results['cases']['good_plus_unreviewed']['per_reference_formal_gate']}))
