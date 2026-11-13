"""保存本轮原页复核结论，区分原件问题、系统误报和同一问题重复报告。"""
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import csv
import json
from pathlib import Path
import re
import unicodedata

import pymupdf

BASE=Path(__file__).resolve().parent
INVENTORY={r['doc_id']:r for r in json.loads((BASE/'inventory.json').read_text())}


def compact(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(text or '')))


def evidence(doc_id,page,quote):
    path=BASE/'documents'/doc_id/'source.pdf'
    with pymupdf.open(path) as pdf:
        chars=[]
        for block in pdf[page-1].get_text('rawdict')['blocks']:
            for line in block.get('lines',[]):
                for span in line['spans']:
                    for char in span['chars']:
                        for token in compact(char['c']):chars.append((token,char['bbox']))
        text=''.join(t for t,_ in chars);needle=compact(quote)
        starts=[m.start() for m in re.finditer(re.escape(needle),text)]
        boxes=[]
        for start in starts:
            selected=[box for _,box in chars[start:start+len(needle)]]
            boxes.append([min(b[0] for b in selected),min(b[1] for b in selected),max(b[2] for b in selected),max(b[3] for b in selected)])
        return dict(page=page,quote=quote,bbox=boxes[0] if len(boxes)==1 else None,
                    matched_spans=boxes,unique_span=len(boxes)==1,
                    location_status='exact_native_span' if len(boxes)==1 else 'multiple_or_unresolved_spans',
                    source_sha256=INVENTORY[doc_id]['sha256'])


