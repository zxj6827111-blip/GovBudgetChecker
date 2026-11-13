from pathlib import Path
import sys,json,collections,math
BASE=Path(__file__).resolve().parent;sys.path.insert(0,str(BASE/'validation-runtime-v30'))
from src.schemas.parse_artifact import ParseArtifact
from src.services.ocr_grid_fragments import align_grid_fragments
OUT=BASE/'income-grid-conflicts-v31';OUT.mkdir(exist_ok=False)
with (BASE/'income-boundaries-v13/artifact.json').open() as f:p=json.load(f)
p['table_fragments']=[];p['logical_tables']=[];p['reconstructed_tables']={}
for key in ('logical_cell_candidates','resolved_logical_cells','reconstruction_diagnostics'):p[key]=[]
a=ParseArtifact.from_dict(p);del p
records=[]
for grid in a.source_grids:
 if not 9<=grid.page_number<=14:continue
 aligned=align_grid_fragments(a,grid)
 for c in aligned['cells']:
  if c['status']!='requires_regional_read' or c['row_idx']<3:continue
  original=next(s for s in grid.cells if (s.row_idx,s.col_idx)==(c['row_idx'],c['col_idx']))
  measured=original.ocr_provenance['physical_grid']['cell']
  item={k:v for k,v in c.items() if k!='fragment_groups'}
  item['page']=grid.page_number;item['physical_measurement']=measured
  item['fragment_groups']=c['fragment_groups'];records.append(item)
  print(json.dumps({'page':grid.page_number,'row':c['row_idx'],'col':c['col_idx'],'text':c['raw_text'],'uncovered_ink':c['uncovered_ink_pixels'],'groups':[{ 'status':g['status'],'text':g['selected']['text'],'reason':g['selected'].get('reasons'),'score':g['selected']['score'],'alternatives':[f['text'] for f in g.get('alternatives',[])]} for g in c['fragment_groups']]},ensure_ascii=False),flush=True)
(OUT/'unresolved-source-cells.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
# 只渲染真实 PDF 的证据区域；每幅图保留实际原页 bbox，未插入任何文字或数字。
import pymupdf
source=BASE.parent/'pdf-input-v2-20261018-joint/additional-official-originals-4/OFFICIAL_c866e2344d35/source.pdf'
with pymupdf.open(source) as pdf:
 for index,item in enumerate(records):
  if index>=28:break
  box=item['bbox'];clip=pymupdf.Rect(box)+(-3,-3,3,3)
  pix=pdf[item['page']-1].get_pixmap(matrix=pymupdf.Matrix(600/72,600/72),clip=clip)
  name=f"p{item['page']}-r{item['row_idx']}c{item['col_idx']}.png";pix.save(OUT/name);item['evidence_crop']=name
(OUT/'crop-manifest.json').write_text(json.dumps([{k:v for k,v in i.items() if k in ['page','row_idx','col_idx','bbox','raw_text','evidence_crop','uncovered_ink_pixels']} for i in records],ensure_ascii=False,indent=2))
