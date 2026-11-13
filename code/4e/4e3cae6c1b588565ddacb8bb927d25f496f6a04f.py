"""相同产物复算最新回执，独立逐格比较不受回执实现影响。"""
import json
from pathlib import Path
import sys
BASE=Path(__file__).resolve().parent;sys.path.insert(0,str(BASE.parents[1]))
from src.schemas.parse_artifact import ParseArtifact,FiscalFact
from src.services.table_import_receipt import build_table_import_receipt
results={}
for phase in ['baseline','final_complete']:
    documents=[]
    for d in sorted((BASE/phase/'documents').iterdir()):
        artifact=ParseArtifact.from_dict(json.loads((d/'artifact.json').read_text()))
        facts=[FiscalFact.from_dict(f) for f in json.loads((d/'facts.json').read_text())]
        receipt=build_table_import_receipt(artifact,facts,org_name=facts[0].org_name)
        (d/'receipt_final_checker.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
        documents.append(dict(doc_id=d.name,**receipt['fiscal_coverage']))
    totals={k:sum(r[k] for r in documents) for k,v in documents[0].items() if isinstance(v,int) and not isinstance(v,bool)}
    results[phase]=dict(totals=totals,documents=documents)
(BASE/'receipt_comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print({k:v['totals'] for k,v in results.items()})
