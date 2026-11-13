from pathlib import Path
import os, sys, json, hashlib, zipfile, io, copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.update(PYTHON_DOTENV_DISABLED='1', DATABASE_URL='', TESTING='true',
                  GOVBUDGET_AUTH_ENABLED='false', AI_ENABLED='false',
                  AI_ASSIST_ENABLED='false', OCR_ENABLED='false',
                  USER_DATA_DIR=str(OUT/'isolated_export_users'),
                  ORG_DATA_DIR=str(OUT/'isolated_export_orgs'),
                  UPLOAD_DIR=str(OUT/'isolated_export_uploads'))
from api.routes import reports
from scripts.run_zc07_acceptance import _evaluate_export_agreement

base = OUT/'export_probe/01_current'
payload = json.loads((base/'report.json').read_text())
issues = payload['review_report']['issues'] + payload['review_report']['tips']
result = {'scope':'current-source generator and saved real-handler bytes; synthetic business input', 'tampering':{}}
buffers=[]
for day in [5,6]:
    with patch.object(reports, 'datetime', SimpleNamespace(now=lambda *a:datetime(2026,10,day,tzinfo=timezone.utc)), create=True), patch.object(zipfile.time,'localtime',lambda *a:(2026,10,day,1,2,4,0,0,0)):
        buffers.append(reports._create_docx_report(payload['job_id'],payload,issues))
assert buffers[0] == buffers[1]
with zipfile.ZipFile(io.BytesIO(buffers[1])) as z:
    text=''.join(ET.fromstring(z.read('word/document.xml')).itertext())
    result['nonempty_historical_docx_cross_day']={'identical':True,'issue_count':len(issues), 'decision_count':len(payload['review_report']['human_decisions']), 'all_issue_markers':all('ISSUE_ID|'+i['id']+'|' in text for i in issues), 'all_decision_markers':all('DECISION_ID|'+d['issue_id']+'|' in text for d in payload['review_report']['human_decisions']), 'zip_dates_fixed':all(e.date_time==(1980,1,1,0,0,0) for e in z.infolist()), 'sha256':hashlib.sha256(buffers[1]).hexdigest()}
(OUT/'historical_nonempty_cross_day.docx').write_bytes(buffers[1])
with zipfile.ZipFile(base/'report.docx') as z:
    entries={n:z.read(n) for n in z.namelist()}
xml=entries['word/document.xml'].decode()
ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
tree=ET.fromstring(xml)
for table in tree.findall('.//w:tbl',ns):
    for row in list(table.findall('w:tr',ns)):
        if 'DECISION_ID|' in ''.join(row.itertext()):table.remove(row)
rev=payload['analysis_revision']
mutations={'deleted_decision_marker':ET.tostring(tree),
           'changed_reason':xml.replace('rule_evidence_incomplete','tampered_reason').encode(),
           'changed_revision':xml.replace(rev,'tampered_revision').encode(),
           'changed_message':xml.replace('文本收入预算与T1不一致','tampered_message').encode()}

# Keep the old actor globally visible, but alter only the DECISION_ID block.
# This checks local identity binding, beyond simple global token presence.
local=ET.fromstring(xml)
actor=payload['review_report']['human_decisions'][0]['actor']
for table in local.findall('.//w:tbl',ns):
    if 'DECISION_ID|' not in ''.join(table.itertext()):continue
    for node in table.findall('.//w:t',ns):
        if node.text == actor:node.text='tampered_actor'
body=local.find('w:body',ns)
p=ET.Element('{'+ns['w']+'}p');r=ET.SubElement(p,'{'+ns['w']+'}r');t=ET.SubElement(r,'{'+ns['w']+'}t');t.text=actor
body.insert(len(body)-1,p)
mutations['decision_actor_changed_old_actor_global_present']=ET.tostring(local)
for name,changed in mutations.items():
    assert changed != entries['word/document.xml']
    path=OUT/(name+'.docx')
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for k,v in entries.items():z.writestr(k,changed if k=='word/document.xml' else v)
    paths=[str(path if suffix=='docx' else base/('report.'+suffix)) for suffix in ['json','csv','docx','pdf']]
    gate=_evaluate_export_agreement({'documents':[{'doc_id':name,'export_paths':paths}]},ROOT)
    result['tampering'][name]=gate
    assert gate['status']=='not_computable' and gate.get('consistent_documents',0)==0,gate
result['history_03_04_sha_identical']={name:hashlib.sha256((OUT/'export_probe/03_historical'/name).read_bytes()).hexdigest()==hashlib.sha256((OUT/'export_probe/04_historical'/name).read_bytes()).hexdigest() for name in ['report.json','review_report.json','report.csv','report.docx','report.pdf','source.pdf']}
assert all(result['history_03_04_sha_identical'].values())
result['pdf_renderer_version']=reports._PDF_EXPORT_RENDERER_VERSION
result['limitations']=['Clock patch is audit input only; business source unchanged.','No cloud AI/OCR/frozen business metrics are evaluated.']
(OUT/'export_supplement.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False))
