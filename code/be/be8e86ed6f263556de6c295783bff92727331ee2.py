import pymupdf,json,hashlib,datetime
from pathlib import Path
from decimal import Decimal
base=Path('/Users/zhangxingjin/Desktop/决算测试上海市');out=Path('outputs/real_intake_review_20261008')
names=['上海市交通委员会','上海市文化和旅游局','上海市民政局','上海市财政局']
docs={n:pymupdf.open(base/(n+'.pdf')) for n in names}
truth={'method':{'rule_set':'v3_3_portable','review_line':'C 独立原件数字审校','amount_tolerance_wan':0.5,'percentage_tolerance_points':0.1,'bbox_system':'PDF points, top-left origin, [x0,y0,x1,y1]; physical PDF page 1-based','basis':'原件文字层，独立pymupdf表格几何提取，pdfplumber交叉提取，重点页渲染核验；未读取本轮或旧轮issues/audit形成本文件。父任务曾传递针对性疑点，故本轮是开发复测，不能称盲测。','frozen_at':datetime.datetime.now().astimezone().isoformat()},'documents':[],'issues':[],'manual_review':[],'pass_checks':[],'limitations':['未逐页逐单元格人工穷尽；本线覆盖的确认真值不是材料全部错误集合。','表内显示数差额小于0.5万元，百分比差额小于0.1百分点未列为确认错误。','财政局p28的pymupdf提取缺文，经pdfplumber与原页图像核对撤除缺章疑点。']}
for n,d in docs.items():truth['documents'].append({'organization':n,'document_type':'决算公开','year':2025,'path':str(base/(n+'.pdf')),'sha256':hashlib.sha256((base/(n+'.pdf')).read_bytes()).hexdigest(),'pages':len(d)})
def ev(n,p,needle):
 page=docs[n][p-1];rs=page.search_for(needle)
 return {'page':p,'bbox':[round(v,2) for v in (min(r.x0 for r in rs),min(r.y0 for r in rs),max(r.x1 for r in rs),max(r.y1 for r in rs))] if rs else None,'needle':needle,'bbox_note':None if rs else '原件搜索未能形成连续文本框，当前无法定位'}
def add(n,id,rule,title,page,section,snippet,expected,actual,difference,reason,needles,manual=False):
 evidence=[ev(n,p,s) for p,s in needles]
 x={'truth_id':id,'rule_id':rule,'title':title,'severity':'manual_review' if manual else 'high','document_type':'决算公开','year':2025,'organization':n,'page':page,'bbox':next((e['bbox'] for e in evidence if e['page']==page and e['bbox']),None),'table_or_section':section,'snippet':snippet,'expected':expected,'actual':actual,'difference':difference,'reason':reason,'suggestion':'核对原始会计/预算口径并同步修正表和说明；原件证据无法决定哪个数应改。' if manual else '复核对应数值与计算口径并同步修正表格及说明。','confidence':0.75 if manual else 0.99,'evidence':evidence,'status':'manual_review' if manual else 'confirmed'}
 truth['manual_review' if manual else 'issues'].append(x)
def trend(n,id,p,label,current,change,direction,actual):
 previous=current-change if direction=='增长' else current+change
 expected=change/previous*100
 add(n,id,'D-007' if '三公' in label else 'D-005' if '一般公共预算' in label else 'C-003',label+direction+'率计算不一致',p,label,f'{current:,.2f} 万元，较上年{direction}对应增减 {change:,.2f} 万元，{direction}{actual:.2f}%',f'{change:,.2f} ÷ ({current:,.2f} {"-" if direction=="增长" else "+"} {change:,.2f}) × 100 = {expected:.4f}%（约{expected:.2f}%）',f'{actual:.2f}%',f'{actual-expected:+.4f} 个百分点','同比增减率应以上年金额作分母；根据本年金额与变动额可重建上年金额。',[(p,f'{actual:.2f}%'),(p,f'{current:,.2f}')])
