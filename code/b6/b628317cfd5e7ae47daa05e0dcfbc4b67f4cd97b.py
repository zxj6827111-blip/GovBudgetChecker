from pathlib import Path
import fitz,json,re,unicodedata,hashlib
from decimal import Decimal as D
base=Path('outputs/real_intake_review_20261008')
src=Path('/Users/zhangxingjin/Desktop/决算测试上海市')
names=['上海市交通委员会','上海市文化和旅游局','上海市商务委员会','上海市民政局','上海市发展和改革委员会','上海市财政局','上海市司法局','上海市监狱管理局']
normalize=lambda s:re.sub(r'\s+','',unicodedata.normalize('NFKC',s))
docs={n:fitz.open(src/(n+'.pdf')) for n in names}
def ev(n,p,s):
 page=docs[n][p-1]
 chars=[]
 for b in page.get_text('rawdict')['blocks']:
  for line in b.get('lines',[]):
   for sp in line['spans']:
    for c in sp['chars']:
     for ch in normalize(c['c']): chars.append((ch,c['bbox']))
 hay=''.join(c[0] for c in chars);needle=normalize(s);start=hay.find(needle)
 if start<0:raise ValueError((n,p,s))
 rect=fitz.Rect(chars[start][1])
 for ch,r in chars[start:start+len(needle)]:rect|=fitz.Rect(r)
 return {'page':p,'bbox':[round(x,2) for x in rect],'bbox_space':'PDF points, top-left origin; physical page 1-based','snippet':s,'source':'original PDF text layer; cited issue pages visually verified'}
issues=[]
def add(i,n,rule,title,severity,section,evidences,expected,actual,difference,reason,suggestion,confidence=.98):
 issues.append({'id':f'D-TRUTH-{i:03}','rule_id':rule,'title':title,'severity':severity,'document_type':'决算公开','year':2025,'organization':n,'page':evidences[0]['page'],'bbox':evidences[0]['bbox'],'table_or_section':section,'snippet':evidences[0]['snippet'],'evidence':evidences,'expected':expected,'actual':actual,'difference':difference,'reason':reason,'suggestion':suggestion,'confidence':confidence})
