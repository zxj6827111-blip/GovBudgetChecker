"""以先冻结的独立原页数字为分母，检查读取和科目/列绑定。"""
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import unicodedata

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
MONEY = re.compile(r'-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,4})?')
CODES = {n: code for n, code in zip(('01','02','03','04','05','06','07','08','09'), (
    'FIN_01_income_expenditure_total', 'FIN_02_income', 'FIN_03_expenditure',
    'FIN_04_fiscal_grant_total', 'FIN_05_general_public_expenditure', 'FIN_06_basic_expenditure',
    'FIN_07_three_public', 'FIN_08_gov_fund', 'FIN_09_state_capital'))}


def compact(s):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(s or ''))).replace('，', ',')


def digits(s):
    return compact(s).replace(',', '')


def contains(box, small, margin=.8):
    if not box or not small:
        return False
    x, y = (small[0]+small[2])/2, (small[1]+small[3])/2
    return box[0]-margin <= x <= box[2]+margin and box[1]-margin <= y <= box[3]+margin


def write(p, v):
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n')


def source_identity(code, row, col, ncols, schema=None):
    indicator = stage = subject_code = None
    if code in ('02','03','05','08','09'):
        maps = {
            '02': ['total_actual','fiscal_allocation','superior_subsidy_income','business_income','operational_income','subsidiary_income','other_income'],
            '03': ['total_actual','basic_actual','project_actual','to_superior_actual','operational_actual','subsidiary_actual'],
            '05': ['total_actual','basic_actual','project_actual'],
            '08': ['beginning_balance','income_actual','total_actual','basic_actual','project_actual','ending_balance'],
            '09': ['beginning_balance','income_actual','total_actual','basic_actual','project_actual','ending_balance'],
        }
        start = min(schema) if schema else ncols - len(maps[code])
        labels = row[:start]
        if schema is not None:
            indicator = schema.get(col)
        elif 0 <= col-start < len(maps[code]):
            indicator = maps[code][col-start]
    elif code == '06':
        half = ncols // 2
        start = 0 if col < half else half
        labels = row[start:col]
        indicator = 'total_actual'
    elif code in ('01','04'):
        labels = row[:1] if col == 1 else row[2:3]
        if code == '01':
            indicator = 'income_actual' if col == 1 else 'expenditure_actual'
        else:
            indicator = {1:'income_actual',3:'expenditure_actual',4:'general_public_budget',5:'government_fund_budget',6:'state_capital_budget'}.get(col)
    elif code == '07':
        # 上海样本原表统一为 6 组预算数/决算数；以原列顺序定义独立预期。
        measures = ['total_actual','overseas','vehicle_purchase_operation','vehicle_purchase','vehicle_operation','reception']
        indicator = measures[col//2] if col//2 < len(measures) else None
        stage = '年初预算' if col % 2 == 0 else '决算'
        return dict(subject_code='total', subject_name='合计', indicator=indicator, budget_stage=stage)
    else:
        labels = []
    codes = [compact(s) for s in labels if re.fullmatch(r'\d{1,7}', compact(s))]
    names = [compact(s) for s in labels if compact(s) and not re.fullmatch(r'\d{1,7}', compact(s)) and compact(s) not in ('类','款','项')]
    name = ''.join(names) or None
    if codes:
        subject_code = ''.join(codes)
    elif name and '合计' in name and code not in ('01','04'):
        subject_code = 'total'
    # 双边总表的 indicator 表示收入/支出物理列，具体余额项目保留在 subject_name。
    # 不能把项目名称和列指标混为一谈而制造验收误报。
    return dict(subject_code=subject_code, subject_name=name, indicator=indicator, budget_stage=stage)


def source_amount_column(code, column, ncols, schema=None):
    if schema is not None:
        return column in schema
    # 独立原表声明的金额车道，整数也计入；编码和行次不作为金额。
    counts = {'02': 7, '03': 6, '05': 3, '08': 6, '09': 6}
    if code in counts:
        return ncols - counts[code] <= column < ncols
    if code == '06':
        return column in (ncols // 2 - 1, ncols - 1)
    if code == '01':
        return column in (1, 3)
    if code == '04':
        return column in (1, 3, 4, 5, 6)
    return code == '07' and ncols == 12


def source_columns(code, table):
    if code == '02' and table['col_count'] < 9:
        declarations = {'本年收入合计':'total_actual','财政拨款收入':'fiscal_allocation',
                        '上级补助收入':'superior_subsidy_income','事业收入':'business_income',
                        '经营收入':'operational_income','附属单位上缴收入':'subsidiary_income','其他收入':'other_income'}
        return {column: declarations[compact(value)] for row in table['rows'][:2]
                for column,value in enumerate(row) if compact(value) in declarations}
    return None


def oracle_amounts(oracle):
    results = []
    regions = []
    regional = {(r['page'],r['table_index']):r['source_core_code'] for r in json.loads((BASE/'source_region_overlay.json').read_text())['records'] if r['doc_id']==oracle['doc_id']}
    for page in oracle['pages']:
        code = page['source_core_code']
        if not code and not any(key[0]==page['page'] for key in regional):
            continue
        consumed = set()
        for table in page['tables']:
            if 'rows' not in table:
                continue
            code = regional.get((page['page'],table['index']),page['source_core_code'])
            if not code:
                continue
            schema = source_columns(code, table)
            regions.append(dict(page=page['page'], code=code, table_index=table['index'], bbox=table['bbox'],
                                rows=table['row_count'], columns=table['col_count']))
            for ri, row in enumerate(table['rows']):
                for ci, value in enumerate(row):
                    literal = compact(value)
                    if not MONEY.fullmatch(literal) or not source_amount_column(code, ci, table['col_count'], schema):
                        continue
                    box = table['cell_boxes'][ri][ci]
                    if not box:
                        continue
                    # 使用独立字符/词的真实位置作为匹配锚点，避免网格跨线造成误配。
                    tokens = [a for a in page['amounts'] if contains(box, a['bbox'])]
                    consumed.update(a['word_idx'] for a in tokens)
                    words = [w for w in page['words'] if contains(box, w[:4])]
                    box_text = compact(''.join(w[4] for w in words))
                    actual_box = [min(w[0] for w in words), min(w[1] for w in words),
                                  max(w[2] for w in words), max(w[3] for w in words)] if words else box
                    results.append(dict(page=page['page'], table_code=CODES[code], oracle_table=table['index'],
                                        source_row=ri, source_col=ci, raw_text=literal, bbox=actual_box,
                                        source_cell_bbox=box, source_row_text=row, oracle_cell_text=value,
                                        source_words_agree=digits(box_text)==digits(literal),
                                        expected=source_identity(code, row, ci, table['col_count'], source_columns(code, table)),
                                        oracle_method='independent_grid_and_native_words'))
            # 单格中存在多笔数字时不拼接；未被单格真值接收的词在下方逐笔计入。
        for amount in page['amounts']:
            if amount['word_idx'] in consumed:
                continue
            locator = []
            for table in page['tables']:
                for ri, row in enumerate(table.get('rows', [])):
                    for ci, box in enumerate(table['cell_boxes'][ri]):
                        if box and contains(box, amount['bbox']):
                            locator.append((table, ri, ci, row))
            expected = dict(subject_code=None, subject_name=None, indicator=None, budget_stage=None)
            if len(locator) == 1:
                table, ri, ci, row = locator[0]
                expected = source_identity(code, row, ci, table['col_count'], source_columns(code, table))
            results.append(dict(page=page['page'], table_code=CODES[code], oracle_table=amount['table_indices'],
                                source_row=locator[0][1] if len(locator)==1 else None,
                                source_col=locator[0][2] if len(locator)==1 else None,
                                raw_text=amount['raw_text'], bbox=amount['bbox'],
                                source_cell_bbox=locator[0][0]['cell_boxes'][locator[0][1]][locator[0][2]] if len(locator)==1 else None,
                                source_row_text=locator[0][3] if len(locator)==1 else None,
                                source_words_agree=True, expected=expected,
                                oracle_method='native_word_unresolved_grid'))
    return results, regions


def main():
    inventory = json.loads((BASE / 'inventory.json').read_text())
    adjudications = json.loads((BASE / 'adjudications.json').read_text())
    manual_pages = {(a['doc_id'],a['page']): a['reason'] for a in adjudications['unresolved_source_identity_pages']}
    adjudicated = {r['truth_id']: r for r in json.loads((BASE / 'identity_adjudication/source_identity_adjudications.json').read_text()) if r['status'] == 'source_confirmed'}
    overlay_path = BASE / 'source_truth_overlay.json'
    overlays = {r['truth_id']:r for r in json.loads(overlay_path.read_text())['overlays']} if overlay_path.exists() else {}
    per_doc = []
    issues = []
    all_rows = []
    for doc in inventory:
        out = BASE / 'documents' / doc['doc_id']
        oracle = json.loads((out / 'source_oracle.json').read_text())
        artifact = json.loads((out / 'artifact.json').read_text())
        facts = json.loads((out / 'facts.json').read_text())
        admission = {r['fact_uid']: r['reason'] for r in json.loads((out / 'fact_admission.json').read_text())}
        amounts, regions = oracle_amounts(oracle)
        for idx, amount in enumerate(amounts):
            overlay = overlays.get(f"{doc['doc_id']}:{idx:05}")
            if overlay:
                if 'raw_text' in overlay:
                    amount['source_grid_amount_before_native_audit'] = amount['raw_text']
                    amount['raw_text'] = overlay['raw_text']
                amount['expected'].update(overlay.get('expected',{}))
                amount['source_only_adjudication'] = overlay['reason']
            identity = adjudicated.get(f"{doc['doc_id']}:{idx:05}")
            if identity:
                amount['expected'] = {**amount['expected'], 'subject_code': identity['subject_code'], 'subject_name': identity['subject_name']}
                amount['identity_adjudication'] = identity['method']
        write(out / 'source_amount_truth.json', amounts)
        cells = defaultdict(list)
        refs = defaultdict(list)
        physical_ids = set()
        logical_codes = defaultdict(set)
        for logical in artifact['logical_tables']:
            for uid in logical['fragment_uids']:
                logical_codes[uid].add(logical['table_code'])
        for table in artifact['physical_fragments']:
            if table['extraction_method'] == 'page_text_fallback':
                continue
            for cell in table['cells']:
                cid = f"{table['fragment_uid']}:r{cell['row_idx']}c{cell['col_idx']}"
                physical_ids.add(cid)
                if cell['numeric_value'] is not None:
                    cells[cell['page_number']].append((table, cell, cid))
        for fact in facts:
            for ref in fact['extra_dims'].get('evidence_refs', []):
                if ref.get('physical_page'):
                    refs[ref['physical_page']].append((fact, ref))
        rows = []
        for idx, source in enumerate(amounts):
            page, box = source['page'], source['bbox']
            possible = [(t,c,cid) for t,c,cid in cells[page] if contains(c.get('bbox'), box)]
            exact = [(t,c,cid) for t,c,cid in possible if digits(c['raw_text']) == digits(source['raw_text'])]
            reading = 'read_exact' if len(exact)==1 else ('ambiguous' if len(exact)>1 else ('wrong_text' if possible else 'missing'))
            fpossible = [(f,r) for f,r in refs[page] if contains(r.get('bbox'), box)]
            fexact = [(f,r) for f,r in fpossible if digits(f.get('raw_value'))==digits(source['raw_text'])]
            status = 'bound_exact' if len(fexact)==1 else ('ambiguous' if len(fexact)>1 else ('wrong_text' if fpossible else 'missing'))
            checks = {}
            expected = source['expected']
            reason = None
            if len(fexact)==1:
                fact, ref = fexact[0]
                checks['table_code'] = fact['table_code']==source['table_code']
                checks['subject_name'] = compact(fact.get('subject_name'))==compact(expected.get('subject_name')) if expected.get('subject_name') else None
                checks['subject_code'] = fact.get('subject_code')==expected['subject_code'] if expected.get('subject_code') else None
                checks['indicator'] = fact['indicator']==expected['indicator'] if expected.get('indicator') else None
                checks['budget_stage'] = fact['budget_stage']==expected['budget_stage'] if expected.get('budget_stage') else None
                checks['physical_cell_id_exists'] = ref.get('source_cell_id') in physical_ids
                checks['file_hash_matches'] = ref['file_hash']==doc['sha256']
                reason = admission.get(fact['fact_uid'])
                if any(v is False for v in checks.values()):
                    status = 'semantic_mismatch'
                elif checks.get('subject_name') is None or checks.get('indicator') is None:
                    status = 'manual_review'
                # 独立网格工具本身有跨线截字/编码挤入下一行的问题；不将其误判为被测系统错误。
                # 尚未逐字裁定的科目身份保持待复核，继续留在总分母。
                if status == 'semantic_mismatch' and f"{doc['doc_id']}:{idx:05}" not in adjudicated and (doc['doc_id'],page) in manual_pages and checks.get('indicator') is not False:
                    status = 'manual_review'
            row = dict(truth_id=f"{doc['doc_id']}:{idx:05}", doc_id=doc['doc_id'], filename=doc['filename'],
                       **source, reading_status=reading, fact_status=status, checks=checks,
                       manual_review_reason=manual_pages.get((doc['doc_id'],page)) if status=='manual_review' else None,
                       fact_admission_rejection=reason,
                       parsed=[dict(table=t['table_code'], row=c['row_idx'], col=c['col_idx'],raw_text=c['raw_text'], source_cell_id=cid,bbox=c['bbox']) for t,c,cid in possible],
                       actual_facts=[dict(fact_uid=f['fact_uid'],subject_code=f['subject_code'],subject_name=f['subject_name'],indicator=f['indicator'],budget_stage=f['budget_stage'],raw_value=f['raw_value'],table_code=f['table_code'],source_header=f['extra_dims'].get('source_header'),source_cell_id=r.get('source_cell_id')) for f,r in fpossible])
            rows.append(row)
            if reading!='read_exact' or status!='bound_exact' or reason:
                issues.append(row)
        coverage=[]
        for region in regions:
            page = region['page']
            tables=[t for t in artifact['physical_fragments'] if t['page_number']==page and
                    (t['table_code']==CODES[region['code']] or CODES[region['code']] in logical_codes[t['fragment_uid']])]
            coverage.append(dict(**region, recognized=bool(tables), parsed_table_count=len(tables)))
        counts = Counter(r['reading_status'] for r in rows)
        bindings = Counter(r['fact_status'] for r in rows)
        result=dict(doc_id=doc['doc_id'],filename=doc['filename'],pages=doc['pages'],
                    source_core_regions=len(regions),recognized_core_regions=sum(c['recognized'] for c in coverage),
                    source_amounts=len(rows),reading=dict(counts),facts=dict(bindings),
                    admitted_and_matched=sum(r['fact_status']=='bound_exact' and not r['fact_admission_rejection'] for r in rows),
                    blocked_by_parser_gate=sum(bool(r['fact_admission_rejection']) for r in rows),
                    oracle_words_disagree=sum(not r['source_words_agree'] for r in rows),
                    source_amount_truth_sha256=hashlib.sha256((out/'source_amount_truth.json').read_bytes()).hexdigest())
        per_doc.append(result)
        write(out / 'comparison.json', rows)
        write(out / 'table_coverage.json', coverage)
        all_rows.extend(rows)
        print(doc['doc_id'],result,flush=True)
    write(BASE / 'comparison_summary.json', per_doc)
    write(BASE / 'issues.json', issues)
    with (BASE/'issues.csv').open('w',encoding='utf-8-sig',newline='') as f:
        keys=['truth_id','filename','page','table_code','raw_text','reading_status','fact_status','fact_admission_rejection','bbox','expected','checks','actual_facts']
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
        for row in issues:
            w.writerow({k:json.dumps(row[k],ensure_ascii=False) if isinstance(row[k],(dict,list)) else row[k] for k in keys})
    summary=dict(pdfs=len(per_doc),pages=sum(d['pages'] for d in per_doc),
                 source_core_regions=sum(d['source_core_regions'] for d in per_doc),
                 recognized_core_regions=sum(d['recognized_core_regions'] for d in per_doc),
                 source_amounts=len(all_rows),reading=dict(Counter(r['reading_status'] for r in all_rows)),
                 facts=dict(Counter(r['fact_status'] for r in all_rows)),
                 admitted_and_matched=sum(d['admitted_and_matched'] for d in per_doc),
                 gate_reasons=dict(Counter(r['fact_admission_rejection'] for r in all_rows if r['fact_admission_rejection'])),
                 oracle_words_disagree=sum(d['oracle_words_disagree'] for d in per_doc),
                 production_code_changed=json.loads((BASE/'code_before.json').read_text()) != json.loads((BASE/'code_after_parse.json').read_text()),
                 scope='native core fiscal tables; source-derived automatic comparator, with visual adjudication required',
                 human_independent_full_truth=False)
    write(BASE / 'comparison_totals.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
