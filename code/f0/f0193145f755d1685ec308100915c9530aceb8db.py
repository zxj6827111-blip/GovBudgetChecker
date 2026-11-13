"""逐项原页裁定：原件问题、系统误报及证据边界分别保留。"""
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import csv
import json
from pathlib import Path
import re
import unicodedata

import pymupdf

BASE=Path(__file__).resolve().parent
INV={r['doc_id']:r for r in json.loads((BASE/'inventory.json').read_text())}


def compact(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(text or '')))


def evidence(doc,page,quote):
    chars=[]
    with pymupdf.open(BASE/'documents'/doc/'source.pdf') as pdf:
        for block in pdf[page-1].get_text('rawdict')['blocks']:
            for line in block.get('lines',[]):
                for span in line['spans']:
                    for char in span['chars']:
                        chars.extend((token,char['bbox']) for token in compact(char['c']))
    text=''.join(token for token,_ in chars)
    boxes=[]
    for match in re.finditer(re.escape(compact(quote)),text):
        chosen=[box for _,box in chars[match.start():match.end()]]
        boxes.append([min(b[0] for b in chosen),min(b[1] for b in chosen),max(b[2] for b in chosen),max(b[3] for b in chosen)])
    assert boxes,(doc,page,quote)
    return dict(page=page,quote=quote,bbox=boxes[0] if len(boxes)==1 else None,matched_spans=boxes,
                location_status='exact_native_span' if len(boxes)==1 else 'multiple_native_spans',sha256=INV[doc]['sha256'])


def owner(doc):
    return re.split('2024',compact(INV[doc]['cover_text']))[0].lstrip('0123456789')


findings=[]
def add(doc,rule,title,severity,page,actual,expected,difference,quotes,suggestion):
    proofs=[evidence(doc,p,q) for p,q in quotes]
    findings.append(dict(issue_id=f'N9-SOURCE-{len(findings)+1:02}',rule_id=rule,title=title,severity=severity,
        document_type='决算公开',year=2024,organization=owner(doc),doc_id=doc,page=page,
        bbox=next((p['bbox'] for p in proofs if p['page']==page and p['bbox']),None),table_or_section=title,
        snippet='；'.join(q for _,q in quotes),actual=actual,expected=expected,difference=difference,
        reason='冻结原件原字符与完整页图像核对；数字独立复算，不以系统候选充当原页真值。',
        suggestion=suggestion,confidence='high',evidence=proofs))


add('SH2024N9_01','NAME-CONSISTENCY','215类名称在表与说明中不一致','low',22,
    '说明“资源勘探信息等支出”','表内“资源勘探工业信息等支出”','漏“工业”',
    [(22,'资源勘探信息等支出（类）'),(12,'资源勘探工业信息等支出')],
    '按同报告原表核对并统一名称；这里只确认内部不一致，不据此判定违反外部科目规定。')
add('SH2024N9_02','YOY','1715.06万元及增加73.25万元对应同比不符','medium',19,
    '增长4.27%','73.25÷(1715.06−73.25)×100=4.46%','约0.19个百分点；同页三个章节重复',
    [(19,'收入支出总计增加73.25万元，增长4.27%'),(19,'一般公共预算财政拨款支出增加73.25万元，增长4.27%')],
    '核对上年基数后同步修订收入支出、财政拨款及一般公共预算三处同比。')
add('SH2024N9_02','R001','职业年金预算金额单位疑似漏万字','high',21,
    '年初预算48元、决算43.35万元，却写决算小于预算','先核对预算真实单位，不能仅把比较方向改为大于','48元仅0.0048万元',
    [(21,'年初预算为48元，支出决算为43.35万元'),(21,'决算数小于预算数的主要原因：人员变动')],
    '回预算底稿确认是否应为48万元；未取得底稿前不直接替原件补单位或改数。')
add('SH2024N9_02','D-007','车辆经费预算完成率不符','high',22,
    '96.58%','3.68÷7.00×100=52.57%','44.01个百分点',
    [(22,'公务用车购置及运行维护费支出决算为3.68万元，完成预算的96.58%'),(16,'7.00'),(16,'3.68')],
    '核对原表预算分母，确认后同步修改说明。')
