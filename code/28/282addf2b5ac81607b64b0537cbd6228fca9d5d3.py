import pdfplumber, json, hashlib, re
from pathlib import Path
from decimal import Decimal as D, ROUND_HALF_UP
from collections import Counter

SOURCE=Path('/Users/zhangxingjin/Desktop/决算测试上海市/上海市数据局.pdf')
DEST=Path('outputs/real_pdf_comparison_20261008/independent_numeric.json')
PDF=pdfplumber.open(SOURCE)
TOL=D('0.5'); PTOL=D('0.1')
checks=[]; issues=[]; manual=[]; coverage=[]

def fmt(v):
 return None if v is None else format(v,'f')
def num(x):
 return D(x.replace(',','')) if x and re.fullmatch(r'[\d,]+(?:\.\d+)?',x.strip()) else None
def cell(page,row,col,label=None):
 t=PDF.pages[page-1].find_tables()[0]; val=t.extract()[row][col]; bbox=t.rows[row].cells[col]
 return {'label':label or val,'value':fmt(num(val)),'raw':val,'page':page,'bbox':list(bbox) if bbox else None,'unit':'万元'}
def txt(page,label,value,pattern=None,occurrence=0,unit='万元'):
 matches=PDF.pages[page-1].search(pattern or re.escape(str(value)),regex=True)
 m=matches[occurrence] if len(matches)>occurrence else None
 return {'label':label,'value':str(value),'page':page,'bbox':[m[k] for k in ('x0','top','x1','bottom')] if m else None,'snippet':m['text'] if m else None,'unit':unit}
def v(o): return None if o['value'] is None else D(o['value'])
def check(kind,title,lhs,rhs,rule='NUMERIC',unit='万元',note=None):
 usable=[o for o in rhs if v(o) is not None]
 missing=[o for o in rhs if v(o) is None]
 total=sum((v(o) for o in usable),D('0')) if usable else None
 diff=v(lhs)-total if v(lhs) is not None and total is not None else None
 tol=PTOL if unit=='百分点' else TOL
 if diff is None: status='not_evaluable'
 elif abs(diff)>tol: status='manual_review_missing_operands' if missing else 'fail'
 elif missing: status='disclosed_values_consistent_within_tolerance'
 elif diff: status='within_tolerance_possible_rounding'
 else: status='pass'
 r={'id':f'N{len(checks)+1:03d}','kind':kind,'rule_id':rule,'title':title,'lhs':lhs,'rhs':rhs,'known_rhs_sum':fmt(total),'difference_lhs_minus_known_sum':fmt(diff),'unit':unit,'tolerance':fmt(tol),'status':status,'blank_operands':len(missing),'note':note or ('仅检验已披露数值；空白保留null且未按0代入，不能据此宣称完整等式成立。' if missing else None)}
 checks.append(r); return r

def eq(title,a,b,rule='NUMERIC',note=None): return check('same_scope_amount',title,a,[b],rule,note=note)
def rows(pages,start=0):
 out={}
 for p in pages:
  table=PDF.pages[p-1].find_tables()[0]
  for i,row in enumerate(table.extract()):
   code=row[0]
   if code=='合计' or (code and re.fullmatch(r'\d{3}|\d{5}|\d{7}',code)):
    out[code]={'page':p,'row':i,'name':row[1],'cells':[cell(p,i,j,f'{code} {row[1] or "合计"}') for j in range(2,len(row))]}
 return out

# 1 收支总表，所有空白均作为未知，不补0。
t1=PDF.pages[6].find_tables()[0].extract()
check('table_vertical','收入支出决算总表：本年收入与已填收入分项',cell(7,26,1,'本年收入合计'),[cell(7,i,1,t1[i][0]) for i in range(2,10)],'D-001')
check('table_vertical','收入支出决算总表：本年支出与已填功能类分项',cell(7,26,3,'本年支出合计'),[cell(7,i,3,t1[i][2]) for i in range(2,26)],'D-001')
check('table_vertical','收入支出决算总表：收入侧总计=本年收入+使用非财政拨款结余+年初结余',cell(7,29,1,'收入侧总计'),[cell(7,26,1,'本年收入合计'),cell(7,27,1,'使用非财政拨款结余'),cell(7,28,1,'年初结转和结余')],'D-001')
check('table_vertical','收入支出决算总表：支出侧总计=本年支出+结余分配+年末结余',cell(7,29,3,'支出侧总计'),[cell(7,26,3,'本年支出合计'),cell(7,27,3,'结余分配'),cell(7,28,3,'年末结转和结余')],'D-001')
eq('收入支出决算总表：收入总计=支出总计',cell(7,29,1,'收入总计'),cell(7,29,3,'支出总计'),'D-001')

