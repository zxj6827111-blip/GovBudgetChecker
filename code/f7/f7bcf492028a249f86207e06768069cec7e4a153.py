import collections
from decimal import Decimal
import gzip
import hashlib
import json
import pathlib
import re
import sys
import pymupdf as fitz

ROOT=pathlib.Path(__file__).resolve().parents[1]
CAND=pathlib.Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
EV=CAND/'outputs/unified_remediation_20261106'
sys.path.insert(0,str(CAND))
from scripts import effect_acceptance_v3 as h
def read(p):
    with(gzip.open(p,'rt')if p.suffix=='.gz'else p.open())as f:return json.load(f)
def dump(n,v):(ROOT/n).write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def pdf(sid):return next((EV/'kit/pdfs'/sid).glob('*.pdf'))

hist=read(EV/'historical24_dispositions.json');boxes=read(ROOT/'receipts/original_cell_reextraction.json')
hist_review=[]
for i,row in enumerate(hist,1):
    sid=row['sample_id'];oid=row['obligation_id'];own=[r for r in boxes if r['item']==i]
    source_pages=sorted({r['physical_page']for r in own})
    status=read(EV/'cover_r2_d1/status'/f'{sid}.status.json.gz')
    actual=next(o for o in status['check_coverage']['instances']if o['obligation_id']==oid)
    assert actual['status']==row['after']['status'] and status['checksum']==row['pdf_sha256']
    if i in [15,18]:
        outcome='CONFIRMED_BOUNDED_RECOVERY'
        reason='p12对应决算格确为空，p18同单位2024年全部无三公财政拨款支出；0=0+0+0，0/5.00×100=0%。只确认当前决算表文关系；预算空白仍未知，主体归档修正不计解析成效。'
        source_pages=[12,18]
    elif i==1:
        outcome='REASONABLE_UNKNOWN'
        reason='p5只有财政拨款收入列，未披露独立本年收入合计列；p14仅披露本年财政拨款收入1622.36。不得借该列替代总收入。'
        source_pages=[5,14]
    else:
        assert own and all(r['blank']for r in own)
        outcome='REASONABLE_UNKNOWN'
        reason='原页网格与列头视觉核对，重新提取所列空格均空；无需也不得按加总反推或补预算零。保留未完成；不据此保证全部诊断动作已最优。'
    hist_review.append({'item':i,'sample_id':sid,'obligation_id':oid,'pdf_sha256':row['pdf_sha256'],
        'candidate_revision':row['candidate_revision'],'current_status':actual['status'],'independent_disposition':outcome,
        'source_pages':source_pages,'source_box_observations':len(own),'reason':reason})
dump('historical24_independent.json',hist_review)

scope=[]
for i,row in enumerate(read(EV/'m12_lineage.json'),1):
    sid=row['sample_id'];key=row['key'];sha_pdf=read(EV/'final_d1/status'/f'{sid}.status.json.gz')['checksum']
    decision=row['disposition'];source=[];limit=None
    if key[1]=='V33-TREND-COMPLETION-RATE':
        reason='原始规则/义务未完成而旧汇总pass，独立代码及实际原始回执已核实；纳入诊断。此裁决不证明底层缺数均是材料缺失。'
        source=['receipts/aggregates_independent.json']
    elif sid in ['RB-2023-U04','RB-2025-U02']:
        reason='同年度p21明确购置决算为0，p14购置决算格空；候选仍未绑定该句。纳入诊断正确，但不能归为材料未知或已修好。'
        source=[{'physical_page':14,'kind':'original blank purchase final cell'},
                {'physical_page':21,'quote':'公务用车购置支出为0万元'}]
        limit='TECHNICALLY_UNRESOLVED_ZERO_READING'
    elif sid=='RB-2025-U01':
        reason='p12对应决算原格为空，p18同主体2025年三公分项均明确为0且全部无支出；这是有依据的决算解释，不是未完成诊断。'
        source=[{'physical_page':12},{'physical_page':18,'quote':'2025年度无“三公”经费财政拨款支出'}]
    else:
        assert sid=='RB-2025-U17'
        reason='p16购置决算格空，p23明确公务用车购置支出为0；解释性N/A有原件依据，不证明预算。'
        source=[{'physical_page':16},{'physical_page':23,'quote':'公务用车购置支出为0万元'}]
    scope.append({'origin':'original28','item':i,'sample_id':sid,'pdf_sha256':sha_pdf,'key':key,
        'diagnostic_scope_decision':decision,'scope_review':'APPROVED_OBJECT_SCOPE_ONLY','reason':reason,
        'sources':source,'business_limit':limit,'lineage_sha256':sha(EV/'m12_lineage.json')})

# 独立手工核读的17个支出决算数；不从候选rule receipt提取。
values=['564.60','268.40','928.48','551.64','8.12','389.62','258.69','6.00','461.57','851.36',
        '6954.79','3141.54','4061.45','117.85','1057.43','1004.36','139.05']
