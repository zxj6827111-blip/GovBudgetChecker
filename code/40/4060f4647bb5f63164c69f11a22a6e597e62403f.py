from pathlib import Path
import json,subprocess
run=Path(__file__).resolve().parents[1];s=json.loads((run/'run_state.json').read_text())
sql="SELECT json_build_object('decisions', (SELECT json_agg(json_build_object('obligation_id',obligation_id,'decision',decision,'note',note,'revision',revision)) FROM review_obligation_decisions),'sessions',(SELECT json_agg(json_build_object('status',status)) FROM review_sessions));"
r=subprocess.run(['docker','exec',s['container'],'psql','-U','validator','-d','validation','-Atc',sql],capture_output=True,text=True,check=True)
proof=json.loads(r.stdout);assert len(proof['decisions'])==2;assert {x['decision'] for x in proof['decisions']}=={'pending'};assert {x['status'] for x in proof['sessions']}=={'in_progress','invalidated'}
(run/'evidence/user_flow/database_persistence.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2));print('Verified database decisions and invalidated/current sessions')