add(1,'上海市文化和旅游局','PERFORMANCE-COUNT','绩效自评项目数不等于评级项目数之和','high','十、预算绩效管理情况',[ev('上海市文化和旅游局',38,'绩效自评的2025年度项目108个，涉及预算金额133,759.01万元，平均得分96.49分（其中，绩效评级为“优”的项目105个；绩效评级为“良”的项目21个；绩效评级为“合格”的项目0个；绩效评级为“不合格”的项目1个。')],'105+21+0+1=127个，应与同段绩效自评108个一致','自评108个；评级合计127个','19个','同段用“其中”列明自评项目评级，没有范围调整说明，分项和与总数不一致','复核自评总数及各评级项目数，补充范围差异说明（若确有不同口径）')
add(2,'上海市监狱管理局','D-007','三公决算占比误用了预算完成率','high','七、财政拨款“三公”经费支出决算情况说明（二）',[ev('上海市监狱管理局',26,'“三公”经费财政拨款支出决算中，因公出国（境）费支出决算43.13万元，占2.87%；公务用车购置及运行维护费支出决算1,438.03万元，占87.51%；公务接待费支出决算20.45万元，占8.33%。'),ev('上海市监狱管理局',25,'支出决算为1,501.60万元')],'用三公合计决算1501.60万元作分母，用车95.77%；接待1.36%','用车87.51%；接待8.33%','用车-8.26个百分点；接待+6.97个百分点','1438.03/1501.60×100=95.7665%；20.45/1501.60×100=1.3619%。现有87.51%/8.33%与上一段预算完成率相同','分别修正为95.77%、1.36%，保留上一段预算完成率')
add(3,'上海市发展和改革委员会','D-007','公务用车完成预算比例计算不一致','high','七、财政拨款“三公”经费支出决算情况说明（一）',[ev('上海市发展和改革委员会',34,'公务用车购置及运行维护费支出决算为70.23万元，完成预算的60.08%'),ev('上海市发展和改革委员会',23,'116.20\n70.23')],'70.23/116.20×100=60.44%','60.08%','-0.36个百分点','对应三公表公务用车预算116.20万元，决算70.23万元；差异超过0.1个百分点容差','复核分母，将公务用车完成预算比例修正为60.44%')
add(4,'上海市商务委员会','D-007','公务用车零购置支出说明与购车用途及无更新原因冲突','medium','七、财政拨款“三公”经费支出决算情况说明',[ev('上海市商务委员会',30,'公务用车购置支出为0万元。主要用于一辆公务用车购置。'),ev('上海市商务委员会',29,'公务用车购置及运行维护费支出减少的主要原因是2025年无公务车辆更新。')],'零购置支出且无车辆更新时，应说明未购置车辆，或补充购置车辆及付款口径','零支出同时描述用于一辆车购置，并在变化原因中说无车辆更新','不适用','金额、用途和变动说明在同一三公章节中不自洽；尚不能据此确认支出金额错误','复核是否有实际购置或跨期付款，按实际情况删除遗留购车文字或说明资金、付款口径',.96)
add(5,'上海市商务委员会','CHANGE-REASON','两项预算决算差异原因仅重复差异结果','medium','五、一般公共预算财政拨款支出决算具体情况第10、11项',[ev('上海市商务委员会',26,'年初预算为17.68万元，支出决算为9.38万元。决算数小于预算数的主要原因：该项实际支出小于预算数。'),ev('上海市商务委员会',26,'年初预算为682.18万元，支出决算为382.60万元。决算数小于预算数的主要原因：该项实际支出小于预算数。')],'说明产生支出减少的具体原因','原因写作“该项实际支出小于预算数”','分别少8.30万元、299.58万元','原因字段只再次陈述已经给出的预算与决算大小关系，未提供变化原因；数字证据明确','补充实际业务量、人员、项目或政策变化等经核实原因，避免仅重述数字结果',.97)
add(6,'上海市商务委员会','CHANGE-REASON','机关运行经费同比原因仅重述支出略增','medium','十一、其他重要事项（一）机关运行经费支出情况',[ev('上海市商务委员会',32,'机关运行经费支出2,196.47万元，比2024年度增加15.2万元，增长0.7%。主要原因是机关运行支出同比略有增多。')],'给出同比增加的具体因素','主要原因是机关运行支出同比略有增多','增加15.2万元','同比金额与变化结果明确，但原因只用相同的变化结果做解释','补充新增运行事项、成本或服务需求等经核实的增支原因',.97)
add(7,'上海市交通委员会','D-007','三公说明预算及分项完成预算比例与表格不一致','high','七、财政拨款“三公”经费支出决算情况说明（一）',[ev('上海市交通委员会',39,'“三公”经费财政拨款支出年初预算为1,435.72万元，支出决算为1,158.99万元，完成预算的80.72%，其中：因公出国（境）费决算为370.23万元，完成预算的80.92%；公务用车购置及运行维护费支出决算为754.61万元，完成预算的88.50%；公务接待费支出决算为34.15万元，完成预算的27.20%。'),ev('上海市交通委员会',26,'1,384.72\n1,158.99\n445.50\n370.23'),ev('上海市交通委员会',26,'86.53\n34.15')],'若三公表预算口径相同，总预算1384.72；完成83.70%；出国83.10%；接待39.47%','文字总预算1435.72；完成80.72%；出国80.92%；接待27.20%','总预算+51万元；完成率分别-2.98、-2.18、-12.27个百分点','三公表和对应说明的年初预算及分项完成率不相符；文字总完成率按文字预算可成立，但缺少表文不同预算口径说明','先核实正确预算数及年初/全年口径，再同步三公表、说明总数及完成比例')
add(8,'上海市交通委员会','D-007','三公同比比例与同段金额及增减额不符','high','七、财政拨款“三公”经费支出决算情况说明（一）',[ev('上海市交通委员会',39,'因公出国（境）费支出决算增加12.51万元，增长3.38%'),ev('上海市交通委员会',39,'公务接待费支出决算减少4.89万元，下降14.32%'),ev('上海市交通委员会',40,'因公出国（境）费支出决算370.23万元'),ev('上海市交通委员会',40,'公务接待费支出决算34.15万元')],'按同段增减额反推上年：出国12.51/(370.23-12.51)×100=3.50%；接待4.89/(34.15+4.89)×100=12.53%','出国增长3.38%；接待下降14.32%','出国-0.12个百分点；接待+1.79个百分点','无需上年原表即可检验同段金额、差额、同比三者内部关系；可能分母误用本年金额。结论只指当前材料内部不自洽','复核上年决算分母、同比差额和口径，按同一口径重新计算',.98)
add(9,'上海市交通委员会','YOY-CONSISTENCY','机关运行经费下降比例与金额及差额不符','high','十一、其他重要事项（一）机关运行经费支出情况',[ev('上海市交通委员会',43,'机关运行经费支出6,679.47万元，比2024年度减少335.66万元，下降5.03%。')],'335.66/(6679.47+335.66)×100=4.78%','5.03%','+0.25个百分点','5.03%接近用本年金额作分母；同比下降应以上年同口径金额为分母。结论仅检验当前材料三字段内部一致性','复核上年金额及口径，将内部一致的下降比例修正为4.78%',.98)
add(10,'上海市交通委员会','R003','三公变化原因出现相邻重复“是是”','low','七、财政拨款“三公”经费支出决算情况说明（一）',[ev('上海市交通委员会',39,'因公出国（境）费支出增加的主要原因是是根据年度外事工作安排'),ev('上海市交通委员会',39,'公务接待费支出减少的主要原因是是落实厉行节约要求')],'主要原因是','主要原因是是','相邻重复1字（2处）','可见页面及文字层均有相邻重复字','删除每处多余的“是”')
add(11,'上海市发展和改革委员会','R003','出国用途出现相邻重复“的的”','low','七、财政拨款“三公”经费支出决算具体情况',[ev('上海市发展和改革委员会',35,'参加市委组织部境外培训研修等产生的的国际旅费')],'产生的国际旅费','产生的的国际旅费','相邻重复1字','可见页面及文字层均出现重复字','删除多余的“的”')
manual=[]
for n,p1,p2,qty1,qty2 in [('上海市文化和旅游局',36,39,50,41),('上海市商务委员会',30,32,22,11),('上海市发展和改革委员会',36,38,30,12),('上海市司法局',28,30,125,111),('上海市监狱管理局',26,28,364,356)]:
 e1=ev(n,p1,f'公务用车保有量为{qty1}辆')
 e2=ev(n,p2,f'共有车辆{qty2}辆')
 manual.append({'rule_id':'ASSET-SCOPE','title':'三公公务车保有量大于年末资产车辆总数，需核对时点与范围','severity':'manual_review','document_type':'决算公开','year':2025,'organization':n,'page':p1,'bbox':e1['bbox'],'evidence':[e1,e2],'expected':'在同一统计时点和范围下，财政拨款公务车辆应为全部车辆的子集','actual':f'三公保有量{qty1}辆；年末全部车辆{qty2}辆','difference':f'{qty1-qty2}辆','reason':'一个数字描述本年财政拨款保有量，另一个为12月31日全部资产；缺少时点、单位范围和车辆增减信息，不直接判数值错误','suggestion':'补充车辆资产台账及年中/年末口径，确认车辆数及资产范围','confidence':.72})