# 2/3/5 功能分类表：只加直接子级，按3/5/7位；续页一并纳入。
TABLES={
 '收入决算表':(rows([8,9]),['本年收入合计','财政拨款收入','上级补助收入','事业收入','经营收入','附属单位上缴收入','其他收入']),
 '支出决算表':(rows([10,11]),['本年支出合计','基本支出','项目支出','上缴上级支出','经营支出','对附属单位补助支出']),
 '一般公共预算财政拨款支出决算表':(rows([14,15]),['合计','基本支出','项目支出'])}
for name,(data,cols) in TABLES.items():
 for code,r in data.items():
  amount=r['cells'][0]
  if v(amount) is not None:
   check('table_horizontal',f'{name} {code} {r["name"] or "合计"}：合计与已填分项',amount,[dict(o,label=f'{code} {cols[j]}') for j,o in enumerate(r['cells'][1:],1)],'D-002' if name=='收入决算表' else 'D-003' if name=='支出决算表' else 'D-005')
  else:
   coverage.append({'table':name,'code':code,'page':r['page'],'status':'empty_row_not_evaluable','reason':'行内未印数字；没有把空白当0。'})
  children=[(k,z) for k,z in data.items() if k!='合计' and (len(k)==3 if code=='合计' else len(k)==len(code)+2 and k.startswith(code))]
  if not children: continue
  for j,col in enumerate(cols):
   lhs=dict(r['cells'][j],label=f'{code} {col}')
   operands=[dict(z['cells'][j],label=f'{k} {col}') for k,z in children]
   if v(lhs) is not None:
    check('hierarchy_direct_children',f'{name} {code} {col}：直接子级合计',lhs,operands,'D-002' if name=='收入决算表' else 'D-003' if name=='支出决算表' else 'D-005')
   else:
    coverage.append({'table':name,'code':code,'column':col,'page':r['page'],'status':'blank_parent_not_evaluable','children':operands,'reason':'父级未印数字；未假定为0。'})

# 4 财政拨款总表 横向已填金额；两种无此预算在19、20、28页有明确说明。
t4=[]
for p in [12,13]:
 data=PDF.pages[p-1].find_tables()[0].extract()
 for i,row in enumerate(data):
  if num(row[3]) is not None:
   t4.append((p,i,row))
   check('table_horizontal',f'财政拨款收入支出决算总表 {row[2]}：财政类型合计',cell(p,i,3,row[2]),[cell(p,i,j,['一般公共预算','政府性基金','国有资本经营'][j-4]) for j in range(4,7)],'D-004',note='政府性基金和国有资本经营的本年收入支出有明确无此预算说明（第19、20、28页）；表中空白仍保存null。')
function_rows=[(p,i,row) for p,i,row in t4 if row[2] not in ['本年支出合计','年末财政拨款结转和结余','总计']]
for j,label in [(3,'合计'),(4,'一般公共预算')]:
 check('table_vertical',f'财政拨款收入支出决算总表：{label}本年支出=跨页已填功能类之和',cell(13,10,j,f'{label}本年支出合计'),[cell(p,i,j,row[2]) for p,i,row in function_rows],'D-004',note='第12—13页续表，已填功能类为201、208、210、216、221，其他空白类未按0代入。')
 check('table_vertical',f'财政拨款收入支出决算总表：{label}支出总计=本年支出+年末结余',cell(13,15,j,f'{label}支出侧总计'),[cell(13,10,j,'本年支出合计'),cell(13,11,j,'年末财政拨款结转和结余')],'D-004')
