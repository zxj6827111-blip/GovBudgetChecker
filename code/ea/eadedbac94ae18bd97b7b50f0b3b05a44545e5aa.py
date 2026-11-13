"""独立构造的小型原件/事实正负控；不读取执行者测试夹具。"""
import copy
import gzip
import hashlib
import json
import pathlib
import sys
import tempfile

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAND = pathlib.Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
sys.path.insert(0, str(CAND))
from scripts import effect_acceptance_v3 as h

def digest(v):
    return hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def write(root, receipts, issues, revision, sha, manual=None):
    meta = {'samples': [{'sample_id':'INDEPENDENT', 'sha256':sha}], 'code':{'digest':'independent-code'},
            'code_after':'independent-code', 'profile':{'OCR_ENABLED':'false'}}
    status = {'checksum':sha, 'analysis_revision':revision, 'fiscal_year':2025,
              'organization_name':'Independent Source Unit', 'report_kind':'final',
              'result':{'meta':{'rule_policy_version':'v3_3_portable', 'rule_registry_version':'independent-v1'}},
              'review_report':{'issues':issues, 'candidate_findings':[], 'tips':[], 'manual_review':manual or [],
                               'check_receipts':receipts}}
    record = {'sample_id':'INDEPENDENT','sha256':sha,'receipts':receipts,'run':{},'findings':[],
              'finding_bodies':{},'obligations':{}}
    root.mkdir(exist_ok=True)
    for folder, suffix, obj in [('status','.status.json.gz',status), ('records','.json.gz',record)]:
        (root/folder).mkdir(exist_ok=True)
        with gzip.open(root/folder/('INDEPENDENT'+suffix), 'wt') as f:json.dump(obj,f)
    (root/'meta.json').write_text(json.dumps(meta))
    return {'sample_id':'INDEPENDENT','pdf_sha256':sha,'analysis_revision':revision,'code_digest':'independent-code',
            'profile_digest':digest(meta['profile']), 'rules_digest':digest({**status['result']['meta'],'versions':None}),
            'organization':'Independent Source Unit','year':2025,'report_kind':'final'}

def run_case(name, fault=None, expected='FAIL'):
    with tempfile.TemporaryDirectory(prefix='gbc-independent-control-') as td:
        root=pathlib.Path(td);kit=root/'kit';(kit/'pdfs/INDEPENDENT').mkdir(parents=True)
        pdf=kit/'pdfs/INDEPENDENT/source.pdf'
        with fitz.open() as doc:
            p=doc.new_page();p.insert_text((50,60),'Total 10.00; Basic 6.00; Project 4.00 (wan).')
            p.insert_text((50,100),'Independent second relation: no assertion about the first relation.')
            doc.save(pdf)
        sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
        old={'obligation_id':'O','rule_id':'R','sub_check_id':'sum','status':'insufficient_data',
             'reason_codes':['operand_missing'],'next_action':'tech_fix','operand_fact_ids':[],
             'formula':'total = sum(parts) (万元)'}
        new={**copy.deepcopy(old),'status':'pass','reason_codes':[],'next_action':None,
             'operand_fact_ids':['ind-total','ind-basic','ind-project']}
        issue={'id':'I1','rule_id':'R','severity':'high','title':'Independent test issue','message':'source mismatch',
               'page_number':1,'check_relation_id':'R:sum','evidence':[{'page':1,'quote':'Total 10.00'}],
               'location':{'page':1},'bbox':None}
        bi,ci=[copy.deepcopy(issue)],[copy.deepcopy(issue)]
        br,cr=[old],[new]
        base=root/'base';cand=root/'candidate'
        before=write(base,br,bi,'ind-before',sha)
        after=write(cand,cr,ci,'ind-after',sha)
        facts=[]
        # 金额及角色在构造原件时已经确定，先于读取被检查回执。
        for fid,value,label,indicator in [('ind-total','10.00','Total','total_actual'),
                                          ('ind-basic','6.00','Basic','basic_actual'),
                                          ('ind-project','4.00','Project','project_actual')]:
            facts.append({'fact_id':fid,'value_wan':value,'quote':f'{label} {value}','page':1,'file_hash':sha,
                          'unit':'万元','year':2025,'organization':'Independent Source Unit',
                          'table_code':'FIN_03_expenditure','indicator':indicator,'row_key':'total',
                          'fund_scope':'all_funds','stage':'决算','relation_keys':[['O','R','sum']]})
        evidence={'schema':'independent-effect-evidence-v1','samples':[{'binding':after,'facts':facts,
           'relation_contracts':[{'key':['O','R','sum'],'receipt_digest':digest(new),'operand_identities':{
               f['fact_id']:{k:f[k] for k in ('table_code','indicator','row_key','fund_scope','stage')} for f in facts}}]}],
           'decisions':[]}
        state={'br':br,'cr':cr,'bi':bi,'ci':ci,'ev':evidence,'before':before,'after':after,'manual':[]}
        if fault:fault(state)
        write(base,br,bi,'ind-before',sha)
        write(cand,cr,ci,'ind-after',sha,state['manual'])
        report=h.compare_collections(base,cand,kit,evidence)
        return {'name':name,'expected':expected,'actual':report['overall'],
                'expectation_met':report['overall']==expected,'problems':report['problems'],'pending':report['pending'],
                'ledger_change_kinds':dict(__import__('collections').Counter(r['change_kind']for r in report['ledger']))}

