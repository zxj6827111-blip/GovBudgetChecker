"""从同一不可变原件重测边界墨迹，只消费已保存真实响应，禁止新增推理。"""
from pathlib import Path
import sys,json,hashlib,io,collections
BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[1]
sys.path.insert(0,str(BASE/'implementation'))
import fitz
from PIL import Image
from src.schemas.parse_artifact import ParseArtifact
from src.services.ocr_image_grid import physical_grid_tables,IMAGE_GRID_VERSION
from src.services.input_geometry import Affine
from src.services.ocr_tiles import png_bytes
from src.services.ocr_grid_fragments import align_grid_fragments
from src.services.fiscal_input_builder import FiscalInputBuilder
SOURCE=ROOT/'artifacts/pdf-input-v2-20261018-joint/additional-official-originals-4/OFFICIAL_c866e2344d35/source.pdf'
input_path=BASE/'integrated-profile-ocr/artifact.json';input_bytes=input_path.read_bytes()
a=ParseArtifact.from_dict(json.loads(input_bytes));original=a.to_dict()
cover=ParseArtifact.from_dict(json.loads((BASE/'wide-source-paragraph-v10/artifact.json').read_text()))
assert a.source_file_hash==cover.source_file_hash==hashlib.sha256(SOURCE.read_bytes()).hexdigest()
old=next(g for g in a.source_grids if g.page_number==8);physical=old.cells[0].ocr_provenance['physical_grid']
with fitz.open(SOURCE) as pdf:
 pix=pdf[7].get_pixmap(matrix=fitz.Matrix(300/72,300/72))
 original_image=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
 assert hashlib.sha256(png_bytes(original_image)).hexdigest()==physical['parent_image_sha256']
 image=original_image.transpose(Image.Transpose.ROTATE_90)
 assert hashlib.sha256(png_bytes(image)).hexdigest()==physical['image_sha256'],'must reproduce exact saved raster, no approximate replacement'
new=physical_grid_tables(image,file_hash=a.source_file_hash,page=8,pixel_to_pdf=Affine(*physical['pixel_to_pdf']),parent_image_sha256=physical['parent_image_sha256'])
assert len(new)==1
fields=('row_idx','col_idx','row_span','col_span','bbox_px')
def geometry(grid):return [{field:c.ocr_provenance['physical_grid']['cell'][field] for field in fields} for c in grid.cells]
assert geometry(old)==geometry(new[0]),'read-only physical grid geometry must remain identical'
# 新解析副本保留对旧输入和旧网格的完整哈希关联；输入文件始终不写。
a.source_grids=[g for g in a.source_grids if g.page_number!=8]+new
a.coordinate_transforms.update(cover.coordinate_transforms)
a.text_blocks.extend(cover.text_blocks);a.source_fragments.extend(cover.source_fragments)
context=FiscalInputBuilder().facts(a)
aligned=align_grid_fragments(a,new[0])
index=len(json.loads((BASE/'integrated-profile-ocr/summaries.json').read_text()))
code_hash=hashlib.sha256(b''.join((BASE/'implementation/src/services'/name).read_bytes() for name in ['ocr_image_grid.py','ocr_grid_fragments.py','ocr_grid_reconstruction.py','fiscal_source_profile.py','fiscal_input_builder.py','table_reading_assessment.py'])).hexdigest()
out=BASE/f'measured-grid-replay-{index}-{code_hash[:12]}';out.mkdir(exist_ok=False)
def write(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
write('facts.json',context.to_dict());write('artifact.json',a.to_dict());write('diagnostics.json',a.reconstruction_diagnostics)
write('cell-comparison.json',[{'row':c['row_idx'],'column':c['col_idx'],'text':c['raw_text'],'status':c['status'],'uncovered_content_pixels':c['uncovered_ink_pixels']} for c in aligned['cells']])
write('receipt.json',{'new_inference':False,'independent_acceptance':False,'code_sha256':code_hash,'old_input_sha256':hashlib.sha256(input_bytes).hexdigest(),'source_file_hash':a.source_file_hash,'old_input_still_unchanged':input_path.read_bytes()==input_bytes,'exact_original_raster_reproduced':True,'physical_grid_geometry_identical':True,'old_grid_uid':old.fragment_uid,'new_grid_uid':new[0].fragment_uid,'measurement_version':IMAGE_GRID_VERSION,'facts':len(context.facts),'definite_facts':len(context.definite_facts()),'cells':len(aligned['cells']),'statuses':dict(collections.Counter(c['status'] for c in aligned['cells']))})
print(json.loads((out/'receipt.json').read_text()))
for d in a.reconstruction_diagnostics:
 if isinstance(d,dict) and d.get('physical_grid_uid'):print({k:d.get(k) for k in ['table_code','header_depth','status']},d.get('profile',{}).get('reasons'))
