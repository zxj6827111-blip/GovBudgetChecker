# -*- coding: utf-8 -*-
import csv, gzip, hashlib, json, os, sys, glob
from datetime import datetime, timezone, timedelta

SRC = "/Volumes/workSSD/software development/GovBudgetChecker"
OUT = sys.argv[1]
CST = timezone(timedelta(hours=8)); NOW = datetime.now(CST).isoformat(timespec="seconds")

def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for c in iter(lambda:f.read(1<<20),b''): h.update(c)
    return h.hexdigest()

# 33 gold
froz={}
with open(os.path.join(SRC,'gold/FROZEN.csv'),encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): froz[r['sample_id']]=r
gold=[]; gold_ok=0
for sid,r in sorted(froz.items()):
    p=os.path.join(SRC,'gold',sid,'annotation.json')
    h=sha(p); ok=h==r['annotation_sha256']; gold_ok+=ok
    gold.append({"sample_id":sid,"annotation_path":p,"sha256":h,"frozen_sha256":r['annotation_sha256'],"match":ok})

# pools
uploads=glob.glob(os.path.join(SRC,'uploads/*/*.pdf'))
upload_hashes={}
for p in uploads: upload_hashes.setdefault(sha(p),p)
recs=glob.glob(os.path.join(SRC,'outputs/fp_batch1_20261103/nongold/new/records/*.json.gz'))
rec_hashes={}
for p in recs:
    d=json.load(gzip.open(p,'rt',encoding='utf-8')); rec_hashes[d['sha256']]=p

# candidate pdfs from annotations + screen log
cands={}
for p in glob.glob(os.path.join(OUT,'annotations','*','annotation.json')):
    a=json.load(open(p,encoding='utf-8')); cands[a['sample_id']]=a['sha256']
# screen log paths
with open(os.path.join(OUT,'candidate_screen_log.csv'),encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): cands.setdefault(r['sample_id'],r['sha256'])

manifest={
 "task":"GovBudgetChecker 金标补样 B1",
 "run_id":os.path.basename(OUT),
 "created_at":NOW,
 "branch":"codex/gold-b1-20261103",
 "plan_commit":"d90ba45956613c13900d1d972a90557a77a1b6cf",
 "snapshot_commit":"48ee4e28441bd6ddbf34beee85bc6883d60db773",
 "source_project":SRC,
 "runtime_profile":"outputs/pending_review_20261101/acceptance_kit/v1-regression-r4/runtime_profile.json",
 "runtime_conditions":{"AI":"off","OCR":"off","DATABASE_URL":"empty","dotenv":"off","GBC_TEMPLATE_SCOPE":"0"},
 "pools":{
   "uploads_pdfs":len(uploads),"uploads_unique_sha":len(upload_hashes),
   "nongold_records_160":len(recs),"nongold_unique_sha":len(rec_hashes),
   "intersection_sha":len(set(upload_hashes)&set(rec_hashes)),
   "screened_unique_docs":len(set(cands.values())),
 },
 "gold":{"expected":33,"frozen_match":gold_ok,"samples":gold},
 "candidate_pdfs":[{"sample_ids":sorted([k for k,v2 in cands.items() if v2==v]),"sha256":v} for v in sorted(set(cands.values()))],
 "dependencies":{
   "python":sys.version.split()[0],
   "pymupdf":__import__("fitz").__doc__ or "unknown",
   "pdfplumber":getattr(__import__("pdfplumber"),"__version__","unknown"),
 },
 "artifacts":{
   "execution_contract":os.path.join(OUT,'execution_contract.json'),
   "candidate_screen_log":os.path.join(OUT,'candidate_screen_log.csv'),
   "candidates":os.path.join(OUT,'candidates.csv'),
   "target_status":os.path.join(OUT,'target_status.csv'),
   "annotations_dir":os.path.join(OUT,'annotations'),
 }
}
files=[]
for root,dirs,names in os.walk(OUT):
    dirs[:]=[d for d in dirs if d not in ("code_snapshot","work")]
    for n in names:
        p=os.path.join(root,n); rel=os.path.relpath(p,OUT)
        if rel.startswith("pipeline/work/"): continue
        files.append({"path":rel,"sha256":sha(p),"bytes":os.path.getsize(p)})
manifest["files"]=sorted(files,key=lambda x:x["path"])
json.dump(manifest,open(os.path.join(OUT,'source_manifest.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=2)

progress={
 "run_id":os.path.basename(OUT),"updated_at":NOW,
 "steps":[
  {"step":"计划与快照固定","status":"done","detail":"plan d90ba459; snapshot 48ee4e28"},
  {"step":"33份旧金标核验","status":"done","detail":f"{gold_ok}/33 FROZEN 一致"},
  {"step":"候选池去重","status":"done","detail":f"uploads {len(uploads)}/{len(upload_hashes)}; 160 records/{len(rec_hashes)}; 交集{len(set(upload_hashes)&set(rec_hashes))}"},
  {"step":"候选筛选","status":"done","detail":f"{len(set(cands.values()))} 份唯一PDF实际核对；5 个候选（含1个非目标C-02）"},
  {"step":"初标","status":"done","detail":"5 份 annotation，24项齐全，PENDING_REVIEW；非目标检查CD待复核"},
  {"step":"机器核验","status":"done","detail":"quote/页码/24项核验通过；见 machine_checks.json"},
  {"step":"独立复核/冻结","status":"blocked","detail":"无可用独立复核角色；未写 reviewed_by/truth_frozen_at；等待 Codex"},
  {"step":"预算规范","status":"draft","detail":"27项草案完成；未批准"},
  {"step":"预算试点","status":"blocked","detail":"本地无预算PDF（唯一文件名“预算”实为决算）；待补材料"},
  {"step":"系统比较","status":"not_run","detail":"无冻结新样本；保留入口，待复核后运行"},
 ]
}
json.dump(progress,open(os.path.join(OUT,'progress.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=2)
print(json.dumps({"gold":gold_ok,"uploads":len(uploads),"records":len(recs),"screened":len(set(cands.values()))},ensure_ascii=False))
