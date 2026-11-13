import json,csv,hashlib
from pathlib import Path
import pymupdf as fitz
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_acceptance_shanghai2022_20261021')
manifest={r['doc_id']:r for r in json.loads((ROOT/'source_manifest.json').read_text())}
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
def source_render(doc,page,box=None,name=''):
 r=manifest[doc];out=ROOT/'defect_evidence'/doc;out.mkdir(parents=True,exist_ok=True)
 with fitz.open(r['frozen_path']) as pdf:
  path=out/f"page-{page}{name}.png";pdf[page-1].get_pixmap(dpi=150,clip=fitz.Rect(box)if box else None).save(path)
 return str(path)
def comparisons(doc):return json.loads((ROOT/'cell_comparisons'/doc/'comparison.json').read_text())['cells']
def defect(id,title,doc,page,table,positions,expected,actual,impact,priority='P1',status='confirmed_original_and_runtime'):
 r=manifest[doc];d={'defect_id':id,'title':title,'priority':priority,'status':status,'file':r['file'],'source_sha256':r['sha256'],'page':page,'table':table,'positions':positions,'original':expected,'system':actual,'impact':impact,'reproduction':'使用冻结原件通过本轮 harness/run_native_batch.py 上传分析，按 source_sha256 找到 immutable_snapshot.json；对照 cell_comparisons 对应物理格。','original_render':source_render(doc,page),'human_verified':False,'production_logic_modified':False};return d
defects=[]
for doc,page in [('SH2022-02',17),('SH2022-15',10)]:
 cs=[c for c in comparisons(doc)if c['physical_page']==page and c['literal_kind']=='amount'and c['comparison']=='no_fact_at_original_candidate']
 defects.append(defect('NEW-FIN04-'+doc[-2:],'财政拨款收支预算总表已识别但未生成金额事实',doc,page,'FIN_04_fiscal_grant_total',[{'occurrence_id':c['occurrence_id'],'bbox':c['bbox'],'source_row':c['source_row'],'raw_value':c['raw_value']}for c in cs],f'{len(cs)} 个独立原件网格金额候选（包括重复出现和零）','logical table 存在，column_mappings=[]，raw/effective 中该表事实数=0','物理表和事实生成之间失去金额覆盖；数据库一致性通过不能证明完整。'))
for doc,page in [('SH2022-02',27),('SH2022-15',15)]:
 cs=[c for c in comparisons(doc)if c['physical_page']==page and c['literal_kind']=='amount'and c['comparison']=='no_fact_at_original_candidate']
 defects.append(defect('NEW-SG-'+doc[-2:],'三公表公务用车及机关运行金额未进入事实',doc,page,'部门“三公”经费和机关运行经费预算表',[{'occurrence_id':c['occurrence_id'],'bbox':c['bbox'],'raw_value':c['raw_value']}for c in cs],[c['raw_value']for c in cs],'对应物理位置无 raw/effective 事实','公务用车小计/购置/运行及机关运行金额覆盖不足；可读 0.00 仍在候选分母。'))
cs=[c for c in comparisons('SH2022-20')if c['physical_page']==12 and 'row_name_requires_visual_review'in c.get('mismatch_fields',[])]
defects.append(defect('NEW-NAME-20','可见科目名称保存为纯编码，资格停留 low_confidence','SH2022-20',12,'2022年部门支出预算总表',[{'occurrence_id':c['occurrence_id'],'bbox':c['bbox'],'source_row':c['source_row'],'facts':c['facts']}for c in cs],{'20113':'商贸事务','2011301':'行政运行'},'subject_name=20113 / 2011301 等纯编码；金额存在','完整身份未恢复；低资格阻止直接准入，但可读名称未恢复仍不能计完成。'))
# The original policy citation is visible; the rule result falsely treats cited historical year as report-year conflict.
runtime=ROOT/'runtime_native/native_final';status=json.loads((runtime/'status.json').read_text())
issue=next(i for i in status['review_report']['issues']if i['rule_id']=='BUD-003'and(i.get('location')or{}).get('page')==33)
loc=issue['location'];defects.append(defect('NEW-RULE-YEAR','引用政策文号中的2019被报为预算年度冲突','SH2022-02',33,'项目立项依据',[{'bbox':loc.get('bbox'),'quote':issue['evidence'][0].get('quote')}],'封面2022；立项依据引用2019政策不改变报告年度',issue.get('message')or issue.get('title'),'增加年份误报。',priority='P2'))
write(ROOT/'defects.json',{'scope':'frozen_first_version_findings_no_production_logic_fix','defects':defects})
with (ROOT/'defects.csv').open('w',encoding='utf-8-sig',newline='')as f:
 w=csv.DictWriter(f,fieldnames=list(defects[0]));w.writeheader();w.writerows(defects)
print('defects',len(defects))
