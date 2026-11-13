"""原件图像浏览后记录物理页和目视数值；运行系统解析前冻结。"""
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
PAGES = {
    'doc01': [[6],[7,8],[9,10],[11,12],[13],[14],[15],[16],[17]],
    'doc02': [[5],[6],[7],[8],[9],[10],[11],[12],[13]],
    'doc03': [[6],[7,8],[9,10],[11,12],[13,14],[15],[16],[17],[18]],
    'doc04': [[6],[7,8],[9,10],[11,12],[13],[14],[15],[16],[17]],
    'doc05': [[5],[6],[7],[8,9],[10],[11],[12],[13],[14]],
    'doc06': [[6],[7],[8],[9,10],[11],[12],[13],[14],[15]],
    'doc07': [[7],[8,9],[10,11],[12,13],[14],[15,16],[17],[18],[19]],
    'doc08': [[6],[7,8],[9,10],[11,12],[13],[14],[15],[16],[17]],
    'doc09': [[6],[7,8,9,10,11],[12,13,14,15,16],[17,18],[19,20,21],[22],[23],[24],[25]],
    'doc10': [[5],[6,7],[8,9],[10,11],[12],[13],[14],[15],[16]],
}
TITLES = ['收入支出决算总表','收入决算表','支出决算表','财政拨款收入支出决算总表','一般公共预算财政拨款支出决算表','一般公共预算财政拨款基本支出决算表','财政拨款三公经费支出决算表','政府性基金预算财政拨款收入支出决算表','国有资本经营预算财政拨款收入支出决算表']
TOTALS = {
    'doc01': [13,'2680.34','2188.18','492.16'],
    'doc02': [9,'1984.98','1548.78','436.20'],
    'doc03': [14,'1417.66','170.06','920.49'],
    'doc04': [13,'1373.59','1020.33','353.26'],
    'doc05': [10,None,None,None],
    'doc07': [14,'4733.14','3365.38','1367.75'],
    'doc08': [13,'199469.38','145785.45','53683.93'],
    'doc09': [21,'7381.86','2637.75','4744.11'],
    'doc10': [12,'1162.48','1154.03','8.45'],
}
TRAVEL = {
    'doc01': ['2.35','.48',None,None,None,None,None,None,None,None,'2.35','.48'],
    'doc02': ['5.56','1.38',None,None,None,None,None,None,None,None,'5.56','1.38'],
    'doc03': ['.07','0','0','0','0','0','0','0','0','0','.07','0'],
    'doc04': ['.27','0','0','0','0','0','0','0','0','0','.27','0'],
    'doc05': ['0','0','0','0','0','0','0','0','0','0','0','0'],
    'doc06': ['28.06','23.92','0','0','28.06','23.92','18','17.99','10.06','5.93','0','0'],
    'doc07': ['21','16.95','0','0','20.50','16.95','0','0','20.50','16.95','.50','0'],
    'doc08': ['1909.50','1895.80',None,None,'1893.50','1893.47','630','629.97','1263.50','1263.50','16','2.33'],
    'doc09': ['33.53','25.86',None,None,'32','25.86','25','25','7','.86','1.53','0'],
    'doc10': ['3.2','.03',None,None,'3.2','.03',None,None,'3.2','.03',None,None],
}
record = dict(reviewer='Codex source-page visual inspection; not independent human signoff or unseen holdout',method='Full-document page montage inventory, original-page visual values, PyMuPDF-only source freeze before product parse',documents=[])
for id,pages in PAGES.items():
    d=dict(id=id,tables=[dict(number=k,title=TITLES[k-1],pages=nums) for k,nums in enumerate(pages,1)],extra_table_pages={'doc01':[5],'doc03':[5],'doc07':[6]}.get(id,[]),
           fin05_total_anchor=TOTALS.get(id),fin07_source_order_12_values=TRAVEL[id],
           all_pages_montage_viewed=True)
    record['documents'].append(d)
(BASE/'visual_source_truth.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
(BASE/'visual_source_truth.sha256').write_text(hashlib.sha256((BASE/'visual_source_truth.json').read_bytes()).hexdigest()+'\n')
print('Frozen 90 core tables, 3 organization tables, visual FIN05 anchors and full FIN07 rows.')
