"""独立构造原件/事实和故障；不导入执行者的测试夹具。"""
import copy
from decimal import Decimal
import gzip
import hashlib
import json
import shutil
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import pymupdf as fitz

ROOT=Path(__file__).resolve().parents[1]
CAND=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
sys.path.insert(0,str(CAND))
from scripts import effect_acceptance_v3 as h
from src.engine.narrative_zero_evidence import narrative_zero_sources

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def narrative_controls():
    org='上海市甲局'
    header='七、财政拨款“三公”经费支出决算情况说明。'
    zero=f'{org}2024年度无“三公”经费财政拨款支出'
    cases=[
      ('legal_no_activity',zero+'。',True),
      ('prior_negation',f'不适用{zero}这一表述。',False),
      ('negation_before_colon',f'以下表述不成立：{zero}。',False),
      ('conditional_before_comma',f'若使用旧模板填报，{zero}。',False),
      ('negated_before_comma',f'不能作此认定，{zero}。',False),
      ('positive_purchase_fee_conflict',zero+'。公务用车购置费支出决算为5万元。',False),
      ('positive_operation_conflict',zero+'。公务用车运行维护支出5万元。',False),
      ('positive_dual_total_conflict',zero+'。“三公”经费财政拨款支出年初预算为5万元，支出决算为4万元。',False),
      ('positive_other_indicator_conflict',zero+'。公务接待费支出决算为5万元。',False),
    ]
    out=[]
    for name,sentence,want in cases:
        doc=SimpleNamespace(page_texts=[header+sentence],fact_context=SimpleNamespace(org_name=org,fiscal_year=2024),report_kind='final')
        refs=narrative_zero_sources(doc,table_code='FIN_07_three_public',indicator='vehicle_purchase',stage='决算')
        out.append({'case':name,'text':header+sentence,'expected_zero_source':want,'actual_zero_source':bool(refs),
                    'expectation_met':bool(refs)==want,'references':refs})
    return out

def write_collection(root,record,status,sha):
    (root/'records').mkdir(parents=True,exist_ok=True);(root/'status').mkdir(exist_ok=True)
    for p,data in [(root/'records/I.json.gz',record),(root/'status/I.status.json.gz',status)]:
        with gzip.open(p,'wt')as f:json.dump(data,f)
    meta={'samples':[{'sample_id':'I','sha256':sha}], 'code':{'digest':digest('independent-code')},
          'code_after':digest('independent-code'),'profile':{'OCR_ENABLED':'false'}}
    (root/'meta.json').write_text(json.dumps(meta))

