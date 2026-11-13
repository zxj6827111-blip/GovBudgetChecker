"""只读执行者42条HTTP/SQL/导出工件，与本轮独立实时复演分开记录。"""
import collections
import hashlib
import json
from pathlib import Path
import re
import zipfile
import pymupdf

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106/outputs/unified_review_remediation_20261106/flow/evidence/user_flow')
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
saved=read(SOURCE/'http_receipts.json');verification=read(SOURCE/'verification.json')
old='SP-20261106-原件预算空白待补充';new='SP-20261106-新代际仍待补充预算原件'
labels={'current_before':('exports_current',True,False),'after_reanalysis':('new_export_no_old_session',False,False),
        'new_session':('new_session_exports',False,True),'historical':('historical_export_no_new_session',False,False)}
exports=[]
for label,(check,want_old,want_new)in labels.items():
    for fmt in ['json','csv','pdf','docx']:
        p=SOURCE/f'{label}.{fmt}'
        if fmt=='pdf':
            with pymupdf.open(p)as doc:text=''.join(page.get_text()for page in doc)
        elif fmt=='docx':
            with zipfile.ZipFile(p)as doc:text=doc.read('word/document.xml').decode()
        else:text=p.read_text(encoding='utf-8-sig')
        compact=re.sub(r'\s+','',text)
        exports.append({'label':label,'format':fmt,'sha256':sha(p),
            'matches_saved_verification_hash':sha(p)==verification['checks'][check][fmt]['sha256'],
            'matches_an_actual_http_response_hash':any(r['response_sha256']==sha(p)and r['http']==200 for r in saved),
            'old_note_present':old in compact,'new_note_present':new in compact,
            'generation_notes_match':(old in compact)==want_old and(new in compact)==want_new})
rejected=[r for r in saved if r['http']==409]
assert len(saved)==42 and len(rejected)==2
assert {r['response']['detail']['error']for r in rejected}=={'review_completion_blocked','analysis_basis_stale'}
db=read(SOURCE/'database_persistence.json')
assert len(db['decisions'])==2 and {r['decision']for r in db['decisions']}=={'pending'}
assert {r['status']for r in db['sessions']}=={'invalidated','in_progress'}
assert all(r['matches_saved_verification_hash']and r['matches_an_actual_http_response_hash']and r['generation_notes_match']for r in exports)
receipt={'scope':'Independent static inspection of executor artifacts; not substituted for independent live replay',
    'http_count':len(saved),'http_status_counts':dict(collections.Counter(str(r['http'])for r in saved)),
    'rejections':rejected,'sql_readback_file_sha256':sha(SOURCE/'database_persistence.json'),
    'sql_pending_rows':2,'sql_session_states':['invalidated','in_progress'],'exports':exports,
    'limits':['DOCX content and note isolation only; actual Word layout unverified','No whole-PDF layout or all-input accuracy acceptance']}
(ROOT/'receipts/executor_flow_artifacts_independent_audit.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
print(json.dumps({'http_count':len(saved),'actual409':len(rejected),'exports_checked':len(exports),'hash_and_notes_match':True},ensure_ascii=False))
