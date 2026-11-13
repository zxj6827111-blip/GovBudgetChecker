"""从已冻结真实响应诊断跨图块网格；不更改识别状态，不作盲验结论。"""
from pathlib import Path
import sys
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
IMPL=ROOT/'artifacts/pdf-input-v2-20261018-stitch/implementation'
sys.path.insert(0,str(IMPL))
import fitz,io,json,hashlib
from PIL import Image
from src.schemas.parse_artifact import ParseArtifact
from src.services.ocr_image_grid import physical_grid_tables
from src.services.ocr_preprocessing import rotate,estimate_layout_angle
from src.services.input_geometry import Affine
from src.services.ocr_tiles import png_bytes
from src.services.fiscal_input_builder import FiscalInputBuilder
out=IMPL.parent/'saved-grid-diagnostic';out.mkdir(exist_ok=False)
source=ROOT/'artifacts/pdf-input-v2-20261018/official-originals/OFFICIAL_95c2b8afdcbf/source.pdf'
original=IMPL.parent/'captured-development-page-pinned/artifact-resume-1.json'
a=ParseArtifact.from_dict(json.loads(original.read_text()))
with fitz.open(source) as pdf:
 page=pdf[2]
 layout=page.get_pixmap(matrix=fitz.Matrix(150/72,150/72))
 angle=estimate_layout_angle(Image.open(io.BytesIO(layout.tobytes('png'))).convert('RGB'))
 pix=page.get_pixmap(matrix=fitz.Matrix(300/72,300/72));im=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
 parent=hashlib.sha256(png_bytes(im)).hexdigest()
 im,inverse,ops=rotate(im,angle)
 affine=inverse.then(Affine(a=72/300,d=72/300,e=pix.x*72/300,f=pix.y*72/300))
 a.source_grids.extend(physical_grid_tables(im,file_hash=a.source_file_hash,page=3,pixel_to_pdf=affine,parent_image_sha256=parent))
 ctx=FiscalInputBuilder().facts(a,org_name='中共苍南县委宣传部',fiscal_year=2025,report_kind='budget')
 grid=a.source_grids[-1];page.get_pixmap(matrix=fitz.Matrix(600/72,600/72),clip=fitz.Rect(grid.bbox)).save(str(out/'original-grid-600dpi.png'))
(out/'artifact.json').write_text(json.dumps(a.to_dict(),ensure_ascii=False))
summary={'scope':'saved_response_grid_diagnostic_not_new_inference_not_blind_acceptance','source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'saved_artifact_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'logical_tables':len(a.logical_tables),'facts':len(ctx.facts),'admitted':len(ctx.definite_facts()),'cells':[{'row':c['row'],'col':c['col_idx'],'raw_text':c['raw_text'],'status':c['status'],'uncovered_ink_pixels':c.get('uncovered_ink_pixels')} for c in a.logical_cell_candidates if c.get('source_relation')=='image_grid_cell'],'diagnostics':a.reconstruction_diagnostics}
(out/'receipt.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps({'cells':len(summary['cells']),'facts':len(ctx.facts),'admitted':len(ctx.definite_facts()),'first_rows':[[c['raw_text'] for c in summary['cells'] if c['row']==r] for r in range(3)]},ensure_ascii=False))
