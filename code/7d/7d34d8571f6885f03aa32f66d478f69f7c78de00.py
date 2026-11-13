"""先由原PDF建立事实/空白簿，后映射回执身份；拒绝错误主体的旧采集授权。"""
import ast
import collections
import copy
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
def dump(name,data):
    path=ROOT/name;path.write_text(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2));return path
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):
    with (gzip.open(path,'rt') if path.suffix=='.gz' else path.open()) as f:return json.load(f)
def norm(text):return re.sub(r'\s+','',text)
def original(sid):
    paths=list((EV/'kit_cover_bound_r2/pdfs'/sid).glob('*.pdf')) or list((EV/'kit/pdfs'/sid).glob('*.pdf'))
    assert len(paths)==1
    return paths[0]

# 原件独立事实簿先创建；不从回执生成金额或空白事实。
truth={}
settings={'SP-ORIG-07':(2024,12,18,'上海市人民政府办公厅离退休干部服务中心'),
          'RB-2025-U01':(2025,12,18,'上海市人民政府办公厅离退休干部服务中心'),
          'RB-2025-U17':(2025,16,23,'上海市质量和标准化研究院')}
indicators=['total_actual','overseas','vehicle_purchase_operation','vehicle_purchase','vehicle_operation','reception']
for sid,(year,tablepage,textpage,org) in settings.items():
    path=original(sid)
    with fitz.open(path) as doc:
        table=doc[tablepage-1].find_tables().tables[0];rows=table.extract()
        assert table.col_count==12 and rows[-2]==['预算数','决算数']*6
        grid=[]
        for col,(value,box) in enumerate(zip(rows[-1],table.rows[-1].cells,strict=True)):
            grid.append({'physical_page':tablepage,'bbox':list(box),'indicator':indicators[col//2],
                         'stage':'年初预算' if col%2==0 else '决算', 'original_value':value,
                         'source':'independently extracted PDF vector grid, visually checked'})
        text=doc[textpage-1].get_text()
        if sid!='RB-2025-U17':
            quote=f'{org}{year}年度无“三公”经费财政拨款支出'
            assert norm(quote) in norm(text)
            statement={'page':textpage,'quote':quote,'file_hash':sha(path),'year':year,'organization':org,
                       'scope':'财政拨款三公决算；明确全部无支出；只证明决算，不解释任何预算空格',
                       'value_wan':'0','origin':'original narrative, not printed blank-cell amount'}
        else:
            assert '公务用车购置支出为0万元' in norm(text)
            statement={'page':textpage,'quote':'公务用车购置支出为0万元','file_hash':sha(path),
                       'year':year,'organization':org,'scope':'财政拨款公务用车购置决算；不证明预算',
                       'value_wan':'0','origin':'original narrative'}
        truth[sid]={'pdf_sha256':sha(path),'pdf_path':str(path),'organization':org,'year':year,
                    'grid':grid,'statement':statement}
truthpath=dump('receipts/independent_original_truth_v1.json',{'version':'original-truth-review-20261106-v1',
    'reviewer':'Codex independent review in current chat','samples':truth,
    'limits':'局部事实簿；既非全PDF金标，也非B1冻结。数值及空白来自PDF原页，不从回执扩充事实池。'})

def blank_verified(sid,cell):
    # 由先验原件格簿匹配指标、阶段、原页、完整格边界；值只能是空白。
    candidates=[g for g in truth[sid]['grid'] if g['indicator']==cell.get('indicator')
        and g['stage']==cell.get('budget_stage') and g['physical_page']==cell.get('physical_page')]
    assert len(candidates)==1,(sid,cell)
    g=candidates[0]
    assert not g['original_value'].strip()
    assert max(abs(float(a)-float(b))for a,b in zip(g['bbox'],cell['bbox'],strict=True))<0.05
    assert cell['file_hash']==truth[sid]['pdf_sha256'] and cell['raw_text']==''
    return True

def bundle_for(row,sid):
    assert row['source_binding']['organization']==truth[sid]['organization']
    assert row['source_binding']['year']==truth[sid]['year']
    return {'binding':row['source_binding'],'facts':[], 'relation_contracts':[],
            'relation_proofs':[],'verified_blank_observations':[],
            'original_truth_sha256':sha(truthpath)}

def add_proof(bundle,sid,r,reason):
    assert not r.get('operand_fact_ids'), '本次只授权已独立核读的叙事零/汇总关系，不借回执构造金额池'
    for cell in r.get('cell_observations',[]):
        blank_verified(sid,cell)
        if cell not in bundle['verified_blank_observations']:bundle['verified_blank_observations'].append(cell)
    key=list(h.receipt_key(r))
    bundle['relation_contracts'].append({'key':key,'receipt_digest':h.digest(r),'operand_identities':{}})
    bundle['relation_proofs'].append({'key':key,'receipt_digest':h.digest(r),'conclusion':r['status'],
        'reason':reason,'sources':[truth[sid]['statement']],
        'independent_calculation':{'declared_final_total_wan':'0','declared_final_parts_total_wan':'0',
                                   'budget_blanks':'remain unknown','raw_monetary_facts':'not augmented'}})

before=h.load_collection(EV/'baseline_main');after=h.load_collection(EV/'final_d1')
evidence={'schema':'independent-effect-evidence-v1','review_version':'independent-review-20261106-v1',
    'source_manifest':{'truth_sha256':sha(truthpath),'baseline_meta_sha256':sha(EV/'baseline_main/meta.json'),
                       'candidate_meta_sha256':sha(EV/'final_d1/meta.json'),'candidate_code_commit':'5ac9e71b8b46d97c786636656f54a504171a19ea'},
    'samples':[],'decisions':[]}
bun=bundle_for(after['RB-2025-U01'],'RB-2025-U01');evidence['samples'].append(bun)
replacements={}
for sid in ['RB-2025-U01']:
    bm={h.receipt_key(r):r for r in before[sid]['receipts']};cm={h.receipt_key(r):r for r in after[sid]['receipts']}
    for key,r in cm.items():
        old=bm.get(key)
        if r['status'] in h.RESOLVED and (old is None or h.receipt_evaluation(old)!=h.receipt_evaluation(r)):
            add_proof(bun,sid,r,'独立核读2025年同单位p18全部无三公财政拨款支出；p12完整格对应决算确为空。仅当前具体关系授权；汇总pass表示这些决算子检查已执行，预算未知仍保留。')
    for key in bm.keys()-cm.keys():
        if key[2].endswith(':missing'):
            kind=key[2].split(':')[-2]
            target=(key[0],key[1],f'three_public:detail:{kind}:no_vehicle_activity')
        else:
            target=(key[0],key[1],'three_public:text:决算:6098')
        assert target in cm and cm[target]['status'] in h.RESOLVED
        evidence['decisions'].append({'action':'replace_relation','before':before[sid]['source_binding'],
            'after':after[sid]['source_binding'],'key':list(key),'replacement_keys':[list(target)],
            'old_digest':h.digest(bm[key]),'new_digest':h.digest([cm[target]]),
            'reason':'同一PDF/主体/年度/指标/阶段的已定位原句替代旧未定位missing或text关系；不删除义务、不证明预算。',
            'sources':[truth[sid]['statement']]})
        replacements[(sid,key)]=target

# 对聚合收严逐对象授权；接受的是未完成状态的暴露，不是底层金额判断。
for sid in ['RA-036','RB-2022-U11','RB-2025-D05','RB-2025-U03','RB-2025-U02','SP-ORIG-01']:
    bm={h.receipt_key(r):r for r in before[sid]['receipts']}
    cm={h.receipt_key(r):r for r in after[sid]['receipts']}
    for key,r in cm.items():
        old=bm.get(key)
        if not old or old['status'] not in h.RESOLVED or r['status'] not in h.UNRESOLVED:continue
        assert key[2].startswith('OBL-')
        with fitz.open(original(sid)) as doc:
            # 独立原页语境仅定位，收严依据另有原始规则/义务未完成链与代码审查。
            needle='财政拨款收入' if sid=='SP-ORIG-01' else '年初预算'
            page_no=next(i+1 for i,p in enumerate(doc) if needle in norm(p.get_text()))
            quote=needle
        evidence['decisions'].append({'action':'justified_regression','before':before[sid]['source_binding'],
            'after':after[sid]['source_binding'],'key':list(key),'old_digest':h.digest(old),'new_digest':h.digest(r),
            'reason':'原始规则/义务本来未完成；旧聚合pass仅覆盖已物化子项。独立代码核对确认fallback未完成须保留。此处仅接受安全收严，底层待核不予完成。',
            'sources':[{'page':page_no,'quote':quote,'file_hash':after[sid]['sha256']}],
            'code_basis':'src/engine/check_obligations.py:1993; receipts/aggregates_independent.json',
            'review_limit':'不把底层无法取数等同原件缺失'})

ep=dump('independent_effect_final_v1.json',evidence)
(ROOT/'independent_effect_final_v1.sha256').write_text(sha(ep)+'  '+ep.name+'\n')
rerun=h.compare_collections(EV/'baseline_main',EV/'final_d1',EV/'kit',h.load_evidence(ep,sha(ep)))
rerun['evidence_sha256']=sha(ep);rerun['tool_sha256']=sha(CAND/'scripts/effect_acceptance_v3.py')
dump('receipts/effect_with_independent_review_v1.json',rerun)

# 正确封面主体的补采另留证据包，不把旧错误归档工件伪装成新包。
cb=h.load_collection(EV/'cover_r2_base');cc=h.load_collection(EV/'cover_r2_d1')
cover={'schema':'independent-effect-evidence-v1','review_version':'independent-cover-review-20261106-v1',
    'source_manifest':{'baseline_meta_sha256':sha(EV/'cover_r2_base/meta.json'),
                       'candidate_meta_sha256':sha(EV/'cover_r2_d1/meta.json'),'truth_sha256':sha(truthpath),
                       'replaces_legacy_meta':False},'samples':[],'decisions':[]}
sid='SP-ORIG-07';bundle=bundle_for(cc[sid],sid);cover['samples'].append(bundle)
bm={h.receipt_key(r):r for r in cb[sid]['receipts']};cm={h.receipt_key(r):r for r in cc[sid]['receipts']}
for key,r in cm.items():
    old=bm.get(key)
    if r['status']in h.RESOLVED and(old is None or h.receipt_evaluation(old)!=h.receipt_evaluation(r)):
        add_proof(bundle,sid,r,'独立核读2024年同封面单位p18三公决算全部无支出；p12决算格确为空，预算5.00与决算0.00严格分离。只授权这份正确封面主体补采。')
for key in bm.keys()-cm.keys():
    target=(key[0],key[1],'three_public:text:决算:6366')
    assert target in cm
    cover['decisions'].append({'action':'replace_relation','before':cb[sid]['source_binding'],'after':cc[sid]['source_binding'],
        'key':list(key),'replacement_keys':[list(target)],'old_digest':h.digest(bm[key]),'new_digest':h.digest([cm[target]]),
        'reason':'2024同主体同原文的已定位关系替代未定位text关系；主体修正本身不计解析成效',
        'sources':[truth[sid]['statement']]})
for sample in sorted(cb):
    old_map={h.receipt_key(r):r for r in cb[sample]['receipts']}
    for r in cc[sample]['receipts']:
        key=h.receipt_key(r);old=old_map.get(key)
        if not old or old['status']not in h.RESOLVED or r['status']not in h.UNRESOLVED:continue
        assert key[2].startswith('OBL-')
        with fitz.open(original(sample)) as doc:
            needle='财政拨款收入' if sample=='SP-ORIG-01' else '年初预算'
            page_no=next(i+1 for i,p in enumerate(doc) if needle in norm(p.get_text()))
        cover['decisions'].append({'action':'justified_regression','before':cb[sample]['source_binding'],
            'after':cc[sample]['source_binding'],'key':list(key),'old_digest':h.digest(old),'new_digest':h.digest(r),
            'reason':'独立核对补采前后同主体、同PDF的原始规则和义务未完成链；旧聚合pass不能覆盖未物化的未完成项。接受收严，底层待核继续保留。',
            'sources':[{'page':page_no,'quote':needle,'file_hash':cc[sample]['sha256']}],
            'code_basis':'src/engine/check_obligations.py:1993'})
cp=dump('independent_effect_cover_v1.json',cover)
(ROOT/'independent_effect_cover_v1.sha256').write_text(sha(cp)+'  '+cp.name+'\n')
cr=h.compare_collections(EV/'cover_r2_base',EV/'cover_r2_d1',EV/'kit_cover_bound_r2',h.load_evidence(cp,sha(cp)))
cr['evidence_sha256']=sha(cp);dump('receipts/effect_cover_independent_v1.json',cr)

# 原始66拒收逐项给去向，未授权者继续拒收；不改旧报告。
rows=[]
original_report=read(EV/'effect_final_sealed/effect_report.json')
for i,text in enumerate(original_report['problems'],1):
    m=re.match(r'^(\S+) (\(.*\)): (.*)$',text);assert m,text
    sid,key,why=m[1],ast.literal_eval(m[2]),m[3]
    remaining=text in rerun['problems']
    rows.append({'item':i,'sample_id':sid,'key':list(key),'original_refusal':why,
        'disposition':'KEEP_REJECTED_WRONG_ORGANIZATION_REQUIRES_SEPARATE_COVER_COLLECTION' if sid=='SP-ORIG-07'
            else 'STILL_REJECTED' if remaining else 'INDEPENDENT_OBJECT_AUTHORIZATION_OR_PROOF_ACCEPTED',
        'remaining_in_tool':remaining,'authorization_package_sha256':sha(ep) if not remaining else None,
        'note':'旧27样本采集主体为父级办公厅，封面为离退休干部服务中心；只在cover_r2独立包中授权，旧meta保持原样。' if sid=='SP-ORIG-07' else '仅此材料、前后版本及对象；不签发规则级通行证'})
dump('receipts/effect66_dispositions.json',rows)

print(json.dumps({'final':{'overall':rerun['overall'],'problems':len(rerun['problems']),'pending':len(rerun['pending'])},
    'cover':{'overall':cr['overall'],'problems':len(cr['problems']),'pending':len(cr['pending'])},
    'final_proofs':len(bun['relation_proofs']),'final_decisions':len(evidence['decisions']),
    'cover_proofs':len(bundle['relation_proofs']),'cover_decisions':len(cover['decisions'])},ensure_ascii=False))
