"""独立追加边界。只写本复验目录，保留原27+9脚本及预期。"""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
CAND = ROOT.parents[1]
sys.path.insert(0, str(CAND))
original = ROOT / 'scripts/independent_boundary_controls.py'
namespace = {'__file__': str(original), '__name__': 'independent_fixture_only'}
fixture_code = original.read_text().split('\nresults=[test(')[0]
# 新测试的扩展中文单位也必须真正印在原页；仅调整新增夹具字体选择。
fixture_code = fixture_code.replace("pdf_unit in ['元','万元','亿元']", "any('\\u4e00' <= ch <= '\\u9fff' for ch in pdf_unit)")
exec(fixture_code, namespace)
test, h = namespace['test'], namespace['h']
zero_sources = namespace['narrative_zero_sources']
results = []

def cropped_amount(f):
    # 原件为会计负数 (6.00)；仅框数字不构成正6。
    for fact in f.ev['samples'][0]['facts']:
        raw = fact['source_cell']['raw_value']
        fact['quote'] = raw
        fact['source_cell']['cell']['quote'] = raw
        with namespace['fitz'].open(f.pdf) as doc:
            boxes = doc[0].search_for(raw)
            fact['source_cell']['cell']['bbox'] = list(next(b for b in boxes if abs(b.x0 - {'T':150,'B':250,'P':350}[fact['fact_id']]) < 15))
    f.seal_contract()

for name, kwargs in [
    ('extra_unit_legal_wan', {'pdf_unit':'万元','want':'PASS'}),
    ('extra_unit_legal_yi', {'pdf_unit':'亿元','want':'PASS'}),
    ('extra_unit_suffix_in_million', {'pdf_unit':'百万元','claimed_unit':'万元'}),
    ('extra_unit_suffix_in_thousand_wan', {'pdf_unit':'千万元','claimed_unit':'万元'}),
    ('extra_accounting_parentheses_crop', {'raws':('(10.00)','(6.00)','(4.00)'), 'claimed_raws':('10.00','6.00','4.00'), 'mutate':cropped_amount}),
    ('extra_scientific_mantissa_crop', {'raws':('10.00e3','6.00e3','4.00e3'), 'claimed_raws':('10.00','6.00','4.00'), 'mutate':cropped_amount}),
]:
    results.append(test(name, **kwargs))

def repetitions(f, illegal=None):
    f.cr['receipts'][0].update(operand_fact_ids=['T','B','B'], required_fact_ids=['T','B','B'])
    f.seal_contract()
    c=f.ev['samples'][0]['relation_contracts'][0]
    del c['operand_identities']['P']
    c['operand_bindings']=[{'role':'total','fact_id':'T'},{'role':'part','fact_id':'B'},{'role':'part','fact_id':'B'}]
    if illegal == 'count': f.cr['receipts'][0]['operand_fact_ids']=['T','B']
    elif illegal == 'role': c['operand_bindings'][1]['role']='total'
    elif illegal == 'required': f.cr['receipts'][0]['required_fact_ids']=['T','B','P']
    c['receipt_digest']=h.digest(f.cr['receipts'][0])
for kind in [None,'count','role','required']:
    results.append(test('extra_repetition_'+str(kind),want='PASS' if kind is None else 'FAIL',raws=('12.00','6.00','4.00'),mutate=lambda f,k=kind:repetitions(f,k)))

def swap(f, wrong=None):
    a=copy.deepcopy(f.bs['review_report']['issues'][0])
    b={**copy.deepcopy(a),'id':'display-2','title':'Second','message':'Second original'}
    f.bs['review_report']['issues']=[a,b]
    na={**copy.deepcopy(a),'id':'display-2','message':'First updated'}
    nb={**copy.deepcopy(b),'id':'display-1','message':'Second updated'}
    f.cs['review_report']['issues']=[nb,na]
    for old,new in [(a,na),(b,nb)]:
        d=f.decide('issue_changed',list(h.formal_identity(old)),old,new)
        if wrong == 'cross_old_key': d['key']=list(h.formal_identity(b if old is a else a))
        elif wrong == 'missing': f.ev['decisions'].pop()
for kind in [None,'cross_old_key','missing']:
    results.append(test('extra_swapped_pair_'+str(kind),want='PASS' if kind is None else 'FAIL',mutate=lambda f,k=kind:swap(f,k)))

