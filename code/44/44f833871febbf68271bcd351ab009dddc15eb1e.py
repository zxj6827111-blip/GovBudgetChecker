"""独立检查新加字形间距分段的相邻边界；原件先生成，后裁框。"""
from pathlib import Path
import copy,json,sys
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
CAND=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
sys.path.insert(0,str(CAND))
p=ROOT/'scripts/independent_boundary_controls.py'
code=p.read_text().split('\nresults=[test(')[0]
code=code.replace("pdf_unit in ['元','万元','亿元']", "any('\\u4e00' <= c <= '\\u9fff' for c in pdf_unit)")
ns={'__file__':str(p),'__name__':'fixture_only'};exec(compile(code,str(p),'exec'),ns)
h=ns['h'];test=ns['test'];fitz=ns['fitz']
def crop(f):
    with fitz.open(f.pdf) as d:
        for fact,x in zip(f.ev['samples'][0]['facts'],[150,250,350],strict=True):
            literal=fact['source_cell']['raw_value']
            box=next(b for b in d[0].search_for(literal) if x<=b.x0<x+40)
            fact['quote']=literal;fact['source_cell']['cell']={'page':1,'quote':literal,'bbox':list(box)}
    f.seal_contract()
results=[]
for spaces in [0,1,2,3,4]:
    gap=' '*spaces
    for name,notate in [('negative',lambda v:'-'+gap+v),('parentheses',lambda v:'('+gap+v+gap+')'),('exponent',lambda v:v+gap+'e3')]:
        results.append(test(f'space_{name}_{spaces}',raws=tuple(notate(v) for v in ['10.00','6.00','4.00']),claimed_raws=('10.00','6.00','4.00'),mutate=crop))
    results.append(test(f'space_extended_unit_{spaces}',pdf_unit='百'+gap+'万元',claimed_unit='万元'))
results.append(test('adjacent_plain_positive',want='PASS'))
results.append(test('adjacent_signed_positive',want='PASS',raws=('-10.00','-6.00','-4.00')))
results.append(test('spaced_signed_complete_positive',want='PASS',raws=('-   10.00','-   6.00','-   4.00'),claimed_raws=('-10.00','-6.00','-4.00')))
# 明确授权交叉配对是合法正控；旧key与旧摘要错配才是负控。
def cross_approved(f):
    a=copy.deepcopy(f.bs['review_report']['issues'][0]);b={**copy.deepcopy(a),'id':'display-2','title':'Second','message':'Second original'}
    na={**copy.deepcopy(a),'id':'display-2','message':'First updated'};nb={**copy.deepcopy(b),'id':'display-1','message':'Second updated'}
    f.bs['review_report']['issues']=[a,b];f.cs['review_report']['issues']=[nb,na]
    for old,new in [(a,nb),(b,na)]:f.decide('issue_changed',list(h.formal_identity(old)),old,new)
results.append(test('explicit_cross_authorization_legal',want='PASS',mutate=cross_approved))
(ROOT/'receipts/neighbor_effect.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print(json.dumps([{k:r[k] for k in ['case','expected','actual','expectation_met']} for r in results],ensure_ascii=False,indent=2))

zero=ns['narrative_zero_sources'];org='上海市甲局';head='七、财政拨款“三公”经费支出决算情况说明。';claim=org+'2024年度无“三公”经费财政拨款支出。'
narr=[]
for name,pages,want in [('qualifier_through_grid',[head+'假设','合计\n预算数\n0',claim],False),('neutral_grid',[head,'合计\n预算数\n0',claim],True),('qualifier_through_blank_page',[head+'假设','',claim],False),('neutral_bare_heading',[head+'具体情况如下',claim],True),('explicit_period_reset',[head+'假设前例。',claim],True)]:
    doc=SimpleNamespace(page_texts=pages,fact_context=SimpleNamespace(org_name=org,fiscal_year=2024),report_kind='final')
    refs=zero(doc,table_code='FIN_07_three_public',indicator='vehicle_purchase',stage='决算')
    narr.append({'case':name,'pages':pages,'expected':want,'actual':bool(refs),'expectation_met':want==bool(refs),'refs':refs})
(ROOT/'receipts/neighbor_narrative.json').write_text(json.dumps(narr,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(narr,ensure_ascii=False,indent=2))
