# -*- coding: utf-8 -*-
import csv, gzip, hashlib, json, os, sys, glob, re
from datetime import datetime, timezone, timedelta
SRC="/Volumes/workSSD/software development/GovBudgetChecker"; OUT=sys.argv[1]
CSF=os.path.join(sys.argv[1],'run_started_at.txt')
CST=timezone(timedelta(hours=8))
if os.path.exists(CSF):
    NOW=open(CSF,encoding='utf-8').read().strip()
else:
    NOW=datetime.now(CST).isoformat(timespec='seconds'); open(CSF,'w',encoding='utf-8').write(NOW+'\n')
def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for c in iter(lambda:f.read(1<<20),b''): h.update(c)
    return h.hexdigest()
# gold
froz={}
with open(os.path.join(SRC,'gold/FROZEN.csv'),encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): froz[r['sample_id']]=r
gold=[]; gold_ok=0
for sid,r in sorted(froz.items()):
    p=os.path.join(SRC,'gold',sid,'annotation.json'); h=sha(p); ok=h==r['annotation_sha256']; gold_ok+=ok
    gold.append({"sample_id":sid,"annotation_path":p,"sha256":h,"frozen_sha256":r['annotation_sha256'],"match":ok})
# pools
uploads=glob.glob(os.path.join(SRC,'uploads/*/*.pdf')); uh={}
for p in uploads: uh.setdefault(sha(p),p)
recs=glob.glob(os.path.join(SRC,'outputs/fp_batch1_20261103/nongold/new/records/*.json.gz')); rh={}
for p in recs:
    d=json.load(gzip.open(p,'rt',encoding='utf-8')); rh[d['sha256']]=p
cands={}
for p in glob.glob(os.path.join(OUT,'annotations','*','annotation.json')):
    a=json.load(open(p,encoding='utf-8')); cands[a['sample_id']]=a['sha256']