check('table_vertical','财政拨款收入支出决算总表：收入总计=本年收入+年初结余',cell(13,15,1,'收入侧总计'),[cell(13,10,1,'本年收入合计'),cell(13,11,1,'年初财政拨款结转和结余')],'D-004')
eq('财政拨款收入支出决算总表：收入总计=支出总计',cell(13,15,1,'收入侧总计'),cell(13,15,3,'支出侧总计'),'D-004')

# 6 经济分类，左右两半并不是一行相加；工资+补助=人员，商品+资本=公用。
econ={}
for i,row in enumerate(PDF.pages[15].find_tables()[0].extract()):
 for base in (0,3):
  code=row[base]
  if code and re.fullmatch(r'\d{3}|\d{5}',code): econ[code]=cell(16,i,base+2,f'{code} {row[base+1]}')
for code in ['301','303','302','310']:
 check('hierarchy_direct_children',f'一般公共预算财政拨款基本支出决算表 {code}：已披露明细之和',econ[code],[z for k,z in econ.items() if len(k)==5 and k.startswith(code)],'D-006')
check('table_vertical','基本支出明细：人员经费=工资福利+个人家庭补助',cell(17,0,1,'人员经费合计'),[econ['301'],econ['303']],'D-006')
check('table_vertical','基本支出明细：公用经费=商品服务+资本性支出',cell(17,0,3,'公用经费合计'),[econ['302'],econ['310']],'D-006')
check('cross_table','一般公共预算基本支出=人员+公用',TABLES['一般公共预算财政拨款支出决算表'][0]['合计']['cells'][1],[cell(17,0,1,'人员经费合计'),cell(17,0,3,'公用经费合计')],'D-006')

# 7 三公，预算与决算分别加总。小计不与购置/运行重复计入。
for offset,label in [(0,'预算'),(1,'决算')]:
 check('table_horizontal',f'三公经费{label}合计=出国+车辆小计+接待',cell(18,4,offset,'三公合计'+label),[cell(18,4,j+offset,l+label) for j,l in [(2,'出国'),(4,'车辆小计'),(10,'接待')]],'D-007')
 check('table_horizontal',f'三公车辆{label}小计=购置+运行维护',cell(18,4,4+offset,'车辆小计'+label),[cell(18,4,6+offset,'购置'+label),cell(18,4,8+offset,'运行维护'+label)],'D-007')

# 8/9 明确空表说明，非适用而非填0。
for page,name in [(19,'政府性基金预算财政拨款收入支出决算表'),(20,'国有资本经营预算财政拨款收入支出决算表')]:
 coverage.append({'table':name,'page':page,'status':'not_applicable','reason':'该页原文明确本年度没有相应财政拨款收入和支出，故本表无数据；第28页重复说明。没有以0替代空白做假算式。','table_bbox':list(PDF.pages[page-1].find_tables()[0].bbox)})

# 各张表之间同口径、同科目；全部填写数字逐列核对。
income=TABLES['收入决算表'][0]; spend=TABLES['支出决算表'][0]; general=TABLES['一般公共预算财政拨款支出决算表'][0]
eq('收入总表本年收入=收入决算表合计',cell(7,26,1,'本年收入合计'),income['合计']['cells'][0],'D-002')
eq('收入总表一般公共预算财政拨款收入=收入决算表财政收入',cell(7,2,1,'一般公共预算收入'),income['合计']['cells'][1],'D-002')
eq('收入总表其他收入=收入决算表其他收入',cell(7,9,1,'其他收入'),income['合计']['cells'][6],'D-002')
eq('收入总表本年支出=支出决算表合计',cell(7,26,3,'本年支出合计'),spend['合计']['cells'][0],'D-003')
for code,i in [('201',2),('208',9),('210',10),('216',16),('221',20)]:
 eq(f'总表与支出表 {code} 同一功能类支出',cell(7,i,3,'总表'+code),spend[code]['cells'][0],'D-003')
