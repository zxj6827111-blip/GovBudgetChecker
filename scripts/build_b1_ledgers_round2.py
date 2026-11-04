# -*- coding: utf-8 -*-
import csv, json, os, sys
OUT=sys.argv[1]
old=list(csv.DictReader(open(os.path.join(OUT,'revisions/round1_ledgers/candidate_screen_log.csv'),encoding='utf-8-sig')))
# targets actually verified per doc (from round1 log + review corrections)
VERIFIED={
 "T01":[],
 "T02":["RA-001","RA-007","RA-021","RB-2023-D01","RA-006","RA-008","RA-012"],
 "T03":["RA-001","RA-007","RB-2023-D01","RA-006","RA-008","RA-012"],
 "T04":["RA-021","RB-2024-D05"],
 "T05":["RA-001","RA-007","RA-021","RB-2023-D01","RA-006","RA-008","RA-012","RA-022"],
 "T06":[],
 "T07":["RA-001","RA-005","RA-011","RA-030","RB-2025-D02","RA-022","RA-016"],
 "T08":["RA-001","RA-008","RA-012","RA-016"],
 "X01":["RA-031"],
 "X02":[],
 "N02":["RA-009","RA-024","RA-031","RA-040"],
 "N06":["RB-2023-D05","RB-2024-D06"],
 "N08":["RA-021"],
}
CAP={"T01":8,"T02":6,"T03":6,"T04":6,"T05":8,"T06":6,"T07":6,"T08":4,"X01":6,"X02":6,"N02":7,"N06":5,"N08":6}
REPORTED={"T01":5,"T02":8,"T03":7,"T04":2,"T05":9,"T06":2,"T07":8,"T08":4,"X01":1,"X02":2,"N02":4,"N06":2,"N08":1}
STATUS={"T01":"INCOMPLETE","T02":"PENDING_REVIEW","T03":"NOT_FOUND","T04":"INCOMPLETE","T05":"PENDING_REVIEW","T06":"INCOMPLETE","T07":"PENDING_REVIEW","T08":"NOT_FOUND","X01":"INCOMPLETE","X02":"INCOMPLETE","N02":"PENDING_REVIEW","N06":"PENDING_REVIEW","N08":"INCOMPLETE"}
CAND={"T02":"B1-2023-D01","T03":"","T05":"B1-2023-D01","T07":"B1-2025-D01;B1-2025-U02","N02":"B1-2025-U01","N06":"B1-2024-D02"}
NOTE={
 "T01":"初版台账自报5份，筛选日志未登记T01目标；本版撤回为0，不虚报。",
 "T02":"初版自报8/上限6；剔除RA-016仅看p14（非T02收入表）后7份有效，仍超1份；保留超限历史，不追加。B1-2023-D01全项初标T02 defect（2140101等5行）。",
 "T03":"初版自报7/上限6；剔除RA-016 p14后6份有效；B1-2025-D01全项初标T03 pass（0.02在24末级项R1包络0.125内），无缺陷。",
 "T04":"2份有效，未用尽6份上限。",
 "T05":"初版自报9/上限8；剔除RA-016 p14后8份有效；B1-2023-D01全项初标T05 defect（2210201项目支出重复5,697.24）。",
 "T06":"初版自报2份，筛选日志未登记T06目标；本版撤回为0，不虚报。",
 "T07":"初版自报8/上限6；剔除RA-024未定位T07页后7份有效，仍超1份；保留超限历史，不追加。B1-2025-D01/U02全项初标T07 defect。",
 "T08":"4份有效，用满4份上限；无T08缺陷。",
 "X01":"1份有效，未用尽6份上限。",
 "X02":"初版自报2份，筛选日志未登记X02目标；本版撤回为0，不虚报。",
 "N02":"4份有效；B1-2025-U01全项初标N02 defect。",
 "N06":"2份有效；B1-2024-D02全项初标N06 defect（0.02，T06保持pass）。",
 "N08":"1份有效，未用尽6份上限。",
}
# target evidence
ev=[]
for t,docs in VERIFIED.items():
    for d in docs:
        row=next((r for r in old if r['sample_id']==d),None)
        ev.append({"target":t,"sample_id":d,"valid":"yes","pages":row['pages_viewed'] if row else "","family":"A" if t[0] in "TX" else "B","note":"逐目标有效核对"})
    # invalid historical
    if t in ("T02","T03","T05"):
        ev.append({"target":t,"sample_id":"RA-016","valid":"no","pages":"14","family":"A","note":"只看了p14三公表，未看收入/支出/功能表，不能算该目标有效核对"})
    if t=="T07":
        ev.append({"target":t,"sample_id":"RA-024","valid":"no","pages":"17-19","family":"A","note":"未定位T07三公表/说明页，不计有效核对"})
