"""Bounded review of the existing seven cues, structural and length fallback paths."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

CODE=Path('/Volumes/workSSD/software development/GovBudgetChecker/artifacts/daily-main')
sys.path.insert(0,str(CODE))
from src.engine.narrative_zero_evidence import narrative_zero_sources

HEAD='甲局2024年度部门决算。'
A='财政拨款公务接待费支出决算0万元。'
B='因公出国（境）费支出决算0万元。'
rows=[]
def check(name,pages,expected):
    doc=SimpleNamespace(page_texts=pages,fact_context=SimpleNamespace(org_name='甲局',fiscal_year=2024),report_kind='final')
    actual={i:bool(narrative_zero_sources(doc,table_code='FIN_07_three_public',indicator=i,stage='决算')) for i in ['reception','overseas']}
    rows.append({'case':name,'page_texts':pages,'expected':expected,'actual':actual,'matches':actual==expected})

check('ordinary_fact',[HEAD+A+B],{'reception':True,'overseas':True})
check('ordinary_colon_without_frame_cue',[HEAD+'填报说明：'+A+B],{'reception':True,'overseas':True})
for cue in ['假设','假定','示例','样例','模板','虚构','演示']:
    for layout,text in [('colon',cue+'说明：'),('separate_line','\n'+cue+'说明\n'),('terminated_sentence',cue+'见附表。')]:
        check(cue+'_'+layout,[HEAD+text+A+B],{'reception':False,'overseas':False})
    check(cue+'_cross_page',[HEAD+cue+'说明：',A+B],{'reception':False,'overseas':False})
    check(cue+'_explicit_closure',[HEAD+cue+'说明：'+A+'以上为'+cue+'。'+B],{'reception':False,'overseas':True})
for prefix in [0,4,12,16,17,32,64,256]:
    for suffix in [0,24,25,128]:
        heading='填'*prefix+'示例'+'说明'*suffix+'：'
        check(f'length_prefix{prefix}_suffix{suffix}',[HEAD+heading+A+B],{'reception':False,'overseas':False})
out=Path(__file__).with_name('scope_matrix_results.json')
out.write_text(json.dumps({'scope':'existing seven recognized cues only; not an arbitrary-language guarantee','cases':len(rows),'failures':[r for r in rows if not r['matches']],'results':rows},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'cases':len(rows),'failed':sum(not r['matches'] for r in rows),'failure_names':[r['case'] for r in rows if not r['matches']]},ensure_ascii=False))
