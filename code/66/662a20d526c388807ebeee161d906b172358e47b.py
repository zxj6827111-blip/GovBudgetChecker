from pathlib import Path
import sys,json,copy
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from tests.test_native_geometry_candidate import _income_candidate
from src.services.pdf_parser import PDFParser
from src.services.source_reconstruction import resolve_native_table_candidates
from src.services.manual_cell_overlay import apply_table_cell_overlays
from src.schemas.parse_artifact import ParseArtifact
parser=PDFParser(None)
full=_income_candidate(drop_last=False,blank_last_cell=True)
lost=_income_candidate(drop_last=True,blank_last_cell=False,profile="snap6")
# 表格边界仅多出 0.1 pt 的空白，不改变任何单元格或来源金额。
lost.bbox=(lost.bbox[0],lost.bbox[1],lost.bbox[2],lost.bbox[3]+0.1)
selected=resolve_native_table_candidates(parser,[full],[lost])[0]
r={"coverage_bypassed_by_point_one_bbox":{"full_priority":parser._table_priority(full),"lost_priority":parser._table_priority(lost),"full_tokens":parser._covered_amount_tokens(full),"lost_tokens":parser._covered_amount_tokens(lost),"selected":selected.fragment_uid}}
a=ParseArtifact(source_file_hash="a"*64);a.table_fragments=[full]
record={"record_id":"missing-id","actor":"验收员","reason":"核验失效表格目标","changes":[{"target_type":"table_instance","target_id":"missing-table-current","field":"table_identity","value":{"table_code":"FIN_02_income"},"location":{"physical_page":1,"bbox":[0,0,270,30]}}]}
r["missing_table_identity_grouping"]=apply_table_cell_overlays(a,[record])
Path(__file__).with_suffix(".json").write_text(json.dumps(r,ensure_ascii=False,indent=2));print(json.dumps(r,ensure_ascii=False,indent=2))
