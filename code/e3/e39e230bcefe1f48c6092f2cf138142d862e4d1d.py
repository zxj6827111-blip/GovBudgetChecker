import json
from pathlib import Path
OUT=Path(__file__).resolve().parent
before=json.loads((OUT/'private/database_before.json').read_text())['history']
after=json.loads((OUT/'private/database_after_final.json').read_text())['history'];rows=[]
for table,old in before.items():
 current={r['id']:r for r in after[table]};allowed={'status','invalidated_at','invalidated_reason','updated_at'} if table=='review_sessions' else set()
 changed=[]
 for record in old:
  now=current.get(record['id']);assert now,(table,record['id'])
  fields={k for k,v in record.items() if now.get(k)!=v};assert fields<=allowed,(table,record['id'],fields)
  if fields:changed.append(dict(id=record['id'],fields=sorted(fields)))
 rows.append(dict(table=table,original_rows=len(old),current_rows=len(current),no_original_rows_deleted=True,no_decision_or_note_changed=True,lifecycle_changes=changed))
(OUT/'database_history_verified.json').write_text(json.dumps(dict(rows=rows),ensure_ascii=False,indent=2));print(json.dumps(rows,ensure_ascii=False))