add('SH2024N9_02','YOY','车辆经费同比降幅不符','medium',23,
    '减少2.34万元、下降38.49%，本期3.68万元','2.34÷(3.68+2.34)×100=38.87%','0.38个百分点',
    [(22,'公务用车购置及运行维护费支出决算为3.68万元'),(23,'公务用车购置及运行维护费支出决算减少2.34万元，下降38.49%')],
    '复核上年车辆经费及增减额，不将别的三公分项金额混入计算。')
add('SH2024N9_02','R003','接待变动原因保留相反方向括号','low',23,
    '公务接待费支出减少（增加）的主要原因','按实际下降方向表述','模板方向未清理',
    [(23,'公务接待费支出减少（增加）的主要原因')],'确认方向后删除多余括号选项。')
add('SH2024N9_02','YOY','机关运行经费同比使用的基数不符','high',25,
    '本期255.27万元、增加54.96万元、增长21.53%','54.96÷(255.27−54.96)×100=27.44%','5.91个百分点',
    [(25,'机关运行经费支出255.27万元，比2023年度增加54.96万元，增长21.53%')],
    '核对上年200.31万元是否成立，再修订增长率。')
add('SH2024N9_02','TEXT','养老保险说明中为字位置错误','low',21,
    '支出决算65.96为万元','语序应清晰表达金额','为字位置异常',
    [(21,'支出决算65.96为万元')],'确认金额后将语序改为“支出决算为65.96万元”。')
add('SH2024N9_05','NAME-CONSISTENCY','20805款名称在两处说明与原表不一致','low',18,
    '说明“行政事业单位离退休”','表内“行政事业单位养老支出”','第18、19页两处',
    [(18,'行政事业单位离退休（款）'),(19,'行政事业单位离退休（款）'),(12,'行政事业单位养老支出')],
    '按同报告原表统一两处名称，不重复列成两个独立金额问题。')
add('SH2024N9_05','COMPARE','一般行政管理事务预算决算比较方向相反','high',20,
    '777.6万元小于760.01万元','777.60大于760.01','17.59万元',
    [(20,'年初预算为760.01万元，支出决算为777.6万元。决算数小于预算数')],
    '复核金额和方向，确认后修订比较文字及原因。')
add('SH2024N9_05','D-005','事业运行财政拨款决算表文不一致','high',20,
    '说明2213.08万元','财政拨款表2170150为2207.61万元','5.47万元',
    [(20,'支出决算为2213.08万元'),(12,'2,207.61')],
    '核对财政拨款与包含其他收入的口径，不能仅凭差额替任一数值补数。')
add('SH2024N9_06','D-007','车辆经费预算完成率不符','high',26,
    '93.12%','103.17÷128.90×100=80.04%','13.08个百分点',
    [(26,'公务用车购置及运行维护费支出决算为103.17万元，完成预算的93.12%'),(17,'128.90'),(17,'103.17')],
    '确认预算分母后修订完成率。')
add('SH2024N9_08','D-005-RATE','一般公共预算完成率不符','medium',19,
    '84.06%','7066.32÷8418.98×100=83.93%','0.13个百分点',
    [(19,'年初预算为8418.98万元，支出决算为7066.32万元，完成年初预算的84.06%')],
    '核对年初预算和决算金额后统一完成率。')
add('SH2024N9_08','NAME-CONSISTENCY','21099款名称与原表不一致','low',21,
    '说明“行政事业单位医疗（款）其他卫生健康支出（项）”','表内21099为其他卫生健康支出','款级名称差异',
    [(21,'行政事业单位医疗（款）其他卫生健康支出（项）'),(13,'其他卫生健康支出')],
    '核对对应科目层级与款名称，不只依据同名项选择父级。')
add('SH2024N9_08','R003','主要原因短语相邻重复','low',18,
    '主要原因：主要原因：','保留一次主要原因','短语重复一次',
    [(18,'主要原因：主要原因：')],'删除重复短语。')
assert len(findings)==15