with open(os.path.join(OUT,'target_evidence.csv'),'w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(ev[0])); w.writeheader(); w.writerows(ev)
# budget ledger
bl=[]
for t in CAP:
    ver=len(VERIFIED[t]); rep=REPORTED[t]; cap=CAP[t]
    bl.append({"target_id":t,"reported_effective":rep,"verified_effective":ver,"cap":cap,
      "reported_over_cap":max(0,rep-cap),"verified_over_cap":max(0,ver-cap),"status":STATUS[t],
      "candidate_sample":CAND.get(t,""),"note":NOTE[t]})
with open(os.path.join(OUT,'budget_ledger.csv'),'w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(bl[0])); w.writeheader(); w.writerows(bl)
# family counts from old log targets
fam={"A":set(),"B":set(),"C":set()}
for r in old:
    ts=[x.strip() for x in r['targets'].split(',') if x.strip()]
    for t in ts:
        if t.startswith('T') or t.startswith('X'): fam['A'].add(r['sample_id'])
        elif t.startswith('N'): fam['B'].add(r['sample_id'])
    if r['sample_id'] in ('RA-001','RA-021','RB-2023-D05'):
        fam['C'].add(r['sample_id'])
fb=[{"family":"A","unique_docs":len(fam['A']),"budget_units":len(fam['A']),"cap":40,"note":"表内/表间家族；按唯一PDF计"},
    {"family":"B","unique_docs":len(fam['B']),"budget_units":len(fam['B']),"cap":20,"note":"说明一致性家族；按唯一PDF计"},
    {"family":"C","unique_docs":len(fam['C']),"budget_units":len(fam['C']),"cap":10,"note":"正常/易误报对照线索；均未完成annotation，状态INCOMPLETE"}]
with open(os.path.join(OUT,'family_budget.csv'),'w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(fb[0])); w.writeheader(); w.writerows(fb)
# target status
ts=[]
NAMES={"T01":"收支总表平衡","T02":"收入表行来源和","T03":"支出表合计=基本+项目","T04":"财政拨款总表平衡","T05":"一般公共预算支出表类=款=项","T06":"基本支出表=人员+公用","T07":"三公表合计=三项","T08":"经济分类表类=款","X01":"总表vs收入/支出表","X02":"基金/国资列","N02":"收入说明vs收入表","N06":"基本支出说明vs表","N08":"基金/国资说明vs表"}
for t in CAP:
    ts.append({"target_id":t,"target_name":NAMES[t],"target_type":"defect","status":STATUS[t],
      "effective_screened":len(VERIFIED[t]),"budget_cap":CAP[t],"candidate_sample":CAND.get(t,""),
      "evidence_path":f"annotations/{CAND[t].split(';')[0]}/annotation.json" if CAND.get(t) else "",
      "note":NOTE[t]})
for cid,name,note in [("C-R1","R1舍入包络内对照","RA-001线索；无annotation，状态INCOMPLETE"),
 ("C-CROSS","跨页表对照","RA-021线索；无annotation，状态INCOMPLETE"),
 ("C-EMPTY","空表正确说明对照","RA-001线索；无annotation，状态INCOMPLETE"),
 ("C-ZERO","零三公正确说明对照","未找到样本；INCOMPLETE")]:
    ts.append({"target_id":cid,"target_name":name,"target_type":"control","status":"INCOMPLETE",
      "effective_screened":0,"budget_cap":4,"candidate_sample":"","evidence_path":"","note":note})
with open(os.path.join(OUT,'target_status.csv'),'w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(ts[0])); w.writeheader(); w.writerows(ts)
# candidates
cands=[
 {"candidate_id":"B1C-01","sample_id":"B1-2025-U01","targets":"N02","pdf_path":"uploads/521b0a03d524f2ec965ad95e1a17ad32/上海商业会计学校单位2025年度决算.pdf","pages":"6,17","annotation_path":"annotations/B1-2025-U01/annotation.json","review_status":"PENDING_REVIEW","note":"N02 defect；同份另有N05/N07/W01 defect"},
 {"candidate_id":"B1C-02","sample_id":"B1-2025-D01","targets":"T07","pdf_path":"uploads/931570468e85953577c1529234e20510/上海市人力资源和社会保障局2025年度部门决算.pdf","pages":"20","annotation_path":"annotations/B1-2025-D01/annotation.json","review_status":"PENDING_REVIEW","note":"T07 defect；同份另有N07/W01 defect"},
 {"candidate_id":"B1C-03","sample_id":"B1-2024-D02","targets":"N06","pdf_path":"uploads/72c3ffe3f84474ba05eb8d238d3d2732/上海市民族和宗教事务局.pdf","pages":"16,24","annotation_path":"annotations/B1-2024-D02/annotation.json","review_status":"PENDING_REVIEW","note":"N06 defect（0.02，T06保持pass）；同份另有S04/N05 defect"},
 {"candidate_id":"B1C-04","sample_id":"B1-2025-U02","targets":"T07","pdf_path":"uploads/74d2f14063620a23468f32aeae4b3c6b/上海航宇科普中心（上海市工业文化发展中心）2025年度决算.pdf","pages":"14","annotation_path":"annotations/B1-2025-U02/annotation.json","review_status":"PENDING_REVIEW","note":"T07 defect；同份另有S03/S04/N05/N09 defect；clean prompt初标"},
 {"candidate_id":"B1C-05","sample_id":"B1-2023-D01","targets":"T02,T05,C-02(非目标)","pdf_path":"uploads/a4e896d1bbfc55a050aa3c6e7cc90130/上海市交通委员会.pdf","pages":"9-11,20","annotation_path":"annotations/B1-2023-D01/annotation.json","review_status":"PENDING_REVIEW","note":"T02/T05 defect；同份另有S03/S04/N05 defect；C-02为free finding"},
]
with open(os.path.join(OUT,'candidates.csv'),'w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(cands[0])); w.writeheader(); w.writerows(cands)
print("targets",len(ts),"candidates",len(cands),"evidence",len(ev),"family",fb)
