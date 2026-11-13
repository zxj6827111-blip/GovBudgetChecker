from pathlib import Path
import sys,json,copy
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from tests.remediation.test_budget_2022_table_semantics import grid
from src.services.pdf_parser import PDFParser
from src.services.source_reconstruction import resolve_native_table_candidates,_amount_coverage_preference,_original_amount_universe
header=["科目编码","科目名称","本年收入合计","财政拨款收入","上级补助收入","事业收入","经营收入","附属单位上缴收入","其他收入"]
rows=[header,["206","科学技术支出","600.00","100.00","100.00","100.00","100.00","100.00","100.00"],["221","住房保障支出","500.00","100.00","100.00","100.00","100.00","100.00",""]]
full=grid("FIN_02_income","收入决算表",copy.deepcopy(rows),{},1,uid="full",width=30)
lost=grid("FIN_02_income","收入决算表",[r[:-1] for r in rows],{},1,uid="lost",width=33.75)
for table in [full,lost]:
 for c in table.cells:
  if c.is_header or c.numeric_value is None:continue
  start=100*c.row_idx+10*c.col_idx
  c.native_text_sources=[{"physical_page":1,"start_char_index":start,"end_char_index":start+len(c.raw_text),"text":c.raw_text}]
# 在修改任何候选前独立留存原页；字符坐标来自完整原页格，而非缺列候选。
chars=[{"text":" ","x0":0,"x1":1,"top":0,"bottom":1} for _ in range(300)]
for c in full.cells:
 if not c.native_text_sources:continue
 start=c.native_text_sources[0]["start_char_index"]
 for i,text in enumerate(c.raw_text):chars[start+i]={"text":text,"x0":c.bbox[0]+1+i,"x1":c.bbox[0]+2+i,"top":c.bbox[1]+1,"bottom":c.bbox[1]+5}
page=SimpleNamespace(chars=copy.deepcopy(chars),images=[])
parser=PDFParser(None);results=[]
for delta in [0.0,0.1]:
 for reverse in [False,True]:
  a,b=copy.deepcopy(full),copy.deepcopy(lost);b.bbox=(*b.bbox[:3],b.bbox[3]+delta)
  first,second=(b,a) if reverse else (a,b)
  pref=_amount_coverage_preference(first,second,page)
  chosen=resolve_native_table_candidates(parser,[first],[second],page=page)[0]
  results.append({"bbox_delta":delta,"reverse":reverse,"verified_preference":pref,"original_amount_characters":len(_original_amount_universe(page,(a,b))),"selected":chosen.fragment_uid})
# 两候选共同遗漏时，不得把候选并集当作原件全集。
a,b=copy.deepcopy(full),copy.deepcopy(lost)
for table in [a,b]:
 c=next(c for c in table.cells if (c.row_idx,c.col_idx)==(1,2));c.raw_text="";c.numeric_value=None;c.native_text_sources=[];table.rows[1][2]=""
missing_pref=_amount_coverage_preference(a,b,page)
# 部分但非零的来源、重认领、错误原字不得被当作完整覆盖。
controls={}
for defect in ["partial","duplicate","wrong_original_text"]:
 a,b=copy.deepcopy(full),copy.deepcopy(lost)
 c=next(c for c in a.cells if (c.row_idx,c.col_idx)==(1,2))
 if defect=="partial":c.native_text_sources=[]
 elif defect=="duplicate":c.native_text_sources=copy.deepcopy(next(c for c in a.cells if (c.row_idx,c.col_idx)==(1,3)).native_text_sources)
 else:c.native_text_sources[0]["text"]="999.00"
 controls[defect]=_amount_coverage_preference(a,b,page)
proof={"selection_cases":results,"jointly_missing_preference":missing_pref,"untrusted_proof_preferences":controls}
assert all(r["selected"]=="full" and r["verified_preference"] is not None for r in results)
assert missing_pref is None and all(v is None for v in controls.values())
Path(__file__).with_suffix(".json").write_text(json.dumps(proof,ensure_ascii=False,indent=2));print(json.dumps(proof,ensure_ascii=False,indent=2))
