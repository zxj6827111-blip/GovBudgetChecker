import os
import sys,json,csv,io,zipfile,copy
from pathlib import Path
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path.cwd()))
from scripts.d4_real_link_acceptance import verify_export_formats
out=Path(os.environ.get('CODEX_REVIEW_OUT',str(Path(__file__).resolve().parents[1]/'replay')));out.mkdir(parents=True,exist_ok=True);inputs=Path(__file__).resolve().parents[1]/'exports';d=json.loads((inputs/'actual_export.json').read_text());issues=d['review_report']['issues'];rev=d['analysis_revision'];contents={f:(inputs/('actual_export.'+f)).read_bytes() for f in ['csv','docx','pdf']}
expected={i['id'] for i in json.loads((Path(__file__).resolve().parents[1]/'fixtures/expected_export_members.json').read_text())}
assert {i['id'] for i in issues}==expected and len(issues)==14
res={'expected_member_ids':sorted(expected),'revision':rev,'normal':verify_export_formats(contents,issues,rev)}
reader=csv.DictReader(io.StringIO(contents['csv'].decode('utf-8-sig')));fields=reader.fieldnames;rows=list(reader);target='V33-CROSS-SAN-GONG-ECON-22'
for mode in ['missing_member','missing_side']:
 mutated=copy.deepcopy(rows)
 if mode=='missing_member':mutated=[r for r in mutated if not(r['record_type']=='issue' and r['issue_id']==target)]
 else:
  r=next(r for r in mutated if r['record_type']=='issue' and r['issue_id']==target);r['evidence_quotes']=json.dumps([e for e in json.loads(r['evidence_quotes']) if e['quote']!='19.56'],ensure_ascii=False)
 b=io.StringIO();w=csv.DictWriter(b,fieldnames=fields);w.writeheader();w.writerows(mutated);c=dict(contents);c['csv']=b.getvalue().encode('utf-8-sig');(out/('actual_export_'+mode+'.csv')).write_bytes(c['csv']);res[mode]=verify_export_formats(c,issues,rev)
ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
for name,field,value in [('wrong_page','证据页码','222'),('wrong_revision','分析版本',rev+'-old')]:
 with zipfile.ZipFile(io.BytesIO(contents['docx'])) as z:files={n:z.read(n) for n in z.namelist()}
 tree=ET.fromstring(files['word/document.xml']);n=0
 for table in tree.findall('.//w:tbl',ns):
  if 'ISSUE_ID|'+target+'|' not in ''.join(table.itertext()):continue
  for tr in table.findall('w:tr',ns):
   cells=tr.findall('w:tc',ns)
   if len(cells)==2 and ''.join(cells[0].itertext())==field:
    ts=cells[1].findall('.//w:t',ns);assert len(ts)==1;ts[0].text=value;n+=1
 assert n==1
 files['word/document.xml']=ET.tostring(tree,encoding='utf-8',xml_declaration=True);b=io.BytesIO()
 with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
  for k,v in files.items():z.writestr(k,v)
 c=dict(contents);c['docx']=b.getvalue();(out/('actual_export_'+name+'.docx')).write_bytes(c['docx']);res[name]={'target':target,'field':field,'new_value':value,'expected':'reject','actual':verify_export_formats(c,issues,rev)}
(out/'actual_export_controls.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
for k,r in res.items():
 if k in ['normal','missing_member','missing_side']:print(k,r['ok'],{f:x.get('member_count') for f,x in r['formats'].items()})
 if k in ['wrong_page','wrong_revision']:print(k,r['actual']['ok'],r['actual']['formats']['docx'])