def remove_authorization(s):
    s['ev']['decisions'].append({'action':'remove_issue','before':s['before'],'after':s['after'],
        'key':list(h.formal_identity(s['bi'][0])),'old_digest':digest(s['bi'][0]),'new_digest':digest(None),
        'reason':'Independently checked exact original test issue','sources':[{'page':1,'quote':'Total 10.00'}]})
    s['ci'].clear()

def unknown(s):
    s['cr'][0]['operand_fact_ids'][0]='does-not-exist'
    s['ev']['samples'][0]['relation_contracts'][0]['receipt_digest']=digest(s['cr'][0])

def rebind_issue(s):
    other={**copy.deepcopy(s['cr'][0]),'sub_check_id':'other'}
    s['br'].append(copy.deepcopy(other));s['cr'].append(other)
    s['ci'][0]['check_relation_id']='R:other'

def displaced_value(s):
    # 只破坏原文引文与金额/列的对应，数值公式保持正确。
    s['ev']['samples'][0]['facts'][1]['quote']='Project 4.00'

def pending_approved(s):
    s['manual'].append({'id':'M1','title':'Independently reviewed new manual observation'})
    s['ev']['decisions'].append({'action':'manual_review_replaced','before':s['before'],'after':s['after'],
       'key':['manual_review','M1'],'old_digest':digest(None),'new_digest':digest(s['manual'][0]),
       'reason':'Independent exact object adjudication','sources':[{'page':1,'quote':'Total 10.00'}]})

cases=[run_case('legal_original_based_recovery',expected='PASS'),
       run_case('nonexistent_fact_only',unknown),
       run_case('evidence_clear_only',lambda s:s['ci'][0].update(evidence=[],location=None,bbox=None)),
       run_case('forged_page_only',lambda s:s['ci'][0]['location'].update(page=999)),
       run_case('legal_exact_issue_removal',remove_authorization,expected='PASS')]
for key in ['sample_id','analysis_revision']:
    def mutate(s,key=key):
        remove_authorization(s);s['ev']['decisions'][0]['after']={**s['after'],key:'wrong'}
    cases.append(run_case('borrow_removal_'+key,mutate))
def other_relation(s):
    remove_authorization(s);s['ev']['decisions'][0]['key'][2]='other issue'
cases.append(run_case('borrow_removal_other_object',other_relation))
def same_identity_other_relation(s):
    remove_authorization(s)
    s['ev']['decisions'][0]['old_digest']=digest({**s['bi'][0],'check_relation_id':'R:other'})