org='上海市甲局'
claim=f'{org}2024年度无“三公”经费财政拨款支出'
head='七、财政拨款“三公”经费支出决算情况说明。'
narratives=[]
for name,pages,want in [
    ('qualifier_page_colon',[head+'以下表述不成立：',claim+'。'],False),
    ('qualifier_page_no_punctuation',[head+'假设',claim+'。'],False),
    ('qualifier_semicolon',[head+'以下表述不成立；'+claim+'。'],False),
    ('qualifier_colon_semicolon',[head+'以下表述不成立：；'+claim+'。'],False),
    ('sentence_resets_qualifier',[head+'以下表述不成立：前例。'+claim+'。'],True),
    ('neutral_page',[head+'具体情况如下：',claim+'。'],True),
    ('neutral_list',[head+'具体情况如下：1、因公出国（境）费支出0万元。'],True),
    ('neutral_false_friend',[head+'若具体情况如下：1、因公出国（境）费支出0万元。'],False),
]:
    doc=SimpleNamespace(page_texts=pages,fact_context=SimpleNamespace(org_name=org,fiscal_year=2024),report_kind='final')
    indicator='overseas' if name.startswith('neutral_list') or name=='neutral_false_friend' else 'vehicle_purchase'
    refs=zero_sources(doc,table_code='FIN_07_three_public',indicator=indicator,stage='决算')
    narratives.append({'case':name,'pages':pages,'expected':want,'actual':bool(refs),'expectation_met':bool(refs)==want,'references':refs})

from scripts import verify_pending_review as gov
import jsonschema
schema=json.loads((CAND/'docs/unified_remediation_20261106/scope_review_v2.schema.json').read_text())
jsonschema.Draft202012Validator.check_schema(schema)
row={'sample_id':'S','sha256':'a'*64,'obligations':{'O':{'status':'insufficient_data'}},'receipts':[{'obligation_id':'O','rule_id':'R','sub_check_id':'x','status':'not_applicable','incomplete_reason':'独立原件未声明比例','formula':'a/b','reason_codes':['not_triggered']}]}
r=row['receipts'][0]
d={'sample_id':'S','pdf_sha256':'a'*64,'key':['O','R','x'],'scope_review':'APPROVED_OBJECT_SCOPE_ONLY','diagnostic_scope_decision':'explanation_only','approved_case_id':gov.receipt_scope_id(row,r),'approved_semantic_digest':gov.scope_semantic_digest(r),'approved_status':r['status'],'approved_reason':r['incomplete_reason'],'reason':'独立精确正控','sources':[{'page':1,'quote':'independent fixture'}]}
review={'version':'independent-diagnostic-scope-review-v2','decisions':[d]}
jsonschema.validate(review,schema)
p=ROOT/'receipts/synthetic_scope_v2.json'
p.write_text(json.dumps(review,ensure_ascii=False))
applied=gov.apply_scope_review({'entries':[]},{'S':row},p,gov.sha256_file(p))
scopes=[{'case':'scope_exact_positive','expectation_met':len(applied['entries'])==1 and not gov.diagnostic_scope(row,applied)[1]}]
for field,value in [('incomplete_reason','新理由'),('formula','a/(b+c)'),('reason_codes',['parse_error']),('status','fail')]:
    changed=copy.deepcopy(row);changed['receipts'][0][field]=value
    reapply=gov.apply_scope_review({'entries':[]},{'S':changed},p,gov.sha256_file(p))
    scopes.append({'case':'scope_mutated_'+field,'expectation_met':not reapply['entries'] and bool(gov.diagnostic_scope(changed,applied)[1])})
for field in ['approved_case_id','approved_semantic_digest','approved_status','approved_reason']:
    bad=copy.deepcopy(review);del bad['decisions'][0][field]
    try: jsonschema.validate(bad,schema);rejected=False
    except jsonschema.ValidationError: rejected=True
    scopes.append({'case':'schema_requires_'+field,'expectation_met':rejected})
for name,value in [('extra_effect',results),('extra_narrative',narratives),('scope_v2_controls',scopes)]:
    (ROOT/'receipts'/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({name:[{k:v for k,v in item.items() if k in ['case','expected','actual','expectation_met']} for item in value] for name,value in [('effect',results),('narrative',narratives),('scope',scopes)]},ensure_ascii=False,indent=2))
