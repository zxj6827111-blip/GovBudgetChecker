"""A normal declaration heading must not become a factual assertion at length 17."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

CODE=Path('/Volumes/workSSD/software development/gbc-worktrees/auto-check-remediation-r22')
sys.path.insert(0,str(CODE))
from src.engine.narrative_zero_evidence import narrative_zero_sources

rows=[]
for heading in ['', '财政拨款三公经费支出填报示例说明：', '本年度财政拨款三公经费支出决算填报示例说明：']:
    text='甲局2024年度部门决算。\n'+heading+'\n财政拨款公务接待费支出决算0万元。因公出国（境）费支出决算0万元。'
    doc=SimpleNamespace(page_texts=[text],fact_context=SimpleNamespace(org_name='甲局',fiscal_year=2024),report_kind='final')
    rows.append({'heading':heading,'heading_length':len(heading),'prefix_length_before_frame':heading.find('示例') if heading else None,
        'text':text,'expected_authorized':not bool(heading),
        'actual_authorized':{i:bool(narrative_zero_sources(doc,table_code='FIN_07_three_public',indicator=i,stage='决算')) for i in ['reception','overseas']}})
Path(__file__).with_name('declaration_boundary_results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(rows,ensure_ascii=False,indent=2))
