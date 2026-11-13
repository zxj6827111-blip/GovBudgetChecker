"""冻结的真实 OCR 响应回放 + 原生漏格故障注入；不是新推理或独立验收。"""
import json,hashlib,copy,time
from pathlib import Path
from src.schemas.parse_artifact import ParseArtifact
from src.services.fiscal_input_builder import FiscalInputBuilder
from src.services.input_geometry import Affine
from src.services.fiscal_fragment_adjudication import supported_literal
from src.services.fiscal_business_rows import valid_samples
root=Path('/Volumes/workSSD/software development/GovBudgetChecker')
base=root/'artifacts/pdf-input-v2-20261018'
out=root/'artifacts/pdf-input-v2-20261018-joint/real-fragment-header-reference'
out.mkdir(exist_ok=False)
saved=json.loads((base/'real-seam-recovery/receipt.json').read_text())
assert hashlib.sha256(Path(saved['source']).read_bytes()).hexdigest()==saved['source_sha256']
p=base/'frozen34-delivery/shanghai_2024_new9_acceptance_20261016/documents/SH2024N9_08'
a=ParseArtifact.from_dict(json.loads((p/'artifact.json').read_text()))
frozen=json.loads((p/'fact_context.json').read_text())
builder=FiscalInputBuilder()
profile={k:frozen.get(k) for k in ('org_name','fiscal_year','report_kind')}
before=builder.facts(a,**profile)
ctx=saved['tile_context'];matrix=Affine(*ctx['pixel_to_pdf'])
target=matrix.box(saved['aligned_candidates'][0]['bbox'])
x,y=(target[0]+target[2])/2,(target[1]+target[3])/2
matches=[(t,c) for t in a.table_fragments for c in t.cells if c.page_number==saved['page'] and c.bbox and c.bbox[0]<x<c.bbox[2] and c.bbox[1]<y<c.bbox[3] and not c.is_header]
assert len(matches)==1
native,cell=matches[0]
physical_id=f'{cell.origin_fragment_uid or native.fragment_uid}:r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}'
original=next(f for f in before.facts if any(e['source_cell_id']==physical_id for e in f.extra_dims['evidence_refs']))
raw_before=cell.raw_text
cell.raw_text=cell.normalized_text='�';cell.numeric_value=None;cell.normalized_value_wan=None;cell.native_text_sources=[]
native.rows[cell.row_idx][cell.col_idx]='�'
source=saved['result']['provenance'];a.ocr_model_fingerprint=source['model_fingerprint']
transform_id='ocr_tile_'+ctx['tile_id']
a.coordinate_transforms[transform_id]={
 'transform_version':'pdf-tile-v3','physical_page':saved['page'],'source_sha256':a.source_file_hash,
 'tile_context':ctx,'ocr_response':source,'render_sha256':ctx['input_sha256'],'render_size_px':ctx['size_px'],
 'bbox_transform_verified':True,'pixel_to_pdf_matrix':ctx['pixel_to_pdf'],'pdf_to_pixel_matrix':ctx['pdf_to_pixel'],
 'page_request':{'physical_page':saved['page'],'source_sha256':a.source_file_hash,'render_sha256':ctx['input_sha256'],'input_sha256':ctx['input_sha256'],
 **{k:source[k] for k in ('schema_version','raw_prediction_sha256','model_fingerprint','pipeline_fingerprint')}}}
start=time.monotonic();after=builder.facts(a,**profile)
recovered=next(f for f in after.facts if any(e['source_cell_id']==physical_id for e in f.extra_dims['evidence_refs']))
view=next(t for t in a.reconstructed_tables.values() if t.fragment_uid==native.fragment_uid)
header_diagnostics=[{'text':c.raw_text,'supported':supported_literal(c,valid_samples(a))} for c in view.cells if c.is_header and c.raw_text.strip()]
receipt={'scope':'saved_real_ocr_response_replay_with_native_field_fault_injection_not_new_inference_or_blind_acceptance',
 'source_sha256':a.source_file_hash,'source':saved['source'],'saved_response_sha256':source['raw_prediction_sha256'],
 'physical_source_id':physical_id,'original':original.to_dict(),'injected_native_literal':'�','actual':recovered.to_dict(),
 'same_business_identity':original.identity_key()==recovered.identity_key(),'same_original_literal':raw_before==recovered.raw_value,
 'selected_visual':recovered.extra_dims['evidence_refs'][0]['source_method']=='ocr',
 'header_diagnostics':header_diagnostics,'decisions':[d for d in a.reconstruction_diagnostics if d.get('physical_source_id')==physical_id],
 'seconds':time.monotonic()-start}
(out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str))
print(json.dumps({k:receipt[k] for k in ('physical_source_id','same_business_identity','same_original_literal','selected_visual','seconds')},ensure_ascii=False))
if not receipt['selected_visual']:print(json.dumps(header_diagnostics,ensure_ascii=False))
assert receipt['same_business_identity'] and receipt['same_original_literal'] and receipt['selected_visual']