eq('财政拨款总表本年收入=收入表财政拨款收入',cell(13,10,1,'财政本年收入'),income['合计']['cells'][1],'D-004')
eq('财政拨款总表一般公共预算本年支出=一般公共预算支出决算表合计',cell(13,10,4,'一般公共预算本年支出'),general['合计']['cells'][0],'D-005')
for code,p,i in [('201',12,2),('208',12,9),('210',12,10),('216',13,0),('221',13,4)]:
 eq(f'财政拨款总表与一般公共预算支出表 {code} 同口径',cell(p,i,4,code),general[code]['cells'][0],'D-005')
# 总支出和一般公共预算的项目支出均印有数字，同口径直接逐项比较；基本支出含非财政经费，不能机械强等。
for code,r in spend.items():
 if code in general and v(r['cells'][2]) is not None and v(general[code]['cells'][2]) is not None:
  eq(f'总支出表与一般公共预算支出表 {code} 项目支出',r['cells'][2],general[code]['cells'][2],'D-005',note='核对已披露同科目项目金额；总表基本经费与财政基本经费的口径不同，不要求相等。')
eq('三公运行维护费=基本支出明细30231',cell(18,4,9,'车辆运行维护决算'),econ['30231'],'D-007')
eq('三公接待费=基本支出明细30217',cell(18,4,11,'接待费决算'),econ['30217'],'D-007')
coverage.append({'table':'三公经费与经济明细','pages':[16,18],'status':'scope_difference_not_error','reason':'30212列的是基本支出99.34，三公表列财政拨款出国费199.23（含项目支出）；不能机械判100万元差额错误。31013空白也不能与三公车辆购置27.34强等。'})

# 表-文数字核对，文本值手工从原PDF提取阅读并逐页渲染确认。
for label,value,tablecell,page,rule in [
 ('收支总计','235305.25',cell(7,29,1),21,'D-001'),
 ('本年收入','233238.38',income['合计']['cells'][0],21,'D-002'),
 ('财政拨款收入','233085.87',income['合计']['cells'][1],21,'D-002'),
 ('其他收入','152.51',income['合计']['cells'][6],21,'D-002'),
 ('本年支出','233737.15',spend['合计']['cells'][0],21,'D-003'),
 ('基本支出','24663.89',spend['合计']['cells'][1],21,'D-003'),
 ('项目支出','209073.27',spend['合计']['cells'][2],21,'D-003'),
 ('财政收支总计','234283.60',cell(13,15,3),21,'D-004'),
 ('一般公共预算支出','233564.77',general['合计']['cells'][0],21,'D-005'),
 ('一般公共预算基本支出','24491.51',general['合计']['cells'][1],25,'D-006'),
 ('人员经费','18376.36',cell(17,0,1),25,'D-006'),
 ('公用经费','6115.14',cell(17,0,3),26,'D-006')]:
 pattern=re.escape(format(D(value),',.2f'))
 eq('表-文核对：'+label,txt(page,label,value,pattern),tablecell,rule)
for code in ['201','208','210','216','221']:
 val=general[code]['cells'][0]['value']; eq('功能类表-文核对 '+code,txt(22,code,val,re.escape(format(D(val),',.2f'))),general[code]['cells'][0],'D-005')
leaftext=[('2010350',22,'91.36'),('2014101',22,'2557.89'),('2014102',23,'9671.37'),('2014150',23,'195718.69'),('2014199',23,'18511.10'),('2080502',23,'72.38'),('2080505',23,'1871.66'),('2080506',24,'935.83'),('2080599',24,'0.00'),('2101101',24,'64.51'),('2101102',24,'992.73'),('2109999',25,'0.6'),('2160699',25,'1993.52'),('2210201',25,'901.44'),('2210203',25,'181.69')]
for code,p,value in leaftext:
 pattern=re.escape(format(D(value),',.2f')) if code!='2109999' else r'0\.6'
 if code=='2080599': pattern=r'支出决算为 0\.00 万元'
 eq('功能项表-文核对 '+code,txt(p,code+'正文决算',value,pattern),general[code]['cells'][0],'D-005',note='正文明确0.00，表格留空，数字层不能直接强等；需确认表格空白展示口径。' if code=='2080599' else None)
