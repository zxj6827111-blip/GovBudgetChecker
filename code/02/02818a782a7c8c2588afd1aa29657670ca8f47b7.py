"""只读重放两个已保存开发采集，不新增推理，不作为独立逐格验收。"""
from pathlib import Path
import sys,json,hashlib,collections
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE/'implementation'))
from src.schemas.parse_artifact import ParseArtifact
from src.services.fiscal_input_builder import FiscalInputBuilder
from src.services.ocr_grid_fragments import align_grid_fragments
p=BASE/'integrated-profile-ocr/artifact.json'
raw=p.read_bytes();artifact=ParseArtifact.from_dict(json.loads(raw))
cover=ParseArtifact.from_dict(json.loads((BASE/'wide-source-paragraph-v10/artifact.json').read_text()))
assert artifact.source_file_hash==cover.source_file_hash
artifact.coordinate_transforms.update(cover.coordinate_transforms)
artifact.text_blocks.extend(cover.text_blocks);artifact.source_fragments.extend(cover.source_fragments)
context=FiscalInputBuilder().facts(artifact)
result={'scope':'saved_development_capture_replay_not_independent_acceptance','new_inference':False,'body_capture_sha256':hashlib.sha256(raw).hexdigest(),'cover_capture_sha256':hashlib.sha256((BASE/'wide-source-paragraph-v10/artifact.json').read_bytes()).hexdigest(),'facts':len(context.facts),'definite_facts':len(context.definite_facts()),'grids':[], 'reconstruction_diagnostics':artifact.reconstruction_diagnostics}
for grid in artifact.source_grids:
 if grid.page_number!=8:continue
 aligned=align_grid_fragments(artifact,grid)
 result['grids'].append({'uid':grid.fragment_uid,'cells':len(grid.cells),'statuses':dict(collections.Counter(c['status'] for c in aligned['cells'])),'cells_preview':[{'row':c['row_idx'],'column':c['col_idx'],'text':c['raw_text'],'status':c['status'],'uncovered_ink_pixels':c.get('uncovered_ink_pixels')} for c in aligned['cells']]})
index=len(json.loads((BASE/'integrated-profile-ocr/summaries.json').read_text()))
out=BASE/f'combined-capture-diagnostic-{index}.json'
out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
print({'output':out.name,'facts':len(context.facts),'admitted':len(context.definite_facts()),'grids':[(g['cells'],g['statuses']) for g in result['grids']]})
for diag in artifact.reconstruction_diagnostics:
 if isinstance(diag,dict) and diag.get('physical_grid_uid'):
  print({k:diag.get(k) for k in ['table_code','header_depth','status']},'profile_reasons',diag.get('profile',{}).get('reasons'))
