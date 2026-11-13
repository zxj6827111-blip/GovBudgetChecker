import sys,json
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from scripts.d4_real_link_acceptance import verify_export_formats
p=Path(__file__).resolve().parents[1]/'exports'
d=json.loads((p/'actual_export.json').read_text());issues=d['review_report']['issues'];rev=d['analysis_revision']
c={f:(p/('actual_export.'+f)).read_bytes() for f in ['csv','docx','pdf']}
assert verify_export_formats(c,issues,rev)['ok'] is True
results={}
for variant in ['wrong_page','wrong_revision']:
 b=dict(c);b['docx']=(p/('actual_export_'+variant+'.docx')).read_bytes()
 results[variant]=verify_export_formats(b,issues,rev)['ok']
print(results)
assert not any(results.values()),'错误主定位页或分析版本不得因含有预期子串而被接受'