class Fixture:
    def __init__(self,folder,raws=('10.00','6.00','4.00'),pdf_unit='wan',claimed_unit=None,claimed_raws=None):
        self.root=folder;self.base=folder/'before';self.cand=folder/'after';self.kit=folder/'kit'
        (self.kit/'pdfs/I').mkdir(parents=True)
        self.pdf=self.kit/'pdfs/I/original.pdf'
        # 金额、行列及单位先确定于原件，随后建立独立事实，不从回执反向产生事实池。
        with fitz.open()as d:
            p=d.new_page();p.insert_text((45,35),'Fiscal table')
            p.insert_text((125,35),pdf_unit,fontname='china-s' if pdf_unit in ['元','万元','亿元']else'helv')
            p.insert_text((45,100),'All')
            for x,label,val in zip((150,250,350),('Total','Basic','Project'),raws,strict=True):
                p.insert_text((x,65),label);p.insert_text((x,100),val)
            p.insert_text((450,65),'Reserve');p.insert_text((450,100),'6.00')
            d.save(self.pdf)
        self.sha=hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        old={'obligation_id':'O','rule_id':'R','sub_check_id':'sum','status':'insufficient_data',
             'reason_codes':['operand_missing'],'next_action':'tech_fix','operand_fact_ids':[],
             'required_fact_ids':['T','B','P'],'formula':'total = sum(parts) (万元)'}
        new={**copy.deepcopy(old),'status':'pass','reason_codes':[],'next_action':None,'operand_fact_ids':['T','B','P']}
        issue={'id':'display-1','rule_id':'R','severity':'high','title':'Source issue','message':'Original source issue',
               'page_number':1,'check_relation_id':'R:sum','evidence':[{'page':1,'quote':raws[0]}],
               'location':{'page':1},'bbox':None}
        status={'checksum':self.sha,'analysis_revision':'before-analysis','organization_name':'上海市甲局',
                'fiscal_year':2024,'report_kind':'final','result':{'meta':{'rule_policy_version':'v3_3_portable','rule_registry_version':'independent-v2'}},
                'review_report':{'issues':[issue],'candidate_findings':[],'tips':[],'manual_review':[],'check_receipts':[old]}}
        self.bs=copy.deepcopy(status);self.cs=copy.deepcopy(status)
        self.cs.update(analysis_revision='after-analysis');self.cs['review_report']['check_receipts']=[new]
        self.br={'sample_id':'I','sha256':self.sha,'receipts':[old],'run':{},'findings':[],'finding_bodies':{},'obligations':{}}
        self.cr={**copy.deepcopy(self.br),'receipts':[new]}
        self.save()
        before=h.load_collection(self.base)['I']['source_binding'];after=h.load_collection(self.cand)['I']['source_binding']
        self.before=before;self.after=after
        self.ev={'schema':'independent-effect-evidence-v2','samples':[{'binding':after,'facts':[],'relation_contracts':[]}],'decisions':[]}
        unit=claimed_unit or pdf_unit
        vals=claimed_raws or raws
        factors={'wan':Decimal(1),'yuan':Decimal('.0001'),'万元':Decimal(1),'元':Decimal('.0001'),'亿元':Decimal(10000)}
        with fitz.open(self.pdf)as d:
            page=d[0]
            def anchor(quote,x=None):
                boxes=page.search_for(quote)
                if x is not None:boxes=[box for box in boxes if abs(box.x0-x)<1]
                assert len(boxes)==1,(quote,x,boxes)
                return {'page':1,'quote':quote,'bbox':list(boxes[0])}
            unit_anchor=anchor(unit)
            for fid,label,x,printed,raw in zip(('T','B','P'),('Total','Basic','Project'),(150,250,350),raws,vals,strict=True):
                cell={'schema':'independent-fiscal-cell-v2','raw_value':raw,'raw_unit':unit,
                    'cell':anchor(printed,x),'row':anchor('All'),'column':anchor(label),
                    'table':{'page':1,'quote':'Fiscal table','bbox':[35,15,520,125]},'unit_source':unit_anchor}
                fact={'fact_id':fid,'value_wan':str(Decimal(raw)*factors[unit]),'unit':'万元','year':2024,
                    'organization':'上海市甲局','file_hash':self.sha,'table_code':'FIN_03_expenditure','indicator':label.lower(),
                    'row_key':'total','fund_scope':'all_funds','stage':'决算','relation_keys':[['O','R','sum']],
                    'page':1,'quote':printed,'source_cell':cell}
                self.ev['samples'][0]['facts'].append(fact)
        self.seal_contract()

    def save(self):
        self.bs['review_report']['check_receipts']=copy.deepcopy(self.br['receipts'])
        self.cs['review_report']['check_receipts']=copy.deepcopy(self.cr['receipts'])
        write_collection(self.base,self.br,self.bs,self.sha);write_collection(self.cand,self.cr,self.cs,self.sha)

    def seal_contract(self):
        self.ev['samples'][0]['relation_contracts']=[{'key':['O','R','sum'],'receipt_digest':digest(self.cr['receipts'][0]),
            'operand_identities':{f['fact_id']:{**{k:f[k]for k in ['table_code','indicator','row_key','fund_scope','stage']},
                'source_cell_digest':digest(f['source_cell'])}for f in self.ev['samples'][0]['facts']}}]

    def decide(self,action,key,old,new):
        dec={'action':action,'before':self.before,'after':self.after,'key':key,'old_digest':digest(old),
             'new_digest':digest(new),'reason':'Independent exact object source check',
             'sources':[{'page':1,'quote':'Fiscal table','file_hash':self.sha}]}
        self.ev['decisions'].append(dec);return dec

    def check(self):
        self.save();path=self.root/'external-evidence.json';path.write_text(json.dumps(self.ev,ensure_ascii=False,sort_keys=True))
        evidence=h.load_evidence(path,hashlib.sha256(path.read_bytes()).hexdigest())
        return h.compare_collections(self.base,self.cand,self.kit,evidence)

