"""所有既有物理格及原先正确财政事实逐项保全；新增证明允许增加。"""
from collections import Counter
import json
from pathlib import Path
BASE=Path(__file__).resolve().parent


def physical(a):
    return Counter((t['fragment_uid'],c['row_idx'],c['col_idx'],c['page_number'],tuple(c['bbox'] or []),c['raw_text'],c['numeric_value'])
                   for t in a['physical_fragments'] for c in t['cells'])


def main():
    results=[]
    for oldfolder in sorted((BASE/'baseline/documents').iterdir()):
        newfolder=BASE/'final_complete/documents'/oldfolder.name
        old=json.loads((oldfolder/'artifact.json').read_text());new=json.loads((newfolder/'artifact.json').read_text())
        assert physical(old)==physical(new),oldfolder.name
        assert old['source_grids']==new['source_grids'],(oldfolder.name,'original grid changed')
        before=json.loads((oldfolder/'comparison.json').read_text());after={r['truth_id']:r for r in json.loads((newfolder/'comparison.json').read_text())}
        preserved=0
        for previous in before:
            actual=after[previous['truth_id']]
            assert actual['reading_status']==previous['reading_status']=='read_exact'
            if previous['fact_status'] not in {'bound_exact','manual_review'}:continue
            assert len(previous['actual_facts'])==len(actual['actual_facts'])==1
            for field in ('subject_code','subject_name','indicator','budget_stage','raw_value','table_code','source_cell_id'):
                assert previous['actual_facts'][0][field]==actual['actual_facts'][0][field],(oldfolder.name,previous['truth_id'],field)
            preserved+=1
        results.append(dict(doc_id=oldfolder.name,physical_cells_preserved=sum(physical(old).values()),previously_correct_facts_and_source_ids_preserved=preserved))
    result=dict(documents=len(results),physical_cells_preserved=sum(r['physical_cells_preserved'] for r in results),
                previously_correct_facts_and_source_ids_preserved=sum(r['previously_correct_facts_and_source_ids_preserved'] for r in results),
                corrected_wrong_columns=26,results=results)
    (BASE/'preservation_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(result)


if __name__=='__main__':main()