t='上海市交通委员会';w='上海市文化和旅游局';m='上海市民政局';f='上海市财政局'
trend(t,'NUM-T01',30,'收入支出总体情况',2468832.94,117674.16,'增长',4.77)
trend(t,'NUM-T02',30,'财政拨款收入支出总体情况',2442677.65,121468.68,'增长',4.97)
add(t,'NUM-T03','D-005','一般公共预算支出占本年支出的分母与表述不一致',31,'一般公共预算财政拨款支出决算总体情况','一般公共预算财政拨款支出2,196,397.98万元，占本年支出合计的90.34%。','2,196,397.98 ÷ 2,443,579.41 ×100 = 89.8844%，约89.88%','90.34%','+0.4556 个百分点','90.34%可由财政拨款本年支出2,431,353.94作分母得到，但原句明确说本年支出合计，支出决算表全口径2,443,579.41。',[(31,'90.34%'),(14,'2,443,579.41'),(20,'2,431,353.94')])
trend(t,'NUM-T04',39,'三公因公出国（境）费',370.23,12.51,'增长',3.38)
trend(t,'NUM-T05',39,'三公公务接待费',34.15,4.89,'下降',14.32)
add(t,'NUM-T06','D-008','政府性基金收入说明与表格本年收入不一致',40,'政府性基金预算财政拨款收入支出决算情况说明','政府性基金预算财政拨款收入78,656.53万元，支出234,955.97万元','本年收入234,955.97万元（p28合计；p8基金收入；p19基金财政拨款收入）','78,656.53万元','-156,299.44万元','说明将数值78,656.53列为基金收入，但该数字在p41政府还贷公路养护项目属于年初预算；原表本年收入为234,955.97。',[(40,'78,656.53'),(28,'234,955.97'),(8,'234,955.97'),(41,'78,656.53')])
trend(t,'NUM-T07',43,'机关运行经费支出情况',6679.47,335.66,'下降',5.03)
add(t,'NUM-TM1','D-007','三公表预算与说明年初预算差异须确认口径',39,'财政拨款三公经费支出决算表及说明','表p26预算合计1,384.72；说明p39年初预算1,435.72；出国完成80.92%，接待完成27.20%。','确认表格预算数究竟是年初预算还是调整后预算，分项完成率分母应可追溯。','表1,384.72，说明1,435.72','51.00万元；出国/接待完成率亦与表预算不合','说明使用年初预算，表仅写预算数；表预算本身分项加总与合计一致，说明合计完成率计算亦一致，存在预算口径差异可能，不直接确认错误。',[(26,'1,384.72'),(39,'1,435.72'),(39,'80.92%'),(39,'27.20%')],True)
add(w,'NUM-W01','D-002','收入说明将其他收入写为经营收入',26,'收入决算情况说明','经营收入16,371.53万元，占8.41%','其他收入16,371.53万元；经营收入0.00万元','经营收入16,371.53万元','经营收入多列16,371.53万元，其他收入未列','p8总表和p9收入表均将16,371.53归其他收入，经营收入0.00，属于核心字段错配。',[(26,'经营收入16,371.53'),(9,'16,371.53'),(8,'经营收入')])
trend(w,'NUM-W02',27,'一般公共预算财政拨款支出总体情况',139407.47,16819.41,'增长',13.14)
add(w,'NUM-W03','D-006','商品和服务支出汇总不等于已列经济分类子项',22,'一般公共预算财政拨款基本支出决算表','302商品和服务支出10,265.86；30201至30299子项之和10,533.26','302 = 30201…30299之和10,533.26万元','10,265.86万元','-267.40万元','依据实际有框线的左右经济表列与原页渲染核验，已列30项的加总与302不符。',[(22,'10,265.86'),(22,'商品和服务支出'),(22,'267.39')])
add(w,'NUM-W04','D-006','资本性支出汇总为零但已列子项合计不为零',22,'一般公共预算财政拨款基本支出决算表','310资本性支出0.00；31001=223.70、31002=15.57、31021=26.13、31022=10,265.86、31099=502.11','310子项之和=223.70+15.57+26.13+10,265.86+502.11=11,033.37万元','310=0.00万元','-11,033.37万元','原页可见310父项为0，子项非零且汇总不成立；应核对数据填充或明细映射。',[(22,'资本性支出'),(22,'无形资产购置'),(22,'10,265.86'),(22,'502.11')])
add(w,'NUM-W05','D-006','公用经费合计不等于商品服务与资本性支出汇总',22,'一般公共预算财政拨款基本支出决算表','公用经费10,533.25；302=10,265.86；310=0.00','10,265.86+0.00=10,265.86万元','10,533.25万元','+267.39万元','同页汇总数不平；与NUM-W03/04同一坏表关联，应在最终问题清单合并说明避免重复计数。',[(22,'10,533.25'),(22,'10,265.86'),(22,'资本性支出')])
add(w,'NUM-W06','D-007','基本经费因公出国与三公全额可能倒挂',22,'一般公共预算财政拨款基本支出决算表/三公决算表','30212因公出国费用997.08；三公因公出国决算363.72','确认分类费用与三公统计口径；若同口径，基本部分不得超过全额。','997.08 > 363.72万元','633.36万元','原页数字均可确认，但需核实经济分类是否包含不纳入三公统计的其他出境费用及已知坏表填值影响，优先人工复核。',[(22,'997.08'),(23,'363.72')],True)
add(w,'NUM-W07','D-007','基本经费公务用车运行维护与三公全额可能倒挂',22,'一般公共预算财政拨款基本支出决算表/三公决算表','30231运行维护217.13；三公运行维护100.77','确认经济分类和三公统计口径；若同口径，基本部分不得超过全额。','217.13 > 100.77万元','116.36万元','原页数字均可确认；同一坏表多处填值异常，需查原始台账确认适用范围和正确数字。',[(22,'217.13'),(23,'100.77')],True)
add(w,'NUM-W08','D-PERFORMANCE','绩效评级分项项目数之和超过自评项目数',38,'预算绩效管理情况','自评108个；优105、良21、合格0、不合格1','105+21+0+1=127，与自评108个应一致','108个','-19个','同句自评项目与分级评级的分类统计不能成立；项目整数不适用金额容差。',[(38,'108个'),(38,'105个'),(38,'21个'),(38,'不合格')])
tables=json.load(open(out/'numeric_source/independent_tables.json'))
for id,key,amount in [('NUM-M01','收入决算表',7274.70),('NUM-M02','支出决算表',7051.21),('NUM-M03','一般公共预算财政拨款支出决算表',5131.83)]:
 r=next(x for x in tables[m]['differences'] if x['table']==key and x.get('code')=='208' and x.get('col')==0)
 children=r['children'];formula='+'.join(f"{c['values'][0]:,.2f}" for c in children)
 add(m,id,'D-002' if id=='NUM-M01' else 'D-003' if id=='NUM-M02' else 'D-005','社会保障和就业支出类合计与已列款级明细不平',r['page'],key,f"208 {r['values'][0]:,.2f}万元；款级明细为"+'、'.join(c['code'] for c in children),formula+f"={r['expected']:,.2f}万元",f"{r['values'][0]:,.2f}万元",f"+{r['difference']:,.2f}万元",'已逐页查看对应表的续页和表末；收入表p13仅注释，无20899等款级行；支出p14-16、一般表p19均未列可补足差额的其他款级行。只能确认本材料所列明细与合计不平，不能推定正确科目或改哪项。',[(r['page'],f"{r['values'][0]:,.2f}")]+[(c['page'],f"{c['values'][0]:,.2f}") for c in children])
for n in names:
 truth['pass_checks'].append({'organization':n,'checks':['收入支出总表收入/支出侧平衡（文旅民政财政在两位小数显示容差内）','收入表总计与总表本年收入一致','支出表总计、基本及项目总计与说明一致','财政拨款总表收支总计与说明一致','三公决算合计=出国+车辆+接待（民政差0.02、财政差0.01在容差内）','三公预算合计与各分项和、车辆小计与购置及运行和一致','政府采购总额=货物+工程+服务（交通差0.01在容差内）','独立几何表提取：收入/支出/一般表逐行分项加总、功能分类层级加总；交通财政无超过0.5万元差额，文旅/民政异常单列'],'caveat':'已列问题及待复核事项不包含在通过结论内，未检查说明之外的未公开原始账簿。'})
(out/'numeric_truth.json').write_text(json.dumps(truth,ensure_ascii=False,indent=2))
print('FROZEN',len(truth['issues']),'confirmed,',len(truth['manual_review']),'manual')
for x in truth['issues']:print(x['truth_id'],x['organization'],x['page'],x['difference'],x['title'])