def test(name,want='FAIL',mutate=None,**kwargs):
    with tempfile.TemporaryDirectory(prefix='gbc-r2-independent-')as td:
        f=Fixture(Path(td),**kwargs)
        if mutate:mutate(f)
        try:r=f.check()
        except Exception as e:r={'overall':'EXCEPTION','problems':[str(type(e).__name__)+': '+str(e)],'pending':[]}
        target=ROOT/'control_cases'/name
        target.parent.mkdir(exist_ok=True)
        if not target.exists():
            shutil.copytree(Path(td),target)
            (target/'observed_report.json').write_text(json.dumps(r,ensure_ascii=False,indent=2))
        return {'case':name,'expected':want,'actual':r['overall'],'expectation_met':r['overall']==want,
                'problems':r['problems'],'pending':r['pending']}

def relation_rebind(f):
    other={**copy.deepcopy(f.cr['receipts'][0]),'sub_check_id':'other'}
    f.br['receipts'].append(copy.deepcopy(other));f.cr['receipts'].append(other)
    f.cs['review_report']['issues'][0]['check_relation_id']='R:other'

def authorized_rebind(f):
    relation_rebind(f)
    old=f.bs['review_report']['issues'][0];new=f.cs['review_report']['issues'][0]
    f.decide('issue_changed',list(h.formal_identity(old)),old,new)

def authorized_body_only(f):
    old=f.bs['review_report']['issues'][0];new=f.cs['review_report']['issues'][0]
    new['message']='Independently reviewed interpretation'
    f.decide('issue_changed',list(h.formal_identity(old)),old,new)

def member_change(f,approved=False):
    for sub in ['other','third']:
        other={**copy.deepcopy(f.cr['receipts'][0]),'sub_check_id':sub}
        f.br['receipts'].append(copy.deepcopy(other));f.cr['receipts'].append(other)
    def loc(members):return {'page':1,'reference_kind':'source_diagnostic_group',
        'relation_ids':members,'relations':[{'relation_id':v}for v in members]}
    f.bs['review_report']['issues'][0]['location']=loc(['R:sum','R:other'])
    f.cs['review_report']['issues'][0]['location']=loc(['R:sum','R:third'])
    if approved:
        old=f.bs['review_report']['issues'][0];new=f.cs['review_report']['issues'][0];key=list(h.formal_identity(old))
        f.decide('issue_changed',key,old,new);f.decide('evidence_replaced',key,old,new)

def permutation(f):
    a=f.bs['review_report']['issues'][0];b={**copy.deepcopy(a),'id':'display-2','title':'Another issue','message':'Another source issue'}
    f.bs['review_report']['issues'].append(b)
    f.cs['review_report']['issues']=[{**copy.deepcopy(b),'id':'display-1'},{**copy.deepcopy(a),'id':'display-2'}]

def changed_issue_and_evidence(f):
    old=f.bs['review_report']['issues'][0];new=f.cs['review_report']['issues'][0]
    new['message']='Updated source interpretation';new['evidence'].append({'page':1,'quote':'Project'})
    key=list(h.formal_identity(old))
    f.decide('issue_changed',key,old,new);f.decide('evidence_replaced',key,old,new)

def changed_reference_kind(f):
    other={**copy.deepcopy(f.cr['receipts'][0]),'sub_check_id':'other'}
    f.br['receipts'].append(copy.deepcopy(other));f.cr['receipts'].append(other)
    loc={'page':1,'reference_kind':'source_diagnostic_group','relation_ids':['R:sum','R:other'],
         'relations':[{'relation_id':'R:sum'},{'relation_id':'R:other'}]}
    f.bs['review_report']['issues'][0]['location']=copy.deepcopy(loc)
    f.cs['review_report']['issues'][0]['location']={**loc,'reference_kind':'check'}

def duplicate_operand(f):
    f.cr['receipts'][0]['operand_fact_ids']=['T','B','B']
    f.seal_contract() # 仅更新被检查对象摘要，独立原件事实及身份/必要操作数保持原样。

