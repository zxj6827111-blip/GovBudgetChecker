from pathlib import Path
import fitz,json,re,unicodedata,hashlib
b=Path('outputs/real_intake_review_20261008'); src=Path('/Users/zhangxingjin/Desktop/决算测试上海市')
truth=json.loads((b/'disclosure_truth.json').read_text()); native=json.loads((b/'native/summary.json').read_text())
files={d['organization']:b/'native'/('document_'+str(i+1).zfill(2))/'issues.json' for i,d in enumerate(native['documents'])}
xs={n:json.loads(f.read_text()) for n,f in files.items()}
normalize=lambda s:re.sub(r'\s+','',unicodedata.normalize('NFKC',s))
def ev(n,p,s):
 doc=fitz.open(src/(n+'.pdf'));page=doc[p-1];chars=[]
 for block in page.get_text('rawdict')['blocks']:
  for line in block.get('lines',[]):
   for sp in line['spans']:
    for c in sp['chars']:
     for ch in normalize(c['c']):chars.append((ch,c['bbox']))
 hay=''.join(c[0] for c in chars);needle=normalize(s);start=hay.find(needle)
 if start<0:raise ValueError((n,p,s))
 rect=fitz.Rect(chars[start][1])
 for ch,r in chars[start:start+len(needle)]:rect|=fitz.Rect(r)
 return {'page':p,'bbox':[round(x,2) for x in rect],'bbox_space':'PDF points, top-left origin; physical page 1-based','snippet':s,'source':'original PDF text layer; pertinent narrative pages rendered and visually verified'}
def nref(n,i):
 x=xs[n][i];return {'path':str(files[n]),'json_array_index':i,'rule':x['rule'],'severity':x['severity'],'page':x.get('location',{}).get('page'),'message':x['message'],'evidence_text':x.get('evidence_text','')}
match={2:[6,7],3:[4],7:[0,9,10,11,12],8:[13,14],10:[15,16],11:[5]}
results=[]
for t in truth['issues']:
 i=int(t['id'].split('-')[-1]);indices=match.get(i,[]);status='detected' if indices else 'missed_in_native_diagnostic'
 reason='本轮系统发出对应提醒，数字/文字证据可与独立原件判断对应。' if indices else {
 1:'本轮文旅没有输出绩效自评项目数与评级数合计检查。',4:'本轮商务没有输出零购置支出/购车用途/无车辆更新三句之间的矛盾检查。',5:'本轮商务没有输出同义重述差异结果的原因检查。',6:'本轮商务没有输出同义重述同比结果的原因检查。',9:'本轮交通检测了总收支及三公同比，但没有检测其他事项中的机关运行经费同比复算。'}[i]
 results.append({'truth_id':t['id'],'organization':t['organization'],'title':t['title'],'classification':status,'native_matches':[nref(t['organization'],j) for j in indices],'reason':reason,'evidence':t['evidence'],'scope_limit':'仅本轮native PDF诊断输出；如真实导入另有AI审校，需另跑该路径确认。'})
