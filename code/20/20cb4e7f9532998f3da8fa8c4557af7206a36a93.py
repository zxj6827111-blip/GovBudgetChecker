import os,sys,json,io,zipfile,copy
from pathlib import Path
from xml.etree import ElementTree as ET
os.environ['TESTING']='true';os.environ['GOVBUDGET_AUTH_ENABLED']='false';os.environ['PYTHON_DOTENV_DISABLED']='1';os.environ['NO_PROXY']='localhost,127.0.0.1';os.environ.pop('DATABASE_URL',None);os.environ.pop('GOVBUDGET_API_KEY',None)
sys.path.insert(0,str(Path.cwd()))
import pymupdf as fitz
from api import runtime
from api.main import app
from fastapi.testclient import TestClient
from scripts.d4_real_link_acceptance import verify_export_formats
out=Path(os.environ.get('CODEX_REVIEW_OUT',str(Path(__file__).resolve().parents[1]/'replay')));out.mkdir(parents=True,exist_ok=True);root=out/'format_control';root.mkdir(exist_ok=True);job='job-r4-export-fields';jd=root/job;jd.mkdir(exist_ok=True)
pdf=fitz.open()
for n in range(1,25):
 p=pdf.new_page();p.insert_text((72,72),{2:'13.24',3:'81.11',24:'19.56'}.get(n,'SOURCE'))
pdf.save(jd/'source.pdf');pdf.close()
rev='rev-20261111T010000-12345678'
def issue(identity,page,quotes):return {'id':identity,'source':'rule','rule_id':'CROSS-CONTROL','severity':'error','title':'金额关系','message':identity+' 金额关系请核对。','location':{'page':page},'evidence_status':'complete','evidence_verification':{'status':'verified','source_identity_complete':True},'evidence':[{'page':p,'quote':q,'evidence_id':identity+'-'+str(p)} for p,q in quotes]}
issues=[issue('A',2,[(2,'13.24'),(24,'19.56')]),issue('B',3,[(3,'81.11')])]
payload={'job_id':job,'status':'done','saved_path':job+'/source.pdf','analysis_revision':rev,'review_report':{'issues':issues,'tips':[],'manual_review':[],'human_decisions':[]},'result':{'rule_findings':issues,'ai_findings':[],'merged':{}}}
(jd/'status.json').write_text(json.dumps(payload,ensure_ascii=False));runtime.UPLOAD_ROOT=root
with TestClient(app) as c:
 contents={}
 for suffix in ['csv','docx','pdf']:
  r=c.get('/api/reports/download',params={'job_id':job,'format':suffix});r.raise_for_status();contents[suffix]=r.content;(out/('field_normal.'+suffix)).write_bytes(r.content)
res={'source':'actual candidate export endpoint, disposable two-member fixture','expected_revision':rev,'issues':issues,'normal':verify_export_formats(contents,issues,rev)}
ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def change_docx(field,newvalue):
 with zipfile.ZipFile(io.BytesIO(contents['docx'])) as z:files={n:z.read(n) for n in z.namelist()}
 tree=ET.fromstring(files['word/document.xml']);changes=0
 for table in tree.findall('.//w:tbl',ns):
  if 'ISSUE_ID|A|' not in ''.join(table.itertext()):continue
  for tr in table.findall('w:tr',ns):
   cells=tr.findall('w:tc',ns)
   if len(cells)==2 and ''.join(cells[0].itertext())==field:
    texts=cells[1].findall('.//w:t',ns);assert len(texts)==1;texts[0].text=newvalue;changes+=1
 assert changes==1,(field,changes)
 files['word/document.xml']=ET.tostring(tree,encoding='utf-8',xml_declaration=True);b=io.BytesIO()
 with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
  for n,v in files.items():z.writestr(n,v)
 return b.getvalue()
for name,field,value in [('wrong_page','证据页码','22'),('wrong_revision','分析版本',rev+'-old')]:
 mutated=dict(contents);mutated['docx']=change_docx(field,value);(out/('field_'+name+'.docx')).write_bytes(mutated['docx']);res[name]={'expected':'reject','new_value':value,'actual':verify_export_formats(mutated,issues,rev)}
(out/'export_field_controls.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
for k in ['normal','wrong_page','wrong_revision']:print(k,res[k].get('ok') if k=='normal' else res[k]['actual']['ok'],res[k].get('formats',{}).get('docx') if k=='normal' else res[k]['actual']['formats']['docx'])
