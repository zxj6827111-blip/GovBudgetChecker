"""Same recognized frame noun, only vary the declaration heading structure."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

CODE=Path('/Volumes/workSSD/software development/gbc-worktrees/auto-check-remediation-r22')
sys.path.insert(0,str(CODE))
from src.engine.narrative_zero_evidence import narrative_zero_sources

results=[]
for declaration in ['', '示例：', '填报示例：', '示例样式：', '模板示例说明：']:
    text='甲局2024年度部门决算。'+declaration+'财政拨款公务接待费支出决算0万元。因公出国（境）费支出决算0万元。'
    doc=SimpleNamespace(page_texts=[text],fact_context=SimpleNamespace(org_name='甲局',fiscal_year=2024),report_kind='final')
    results.append({'declaration':declaration,'text':text,'expected_authorized':not bool(declaration),
        'actual_authorized':{i:bool(narrative_zero_sources(doc,table_code='FIN_07_three_public',indicator=i,stage='决算')) for i in ['reception','overseas']}})
out=Path(__file__).with_name('declaration_structure_results.json')
out.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(results,ensure_ascii=False,indent=2))
