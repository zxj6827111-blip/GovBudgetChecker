import fitz,pdfplumber,pathlib,json,datetime,re,hashlib
out=pathlib.Path('outputs/real_intake_review_20261008')
names=['上海市交通委员会','上海市文化和旅游局','上海市商务委员会','上海市民政局','上海市发展和改革委员会','上海市财政局','上海市司法局','上海市监狱管理局']
titles=['收入支出决算总表','收入决算表','支出决算表','财政拨款收入支出决算总表','一般公共预算财政拨款支出决算表','一般公共预算财政拨款基本支出决算表','财政拨款“三公”经费支出决算表','政府性基金预算财政拨款收入支出决算表','国有资本经营预算财政拨款收入支出决算表']
tpages=[[8,9,14,19,21,24,26,27,29],[8,9,13,17,19,22,23,24,25],[9,10,12,15,17,18,20,21,22],[10,11,14,17,19,20,22,23,24],[10,11,14,17,19,21,23,24,25],[8,9,11,13,15,17,18,19,20],[8,9,11,13,15,16,17,18,19],[7,8,10,12,14,15,17,18,19]]
sections=['一、收入支出决算总体情况说明','二、收入决算情况说明','三、支出决算情况说明','四、财政拨款收入支出决算总体情况说明','五、一般公共预算财政拨款支出决算情况说明','六、一般公共预算财政拨款基本支出决算情况说明','七、财政拨款“三公”经费支出决算情况说明','八、政府性基金预算财政拨款收入支出决算情况说明','九、国有资本经营预算财政拨款收入支出决算情况说明','十、预算绩效管理情况']
spages=[[30,30,30,30,30,38,39,40,42,42],[26,26,26,26,26,34,35,37,37,38],[23,23,23,23,23,28,29,30,31,31],[25,25,25,25,25,31,31,33,34,34],[26,26,26,26,26,33,34,36,36,36],[21,21,21,21,21,26,27,28,28,29],[20,20,20,20,20,26,27,28,28,29],[20,20,20,20,20,25,25,27,27,27]]
def norm(s):return re.sub(r'\s+','',s)
def box_chars(page,target):
 arr=[];text=''
 for ch in page.chars:
  for c in ch.get('text',''):
   if not c.isspace():text+=c;arr.append(ch)
 target=norm(target);idx=text.find(target)
 if idx<0:return None
 cc=arr[idx:idx+len(target)]
 return [round(min(c['x0'] for c in cc),2),round(min(c['top'] for c in cc),2),round(max(c['x1'] for c in cc),2),round(max(c['bottom'] for c in cc),2)]
def ev(pdf,p,needle):
 q=pdf.pages[p-1]
 return {'page':p,'bbox':box_chars(q,needle),'snippet':needle,'bbox_coordinate_system':'PDF points; origin top-left; physical page'}
