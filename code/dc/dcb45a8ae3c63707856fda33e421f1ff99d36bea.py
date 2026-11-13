"""以原件裁定金额直接勾稽，不把系统生成的业务问题作为真值。"""
from collections import Counter, defaultdict
from decimal import Decimal
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent


def name(value):
    return re.sub(r'\s+', '', str(value or ''))


def main():
    checks = []
    structures = []
    duplicate_rows = []
    for doc in json.loads((BASE / 'inventory.json').read_text()):
        folder = BASE / 'documents' / doc['doc_id']
        truth = json.loads((folder / 'source_amount_truth.json').read_text())
        oracle = json.loads((folder / 'source_oracle.json').read_text())
        values = []
        groups = defaultdict(dict)
        for idx, row in enumerate(truth):
            item = dict(row, truth_id=f"{doc['doc_id']}:{idx:05}", value=Decimal(row['raw_text'].replace(',', '')))
            values.append(item)
            identity = row['expected']
            key = (row['table_code'], row['page'], str(row['oracle_table']), row['source_row'], identity['subject_code'], identity['subject_name'])
            if identity['indicator']:
                groups[key][identity['indicator']] = item

        def relation(rule, title, left, right):
            if not left or not right or any(v is None for v in right):
                checks.append(dict(doc_id=doc['doc_id'], filename=doc['filename'],rule_id=rule,title=title,
                                   status='insufficient_data',reason='原页没有唯一、同口径且完整的比较项，未以空白补零'))
                return
            expected = sum((v['value'] for v in right), Decimal(0))
            delta = left['value'] - expected
            status = 'pass' if not delta else ('within_tolerance' if abs(delta) <= Decimal('.5') else 'difference')
            checks.append(dict(doc_id=doc['doc_id'],filename=doc['filename'],rule_id=rule,title=title,
                status=status,actual=str(left['value']),expected=str(expected),difference=str(delta),
                pages=sorted({v['page'] for v in [left,*right]}),source_truth_ids=[v['truth_id'] for v in [left,*right]],
                evidence=[dict(page=v['page'],bbox=v['bbox'],amount=v['raw_text'],subject=v['expected']['subject_name'],indicator=v['expected']['indicator']) for v in [left,*right]],
                note='0.5万元默认容差内只记录差异，不判定错误；空白和缺项不视为0'))

        for key, row in groups.items():
            table, page, oracle_table, source_row, code, subject = key
            if table=='FIN_02_income':
                fields=['fiscal_allocation','superior_subsidy_income','business_income','operational_income','subsidiary_income','other_income']
                relation('D-002-ROW',f'收入合计与全部六类来源：{code or subject}',row.get('total_actual'),[row.get(v) for v in fields])
            elif table=='FIN_03_expenditure':
                fields=['basic_actual','project_actual','to_superior_actual','operational_actual','subsidiary_actual']
                relation('D-003-ROW',f'支出合计与全部五类支出：{code or subject}',row.get('total_actual'),[row.get(v) for v in fields])
            elif table=='FIN_05_general_public_expenditure':
                relation('D-005-ROW',f'一般公共预算支出=基本+项目：{code or subject}',row.get('total_actual'),[row.get('basic_actual'),row.get('project_actual')])

        def find(table, indicator, subject=None, code=None, stage=None):
            found=[v for v in values if v['table_code']==table and v['expected']['indicator']==indicator
                   and (subject is None or name(v['expected']['subject_name'])==subject)
                   and (code is None or v['expected']['subject_code']==code)
                   and (stage is None or v['expected']['budget_stage']==stage)]
            return found[0] if len(found)==1 else None

        summary = 'FIN_01_income_expenditure_total'
        relation('D-001','收支总表支出总计=本年支出+结余分配+年末结余',
                 find(summary,'expenditure_actual','总计'),
                 [find(summary,'expenditure_actual','本年支出合计'),find(summary,'expenditure_actual','结余分配'),find(summary,'expenditure_actual','年末结转和结余')])
        relation('D-002-CROSS','收入决算表合计=收支总表本年收入',find('FIN_02_income','total_actual',code='total'),[find(summary,'income_actual','本年收入合计')])
        relation('D-003-CROSS','支出决算表合计=收支总表本年支出',find('FIN_03_expenditure','total_actual',code='total'),[find(summary,'expenditure_actual','本年支出合计')])
        relation('D-006','一般公共预算基本支出=人员经费+公用经费',
                 find('FIN_05_general_public_expenditure','basic_actual',code='total'),
                 [find('FIN_06_basic_expenditure','total_actual','人员经费合计'),find('FIN_06_basic_expenditure','total_actual','公用经费合计')])
        relation('D-006-PERSONNEL','人员经费合计=工资福利支出+对个人和家庭的补助',
                 find('FIN_06_basic_expenditure','total_actual','人员经费合计'),
                 [find('FIN_06_basic_expenditure','total_actual','工资福利支出'),find('FIN_06_basic_expenditure','total_actual','对个人和家庭的补助')])
        for stage in ['年初预算','决算']:
            table='FIN_07_three_public'
            relation('D-007-THREE',f'三公合计=出国+车辆+接待（{stage}）',find(table,'total_actual',stage=stage),
                     [find(table,k,stage=stage) for k in ['overseas','vehicle_purchase_operation','reception']])
            relation('D-007-VEHICLE',f'车辆小计=购置+运行（{stage}）',find(table,'vehicle_purchase_operation',stage=stage),
                     [find(table,k,stage=stage) for k in ['vehicle_purchase','vehicle_operation']])

        # 同一表内按原页明确编码层级求和，不按数字位置猜父子。
        for table in ['FIN_02_income','FIN_03_expenditure','FIN_05_general_public_expenditure']:
            indicators=sorted({v['expected']['indicator'] for v in values if v['table_code']==table})
            for indicator in indicators:
                coded={v['expected']['subject_code']:v for v in values if v['table_code']==table and v['expected']['indicator']==indicator and re.fullmatch(r'\d{3}(?:\d{2}){0,2}',str(v['expected']['subject_code'] or ''))}
                total=find(table,indicator,code='total')
                classes=[v for code,v in coded.items() if len(code)==3]
                if total and classes:
                    relation('D-HIERARCHY',f'{table}/{indicator}合计与类级科目',total,classes)
                    roots={code for code in coded if len(code)==3}
                    orphan_codes=sorted({code for code in coded if len(code)>3 and code[:3] not in roots})
                    if orphan_codes and checks[-1]['status']=='difference':
                        checks[-1]['status']='hierarchy_incomplete'
                        checks[-1]['orphan_codes']=orphan_codes
                        checks[-1]['note']='原件存在只有下级、没有类级合计的分支；差额不能直接解释为总收入错误，需复核源表完整性。'
                for code,parent in coded.items():
                    children=[v for child,v in coded.items() if len(child)==len(code)+2 and child.startswith(code)]
                    if children:
                        relation('D-HIERARCHY',f'{table}/{indicator}/{code}与直接下级',parent,children)

        repeated=defaultdict(list)
        for key,row in groups.items():
            table,page,oracle_table,source_row,code,subject=key
            if not code or code=='total':
                continue
            signature=(table,code,name(subject),tuple(sorted((k,str(v['value'])) for k,v in row.items())))
            repeated[signature].append(dict(page=page,oracle_table=oracle_table,source_row=source_row,
                 evidence=[dict(page=v['page'],bbox=v['bbox'],amount=v['raw_text'],indicator=k) for k,v in row.items()]))
        for signature,appearances in repeated.items():
            if len(appearances)>1:
                duplicate_rows.append(dict(doc_id=doc['doc_id'],filename=doc['filename'],table=signature[0],
                    subject_code=signature[1],subject_name=signature[2],amounts=dict(signature[3]),appearances=appearances,
                    note='同科目、同指标金额在不同原页行重复出现，须核对是源表重复粘贴还是有明确口径分组，不擅自去重。'))

        full='\n'.join(p['text'] for p in oracle['pages'])
        core={r['source_core_code'] for r in json.loads((BASE/'source_region_overlay.json').read_text())['records'] if r['doc_id']==doc['doc_id']}
        structures.append(dict(doc_id=doc['doc_id'],filename=doc['filename'],year=2024,
            cover=oracle['pages'][0]['text'],core_tables=sorted(core),core_types_complete=len(core)==9,
            three_public_text_present='三公' in full,performance_text_present='绩效' in full,
            native_text_all_pages=all(p['text'].strip() for p in oracle['pages']),
            caveat='存在关键词不等于全部公开义务或绩效附件齐全，未以目录代替正文核心表'))
    for filename,data in [('source_arithmetic_checks.json',checks),('source_structure_checks.json',structures)]:
        (BASE/filename).write_text(json.dumps(data,ensure_ascii=False,indent=2))
    (BASE/'source_duplicate_rows.json').write_text(json.dumps(duplicate_rows,ensure_ascii=False,indent=2))
    counts=Counter(r['status'] for r in checks)
    (BASE/'source_arithmetic_summary.json').write_text(json.dumps(dict(checks=len(checks),statuses=dict(counts)),ensure_ascii=False,indent=2))
    print('CHECKS',len(checks),dict(counts))
    for row in checks:
        if row['status']=='difference':print(row['doc_id'],row['rule_id'],row['title'],row['actual'],row['expected'],row['difference'])


if __name__=='__main__':
    main()