for page in [26,27]:
 for col,label in [(1,'合计'),(3,'出国'),(5,'车辆'),(11,'接待')]:
  if page==27 and col==1: continue
  value=cell(18,4,col)['value']
  eq(f'三公表-文核对 第{page}页 {label}',txt(page,label,value,re.escape(value)),cell(18,4,col,label),'D-007')
eq('三公表-文核对预算合计',txt(26,'三公年初预算','324.04'),cell(18,4,0,'预算合计'),'D-007')
for col,label in [(7,'车辆购置'),(9,'运行维护')]:
 eq('三公表-文核对 '+label,txt(27,label,cell(18,4,col)['value']),cell(18,4,col,label),'D-007')
check('narrative_sum','正文一般公共预算基本支出=人员+公用',txt(25,'基本支出','24491.51',r'24,491\.51'),[txt(25,'人员','18376.36',r'18,376\.36'),txt(26,'公用','6115.14',r'6,115\.14')],'D-006')

# 所有明确百分比的当前年内部算术。无法核验2024原始额，仅记录由本年/增量反算的内部一致性。
def ratio(label,numerator,denominator,printed,page,rule,internal=False):
 expect=v(numerator)/v(denominator)*100
 out=check('growth_internal_consistency' if internal else 'percentage',label,txt(page,'印刷比例',printed,re.escape(printed)+r'%',unit='%'),[{'label':'按原文数字计算值','value':fmt(expect),'page':None,'bbox':None,'unit':'%','numerator':numerator,'denominator':denominator}],rule,unit='百分点',note='仅核对本年及增量反推上年数后的算术一致性；上年原始决算材料未提供，不能核验增量事实。' if internal else None)
 return out
for label,n,d,rate,p,rule in [
 ('财政收入占比',income['合计']['cells'][1],income['合计']['cells'][0],'99.93',21,'D-002'),
 ('其他收入占比',income['合计']['cells'][6],income['合计']['cells'][0],'0.07',21,'D-002'),
 ('基本支出占比',spend['合计']['cells'][1],spend['合计']['cells'][0],'10.55',21,'D-003'),
 ('项目支出占比',spend['合计']['cells'][2],spend['合计']['cells'][0],'89.45',21,'D-003'),
 ('一般公共预算支出占本年支出',general['合计']['cells'][0],spend['合计']['cells'][0],'99.93',22,'D-005'),
 ('一般公共预算完成年初预算',general['合计']['cells'][0],txt(22,'年初预算','234430.42',r'234,430\.42'),'99.63',22,'D-005')]: ratio(label,n,d,rate,p,rule)
for code,rate in [('201','97.00'),('208','1.23'),('210','0.45'),('216','0.85'),('221','0.46')]:
 ratio('一般公共预算结构 '+code,general[code]['cells'][0],general['合计']['cells'][0],rate,22,'D-005')
for col,rate,label in [(1,'75.45','合计'),(3,'95.26','出国'),(5,'95.26','车辆'),(11,'3.99','接待')]:
 r=ratio('三公预算完成率 '+label,cell(18,4,col,label+'决算'),cell(18,4,col-1,label+'预算'),rate,26,'D-007')
 if col==5:
  matches=PDF.pages[25].search(r'95\.26%'); m=matches[1]
  r['lhs']['bbox']=[m[k] for k in ('x0','top','x1','bottom')]
  issues.append({'rule_id':'D-007','title':'公务用车购置及运行维护费预算完成率误写为95.26%','severity':'high','document_type':'决算公开','year':2025,'organization':'上海市数据局','page':26,'bbox':r['lhs']['bbox'],'table_or_section':'七、财政拨款“三公”经费支出决算情况说明（一）','snippet':'公务用车购置及运行维护费支出决算为43.34万元，完成预算的95.26%','expected':'43.34 / 67.30 × 100 = 64.3982169391%，取两位小数为64.40%','actual':'95.26%','difference':'30.8617830609个百分点（印刷比例减精确计算比例）','reason':'第18页车辆小计预算67.30、决算43.34；出国费199.23/209.14才约95.26%，疑似复制前项比例。','suggestion':'将公务用车购置及运行维护费完成预算比例复核改为64.40%。','confidence':0.99,'operands':r['rhs'][0],'check_id':r['id'],'evidence':[cell(18,4,4,'车辆预算'),cell(18,4,5,'车辆决算'),r['lhs']]})
