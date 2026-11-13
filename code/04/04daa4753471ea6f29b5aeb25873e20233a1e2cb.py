"""只读原始 v3 响应和断点，验证新重建器；不推理、不重置轮次。"""
from pathlib import Path
import sys,json,hashlib,collections
BASE=Path(__file__).resolve().parent;RUNTIME=BASE/'fragment-runtime-v18'
sys.path.insert(0,str(RUNTIME))
from src.schemas.parse_artifact import ParseArtifact
from src.services.fiscal_input_builder import FiscalInputBuilder
from src.services.ocr_tiles import digest,_restore_record
from src.services.ocr_tiled_pipeline import append_tile_results
from src.services.input_geometry import Affine
p=BASE/'income-capture-v13/artifact.json';raw=p.read_bytes()
journal=BASE/'income-capture-v13/checkpoint.page-7.v3.json';raw_journal=journal.read_bytes()
state=json.loads(raw_journal);unsigned={k:v for k,v in state.items() if k!='checkpoint_sha256'}
assert digest(unsigned)==state['checkpoint_sha256']
a=ParseArtifact.from_dict(json.loads(raw));assert state['identity']['file']==a.source_file_hash
old=next(t for t in a.coordinate_transforms.values() if t.get('physical_page')==7 and t.get('ocr_response'))
physical_before=[g.to_dict() for g in a.source_grids]
records=[]
for record in state['completed'].values():
 context=record['context'];response=_restore_record(record,context);assert response is not None
 assert context['source_sha256']==a.source_file_hash and context['physical_page']==7
 if 'ocr_tile_'+context['tile_id'] not in a.coordinate_transforms:records.append((context,response))
append_tile_results(a,records,page_number=7,layout_sha=old['layout_sha256'],page_recovery=old['page_recovery'],page_render=old['page_render'])
other_path=BASE/'measured-grid-replay-5-92eabe9d6c12/artifact.json'
other=ParseArtifact.from_dict(json.loads(other_path.read_bytes()));assert a.source_file_hash==other.source_file_hash
if not any(g.page_number==8 for g in a.source_grids):
 a.source_grids.extend(g for g in other.source_grids if g.page_number==8)
for key, value in other.coordinate_transforms.items():
 if value.get('physical_page')!=8:continue
 if key in a.coordinate_transforms:
  assert a.coordinate_transforms[key]['ocr_response']==value['ocr_response'],'same tile has distinct saved predictions; preserve separately'
 else:a.coordinate_transforms[key]=value
known_blocks={t.block_uid for t in a.text_blocks}
a.text_blocks.extend(t for t in other.text_blocks if t.page_number==8 and t.block_uid not in known_blocks)
known_fragments={s.get('source_id') for s in a.source_fragments}
a.source_fragments.extend(s for s in other.source_fragments if s.get('physical_page')==8 and s.get('source_id') not in known_fragments)
source_grids=[g.to_dict() for g in a.source_grids]
context=FiscalInputBuilder().facts(a)
assert [g.to_dict() for g in a.source_grids]==source_grids
out=BASE/('income-replay-v18-'+hashlib.sha256(raw_journal).hexdigest()[:12]);out.mkdir(exist_ok=False)
def write(name,data):(out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2))
write('input-artifact.json',json.loads(raw));write('input-page7-journal.json',state)
write('artifact.json',a.to_dict());write('context.json',context.to_dict())
receipt={'scope':'development_saved_response_replay_not_independent_acceptance','new_inference':False,'round_counters_reset':False,'runtime':'fragment-runtime-v18','source_file_hash':a.source_file_hash,'input_artifact_sha256':hashlib.sha256(raw).hexdigest(),'input_journal_sha256':hashlib.sha256(raw_journal).hexdigest(),'source_grids_unchanged':True,'page7_capture_status':state['status'],'page7_completed_requests':len(state['completed']),'appended_saved_requests':len(records),'facts':len(context.facts),'operands':len(context.definite_facts()),'instances':[{'code':i.table_code,'page_start':i.page_start,'page_end':i.page_end,'fragments':i.fragment_uids} for i in a.logical_tables],'roles':dict(collections.Counter(f.extra_dims.get('row_role','body') for f in context.facts))}
write('receipt.json',receipt)
print(json.dumps(receipt,ensure_ascii=False))
for d in a.reconstruction_diagnostics:
 if d.get('physical_grid_uid'):print({k:d.get(k) for k in ['physical_grid_uid','table_code','header_depth','status']},{k:d.get('profile',{}).get(k) for k in ['org_name','report_year','unit','table_serial','reasons']})