manual.append({'rule_id':'ASSET-SCOPE','title':'财政局混合页显示公务车保有量17辆，年末全部车辆3辆，需复核口径','severity':'manual_review','document_type':'决算公开','year':2025,'organization':'上海市财政局','page':28,'bbox':None,'bbox_reason':'该段为PDF图像内容，当前仅视觉核对未做OCR bbox；不编造坐标','evidence':[{'page':28,'bbox':None,'snippet':'2025年，上海市财政局所属各预算单位开支财政拨款的公务用车保有量为17辆。','source':'original PDF raster rendering; visual transcription'},ev('上海市财政局',30,'共有车辆3辆')],'expected':'同一时点/范围内财政拨款车辆应是全部车辆的子集','actual':'三公保有量17辆；12月31日全部车辆3辆','difference':'14辆','reason':'混合文字和图像页，文本层未包含车辆段；时点及单位统计范围尚不确定，不能确定数值错误','suggestion':'对混合页补做区域OCR并核对车辆统计口径','confidence':.72})
observations=[]
for n in names:
 ps=list(docs[n]); headings=[]
 for i,p in enumerate(ps):
  t=p.get_text()
  if i>15 and '预算绩效管理情况' in t:headings.append(i+1)
 observations.append({'organization':n,'performance_section_pages':headings,'performance_result':'均有预算绩效管理说明；未据此要求另有目标表或评级附件','procurement_result':'采购总金额按货物+工程+服务核验，未发现超过0.5万元的合计差额；中小企业、小微企业预留预算与合同金额不可直接相加或作同比','conditional_table_result':'均有国有资本经营无收入支出等效说明；政府基金有支出者有说明，无支出者有等效说明。财政局p28基金说明在图像区域，不能判缺失'})
obj={'review_lane':'D: disclosure, scope and semantic review','rule_set':'v3_3_portable','frozen_before_system_issues_comparison':True,'date':'2026-10-08','method':'先只读8份原始PDF，文字提取和问题页渲染复核后冻结；未读旧轮结果或本轮issues。纯文本层无法确认的内容通过渲染判定或降级为待复核。仅当前样本，不作全产品准确率推断。','bbox_convention':'PDF points, top-left origin; page refers to 1-based physical PDF page','sample_sources':[{'organization':n,'path':str(src/(n+'.pdf')),'pages':len(docs[n]),'sha256':hashlib.sha256((src/(n+'.pdf')).read_bytes()).hexdigest()} for n in names],'issues':issues,'manual_review':manual,'observations':observations,'risk_flags':[{'organization':'上海市财政局','page':28,'type':'mixed_text_image_page','detail':'整页可见三公具体说明和政府性基金无此项说明，但fitz纯文本仅提取九、国有资本经营标题与两行说明；必须做逐页图像区域覆盖检测，不能只凭整份PDF有文字层决定不OCR。'}]}
(base/'disclosure_truth.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2))
print('frozen',len(issues),'issues',len(manual),'manual_review',hashlib.sha256((base/'disclosure_truth.json').read_bytes()).hexdigest())