cases.append(run_case('borrow_removal_same_rule_other_relation_digest',same_identity_other_relation))
for field in ['sample_id','pdf_sha256','analysis_revision','code_digest']:
    def wrong_binding(s,field=field):
        s['ev']['samples'][0]['binding']={**s['after'],field:'wrong'}
    cases.append(run_case('borrow_recovery_snapshot_'+field,wrong_binding))
def approved_forged_bbox(s):
    s['ci'][0]['location']['bbox']=[400,400,450,450]
    s['ev']['decisions'].append({'action':'evidence_replaced','before':s['before'],'after':s['after'],
        'key':list(h.formal_identity(s['bi'][0])),'old_digest':digest(s['bi'][0]),'new_digest':digest(s['ci'][0]),
        'reason':'exact object location update','sources':[{'page':1,'quote':'Total 10.00'}]})
cases.append(run_case('forged_bbox_with_exact_change_authorization',approved_forged_bbox))
def cleanup(s):
    s['br'].clear();s['br'].append(copy.deepcopy(s['cr'][0]))
    s['br'][0].update(reason_codes=['operand_missing'],next_action='system_recheck')
    s['ev']['samples'].clear()
cases.append(run_case('diagnostic_cleanup_no_business_recovery',cleanup,expected='PASS'))
cases.append(run_case('issue_relation_rebound_without_authorization',rebind_issue))
cases.append(run_case('fact_quote_from_other_column',displaced_value))
cases.append(run_case('authorized_manual_layer_change_no_supported_route',pending_approved,expected='PASS'))
(ROOT/'receipts/independent_controls.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2))
print(json.dumps([{k:r[k] for k in ['name','expected','actual','expectation_met']} for r in cases],ensure_ascii=False,indent=2))

# 当前真实PDF上有明确零声明仍未读取：独立原文语境核查。
from types import SimpleNamespace
from src.engine.narrative_zero_evidence import narrative_zero_sources, _clauses, _LABELS, _NON_FISCAL_SCOPE_RE
from src.engine.native_narrative_checks import _Text
import re
real=[]
for sid in ['RB-2023-U04','RB-2025-U02']:
    p=next((CAND/'outputs/unified_remediation_20261106/kit/pdfs'/sid).glob('*.pdf'))
    with fitz.open(p) as doc: texts=[page.get_text() for page in doc]
    with gzip.open(CAND/'outputs/unified_remediation_20261106/final_d1/status'/f'{sid}.status.json.gz','rt') as f: raw=json.load(f)
    test_doc=SimpleNamespace(page_texts=texts,report_kind='final',fact_context=SimpleNamespace(
        org_name=raw['organization_name'],fiscal_year=raw['fiscal_year']))
    refs=narrative_zero_sources(test_doc,table_code='FIN_07_three_public',indicator='vehicle_purchase',stage='决算')
    compact=_Text.from_pages(texts)
    poison=next((compact.compact[a:b] for a,b in _clauses(compact.compact)
                 if re.search('|'.join(_LABELS['vehicle_purchase']),compact.compact[a:b])
                 and _NON_FISCAL_SCOPE_RE.search(compact.compact[a:b])),None)
    rs=[r for r in raw['review_report']['check_receipts'] if r['rule_id']=='V33-224'
        and r['obligation_id']=='OBL-TXT-T7' and 'vehicle_purchase:' in r['sub_check_id']]
    real.append({'sample_id':sid,'pdf_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
        'independent_original_assertion':{'physical_page':21,'quote':'公务用车购置支出为0万元'},
        'expected_zero_source':True,'actual_zero_source':bool(refs),
        'source_of_false_rejection':'整段无句号表格文本包含非财政拨款/事业收入等其他表头；尚未核对该句金额即return None',
        'poison_clause_length':len(poison or ''),'poison_clause_prefix':(poison or '')[:220],
        'candidate_relations':[{k:r.get(k) for k in ['sub_check_id','status','reason_codes','next_action']}for r in rs]})
(ROOT/'receipts/real_zero_reading_unresolved.json').write_text(json.dumps(real,ensure_ascii=False,indent=2))
