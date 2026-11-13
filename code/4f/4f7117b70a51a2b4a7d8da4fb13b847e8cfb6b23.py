"""只读核验当前交付，不启动业务服务、不改原始工件。"""
from pathlib import Path
from collections import Counter
import csv
import gzip
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
BUSINESS = Path('/Volumes/workSSD/software development/gbc-worktrees/real-painpoint-fixes-20261104')
PACKAGE = ROOT / 'outputs/real_painpoint_effect_closeout_20261105'
OUT = Path(__file__).resolve().parent
BASE = ROOT / 'outputs/real_painpoint_fixes_followup_20261104/regression160/base'
ONE, TWO = PACKAGE/'candidate_d1_final', PACKAGE/'candidate_d2_final'

def read(p):
    if p.suffix == '.gz':
        with gzip.open(p, 'rt', encoding='utf-8') as f:
            return json.load(f)
    return json.loads(p.read_text())

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def dump(name, obj):
    (OUT/name).write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n')

def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)

# 全文比较仅排除逐次运行产生的字段；字段清单随证据公开，不靠数量比较。
VOLATILE = {'id', 'created_at', 'analysis_revision', 'created_at_ms', 'updated_at'}
def normalized(obj):
    if isinstance(obj, dict):
        return {k:normalized(v) for k,v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [normalized(v) for v in obj]
    return obj

handoff = read(PACKAGE/'FINAL_HANDOFF.json')
hash_check = []
for name, expected in handoff['artifacts_sha256'].items():
    hash_check.append({'path':name, 'expected':expected, 'actual':sha(PACKAGE/name)})
manifest = []
for line in (PACKAGE/'SHA256SUMS').read_text().splitlines():
    m = re.fullmatch(r'([0-9a-f]{64})\s+\*?(.*)',line)
    if m:
        manifest.append({'path':m[2], 'expected':m[1], 'actual':sha(PACKAGE/m[2])})
metas = [read(r/'meta.json') for r in [BASE,ONE,TWO]]
identity = {'business_head':subprocess.check_output(['git','-C',str(BUSINESS),'rev-parse','HEAD'],text=True).strip(),
            'candidate_dirty':bool(subprocess.check_output(['git','-C',str(BUSINESS),'status','--porcelain'],text=True).strip()),
            'frozen_rule_identity_sha256':sha(BUSINESS/'src/engine/rule_identity.py'),
            'policy_sha256':sha(BUSINESS/'scripts/effect_acceptance.py'),
            'handoff_hash_registered':len(hash_check), 'handoff_hash_mismatch':[r for r in hash_check if r['expected']!=r['actual']],
            'manifest_registered':len(manifest), 'manifest_mismatch':[r for r in manifest if r['expected']!=r['actual']],
            'collections':[]}
for root,meta in zip([BASE,ONE,TWO],metas):
    identity['collections'].append({'root':str(root),'head':meta['code']['git_head'],
        'code_digest':meta['code']['digest'],'started_at':meta['started_at'],'finished_at':meta['finished_at'],
        'entries':len(meta['entries']), 'failed':meta['failed'], 'jobs':meta['jobs'],
        'sample_count':len(meta['samples'])})
identity['sample_id_and_sha_equal'] = metas[0]['samples']==metas[1]['samples']==metas[2]['samples']
identity['candidate_code_files_mismatch'] = [name for name,digest in metas[1]['code']['files'].items()
                                            if not (BUSINESS/name).exists() or sha(BUSINESS/name)!=digest]
dump('identity_and_hashes.json',identity)

spec = importlib.util.spec_from_file_location('effect_review_replay',BUSINESS/'scripts/effect_acceptance.py')
effect = importlib.util.module_from_spec(spec); spec.loader.exec_module(effect)
replay = effect.compare_collections(BASE,ONE)
dump('effect_report_replayed.json',replay['report'])
dump('transition_ledger_replayed.json',replay['ledger'])

full_record_diffs, repeated_formal_diffs, base_formal_diffs = [], [], []
raw_counts = Counter()
independent_revisions = 0
transition_counts = Counter(row['conclusion'] for row in replay['ledger'])
for path in sorted((ONE/'records').glob('*.json.gz')):
    name = path.name
    r1,r2 = read(path),read(TWO/'records'/name)
    if r1!=r2:
        full_record_diffs.append({'sample_id':r1['sample_id'], 'fields':[k for k in r1 if r1[k]!=r2.get(k)]})
    statusname = name.replace('.json.gz','.status.json.gz')
    s0,s1,s2 = [read(r/'status'/statusname) for r in [BASE,ONE,TWO]]
    if s1['analysis_revision']!=s2['analysis_revision']:
        independent_revisions+=1
    sets=[]
    for prefix,status,record in [('baseline',s0,None),('candidate1',s1,r1),('candidate2',s2,r2)]:
        issues=status['review_report']['issues']
        raw_counts[f'{prefix}_formal']+=len(issues)
        raw_counts[f'{prefix}_result_all']+=len(status['result']['issues']['all'])
        if record is not None: raw_counts[f'{prefix}_normalized_findings']+=len(record['findings'])
        sets.append(Counter(canonical(normalized(i)) for i in issues))
    def delta(a,b):
        return {'removed':[json.loads(k) for k,n in (a-b).items() for _ in range(n)],
                'added':[json.loads(k) for k,n in (b-a).items() for _ in range(n)]}
    if sets[1]!=sets[2]: repeated_formal_diffs.append({'sample_id':r1['sample_id'],**delta(sets[1],sets[2])})
    if sets[0]!=sets[1]: base_formal_diffs.append({'sample_id':r1['sample_id'],**delta(sets[0],sets[1])})
dump('raw_formal_findings_diffs.json', {'volatile_exclusions':sorted(VOLATILE),
      'repeat_diffs':repeated_formal_diffs,'base_candidate_diffs':base_formal_diffs})

ten=[]
for num,entry in handoff['ten_material'].items():
    directory=next((PACKAGE/'evidence/current').glob(f'{int(num):02d}_{entry["job_id"][:8]}'))
    status=read(directory/'status_final.json')
    check={r['obligation_id']:r for r in status['check_coverage']['instances']}
    ten.append({'order':int(num),'job_id':status['job_id'],'revision':status['analysis_revision'],
       'status':status['status'],'completed':status['check_coverage']['completed_total'],
       'applicable':status['check_coverage']['applicable_total'], 'unresolved':status['check_coverage']['unresolved_total'],
       'CMM007':[{'title':i['title'],'severity':i['severity']} for i in status['review_report']['issues'] if i.get('rule_id')=='CMM-007'],
       'key_obligations':{k:{'status':check[k]['status'],'reason_codes':check[k].get('reason_codes'),
                 'next_action':check[k].get('next_action'),
                 'operand_pages':sorted({m['page'] for m in check[k].get('missing_operands',[]) if isinstance(m.get('page'),int)}),
                 'operand_stages':sorted({m['stage'] for m in check[k].get('missing_operands',[]) if m.get('stage')})}
          for k in ['OBL-INTERNAL-T3-TOTAL','OBL-TXT-T7','OBL-SG-TOTAL']}})
dump('ten_material_checked.json',ten)

# 导出文件内容补证；无需重启数据库即可核对已保存响应。
export=PACKAGE/'evidence/final_chain'
j=read(export/'export_before.json')
issues=j['issues']['all'] if isinstance(j['issues'],dict) else j['issues']
with (export/'export_before.csv').open(encoding='utf-8-sig',newline='') as f:
    csvrows=list(csv.DictReader(f))
csvissues={r['issue_id']:r for r in csvrows if r['record_type'] in ('issue','tip')}
csv_mismatches=[]
summary_row=next(r for r in csvrows if r['record_type']=='summary')
for field in ['analysis_revision','review_report_sha256']:
    if summary_row[field]!=j[field]:csv_mismatches.append({'scope':'document','field':field})
for i in issues:
    r=csvissues.get(i['id'])
    if not r:
        csv_mismatches.append({'id':i['id'],'reason':'missing'});continue
    for jk,ck in [('rule_id','rule_id'),('title','title'),('severity','severity')]:
        if str(i.get(jk) or '')!=r[ck]:csv_mismatches.append({'id':i['id'],'field':jk,'json':i.get(jk),'csv':r[ck]})
    for field in ['analysis_revision','review_report_sha256']:
        # CSV 的报告身份可从 summary 行继承；空的逐行字段不是矛盾身份。
        if r[field] and r[field]!=j[field]:csv_mismatches.append({'id':i['id'],'field':field})
import fitz
with fitz.open(export/'export_before.pdf') as pdf:
    pdftext=''.join(p.get_text() for p in pdf)
compact=re.sub(r'\s+','',pdftext)
pdf_checks=[{'rule_id':i['rule_id'],'title':i['title'],
             'rule_present':i['rule_id'] in compact,
             'title_present':re.sub(r'\s+','',i['title']) in compact} for i in issues]
export_check={'json_count':len(issues),'csv_real_rows':len(csvrows),'csv_issue_or_tip_count':len(csvissues),
              'csv_document_identity':{k:summary_row[k] for k in ['analysis_revision','review_report_sha256']},
              'csv_issue_fields_mismatches':csv_mismatches,'pdf_rule_title_checks':pdf_checks,
              'json_revision':j['analysis_revision'],'pdf_revision_present':j['analysis_revision'] in compact,
              'pdf_report_sha_present':j['review_report_sha256'] in compact,
              'scope':'静态核对保存的三种导出；旧 revision 与 basis HTTP 依据已有运行日志，未重新启动服务'}
dump('export_content_checked.json',export_check)
summary={'identity_ok':not(identity['candidate_dirty'] or identity['candidate_code_files_mismatch'] or identity['manifest_mismatch'] or identity['handoff_hash_mismatch']),
 'raw_counts':dict(raw_counts),'independent_analysis_revisions':independent_revisions,
 'full_record_repeat_diffs':full_record_diffs, 'raw_formal_repeat_changed_samples':len(repeated_formal_diffs),
 'raw_formal_base_changed_samples':len(base_formal_diffs), 'transition_counts':dict(transition_counts),
 'raw_formal_delta':{'removed':sum(len(x['removed']) for x in base_formal_diffs),'added':sum(len(x['added']) for x in base_formal_diffs)},
 'completed_10':sum(x['completed'] for x in ten), 'csv_mismatches':len(csv_mismatches),
 'pdf_rule_title_missing':sum(not(x['rule_present'] and x['title_present']) for x in pdf_checks)}
dump('audit_summary.json',summary)
print(json.dumps(summary, ensure_ascii=False,indent=2))