total=sum(map(Decimal,values));stated=Decimal('26123.47')
assert total==Decimal('20764.95') and stated-total==Decimal('5358.52')
math={'source_sample':'SP-ORIG-08','pdf_sha256':sha(pdf('SP-ORIG-08')),
      'source_pages':[21,22,23,24,25],'line_item_final_values_wan':values,'independent_sum_wan':str(total),
      'stated_total_wan':str(stated),'difference_wan':str(stated-total),
      'note':'逐项取说明所列决算数；住房公积金1，057.43按原文全角千分号核读为1057.43；不是从表内反推说明值。'}
dump('receipts/additional3_independent_calculation.json',math)
extras=read(EV/'additional_scope3_candidate.json')
for i,row in enumerate(extras,1):
    if i==1:reason='原件p14仅写本年财政拨款收入1622.36万元，未声明财政拨款占本年收入比例；比例关系条件未触发，解释性N/A范围成立。'
    elif i==2:reason='原件p15支出说明只列项目支出12518.13万元及100.00%，未声明基本支出占比；该条件性关系是解释性N/A，不代表其它支出义务完成。'
    else:reason='独立核读原件p21—25的17项决算并复算20764.95，与总额26123.47差5358.52；fail是正式问题计算说明，纳入解释层。'
    scope.append({'origin':'additional3','item':i,'sample_id':row['sample_id'],'key':row['key'],
       'pdf_sha256':read(EV/'cover_r2_d1/status'/f"{row['sample_id']}.status.json.gz")['checksum'],
       'source_status_sha256':row['raw_status_sha256'],'diagnostic_scope_decision':'explanation_only',
       'scope_review':'APPROVED_OBJECT_SCOPE_ONLY','reason':reason})
scope_package={'version':'independent-diagnostic-scope-review-20261106-v1','reviewer':'Codex independent review current chat',
   'frozen_r4_modified':False,'new_full_kit_issued':False,
   'collection_note':'此为逐对象裁决补充契约，不回写既有采集meta，不宣称旧采集属于新包；工具需按新契约身份另留适配或新采集。',
   'sources':{'m12_lineage_sha256':sha(EV/'m12_lineage.json'),'additional_scope3_sha256':sha(EV/'additional_scope3_candidate.json')},
   'decisions':scope}
dump('independent_scope31_v1.json',scope_package)

# 待核层逐对象留痕。支持局部来源者单列，其余继续待原件复核；工具本身也没有消费裁决的入口。
a=h.load_collection(EV/'baseline_main');b=h.load_collection(EV/'final_d1');manual=[]
for sid in sorted(a):
    old=a[sid]['finding_layers']['manual_review'];new=b[sid]['finding_layers']['manual_review']
    if h.stable(old)==h.stable(new):continue
    def group(rows):
        out=collections.defaultdict(list)
        for r in rows:out[(r.get('record_type'),r.get('code'),r.get('rule_id'))].append(r)
        return out
    og,ng=group(old),group(new);objects=[]
    for key in sorted(og.keys()|ng.keys(),key=str):
        if h.stable(og[key])==h.stable(ng[key]):continue
        item={'object_key':key,'old_digest':h.digest(og[key]),'new_digest':h.digest(ng[key]),
              'before_count':len(og[key]),'after_count':len(ng[key])}
        def rule_states(rows):
            return {v['rule_id']:v.get('status')for x in rows for v in x.get('unresolved_rules',[])if 'rule_id'in v}
        if key[1]=='rules_insufficient_data':
            os,ns=rule_states(og[key]),rule_states(ng[key]);item['removed_unresolved_rules']=sorted(os.keys()-ns.keys());item['retained_rule_statuses']=ns
        objects.append(item)
    if sid=='SP-ORIG-07':result='KEEP_PENDING_LEGACY_ORGANIZATION_BINDING';reason='原27样本主体错误，补采独立包不能替换旧meta。'
    elif sid in ['SP-ORIG-01','SP-ORIG-04','SP-ORIG-06','SP-ORIG-08','RB-2025-U01','RB-2025-U17']:
        result='ORIGINAL_SUPPORTS_LOCAL_CHANGES_TOOL_ROUTE_STILL_PENDING';reason='局部原件和未完成保留有支持，详见历史24/原件事实簿；待核层全对象授权消费入口尚未实现，不能清空PENDING。'
    else:result='NEEDS_FURTHER_INDEPENDENT_ORIGINAL_ADJUDICATION';reason='变化多为原件空白诊断/动作重分类，未逐对象完成所有原格语义签收；本轮不以候选诊断反向证明原件。'
    manual.append({'sample_id':sid,'layer':'manual_review','before_binding':a[sid]['source_binding'],
      'after_binding':b[sid]['source_binding'],'old_layer_digest':h.digest(old),'new_layer_digest':h.digest(new),
      'before_count':len(old),'after_count':len(new),'changed_objects':objects,'disposition':result,'reason':reason})
dump('manual16_independent_dispositions.json',manual)
print(json.dumps({'historical24':dict(collections.Counter(r['independent_disposition']for r in hist_review)),
    'scope31':dict(collections.Counter(r['diagnostic_scope_decision']for r in scope)),
    'manual16':dict(collections.Counter(r['disposition']for r in manual))},ensure_ascii=False))
