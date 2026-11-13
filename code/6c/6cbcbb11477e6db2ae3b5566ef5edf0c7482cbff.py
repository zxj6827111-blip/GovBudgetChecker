import sys,json,csv,io,os
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from scripts.d4_real_link_acceptance import verify_export_formats
pkg=Path(__file__).resolve().parents[1]
out=Path(os.environ.get('CODEX_REVIEW_OUT',str(pkg/'replay')));out.mkdir(parents=True,exist_ok=True)
j=json.loads((pkg/'exports/actual_export.json').read_text())
c={f:(pkg/('exports/actual_export.'+f)).read_bytes() for f in ['csv','docx','pdf']}
r=csv.DictReader(io.StringIO(c['csv'].decode('utf-8-sig')));fields=r.fieldnames;rows=list(r)
target='V33-CROSS-SAN-GONG-ECON-22'
kept=[a for a in rows if not(a['record_type']=='issue' and a['issue_id']==target)]
assert len(kept)==len(rows)-1
s=io.StringIO();w=csv.DictWriter(s,fieldnames=fields);w.writeheader();w.writerows(kept);c['csv']=s.getvalue().encode('utf-8-sig')
res=verify_export_formats(c,j['review_report']['issues'],j['analysis_revision'])
(out/'actual_export_removed_issue_control.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
print(res)
assert res['ok'] is False,'验收器误接受：真实CSV问题成员14→13'