reviews=[]
for doc in INV:
    for index,candidate in enumerate(json.loads((BASE/'documents'/doc/'engine_issues.json').read_text())):
        if candidate['severity']=='info':continue
        rule=candidate['rule'];decision='confirmed_source_issue';reason='原页复核成立，见独立原件问题清单。'
        proofs=[]
        if doc=='SH2024N9_02' and rule=='V33-246':
            decision='false_positive';reason='原第24页明确列示国内接待18次、114人次；将“次”变体当作未披露。'
            proofs=[evidence(doc,24,'2024年国内公务接待共18次、114人次')]
        elif doc=='SH2024N9_03' and rule=='V33-234':
            decision='false_positive';reason='把新章节三公预算29.98/决算18.69串到前段购房补贴；跨页第21页已有团组减少的原因。'
            proofs=[evidence(doc,20,'“三公”经费财政拨款支出年初预算为29.98万元，支出决算为18.69万元'),
                    evidence(doc,21,'因公出国（境）团组和公务接待团组减少')]
        elif doc=='SH2024N9_04' and rule=='V33-106':
            decision='false_positive';reason='把社会保障分项672.47当成总体金额；原表1622.36=672.47+209.65+740.24，没有相应总额矛盾。'
            proofs=[evidence(doc,14,'“社会保障和就业支出”科目672.47万元'),evidence(doc,9,'1622.36')]
        elif doc=='SH2024N9_04':
            decision='manual_review';reason='确认存在空表缺解释或未见国资表，但条件性范围和披露边界不明。不能将空白认作零、自动补“无支出”说明或直接定性缺表重大错误；三公空表有重复候选。'
        elif doc=='SH2024N9_03' and rule=='V33-SG-COMPLETION':
            decision='manual_review';reason='表内购置费为空，说明只明示运行维护7万元；应核实是否需补无购置说明，不能把空白直接补零。'
            proofs=[evidence(doc,21,'公务用车运行维护支出7.00万元')]
        elif doc=='SH2024N9_05' and rule=='V33-224':
            decision='manual_review';reason='63.24/110.06=57.4595675%，显示到两位为57.46%；原57.56相差0.10个百分点，位于默认容差与舍入边界，保留提示而不直接确定为金额/占比错误。'
            proofs=[evidence(doc,22,'因公出国（境）费支出决算63.24万元，占57.56%')]
        reviews.append(dict(doc_id=doc,candidate_id=f'{doc}:{index:02}',rule_id=rule,severity=candidate['severity'],
                            message=candidate['message'],location=candidate.get('location'),decision=decision,reason=reason,evidence=proofs))
assert Counter(r['decision'] for r in reviews)=={'confirmed_source_issue':14,'false_positive':3,'manual_review':6}
manual=[dict(title='同一报告车辆数量2辆与0辆需核对范围和时点',doc_id='SH2024N9_02',severity='manual_review',
    evidence=[evidence('SH2024N9_02',23,'公务用车保有量为2辆'),evidence('SH2024N9_02',25,'共有车辆0辆')],
    reason='全年开支财政拨款口径与年末资产口径可能不同，未取得底稿前不判确定矛盾。')]
for name,data in [('confirmed_source_issues',findings),('engine_candidate_review',reviews),('additional_manual_review',manual)]:
    (BASE/f'{name}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
with (BASE/'原件问题清单.csv').open('w',encoding='utf-8-sig',newline='') as stream:
    fields=['issue_id','rule_id','organization','year','severity','page','title','actual','expected','difference','suggestion','bbox']
    writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
    for issue in findings:writer.writerow({key:json.dumps(issue[key],ensure_ascii=False) if isinstance(issue[key],(dict,list)) else issue[key] for key in fields})
(BASE/'business_review_summary.json').write_text(json.dumps(dict(confirmed_original_issues=len(findings),severities=dict(Counter(r['severity'] for r in findings)),
    engine_noninfo_candidates=len(reviews),candidate_decisions=dict(Counter(r['decision'] for r in reviews)),
    additional_manual_review=1,human_full_page_signoff=False),ensure_ascii=False,indent=2))
print('原件问题',len(findings),'系统候选裁定',dict(Counter(r['decision'] for r in reviews)),flush=True)
