import collections
import gzip
import hashlib
import json
import pathlib
import re
import subprocess
import zipfile
import xml.etree.ElementTree as ET
import logging
from pypdf import PdfReader
logging.getLogger('pypdf').setLevel(logging.ERROR)

ROOT=pathlib.Path(__file__).resolve().parents[1]
CAND=pathlib.Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
EV=CAND/'outputs/unified_remediation_20261106';FLOW=EV/'flow';UF=FLOW/'evidence/user_flow'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):
    with(gzip.open(p,'rt')if p.suffix=='.gz'else p.open())as f:return json.load(f)
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def dump(n,v):(ROOT/'receipts'/n).write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2))

# 业务输出事实只做前后完整投影比较，不把它们当独立真值。
facts=[]
keys=['org_name','org_scope','fiscal_year','table_code','indicator','fund_scope','period','budget_stage',
      'subject_code','subject_name','row_order','raw_value','normalized_value','unit','recognition_status']
samples=read(EV/'final_d1/meta.json')['samples']
for item in samples:
    sid=item['sample_id'];counts=[];sets=[];hashes=[]
    for label in ['baseline_main','final_d1']:
        path=EV/label/'work'/sid/'uploads'/sid/'fact_context.json'
        data=read(path);rows=data['facts'];counts.append(len(rows));hashes.append(sha(path))
        sets.append(collections.Counter(json.dumps({k:r.get(k)for k in keys},ensure_ascii=False,sort_keys=True)for r in rows))
    facts.append({'sample_id':sid,'before_count':counts[0],'after_count':counts[1],
        'complete_amount_and_identity_projection_equal':sets[0]==sets[1],'before_context_sha256':hashes[0],
        'after_context_sha256':hashes[1],'new_fact_projections':sum((sets[1]-sets[0]).values())})
dump('independent_monetary_preservation.json',{'projection_keys':keys,'rows':facts,
    'limit':'本项验证是否新增/改写业务金额事实，独立真值另见原件事实簿。'})

verification=read(UF/'verification.json');old='SP-20261106-原件预算空白待补充';new='SP-20261106-新代际仍待补充预算原件'
labels={'current_before':('exports_current',True,False),'after_reanalysis':('new_export_no_old_session',False,False),
        'new_session':('new_session_exports',False,True),'historical':('historical_export_no_new_session',False,False)}
exports=[]
for label,(check,expected_old,expected_new)in labels.items():
    data=read(UF/f'{label}.json')
    for ext in ['json','csv','pdf','docx']:
        f=UF/f'{label}.{ext}'
        if ext=='pdf':
            text=''.join(p.extract_text()for p in PdfReader(str(f)).pages)
            # 渲染警告不等于工件损坏；附录p22已另行视觉核读。
        elif ext=='docx':
            with zipfile.ZipFile(f)as z:
                doc=ET.fromstring(z.read('word/document.xml'))
                text=''.join(doc.itertext())
        else:text=f.read_text(encoding='utf-8-sig')
        compact=re.sub(r'\s+','',text)
        exports.append({'label':label,'format':ext,'sha256':sha(f),
            'matches_saved_http_receipt_hash':sha(f)==verification['checks'][check][ext]['sha256'],
            'old_note_present':old in compact,'new_note_present':new in compact,
            'expected_old_note':expected_old,'expected_new_note':expected_new,
            'note_generation_isolation_matches':(old in compact)==expected_old and(new in compact)==expected_new,
            'analysis_revision':data['analysis_revision']})
uploads=[]
for folder in sorted((FLOW/'evidence/current').iterdir()):
    if not folder.is_dir()or not(folder/'upload_response.json').exists():continue
    response=read(folder/'upload_response.json');status=read(folder/'status_final.json');job=response['job_id']
    upload=FLOW/'db_env/uploads'/job/response['filename'];assert upload.is_file()
    uploads.append({'sample_prefix':folder.name.split('_')[0],'pdf_sha256':sha(upload),
        'upload_checksum_matches_original':response['checksum']==sha(upload)==status['checksum'],
        'organization_same_in_upload_and_status':response['organization_name']==status['organization_name'],
        'analysis_revision':status['analysis_revision'],'job_id':job,'status':status['status'],
        'ai_assist':status.get('use_ai_assist'),'report_kind':status['report_kind']})
run=read(FLOW/'RUN_IDENTITY.json')
git=lambda*args:subprocess.check_output(['git',*args],cwd=CAND).decode().splitlines()
flow={'uploads':uploads,'exports':exports,
    'safe_identity':{k:run.get(k)for k in ['business_head','api_module','worker_module','migrations_done']},
    'business_diff_from_recorded_flow_to_candidate':git('diff','--name-only',run['business_head']+'..5ac9e71b','--','src','api','config','rules','app','contracts'),
    'stopped_validation_services_receipt_present':(FLOW/'cleanup.json').exists(),
    'static_generation_semantics':{
        'current_before':read(UF/'current_before.json')['obligation_review'].get('session_status'),
        'after_reanalysis':read(UF/'after_reanalysis.json')['obligation_review'].get('session_status'),
        'new_session':read(UF/'new_session.json')['obligation_review'].get('session_status'),
        'historical_reason':read(UF/'historical.json')['obligation_review'].get('reason')},
    'scope':'独立静态复核六份上传工件、16导出文件、源代码及截图；不是本轮独立重新执行PostgreSQL/API/浏览器流程。',
    'live_not_reexecuted':['database persistence','HTTP 409 stale basis rejection','review complete rejection','worker service health'],
    'docx_visual_status':'bundled LibreOffice completed render 118 pages; Chinese glyphs missing on first page. OOXML contains Chinese. Bundled renderer fonts lack SimSun/SimHei/CJK fallback. Cannot sign visual Word compatibility; actual Word unverified.',
    'pdf_visual_status':'Poppler p22 appendix visually checked; original source p15 screenshot matches quote/bbox. Full102-page export layout not independently signed.',
    'historical_note_policy':'historical export deliberately excludes live review-session notes (api/routes/reports.py:2044); absence of both old/new notes is not a demonstrated loss bug.'}
dump('flow_exports_independent_static.json',flow)
print(json.dumps({'facts_equal':sum(r['complete_amount_and_identity_projection_equal']for r in facts),
    'facts_total':len(facts),'uploads':len(uploads),'export_hashes_match':sum(r['matches_saved_http_receipt_hash']for r in exports),
    'note_isolation_matches':sum(r['note_generation_isolation_matches']for r in exports),
    'exports':len(exports)},ensure_ascii=False))