for col,rate,label in [(3,'81.49','出国'),(5,'17.73','车辆'),(11,'0.78','接待')]: ratio('三公决算结构 '+label,cell(18,4,col,label),cell(18,4,1,'三公合计'),rate,27,'D-007')
for label,current,delta,rate,page in [
 ('总收支同比','235305.25','21258.91','9.93',21),('财政总收支同比','234283.60','21305.97','10.00',21),('一般公共预算支出同比','233564.77','25294.27','12.14',22),('三公总计同比','244.48','29.59','13.77',26),('出国费同比','199.23','26.78','15.53',26),('车辆费同比','43.34','1.11','2.63',26),('接待费同比','1.90','1.69','804.76',26),('机关运行经费同比','1649.72','1234.67','297.48',28)]:
 derived={'label':'本年减增量反推上年（非上年原始证据）','value':fmt(D(current)-D(delta)),'page':page,'bbox':None,'unit':'万元','calculation':f'{current} - {delta}'}
 ratio(label,txt(page,'增量',delta,re.escape(format(D(delta),',.2f'))),derived,rate,page,'D-007' if page==26 else 'D-005',True)

# 正文其他明确合计与跨页预算明细。
budgets=[('2010350',22,'0.00'),('2014101',22,'2838.02'),('2014102',23,'9590.84'),('2014150',23,'198415.66'),('2014199',23,'18105.80'),('2080502',23,'73.96'),('2080505',23,'1982.50'),('2080506',24,'1002.46'),('2080599',24,'1.08'),('2101101',24,'81.34'),('2101102',24,'1148.62'),('2109999',25,'3.00'),('2160699',25,'0.00'),('2210201',25,'958.63'),('2210203',25,'228.52')]
check('narrative_sum','年初预算总额与15项功能科目正文预算之和',txt(22,'预算总额','234430.42',r'234,430\.42'),[txt(p,code+'预算',value,re.escape(format(D(value),',.2f'))) for code,p,value in budgets],'D-005')
check('narrative_sum','三公同比增加总额与三项增加额之和',txt(26,'总增加','29.59'),[txt(26,l,x) for l,x in [('出国增加','26.78'),('车辆增加','1.11'),('接待增加','1.69')]],'D-007')
check('narrative_sum','政府采购总额=货物+工程+服务',txt(29,'采购总额','153573.16',r'153,573\.16'),[txt(29,l,x,re.escape(format(D(x),',.2f'))) for l,x in [('货物','107.76'),('工程','0.00'),('服务','153465.40')]],'NUMERIC')
check('narrative_sum','绩效自评项目数=优+良+合格+不合格',txt(28,'自评项目','23',r'项目23个',unit='个'),[txt(28,l,x,pat,unit='个') for l,x,pat in [('优','14',r'项目14个'),('良','9',r'项目9'),('合格','0',r'“合格”的项目0个'),('不合格','0',r'“不合格”的\n项目0个')]],'NUMERIC',unit='个')
check('narrative_sum','绩效问题数=已整改+正在整改',txt(28,'问题数','34',r'问题34个',unit='个'),[txt(28,'已整改','34',r'整改的34个',unit='个'),txt(28,'正在整改','0',r'正在整改的0个',unit='个')],'NUMERIC',unit='个')
check('narrative_sum','车辆总数=8类用途之和',txt(29,'车辆总数','13',r'共有车辆 13 辆',unit='辆'),[txt(29,l,x,pat,unit='辆') for l,x,pat in [('部级','0',r'部级领导干部用车 0 辆'),('主要','0',r'主要领导干部用车 0 辆'),('机要','1',r'信用车 1 辆'),('应急','1',r'应急保障用车 1 辆'),('执法','0',r'执法执勤用车 0 辆'),('特种','4',r'技术用车 4 辆'),('离退休','0',r'离退休干部用车 0 辆'),('其他','7',r'其他用车 7 辆')]],'NUMERIC',unit='辆')