fp=[{'id':'D-FP-001','organization':'上海市民政局','classification':'confirmed_false_positive','native_issue':nref('上海市民政局',15),'reason':'民政原件p31用车166.30万元完成预算65.05%；接待费句跨p31末尾“公务接”与p32首行“待费”续接，25.52万元完成74.40%。系统跨页绑定把接待完成率错配到上一项公务用车。','expected':'用车166.30÷255.63×100=65.05%；接待25.52÷34.30×100=74.40%，两者均成立','actual':'系统把74.40%赋给公务用车项并报error','evidence':[ev('上海市民政局',31,'公务用车购置及运行维护费支出决算为166.30万元，完成预算的65.05%；公务接'),ev('上海市民政局',32,'待费支出决算为25.52万元，完成预算的74.40%'),ev('上海市民政局',22,'255.63\n166.30'),ev('上海市民政局',22,'34.30\n25.52')],'suggestion':'按原文字符顺序拼接跨页句，剔除页码，限定百分比绑定同一金额业务项；歧义候选先转待人工复核。'}]
additional=[{'id':'D-POSTFREEZE-001','organization':'上海市监狱管理局','classification':'additional_system_finding_confirmed','native_issue':nref('上海市监狱管理局',8),'reason':'冻结后检查系统额外提示。原件p24真的写“其他卫生健康支出（类）”，p14表的类级210名为“卫生健康支出”；不能把该提示认定误报。此项不计入独立冻结发现，也不修改原truth。','evidence':[ev('上海市监狱管理局',24,'16、其他卫生健康支出（类）其他卫生健康支出（款）其他卫生健康支出（项）'),ev('上海市监狱管理局',14,'210\n卫生健康支出')]}]
quality=[{'id':'D-QUALITY-001','organization':'上海市交通委员会','type':'incorrect_primary_location_and_duplicate_issue','native_issues':[nref('上海市交通委员会',0),nref('上海市交通委员会',9)],'detail':'相同三公总预算表文冲突，CMM-001给p1，V33-108给p39；实际说明p39、表p26。p1没有三公金额。用户问题列表应聚合并保留正确的双证据定位。','evidence':truth['issues'][6]['evidence']},{'id':'D-QUALITY-002','organization':'上海市财政局','type':'mixed_page_coverage_blind_spot','detail':'32页都被统计为text_pages，但p28大部分三公和基金说明在图像区域。原件可见的17辆保有量、接待数据和基金无此项段未进入纯文本。当前未据此确定核心材料错误；这是逐页区域OCR/提取覆盖率功能缺口。','evidence':[{'page':28,'bbox':None,'snippet':'公务用车保有量为17辆；政府性基金：上海市财政局2025年度无政府性基金预算财政拨款收入和支出。','source':'visible original PDF raster rendering','bbox_reason':'仅做视觉核查，无OCR定位；不编造坐标'}],'native_summary':{'text_pages':native['documents'][-1]['text_pages'],'pages':native['documents'][-1]['pages'],'source_errors':native['documents'][-1]['source_errors']}}]
features=[{'priority':'P1','feature':'跨页句子拼接及金额/业务项/百分比绑定','evidence_ids':['D-FP-001'],'needed_behavior':'接待费跨页后继续原业务项，不回绑到公务用车；有歧义时不输出确定性error。'},{'priority':'P1','feature':'绩效自评项目数量勾稽','evidence_ids':['D-TRUTH-001'],'needed_behavior':'自评总项目数与“其中”四级评级合计比较；不同范围时要求范围说明。'},{'priority':'P1','feature':'其他重要事项同比一致性覆盖','evidence_ids':['D-TRUTH-009'],'needed_behavior':'机关运行经费也应进入本期、增减额、同比三字段一致性检查。'},{'priority':'P1','feature':'混合文字图像PDF逐页区域识别及覆盖提示','evidence_ids':['D-QUALITY-002'],'needed_behavior':'不能以整份有文字层或每页有几个字判定文本提取完整；对大量图像内容做区域OCR，保留图像原件证据。'},{'priority':'P2','feature':'金额与文字用途/更新状态一致性检查','evidence_ids':['D-TRUTH-004'],'needed_behavior':'零支出并说用于购置，同时说无更新时给出矛盾提醒；保留跨期支付或其他资金口径复核路径。'},{'priority':'P2','feature':'变化原因实质性检查','evidence_ids':['D-TRUTH-005','D-TRUTH-006'],'needed_behavior':'“主要原因”后只重述大小关系或增减方向不算解释；语义弱证据优先人工复核，有明确同义重述和数字证据时可medium。'},{'priority':'P2','feature':'车辆数量范围、时点与集合比较','evidence_ids':[],'needed_behavior':'把年度财政拨款车保有量与年末资产车辆对照时先核对统计单位/时点；不直接判大小即错误，要求车辆台账解释。','scope_limit':'本轮6例仅为待复核候选，不计漏报。'},{'priority':'P2','feature':'同一问题去重和真实证据页码','evidence_ids':['D-QUALITY-001'],'needed_behavior':'聚合同一预算冲突，展示原件p26+p39；禁止p1占位定位。'}]
obj={'review_lane':'D','date':'2026-10-08','scope':'8份原件，独立冻结后与当前native/document_*/issues.json比对；并非真实UI全链路或全产品准确率','truth_path':str(b/'disclosure_truth.json'),'truth_sha256':hashlib.sha256((b/'disclosure_truth.json').read_bytes()).hexdigest(),'counts_note':'按D线自身归并后的问题计数；与C线/结构线存在重合，父报告须去重，不能直接相加。','summary':{'independent_frozen_findings':11,'detected_grouped_findings':6,'missed_grouped_findings':5,'confirmed_false_positive_findings':1,'manual_scope_candidates':6},'truth_comparison':results,'false_positives':fp,'additional_confirmed_system_findings':additional,'delivery_quality_gaps':quality,'functional_gaps':features,'manual_review_comparison':[{'organization':x['organization'],'title':x['title'],'classification':'candidate_only_not_counted_as_missed','evidence':x['evidence'],'reason':x['reason']} for x in truth['manual_review']]}
(b/'disclosure_comparison.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2)); print(json.dumps(obj['summary'],ensure_ascii=False));print('comparison written; truth unchanged',obj['truth_sha256'])