def wrong_column_anchor(f):
    facts=f.ev['samples'][0]['facts']
    facts[1]['source_cell']['column']=copy.deepcopy(facts[2]['source_cell']['column'])
    f.seal_contract() # 保持外部摘要一致，单独核对原格/列头几何关系而非靠摘要错误拒收。

def repeated_number_wrong_bbox(f):
    with fitz.open(f.pdf)as d:
        f.ev['samples'][0]['facts'][1]['bbox']=list(d[0].search_for('6.00')[-1])

def layer_change(f,layer='manual_review',bad=None):
    field={'manual_review':'manual_review','tips':'tips','candidate':'candidate_findings'}[layer]
    new={'id':'M','title':'Exact current observation','message':'Independent original interpretation'}
    f.cs['review_report'][field]=[new]
    d=f.decide('layer_object_changed',[layer,'id','M'],None,new)
    if bad=='layer':d['key'][0]='tips' if layer!='tips'else'manual_review'
    elif bad=='sample':d['after']={**f.after,'sample_id':'OTHER'}
    elif bad=='revision':d['after']={**f.after,'analysis_revision':'old-analysis'}
    elif bad=='source':d['sources'][0]['quote']='not on original'
    elif bad=='v1':f.ev['schema']='independent-effect-evidence-v1'

results=[test('original_bound_addition_positive',want='PASS'),
         test('original_yuan_conversion_positive',want='PASS',raws=('100000','60000','40000'),pdf_unit='yuan'),
         test('original_chinese_yuan_conversion_positive',want='PASS',raws=('100000','60000','40000'),pdf_unit='元'),
         test('original_chinese_wan_conversion_positive',want='PASS',pdf_unit='万元'),
         test('true_quote_other_column_rejected',mutate=lambda f:f.ev['samples'][0]['facts'][1].update(quote='4.00')),
         test('same_number_other_bbox_rejected',mutate=repeated_number_wrong_bbox),
         test('wrong_column_geometry_rejected_with_consistent_seal',mutate=wrong_column_anchor),
         test('negative_amounts_legal_positive',want='PASS',raws=('-10.00','-6.00','-4.00')),
         test('true_quote_wrong_signed_value',raws=('10.00','-6.00','4.00'),claimed_raws=('10.00','6.00','4.00')),
         test('unit_subtoken_yuan_from_original_wan',raws=('100000','60000','40000'),pdf_unit='万元',claimed_unit='元'),
         test('same_rule_relation_rebind_without_authorization',mutate=relation_rebind),
         test('same_rule_relation_rebind_exact_authorization',want='PASS',mutate=authorized_rebind),
         test('body_only_exact_authorization',want='PASS',mutate=authorized_body_only),
         test('source_group_member_change_without_authorization',mutate=member_change),
         test('source_group_member_change_exact_authorizations',want='PASS',mutate=lambda f:member_change(f,True)),
         test('display_id_permutation_legal',want='PASS',mutate=permutation),
         test('body_and_evidence_exact_authorizations_legal',want='PASS',mutate=changed_issue_and_evidence),
         test('reference_group_kind_changed_without_authorization',mutate=changed_reference_kind),
         test('duplicate_operand_omits_required_project',raws=('12.00','6.00','4.00'),mutate=duplicate_operand)]
for layer in ['manual_review','tips','candidate']:
    results.append(test('layer_exact_decision_'+layer,want='PASS',mutate=lambda f,layer=layer:layer_change(f,layer)))
for bad in ['layer','sample','revision','source','v1']:
    results.append(test('layer_wrong_'+bad,want='PENDING',mutate=lambda f,bad=bad:layer_change(f,bad=bad)))

(ROOT/'receipts/narrative_boundary_controls.json').write_text(json.dumps(narrative_controls(),ensure_ascii=False,indent=2))
(ROOT/'receipts/effect_boundary_controls.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps({'narrative':[{k:r[k]for k in ['case','expected_zero_source','actual_zero_source','expectation_met']}for r in narrative_controls()],
                  'effect':[{k:r[k]for k in ['case','expected','actual','expectation_met']}for r in results]},ensure_ascii=False,indent=2))