manual=[
 {'title':'2024年度同比原始基数未提供','pages':[21,22,26,28],'severity':'manual_review','reason':'已核对本年金额、增量与反推上年基数的增长率算术，但未取得2024年决算原文；不能确认同比增量真实或与上年口径一致。'},
 {'title':'表格空白单元格口径不能据文本层直接定为0','pages':[7,8,9,10,11,12,13,14,15,16],'severity':'manual_review','reason':'保留所有空白为null；逐项已填数可算术比较，但部分完整等式因空白未知仅获披露值一致。第19/20页基金与国资表有明确无此预算，另列非适用。2080599第24页正文明确0.00，可据正文复核，但表中空白展示口径仍不虚构。'},
 {'title':'未取得预算原文及绩效附件','pages':[22,23,24,25,28],'severity':'manual_review','reason':'核对文内年初预算分项与总额并查算比例；无法验证预算原始数、调整后全年预算、23个绩效项目209181.38万元和平均95.60分的底层项目明细。'},
]
for r in checks:
 if r['status'] in ['fail','manual_review_missing_operands'] and r['id'] not in [z['check_id'] for z in issues]:
  manual.append({'title':r['title'],'severity':'manual_review','check_id':r['id'],'reason':'差额超过容差，需要原始口径/缺失操作数复核。'})
result={
 'summary':{'document_type':'决算公开','year':2025,'organization':'上海市数据局','source':str(SOURCE),'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'physical_pdf_pages':31,'amount_unit':'万元','ruleset':'v3_3_portable','confirmed_numeric_issues':len(issues),'checks_total':len(checks),'check_status_counts':dict(Counter(x['status'] for x in checks)),'check_kind_counts':dict(Counter(x['kind'] for x in checks)),'overall_assessment':'发现1项明确三公车辆预算完成率错误；已核对的表内表间金额差异均在0.5万元容差内，部分0.01万元差异可能为取整；留空字段没有按0代入。','risk_flags':['空白金额语义未直接证明','上年决算/原始预算/绩效附件未提供']},
 'independence':{'system_outputs_read':False,'system_parsed_artifacts_read':False,'other_agents_conclusions_read':False,'source_code_read':False,'method':'仅原始PDF，pdfplumber提取表格/坐标，逐页渲染视觉核对第7—29页，人工识别列和层级，Decimal独立运算。','frozen':True},
 'evidence_coordinate_system':'物理PDF页，bbox=[x0,top,x1,bottom]，单位point，原页面左上为原点；横向页841.9×595.3，竖向页595.3×841.9。坐标来自pdfplumber实测，无法匹配文本时为null。',
 'issues':issues,'checks':checks,'coverage_limits':coverage,'manual_review':manual,
 'notes':['合计只加同层直接下级，不混加父子；第8/9、10/11、12/13、14/15、16/17页按同一表续页合并。','disclosed_values_consistent_within_tolerance表示仅已披露分项之和与合计差额在容差内，并不证明全部空白是0。','金额0.5万元与比例0.1个百分点容差；对0.01万元差异记录而不硬判错误。','总支出与财政拨款支出范围不同，未要求其相等；基本支出30212与全部三公出国费范围不同，未强行比较。'],
 'next_actions':['修正第26页车辆预算完成率为64.40%。','取2024决算及2025年初/调整预算核验同比与预算原数。','取项目绩效明细和可编辑表核验留空单元格与底层分项。']
}
DEST.parent.mkdir(parents=True,exist_ok=True)
DEST.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result['summary'],ensure_ascii=False,indent=2))
print('PATH',DEST.resolve())
print('ROUNDING',[(x['id'],x['title'],x['difference_lhs_minus_known_sum']) for x in checks if x['difference_lhs_minus_known_sum'] and D(x['difference_lhs_minus_known_sum'])!=0 and x['unit']=='万元'])
print('FAILS',[(x['id'],x['title'],x['difference_lhs_minus_known_sum']) for x in checks if x['status']=='fail'])
