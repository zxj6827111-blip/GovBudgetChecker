import os
import sys,json,io
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from scripts.d4_real_link_acceptance import verify_export_formats
import zipfile
import pymupdf as fitz
from xml.sax.saxutils import escape
out=Path(os.environ.get('CODEX_REVIEW_OUT', str(Path(__file__).resolve().parents[1] / 'replay')))
out.mkdir(parents=True,exist_ok=True)
rev='rev-independent-R3'
issues=[{'id':'relation-A','rule_id':'CROSS-TEST','location':{'page':22},'evidence':[{'quote':'13.24'},{'quote':'0.00'}]}, {'id':'relation-B','rule_id':'CROSS-TEST','location':{'page':24},'evidence':[{'quote':'81.11'},{'quote':'19.56'}]}]
# All three real formats deliberately contain A only, omit B and its page/operands.
text='analysis_revision: '+rev+'\nrelation-A CROSS-TEST page 22; 13.24 / 0.00\n'
db=io.BytesIO()
with zipfile.ZipFile(db,'w') as z:
 z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
 z.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
 z.writestr('word/document.xml','<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+escape(text)+'</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
pdf=fitz.open();pg=pdf.new_page();pg.insert_text((50,50),rev);pg.insert_text((50,70),'relation-A CROSS-TEST page 22; 13.24 / 0.00');pdfbytes=pdf.tobytes();pdf.close()
contents={'csv':text.encode(),'docx':db.getvalue(),'pdf':pdfbytes}
result=verify_export_formats(contents,issues,rev)
for k,v in contents.items():(out/('export_missing_B.'+k)).write_bytes(v)
res={'expected':'reject: relation-B/page24/81.11/19.56 omitted in all three formats','issues':issues,'revision':rev,'actual':result}
(out/'export_membership_control.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
print(json.dumps(res,ensure_ascii=False,indent=2))