result={'review_date':'2026-10-08','rule_version':'v3_3_portable','scope':'A+B 文档识别、结构、核心表完整性；原PDF先行独立复核，不读取系统issues/audit后冻结；非盲测、非全产品准确率','freeze_timestamp':datetime.datetime.now().astimezone().isoformat(),'physical_page_numbering':True,'documents':[],'issues':[],'manual_review':[]}
for k,name in enumerate(names):
 path=pathlib.Path('/Users/zhangxingjin/Desktop/决算测试上海市')/(name+'.pdf')
 raw=fitz.open(path)
 with pdfplumber.open(path) as pdf:
  item={'source_file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'document_type':'决算公开','year':2025,'organization':name,'publication_date':None,'source_url':None,'page_count':len(raw),'cover':ev(pdf,1,name),'toc_pages':[2,3],'toc_has_target_page_numbers':False,'toc_assessment':'目录列出九张核心表、一至九说明及绩效管理，标题与正文对应；目录未标目标页码，不能判定页码偏移','tables':[],'sections':[],'conditional_tables':[],'assessment':'九张核心表及一至九说明均在原件存在，未发现确定缺表、重表或必备说明缺章','risk_flags':[]}
  for j,(title,p) in enumerate(zip(titles,tpages[k])):
   end=(tpages[k][j+1]-1) if j<8 else spages[k][0]-1
   t=ev(pdf,p,title);t.update({'table_id':j+1,'title':title,'pages':list(range(p,end+1)),'presence':'present','content_header_matches':True});item['tables'].append(t)
  for title,p in zip(sections,spages[k]):
   e=ev(pdf,p,title);e.update({'title':title,'presence':'present'});item['sections'].append(e)
  for j,typ in [(7,'政府性基金'),(8,'国有资本经营')]:
   p=tpages[k][j];q=pdf.pages[p-1];txt=q.extract_text() or ''
   state='empty_with_equivalent_explanation' if ('故本表无数据' in txt) else 'data_present'
   item['conditional_tables'].append({'type':typ,'table_page':p,'state':state,'table_empty_explanation': next((l for l in txt.splitlines() if '故本表无数据' in l),None),'corresponding_section':ev(pdf,spages[k][j],sections[j])})
  if name=='上海市财政局':
   item['risk_flags'].append({'type':'extractor_compatibility','page':28,'description':'fitz get_text仅63字，pdfplumber 536个字符（空格存在少量cid映射）；页面渲染可见三公分项具体说明及八/九条件说明完整；不是原件缺章','fitz_text_chars':len(raw[27].get_text()),'pdfplumber_char_count':len(pdf.pages[27].chars),'image_count':len(raw[27].get_image_info()),'visual_checked':True,'risk_bbox':[89.0,73.0,511.0,753.0]})
   result['manual_review'].append({'organization':name,'rule_id':'EXTRACTOR-COVERAGE','severity':'manual_review','page':28,'bbox':ev(pdf,28,sections[7])['bbox'],'snippet':sections[7],'reason':'不同PDF提取器覆盖程度不一致；原件可见内容不能只依据fitz缺失判缺章','expected':'完整提取三公说明和政府性基金无此项说明','actual':'fitz仅返回国有资本经营说明','confidence':1.0})
  if name=='上海市民政局':item['risk_flags'].append({'type':'layout_note','page':13,'description':'原件第13页实际仅有收入决算表表下注和页码，为上页表格尾注；不按少字判OCR失败/缺表','visual_checked':True})
  result['documents'].append(item)
  if name=='上海市监狱管理局':
   evs=[]
   for p,needle in [(18,'无政府性基金预算财政拨款收入支出的，仍保留该表格式，并在本表下方作下述说明：'),(19,'无国有资本经营预算财政拨款收入支出的，仍保留该表格式，并在本表下方作下述说明：')]:evs.append(ev(pdf,p,needle))
   result['issues'].append({'rule_id':'STRUCT-TEMPLATE-RESIDUE','title':'条件空表仍保留编制模板提示语','severity':'low','document_type':'决算公开','year':2025,'organization':name,'page':18,'bbox':evs[0]['bbox'],'table_or_section':titles[7]+'；'+titles[8],'snippet':evs[0]['snippet'],'expected':'保留空表及“无收入和支出，故本表无数据”说明，删除给编制者的操作提示','actual':'两表注后仍保留“仍保留该表格式，并在本表下方作下述说明”模板提示','difference':None,'reason':'公开材料中模板编制指令未清理；不影响空表说明完整性，不判缺表','suggestion':'删除编制操作提示，保留事实性空表说明','confidence':0.99,'evidence':evs,'visual_checked':True})
(out/'structure_truth.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('FROZEN',out/'structure_truth.json','documents',len(result['documents']),'confirmed_issues',len(result['issues']),'manual_review',len(result['manual_review']))
print('MISSING BBOX',[(d['organization'],e['page'],e['title']) for d in result['documents'] for e in d['tables']+d['sections'] if e['bbox'] is None])
