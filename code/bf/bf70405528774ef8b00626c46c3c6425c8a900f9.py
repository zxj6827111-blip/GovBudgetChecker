from pathlib import Path
import sys, json, copy
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from src.schemas.parse_artifact import ParseArtifact, LogicalTableInstance
from tests.remediation.test_budget_2022_table_semantics import grid
from src.services.manual_cell_overlay import apply_table_cell_overlays
from src.services.pdf_parser import PDFParser
from src.services.source_reconstruction import resolve_native_table_candidates
from scripts.score_cell_truth import score_sample
from src.services.ocr_pipeline import image_page_gate
import pymupdf
OUT = Path(__file__).parent
results={}
def artifact(code="FIN_02_income"):
 t=grid(code,"2024年收入决算表",[["科目编码","科目名称","合计","财政拨款收入"],["206","科学技术支出","300","300"]],{},1,uid="t1")
 a=ParseArtifact(source_file_hash="a"*64)
 a.table_fragments=[t];a.physical_fragments=[copy.deepcopy(t)]
 a.logical_tables=[LogicalTableInstance(instance_uid="i1",table_code=code,title=t.title,fragment_uids=["t1"],page_start=1,page_end=1,org_name="测试局",fiscal_year=2024)]
 return a

def record(field,value,target="t1"):
 return {"record_id":"r1","actor":"验证员","reason":"原件核验","changes":[{"target_type":"table_instance","target_id":target,"field":field,"value":value,"location":{"physical_page":1,"bbox":[0,0,400,100]}}]}

a=artifact("UNKNOWN_P1_T0")
r=apply_table_cell_overlays(a,[record("table_identity",{"table_code":"FIN_02_income"})])
results["unknown_table_confirmation"]={"report":r,"after_code":a.table_fragments[0].table_code}
a=artifact("UNKNOWN")
r=apply_table_cell_overlays(a,[record("table_rows",{"table_code":"FIN_02_income","mode":"append_rows","rows":[{"cells":{"0":"221","1":"住房保障支出","2":"非数字","3":"200"}}]})])
results["rejected_rows_mutate_identity"]={"report":r,"after_code":a.table_fragments[0].table_code,"parse_run_uid":a.parse_run_uid,"manifest":a.processing_manifest}

truth={"sample_id":"denominator-probe","table_code":"FIN_02_income","physical_page":1,"rows":[{"code":"206","cells":{"total_actual":"100"}},{"code":"221","cells":{"total_actual":"200"}}]}
snapshot={"tables":[{"table_code":"FIN_02_income","page_start":1,"page_end":1,"rows":[["206","100"]],"cells":[{"row_idx":0,"col_idx":1,"raw_text":"100"}],"mappings":[{"source_col_idx":1,"canonical_measure":"total_actual"}]}]}
results["missing_row_recall"]=score_sample(truth,snapshot)
results["missing_table_recall"]=score_sample(truth,{"tables":[]})

parser=PDFParser(None)
header=["科目编码","科目名称","本年收入合计","财政拨款收入","上级补助收入","事业收入","经营收入","附属单位上缴收入","其他收入"]
values=["206","科学技术支出","600.00","100.00","100.00","100.00","100.00","100.00","100.00"]
full=grid("FIN_02_income","收入决算表",[header,values],{},1,uid="full",width=30)
lost=grid("FIN_02_income","收入决算表",[header[:-1],values[:-1]],{},1,uid="lost",width=33.75)
lost.geometry_profile="snap6"
selected=resolve_native_table_candidates(parser,[full],[lost])[0]
results["geometry_drops_legitimate_repeated_amount"]={"full_columns":9,"candidate_columns":8,"full_priority":parser._table_priority(full),"candidate_priority":parser._table_priority(lost),"full_structure":parser._structure_priority(full),"candidate_structure":parser._structure_priority(lost),"selected":selected.fragment_uid}

pdf=OUT/"sparse_vector_digits.pdf"
with pymupdf.open() as d:
 page=d.new_page(width=595,height=842)
 # Outline-style digits 100: vector strokes are visible but have no text layer or raster image.
 page.draw_line((70,80),(70,110),color=(0,0,0),width=3)
 for x in [90,115]:page.draw_rect(pymupdf.Rect(x,80,x+15,110),color=(0,0,0),width=3)
 d.save(pdf)
with pymupdf.open(pdf) as d:
 page=d[0];pix=page.get_pixmap(dpi=72);pix.save(OUT/"sparse_vector_digits.png")
 nonwhite=sum(v<245 for v in pix.samples)/len(pix.samples)
 results["sparse_vector_ocr_gate"]={"has_text":bool(page.get_text().strip()),"embedded_images":len(page.get_images(full=True)),"nonwhite_fraction":nonwhite,"gate":image_page_gate(str(pdf),[1],no_text_pages={1})}
(OUT/"reproductions.json").write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps(results,ensure_ascii=False,indent=2))
