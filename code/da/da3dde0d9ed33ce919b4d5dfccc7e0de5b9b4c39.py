from pathlib import Path
import sys, json, copy
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from src.services.pdf_parser import PDFParser
from src.services.source_reconstruction import resolve_native_table_candidates
from src.services.org_binding_decision import decide_auto_binding
from src.services.manual_cell_overlay import apply_table_cell_overlays
from tests.remediation.test_budget_2022_table_semantics import grid
from src.schemas.parse_artifact import ParseArtifact,LogicalTableInstance
parser=PDFParser(None)
header=["科目编码","科目名称","本年收入合计","财政拨款收入","上级补助收入","事业收入","经营收入","附属单位上缴收入","其他收入"]
values=["206","科学技术支出","600.00","100.00","100.00","100.00","100.00","100.00","100.00"]
second=["221","住房保障支出","500.00","100.00","100.00","100.00","100.00","100.00",""]
full=grid("FIN_02_income","收入决算表",[header,values,second],{},1,uid="full",width=30)
lost=grid("FIN_02_income","收入决算表",[header[:-1],values[:-1],second[:-1]],{},1,uid="lost",width=33.75)
lost.geometry_profile="snap6"
for t in [full,lost]:
 for c in t.cells:
  if c.numeric_value is not None and not c.is_header:
   c.native_text_sources=[{"physical_page":1,"start_char_index":100*c.row_idx+10*c.col_idx,"end_char_index":100*c.row_idx+10*c.col_idx+len(c.raw_text)}]
selected=resolve_native_table_candidates(parser,[full],[lost])[0]
results={"geometry_coverage_with_one_legitimate_blank":{"full_priority":parser._table_priority(full),"lost_priority":parser._table_priority(lost),"full_structure":parser._structure_priority(full),"lost_structure":parser._structure_priority(lost),"full_duplicate_claims":parser._duplicate_source_claims(full),"lost_duplicate_claims":parser._duplicate_source_claims(lost),"selected":selected.fragment_uid,"missing_original_token":{"page":1,"start":180,"end":186,"raw_value":"100.00"}}}
r=decide_auto_binding(filename="验证.pdf",page_texts=["甲市安全生产监督管理中心\n2025年度单位决算","第一部分 甲市安全生产管理中心概况"],directory=[{"id":"u","name":"甲市安全生产监督管理中心","level":"unit","parent_id":"d"}],report_kind="final")
results["name_conflict"]={k:r.get(k) for k in ["decision","organization_id","reasons","original_text_inconsistencies"]}
a=ParseArtifact(source_file_hash="a"*64);t=copy.deepcopy(full);t.fragment_uid="t1";t.table_code="UNKNOWN_P1_T0";a.table_fragments=[t];a.logical_tables=[LogicalTableInstance(instance_uid="i1",table_code=t.table_code,title=t.title,fragment_uids=[t.fragment_uid],page_start=1,page_end=1)]
initial_run=a.parse_run_uid
r=apply_table_cell_overlays(a,[{"record_id":"bad-record","actor":"验证员","reason":"原件核验","changes":[{"target_type":"table_instance","target_id":"t1","field":"table_rows","value":{"table_code":"FIN_02_income","mode":"append_rows","rows":[{"cells":{"0":"221","1":"住房保障支出","2":"非数字"}}]},"location":{"physical_page":1,"bbox":[0,0,270,30]}}]}])
results["invalid_rows_still_mutate_real_unknown"]={"report":r,"table_code_after":t.table_code,"instance_code_after":a.logical_tables[0].table_code,"parse_run_unchanged":initial_run==a.parse_run_uid,"manifest":a.processing_manifest}
Path(__file__).with_name("extended_probes.json").write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False,indent=2))
