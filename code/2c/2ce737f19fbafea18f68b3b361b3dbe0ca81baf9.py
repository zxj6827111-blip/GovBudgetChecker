"""汇总独立原件比对与真实数据库证据，保留原件缺项而不伪造全通过。"""
from collections import Counter
import copy
from decimal import Decimal
import hashlib
import html
import json
from pathlib import Path

from compare import BASE,norm,num,refs,matches,indexed_cells,facts_at,decoded
from adjudicate import check_identity

selection=json.loads((BASE/'selection.json').read_text())['documents']
visual=json.loads((BASE/'visual_source_truth.json').read_text())['documents']
initial=json.loads((BASE/'comparison_initial_summary.json').read_text())
adjudicated=json.loads((BASE/'adjudicated_summary.json').read_text())
service=json.loads((BASE/'service/service_receipt.json').read_text())
frozen=json.loads((BASE/'source_frozen_manifest.json').read_text())
for frozen_doc in frozen['documents']:
    assert hashlib.sha256((BASE/'source'/f'{frozen_doc["id"]}.json').read_bytes()).hexdigest()==frozen_doc['source_truth_sha256']
assert hashlib.sha256((BASE/'visual_source_truth.json').read_bytes()).hexdigest()==(BASE/'visual_source_truth.sha256').read_text().strip()
assert service['status']=='passed' and service['cleanup']=='complete'
rows=[];checks=[];all_semantic=[]
for item,inv,first,last in zip(selection,visual,initial,adjudicated):
    id=item['id']
    assert first['id']==last['id']==inv['id']==id
    assert hashlib.sha256(Path(item['source_pdf']).read_bytes()).hexdigest()==item['sha256']
    assert not last['semantic_checks'].get('binding_difference') and not last['remaining_initial_differences']
    assert last['travel_checks']=={'match':12}
    assert last['table_checks']=={'match':first['core_table_pages']}
    assert not any(a=='unresolved' for a in last['adjudications'])
    a=json.loads((BASE/'system'/id/'artifact.json').read_text())
    source=json.loads((BASE/'source'/f'{id}.json').read_text())
    facts=json.loads((BASE/'system'/id/'facts.json').read_text())
    comparison=json.loads((BASE/f'{id}_comparison.json').read_text())
    final=json.loads((BASE/f'{id}_adjudicated.json').read_text())
    physical=indexed_cells(a,'physical_fragments')
    covered=set()
    for c in final['semantic']:
        if c['status']=='bound_match':
            for f in c['observed']:covered.add(f['fact_uid'])
        all_semantic.append(dict(document=id,**c))
    for anchor in comparison['anchors']:
        if anchor['table']!=7 or anchor['expected'] is None:continue
        assert anchor['status']=='value_match'
        page=anchor['page'];col=anchor['column']
        # 这些来源已通过完整12列位置、业务分项和预算/决算检查。
        found=[f for f in facts if f['table_code']=='FIN_07_three_public' and f['indicator']==['total_actual','overseas','vehicle_purchase_operation','vehicle_purchase','vehicle_operation','reception'][col//2]
               and norm(decoded(f['extra_dims']).get('source_header')).endswith('预算数' if col%2==0 else '决算数')]
        assert len(found)==1
        assert num(found[0]['normalized_value'])==Decimal(anchor['expected'])
        covered.add(found[0]['fact_uid'])
    assert covered=={f['fact_uid'] for f in facts},(id,len(covered),len(facts))
    physical_blank_checks=0
    for page in source['pages']:
        for c in page['cells']:
            found=matches(physical,page['page'],c['bbox'])
            assert found,(id,page['page'],c)
            if not norm(c['text']):
                assert num(found[0][2]['raw_text']) is None,(id,page['page'],c)
                physical_blank_checks+=1
    for f in facts:
        r=refs(f)
        assert len(r)==1
        p=r[0]['physical_page'];box=r[0]['bbox']
        found=matches(physical,p,box,tolerance=.01)
        assert found and norm(f['raw_value'])==norm(found[0][2]['raw_text'])
    manual=comparison['anchors']
    assert len(manual)==(15 if inv.get('fin05_total_anchor') else 12)
    assert all(m['status']!='mismatch' for m in manual)
    rows.append(dict(id=id,filename=item['filename'],category=item['category'],source_pdf=item['source_pdf'],pages=first['pages'],core_tables=9,core_table_pages=first['core_table_pages'],original_grid_cells=first['source_cells']['cells'],original_grid_number_cells=first['source_cells']['number_cells'],original_blank_cells=physical_blank_checks,source_decimal_tokens=first['decimal_tokens'],source_decimal_tokens_matched=first['decimal_matched'],bound_facts_verified=len(facts),source_missing_label=last['semantic_checks'].get('source_missing_label',0),manual_anchor_count=len(manual),raw_label_segmentation_differences=first['source_cells']['cells']-first['source_cells']['literal_match'],status='original_missing_label_preserved' if last['semantic_checks'].get('source_missing_label') else 'matched',tables=inv['tables']))

# 比对器反例：确认数字、科目、指标、页码、坐标和单位变化会被拒绝。
docid='doc01'
facts=json.loads((BASE/'system'/docid/'facts.json').read_text())
sample=next(c for c in json.loads((BASE/f'{docid}_adjudicated.json').read_text())['semantic'] if c.get('table')==5 and c.get('subject_code')=='201' and c.get('indicator')=='total_actual')
cell=dict(text=sample['raw'],bbox=sample['bbox'])
def evaluate(candidate):
    return check_identity(candidate,sample['page'],cell,sample['subject_code'],sample['subject_name'],sample['indicator'])['status']=='bound_match'
assert evaluate(facts)
target=facts_at(facts,sample['page'],sample['bbox'])[0]['fact_uid']
for name,change in [
    ('amount_changed',lambda f:f.update(normalized_value=str(num(f['normalized_value'])+Decimal('.01')))),
    ('subject_code_changed',lambda f:f.update(subject_code='9999999')),
    ('subject_name_changed',lambda f:f.update(subject_name='测试错配科目')),
    ('indicator_changed',lambda f:f.update(indicator='project_actual')),
    ('unit_changed',lambda f:f.update(unit='亿元')),
    ('page_changed',None),('bbox_changed',None),
]:
    candidate=copy.deepcopy(facts)
    f=next(f for f in candidate if f['fact_uid']==target)
    if change:change(f)
    else:
        extra=decoded(f['extra_dims']);r=extra['evidence_refs'][0]
        if name=='page_changed':r['physical_page']+=1
        else:r['bbox']=[v+20 for v in r['bbox']]
        f['extra_dims']=extra
    assert not evaluate(candidate),name
    checks.append(dict(control=name,rejected=True))
artifact=json.loads((BASE/'system/doc01/artifact.json').read_text())
without=copy.deepcopy(artifact)
without['table_fragments']=[t for t in without['table_fragments'] if t['table_code']!='FIN_05_general_public_expenditure']
assert not any(t.get('table_code')=='FIN_05_general_public_expenditure' for t,c in indexed_cells(without,'table_fragments')[13])
checks.append(dict(control='table_removed',rejected=True))
assert num('') is None and num('0.00')==Decimal('0')
checks.append(dict(control='blank_and_zero_distinct',rejected=True))

summary=dict(scope='10 stratified seeded native-text PDFs; original renders + source-first independent MuPDF grid/character reference + content-stream adjudication; actual isolated API/Worker/PostgreSQL readback; not unseen blind or human-signed truth',seed=2026101410,
    pdfs=10,pages=sum(r['pages'] for r in rows),core_tables=90,core_table_pages=sum(r['core_table_pages'] for r in rows),extra_organization_tables=3,
    original_grid_cells=sum(r['original_grid_cells'] for r in rows),original_grid_number_cells=sum(r['original_grid_number_cells'] for r in rows),original_blank_cells=sum(r['original_blank_cells'] for r in rows),
    original_decimal_tokens=sum(r['source_decimal_tokens'] for r in rows),original_decimal_tokens_matched=sum(r['source_decimal_tokens_matched'] for r in rows),
    bound_facts_verified=sum(r['bound_facts_verified'] for r in rows),source_missing_label=sum(r['source_missing_label'] for r in rows),
    source_monetary_cells=sum(r['bound_facts_verified']+r['source_missing_label'] for r in rows),all_source_amounts_have_subject=False,
    manual_visual_anchors=sum(r['manual_anchor_count'] for r in rows),raw_label_segmentation_differences=sum(r['raw_label_segmentation_differences'] for r in rows),
    reading_mismatches_after_original_adjudication=0,missing_core_tables=0,blank_to_zero_errors=0,
    real_original_service_cases=10,synthetic_service_controls=1,service_passed=True,temporary_cleanup=service['cleanup'],source_drift=not service['source_unchanged_during_run'],
    comparator_negative_controls=checks,production_historical_jobs_rewritten=False,product_code_modified=False,documents=rows)
(BASE/'final_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
(BASE/'all_semantic_checks.json').write_text(json.dumps(all_semantic,ensure_ascii=False,indent=2))

labels=['区人大常委会办公室（部门）','区政协办公室（单位）','区妇联（部门）','区总工会（单位）','区人才发展服务中心','区融媒体中心','区生态环境局（部门）','公安普陀分局（单位）','石泉路街道办事处本级','真如镇街道城市运行管理中心']
trs=''.join(f'<tr><td>{html.escape(label)}</td><td>{r["pages"]}</td><td>9 / {r["core_table_pages"]}页</td><td>{r["bound_facts_verified"]}</td><td>{"金额已读入；另1格原件缺科目" if r["source_missing_label"] else "比对一致"}</td></tr>' for label,r in zip(labels,rows))
links=''.join(f'<li>{html.escape(label)}：<a href="{html.escape(r["source_pdf"],quote=True)}">原件</a>；<a href="{r["id"]}_adjudicated.json">逐格科目与金额记录</a>；<a href="{r["id"]}_comparison.json">原始差异及锚点</a></li>' for label,r in zip(labels,rows))
page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>普陀10份报告原件与系统数据核验</title><style>body{{max-width:1120px;margin:35px auto;padding:0 24px;font:16px/1.7 system-ui;color:#20313a}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccd6da;padding:8px;text-align:left}}th{{background:#eaf2f2}}.note{{padding:15px 20px;background:#fff4db;border-left:4px solid #d38d10}}img{{max-width:100%;border:1px solid #ddd}}code{{background:#eef2f4;padding:2px 5px}}a{{color:#006b65}}</style><h1>普陀10份报告：原件与系统数据比对</h1>
<p>结果：本次抽样未发现新增漏表、金额错读、已命名科目错配。原件缺行名1格保留未绑定，不能将其算作已成功匹配科目。</p>
<p>固定种子2026101410，每个顶层类别随机抽取两个不同单位，共10份272页。覆盖90张核心表、123个核心表物理页，另3张单位名录表。10份均为有文本层的决算PDF。</p>
<table><thead><tr><th>报告</th><th>总页数</th><th>核心表 / 表页数</th><th>已核对金额事实</th><th>结果</th></tr></thead><tbody>{trs}</tbody></table>
<p>12,844个闭合网格原格均在系统正式表格中找到；4,186个数值或编码原值一致；5,526个原始空白未被改成数字。独立提取的2,712个小数金额全部在系统对应位置找到。2,737条已绑定金额逐项核对科目、金额、列指标和来源；其中三公经费完整12列共120个位置另以目视标注核对。目视金额/空白锚点合计147项。</p>
<h2>原件本身的缺口</h2><div class="note"><p>区妇联部门决算第15页：454.17万元所在行没有科目编码或名称，系统已保存原数字及位置，没有猜测其科目。该格计入2,738个原件金额格总数，未计入2,737条已绑定金额。</p><p>同份第14页的合计行原文是1417.66、170.06、920.49，系统忠实读取；其本身不满足合计关系。本次核验识别与原件一致，不代表材料数据正确。</p></div>
<p><a href="images/doc03_p15.png">妇联第15页原件图</a>；<a href="images/doc03_p14.png">妇联第14页原件图</a>。人才中心第10页合计金额本来留空，系统也留空；融媒体中心一般公共预算表原件没有合计行，本次未补造合计。</p>
<h2>七处文字分格差异如何处理</h2><p>生态环境局原件部分行文字换行后被绘制裁切，可见页面只显示名称前半段。独立可见文字抽取与系统原始网格文本有7处差异，全部保留在初轮记录。以原始内容流（pypdf）和放大原图复核后，系统正式科目名称均有原文支持，金额对应正确。没有把这些位置描述为页面文字逐字相同，也没有用金额倒推科目。</p>
<p><a href="images/doc07_p14_overflow_235.png">生态环境局第14页裁切图</a>；<a href="source/doc07_pypdf_content_stream.json">原始内容流独立读取</a>。真如城运第7、9页顶部没有横线，独立闭合网格漏掉首行，另以原始字符与页面补核5个金额；系统已正确读取。</p>
<h2>方法与服务证据</h2><ol><li>运行系统前，冻结PyMuPDF原件字符、向量线构成的独立网格，以及全部页面图；浏览全文件页面登记表与续页。</li><li>在全新临时数据库中，经实际上传API、Worker、入库，再回读完整表格、事实和证据。10个原件场景及1个合成控制场景通过；代码指纹未变化。</li><li>比较原始格、正式物理格、跨页逻辑表、金额事实；差异通过另一原件内容流工具及图像逐项核实。初轮比较器对双栏空白及续页的局限也保留日志。</li><li>9项反例控制确认金额、科目代码、名称、指标、单位、页码、坐标和整表缺失会被检测，空白与零区分。</li></ol>
<p>临时服务及数据库清理完成。本次没有修改产品代码，也没有改写既有线上任务或人工复核结果。本次属于已见材料的抽样核验，不是独立人工签署或未见样本盲测，也未验证扫描表格。</p>
<p><a href="final_summary.json">汇总JSON</a>；<a href="service/service_receipt.json">真实服务回执</a>；<a href="selection.json">抽样清单</a>；<a href="source_frozen_manifest.json">原件参照冻结记录</a>；<a href="all_semantic_checks.json">全部科目与金额核对记录</a></p><ul>{links}</ul></html>'''
(BASE/'核验报告.html').write_text(page)
(BASE/'final_evidence_hashes.json').write_text(json.dumps({str(p.relative_to(BASE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in BASE.rglob('*') if p.is_file() and p.name!='final_evidence_hashes.json' and '__pycache__' not in p.parts},ensure_ascii=False,indent=2))
print(json.dumps({k:summary[k] for k in ['pdfs','pages','core_tables','core_table_pages','original_grid_cells','original_grid_number_cells','original_blank_cells','bound_facts_verified','source_missing_label','source_monetary_cells','manual_visual_anchors','reading_mismatches_after_original_adjudication','service_passed','temporary_cleanup']},ensure_ascii=False,indent=2))