with open(os.path.join(OUT,'candidate_screen_log.csv'),encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): cands.setdefault(r['sample_id'],r['sha256'])
# b1 pdfs mapping self-contained (annotation sample_id -> absolute PDF path)
ann_ids={json.load(open(p,encoding='utf-8'))['sample_id'] for p in glob.glob(os.path.join(OUT,'annotations','*','annotation.json'))}
pdf_map={}
with open(os.path.join(OUT,'candidates.csv'),encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        if r['sample_id'] in ann_ids:
            pdf_map[r['sample_id']]=os.path.join(SRC,r['pdf_path'])
json.dump(dict(sorted(pdf_map.items())),open(os.path.join(OUT,'b1_pdfs.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=2)
# checksum audit
audit=[]
for p in sorted(glob.glob(os.path.join(OUT,'pipeline','status','*.status.json.gz'))):
    d=json.load(gzip.open(p,'rt',encoding='utf-8')); raw=d.get('checksum') or ''
    norm=raw.strip().lower(); audit.append({"sample_id":d.get('job_id') or os.path.basename(p).split('.')[0],
      "raw_len":len(raw),"trailing_cr":raw.endswith('\r'),"normalized_len":len(norm),
      "normalized_64":bool(re.fullmatch(r'[0-9a-f]{64}',norm)),"raw_checksum":raw,"normalized_checksum":norm})
with open(os.path.join(OUT,'checksum_audit.csv'),'w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(audit[0])); w.writeheader(); w.writerows(audit)
# progress first
progress={"run_id":os.path.basename(OUT),"updated_at":NOW,"steps":[
 {"step":"计划与快照固定","status":"done","detail":"plan d90ba459; snapshot 48ee4e28"},
 {"step":"33份旧金标核验","status":"done","detail":f"{gold_ok}/33 FROZEN 一致"},
 {"step":"候选池去重","status":"done","detail":f"uploads {len(uploads)}/{len(uh)}; 160 records/{len(rh)}; 交集{len(set(uh)&set(rh))}"},
 {"step":"候选筛选","status":"done","detail":f"{len(set(cands.values()))} 份唯一PDF实际核对；5 个候选（含1个非目标C-02）"},
 {"step":"独立初标","status":"done","detail":"5 份全项 annotation，24 项齐全，0 个占位 CD；PENDING_REVIEW"},
 {"step":"机器核验","status":"done","detail":"full checker + Decimal 复算；见 machine_checks_full.json / decimal_checks.csv"},
 {"step":"独立复核/冻结","status":"blocked","detail":"无可用独立复核角色；未写 reviewed_by/truth_frozen_at；等待 Codex"},
 {"step":"预算规范","status":"draft","detail":"27项草案完成；未批准"},
 {"step":"预算试点","status":"blocked","detail":"扫描范围内0份可用预算PDF；待补材料"},
 {"step":"系统比较","status":"not_run","detail":"无冻结新样本；保留入口，待复核后运行"},
]}
json.dump(progress,open(os.path.join(OUT,'progress.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=2)
# manifest
manifest={"task":"GovBudgetChecker 金标补样 B1","run_id":os.path.basename(OUT),"created_at":NOW,
 "branch":"codex/gold-b1-20261103","plan_commit":"d90ba45956613c13900d1d972a90557a77a1b6cf",
 "snapshot_commit":"48ee4e28441bd6ddbf34beee85bc6883d60db773","source_project":SRC,
 "runtime_profile":"outputs/pending_review_20261101/acceptance_kit/v1-regression-r4/runtime_profile.json",
 "runtime_conditions":{"AI":"off","OCR":"off","DATABASE_URL":"empty","dotenv":"off","GBC_TEMPLATE_SCOPE":"0"},
 "pools":{"uploads_pdfs":len(uploads),"uploads_unique_sha":len(uh),"nongold_records_160":len(recs),
   "nongold_unique_sha":len(rh),"intersection_sha":len(set(uh)&set(rh)),"screened_unique_docs":len(set(cands.values()))},
 "gold":{"expected":33,"frozen_match":gold_ok,"samples":gold},
 "candidate_pdfs":[{"sample_ids":sorted([k for k,v2 in cands.items() if v2==v]),"sha256":v} for v in sorted(set(cands.values()))],
 "dependencies":{"python":sys.version.split()[0],"pymupdf":__import__("fitz").__doc__ or "unknown",
   "pdfplumber":getattr(__import__("pdfplumber"),"__version__","unknown")},
 "helper_scripts":{p:sha(os.path.join(SRC,p)) for p in ("scripts/gold_baseline_run.py",)} | {"scripts/run_candidate_pipeline.py":sha("/Volumes/workSSD/software development/gbc-worktrees/gold-b1-20261103/scripts/run_candidate_pipeline.py")},
 "checksum_audit":{"raw_with_trailing_cr":sum(1 for a in audit if a["trailing_cr"]),
   "normalized_64":sum(1 for a in audit if a["normalized_64"]),"total":len(audit)},
 "artifacts":{"execution_contract":os.path.join(OUT,'execution_contract.json'),
   "candidate_screen_log":os.path.join(OUT,'candidate_screen_log.csv'),"candidates":os.path.join(OUT,'candidates.csv'),
   "target_status":os.path.join(OUT,'target_status.csv'),"annotations_dir":os.path.join(OUT,'annotations')},
}
files=[]
for root,dirs,names in os.walk(OUT):
    dirs[:]=[d for d in dirs if d not in ("code_snapshot","work")]
    for n in names:
        p=os.path.join(root,n); rel=os.path.relpath(p,OUT)
        if rel in ("source_manifest.json","selfcheck.json") or rel.startswith("pipeline/work/"): continue
        files.append({"path":rel,"sha256":sha(p),"bytes":os.path.getsize(p)})
manifest["files"]=sorted(files,key=lambda x:x["path"])
json.dump(manifest,open(os.path.join(OUT,'source_manifest.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=2)
print(json.dumps({"gold":gold_ok,"uploads":len(uploads),"records":len(recs),"screened":len(set(cands.values())),"files":len(files),"checksum_cr":manifest["checksum_audit"]},ensure_ascii=False))