def main():
    findings=[]

    def add(doc_id,rule_id,title,severity,page,actual,expected,difference,quotes,suggestion,extra=None):
        proofs=[evidence(doc_id,p,q) for p,q in quotes]
        assert all(p['matched_spans'] for p in proofs),(doc_id,title,[p['quote'] for p in proofs if not p['matched_spans']])
        findings.append(dict(issue_id=f'SOURCE-{len(findings)+1:02}',rule_id=rule_id,title=title,severity=severity,
            issue_kind='source_document_issue',document_type='决算公开',year=2024,
            organization=INVENTORY[doc_id]['filename'].removesuffix('.pdf'),doc_id=doc_id,page=page,
            bbox=next((p['bbox'] for p in proofs if p['page']==page and p['bbox']),None),
            table_or_section=title,snippet='；'.join(p['quote'] for p in proofs),actual=actual,expected=expected,
            difference=difference,reason='已对冻结原件文字、原始字符坐标及相关原页图像复核',
            suggestion=suggestion,confidence='high',evidence=proofs,**(extra or {})))

    add('SH2024NEW_01','R001','事业单位离退休说明金额漏写“万”字','high',24,
        '同一段首句10.13万元，后句支出决算10.13元','金额单位与原表及段落首句保持一致',
        '10.13元=0.001013万元，与10.13万元相差10.128987万元',
        [(24,'事业单位离退休（项）10.13万元'),(24,'支出决算为10.13元')],
        '复核底稿后统一单位；若表格及首句10.13万元正确，后句补“万”字。')
    add('SH2024NEW_02','PROC-SUM','政府采购总额与货物、工程、服务之和不一致','high',31,
        '总额63,326.84万元','9,942.60+4,340.77+43,892.22=58,175.59万元','5,151.25万元',
        [(31,'为63,326.84万元'),(31,'货物采购金额9,942.60万元'),(31,'工程采购金额4,340.77万元'),(31,'服务采购金额43,892.22万元')],
        '核对采购台账、合计与三项口径后同步修订，不能根据差额替任何分项补数。')
    add('SH2024NEW_03','D-007','公务用车购置及运行维护费预算完成率错误','high',35,
        '说明87.60%','144.77÷182.97×100=79.12%','8.48个百分点',
        [(35,'公务用车购置及运行维护费支出决算为144.77万元，完成预算的87.60%'),(22,'182.97'),(22,'144.77')],
        '核对预算分母，若表内预算182.97万元正确，将车辆完成率修订为79.12%。')
    add('SH2024NEW_03','NAME-CONSISTENCY','208-05科目名称与对应表不一致','low',27,
        '说明使用“行政事业单位离退休”','与表中的“行政事业单位养老支出”统一','名称差异，不判定为金额错误',
        [(27,'行政事业单位离退休')],
        '按本报告对应表的科目名称统一文字；此处仅确认内部命名不一致，不据此声称违反最新法定科目。')
    add('SH2024NEW_03','R003','出国费用说明出现重复冒号','low',36,'开支内容包括：：','开支内容包括：','重复1个冒号',
        [(36,'开支内容包括：：公务出国（境）')],'删除重复冒号。')
    add('SH2024NEW_05','D-005','行政单位医疗表文金额不一致','high',23,
        '说明支出决算759.21万元，表内2101101为718.20万元','同科目同口径金额保持一致','41.01万元',
        [(23,'支出决算为759.21万元'),(14,'718.20'),(14,'41.01')],
        '核对行政与事业单位医疗分项，避免将两者合计写作行政单位医疗支出；不能仅凭差额确定哪处应改。')
    add('SH2024NEW_05','D-007','公务用车购置及运行维护费预算完成率错误','high',24,
        '说明66.50%','19.76÷34.40×100=57.44%','9.06个百分点',
        [(24,'公务用车购置及运行维护费支出决算为19.76万元，完成预算的66.50%'),(17,'34.40'),(17,'19.76')],
        '核对车辆预算数并修订完成率，不能沿用因公出国费的完成率。')
    add('SH2024NEW_06','D-005-RATE','一般公共预算支出年初预算完成率不一致','medium',26,
        '说明164.79%','865,642.68÷524,698.94×100=164.98%','0.19个百分点',
        [(26,'年初预算为524,698.94万元，支出决算为865,642.68万元，完成年初预算的164.79%')],
        '核对年初预算及决算金额后统一完成率；当前差异超过0.1个百分点默认容差。')
    add('SH2024NEW_10','D-006','人员经费合计不等于工资福利与个人家庭补助之和','high',19,
        '人员经费合计40,471.62万元','38,301.58+2,281.11=40,582.69万元','111.07万元',
        [(19,'38,301.58'),(19,'2,281.11'),(19,'40,471.62')],
        '复核人员经费合计和两类支出，不直接将差额填入任何科目。')
    add('SH2024NEW_10','R001','因公出国费用明细漏写“万”字','high',30,
        '明细写266.18元；同页总体说明及表格为266.18万元','同一费用金额单位保持一致',
        '266.18元=0.026618万元，与266.18万元相差266.153382万元',
        [(30,'因公出国（境）费支出266.18元'),(30,'因公出国（境）费决算为266.18万元')],
        '复核原始底稿，若266.18万元正确，在明细金额后补“万”字。')
    add('SH2024NEW_10','D-002-STRUCTURE','收入决算表出现层级缺项和重复行，无法完整勾稽','high',11,
        '类级可见金额合计89,325.95万元，合计行189,145.76万元；210类与其可见下级差4.20万元；5组科目行重复',
        '恢复完整、无重复且层级清晰的收入表后重新汇总','类级可见汇总差99,819.81万元；不能据此认定总收入本身错误',
        [(10,'189,145.76'),(10,'3,815.04'),(10,'3,810.84'),(11,'2120101'),(11,'21211')],
        '回原始表格或底稿检查缺失的层级行及重复粘贴区并重导出；不猜填缺失父级金额，不擅自去重。',
        extra=dict(duplicate_rows_file='source_duplicate_rows.json',hierarchy_file='source_arithmetic_checks.json'))

    candidates=[]
    for path in sorted((BASE/'service').glob('*/report.json')):
        if path.parent.name=='native_budget':
            continue
        report=json.loads(path.read_text())
        sha=(BASE/'service'/path.parent.name/'source.pdf').read_bytes()
        import hashlib
        doc_id=next(d for d,r in INVENTORY.items() if r['sha256']==hashlib.sha256(sha).hexdigest())
        for issue in report.get('issues',[]):
            if issue.get('severity')=='info':
                continue
            rule=issue.get('rule_id');decision='confirmed_source_issue';reason='原页复核与本轮原件问题清单对应；同一问题的多条规则报告需去重。'
            if doc_id=='SH2024NEW_02' and rule=='V33-240':
                decision='false_positive';reason='横向求和漏掉原页明确列示的上级补助收入；补入后相等或仅0.01万元舍入差异。'
            elif doc_id=='SH2024NEW_02' and rule=='V33-235':
                decision='false_positive';reason='原段开头没有本年决算0.00声明，0是年初预算；将年初预算误作本年支出，不能与3,631.14万元决算直接判矛盾。'
            elif doc_id=='SH2024NEW_06' and rule=='CMM-007':
                decision='false_positive';reason='将出国费241.15与车辆增额58.59及增长30.51%混用；正确车辆250.60，250.60减58.59为192.01，增长30.51%。'
            elif doc_id=='SH2024NEW_09' and rule=='CMM-007':
                decision='false_positive';reason='将出国费123.99与车辆增额50.02及增长18.06%混用；正确车辆326.94，326.94减50.02为276.92，增长18.06%。'
            elif doc_id=='SH2024NEW_10' and rule=='V33-240':
                reason='确认源表层级不完整和重复行；差额作为结构复核线索，不能直接定性为总收入金额错误。'
            candidates.append(dict(doc_id=doc_id,engine_issue_id=issue.get('id'),rule_id=rule,title=issue.get('title'),
                page=(issue.get('location') or {}).get('page'),message=issue.get('message'),decision=decision,reason=reason))
    assert sum(r['decision']=='false_positive' for r in candidates)==10
    assert len(findings)==11
    (BASE/'confirmed_source_issues.json').write_text(json.dumps(findings,ensure_ascii=False,indent=2))
    (BASE/'engine_candidate_review.json').write_text(json.dumps(candidates,ensure_ascii=False,indent=2))
    with (BASE/'原件问题清单.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['issue_id','rule_id','organization','year','severity','page','title','snippet','actual','expected','difference','suggestion','bbox']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in findings:
            writer.writerow({k:json.dumps(row[k],ensure_ascii=False) if isinstance(row[k],(dict,list)) else row[k] for k in fields})
    print('CONFIRMED_SOURCE_ISSUES',len(findings),'ENGINE_NONINFO',len(candidates),'FALSE_POSITIVES',sum(r['decision']=='false_positive' for r in candidates))
    print('SEVERITY',dict(Counter(r['severity'] for r in findings)))


if __name__=='__main__':
    main()
