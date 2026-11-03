# -*- coding: utf-8 -*-
"""生成 B1 的筛选日志、候选清单、目标状态、合同/清单/进度。"""
import csv, hashlib, json, os, sys
from datetime import datetime, timezone, timedelta

SRC = "/Volumes/workSSD/software development/GovBudgetChecker"
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
CST = timezone(timedelta(hours=8)); NOW = datetime.now(CST).isoformat(timespec="seconds")

# sid -> (pdf_path, year, scope, targets, family, pages, quote, result, reason)
ROWS = {
"RA-001": ("uploads/075e31ba6f282134daad0b9ae225d0a5/上海科技管理学校2025年度决算.pdf", 2025, "unit",
  "T02,T03,T05,T07,T08", "A", "5-7,12-14",
  "收入合计10,367.43；支出10,511.02；三公合计4.15=0+4.15+0",
  "exclude", "T02/T03/T05 差0.01在R1包络内；T07表内合计成立；空表说明正确。另有非目标V33-251(4.00)/CMM-007(37.67)真实问题，供B2。"),
"RA-005": ("uploads/267d5edb686f1a041b9a6294c0aeeba7/复旦大学附属中学2025年度决算.pdf", 2025, "unit",
  "T07", "A", "13,19-20",
  "三公表合计53.45=8.00+37.45+8.00；说明运行维护12.5 vs 表12.45",
  "exclude", "T07表内合计成立；12.5 vs 12.45 是N07/B-07表文差异（非本轮13目标），供B2。"),
"RA-007": ("uploads/34bb0eeb41d8c7571ee10a6de09aca21/上海市建设用地和土地整理事务中心2025年度决算.pdf", 2025, "unit",
  "T02,T03,T05", "A", "8,10",
  "21211 农业土地开发资金安排的支出整行重复两遍，系统按一行参与合计",
  "exclude", "重复行属结构/模板问题；合计在R1内，无T02/T03/T05数值缺陷。"),
"RA-009": ("uploads/521b0a03d524f2ec965ad95e1a17ad32/上海商业会计学校单位2025年度决算.pdf", 2025, "unit",
  "N02", "B", "6,17",
  "表内财政拨款收入13,859.21；说明写17976.00；本年收入合计17975.85",
  "candidate", "N02真实缺陷：说明财政拨款收入与表差4,116.79，疑将本年收入合计误作财政拨款收入。"),
"RA-011": ("uploads/5c244dd027caa04fc47188406051cd7e/上海市粮食和物资储备局2025年度决算.pdf", 2025, "unit",
  "T07", "A", "19-21",
  "三公表与说明未见T07合计不平；V33-227/CMM-007为非目标",
  "exclude", "T07无候选；非目标名称/完成率为真实问题，供B2。"),
"RA-021": ("uploads/8ff8c793e1b074f694884c12c7b77966/上海市规划和自然资源局2025年度部门决算.pdf", 2025, "department",
  "T02,T04,T05,N08", "A", "9-11,21-22",
  "收入合计120,364.19；212父级100,298.27；212明细12,806.28+42,625.33+16,728.78+21,093.38+7,044.50=100,298.27",
  "control", "系统0.08/0.09差额为提取伪差；手工复算相等。作为T02/T05跨页表正常对照。"),
"RA-022": ("uploads/931570468e85953577c1529234e20510/上海市人力资源和社会保障局2025年度部门决算.pdf", 2025, "department",
  "T03,T07", "A", "11,20",
  "三公合计808.46；457.69+328.11+22.71=808.51；支出合计274,528.36；基本106,641.23+项目167,887.11=274,528.34",
  "candidate", "T07真实差0.05（3项包络0.02）；T03差0.02刚超2项包络0.015，列待复核边界。"),
"RA-024": ("uploads/948b3f9c11e83aaac5593d52e96c198d/上海市节能中心2025年度决算.pdf", 2025, "unit",
  "T07,N02", "A/B", "17-19",
  "三公表/说明未见T07/N02目标问题；V33-222住房公积金82.33 vs 62.33为非目标",
  "exclude", "T07/N02无候选；住房公积金20.00差额是真实N05/W01类问题，供B2。"),
"RA-031": ("uploads/c100925258b373fe9a07f252fe70b1e0/上海市军粮供应和物资储备事务中心2025年度决算.pdf", 2025, "unit",
  "N02,X01", "B", "7,18",
  "表内其他收入0.03；说明写0.03元；总表/收入表合计2,131.63一致",
  "exclude", "N02金额一致，差在单位（0.03元应0.03万元），归W01/C-14非目标；X01总表与收入表一致。"),
"RB-2023-D01": ("uploads/a4e896d1bbfc55a050aa3c6e7cc90130/上海市交通委员会.pdf", 2023, "department",
  "T02,T05", "A", "9-11",
  "收入表21401父级286,236.11；项级之和286,297.57，差61.46；合计2,142,495.38 vs 明细2,142,433.93，差61.45",
  "exclude", "真实层级缺陷，但属收入决算表C-02，不属T02（行来源和）或T05（一般公共预算支出表）；列入非目标候选供B2。"),
"RB-2023-D05": ("uploads/65324c6dc7b65e01eb69bde0ef235ab5/上海市商务委员会.pdf", 2023, "department",
  "N06", "B", "16,27",
  "基本支出表合计20,518.47；说明20,518.47；人员+公用=20,518.47",
  "control", "系统20,518.50为提取伪差；表文一致。作为N06正常对照。"),
"RB-2024-D05": ("uploads/7be587eeff3114c0232c5f6c56033816/上海市文化和旅游局.pdf", 2024, "department",
  "T04", "A", "14-17",
  "财政拨款总表收入总计=支出总计；未见T04不平衡",
  "exclude", "T04无候选；系统无目标问题。"),
"RB-2024-D06": ("uploads/72c3ffe3f84474ba05eb8d238d3d2732/上海市民族和宗教事务局.pdf", 2024, "department",
  "N06,T05", "B/A", "15-16,24",
  "人员经费5,350.25+公用1,395.40=6,745.65；说明基本支出6,745.63，差0.02；20805父级1,136.48 vs子级298.85（系统）",
  "candidate", "N06真实差0.02；20805父级差837.63疑提取缺行，待回原文核（不直接算T05）。"),
"RA-040": ("uploads/ff2ec03d1f8b9aebc2eb6ca99367e73c/上海市城市建设档案馆2025年度决算.pdf", 2025, "unit",
  "N02", "B", "7,18",
  "收入表与说明未见N02金额差；V33-224三公占比为非目标",
  "exclude", "N02无候选；三公占比问题非本轮目标。"),
"RA-030": ("uploads/c068b058bd046e30558be3747f1f4b1d/上海市财政局2022年度部门决算.pdf", 2022, "department",
  "T07", "A", "14-16",
  "系统0 issues；三公表/说明未见T07不平",
  "control", "作为零问题/正常对照候选；T07未发现缺陷。"),
"RB-2025-D02": ("uploads/2feaa1a724a537204dd2f3ff0d808f46/上海市数据局.pdf", 2025, "department",
  "T07", "A", "17-20",
  "三公表/说明未见T07不平",
  "exclude", "T07无候选；其余问题非目标。"),
"RA-008": ("uploads/369cfed69ce981643e758df655a2cec4/上海市发展改革研究院2025年度决算.pdf", 2025, "unit",
  "T02,T03,T05,T08", "A", "5-9",
  "V33-202 财政拨款总表6451.24 vs功能表6451.22差0.02（非目标C-04.1）；目标表未见0.05以上差",
  "exclude", "T02/T03/T05/T08无候选；0.02为非目标拨款总表-功能表差。"),
"RA-016": ("uploads/74d2f14063620a23468f32aeae4b3c6b/上海航宇科普中心（上海市工业文化发展中心）2025年度决算.pdf", 2025, "unit",
  "T02,T03,T05,T07,T08", "A", "14",
  "三公预算数合计7.40；0.00+7.40+1.00=8.40，差1.00",
  "candidate", "T07真实缺陷（预算数）；决算数1.70=0+1.70+0成立。"),
"RA-012": ("uploads/5c9899d5f9d57bff5f49a35b82514203/上海市文化和旅游局2022年度部门决算.pdf", 2022, "department",
  "T02,T03,T05,T08", "A", "9-11",
  "收入表207父级128,810.36 vs子级128,810.40，差0.04（收入表层级C-02）",
  "exclude", "T02/T03/T05/T08无候选；0.04为收入表C-02层级，不属T05（一般公共预算支出表）。"),
"RA-006": ("uploads/3090701887a9091c0d556416d16ec7b7/上海市价格认证中心2025年度决算.pdf", 2025, "unit",
  "T02,T03,T05", "A", "7-9",
  "收入/支出表合计与分项未见目标差；系统目标规则未报",
  "exclude", "T02/T03/T05无候选。"),
}

def sha(p): return hashlib.sha256(open(os.path.join(SRC,p),'rb').read()).hexdigest()

log_rows=[]; cand_rows=[]
for i,(sid,(path,year,scope,targets,family,pages,quote,result,reason)) in enumerate(ROWS.items(),1):
    h=sha(path)
    log_rows.append({"log_id":f"CS-{i:03d}","sample_id":sid,"source":"160非金标回归→uploads原件",
        "sha256":h,"pdf_path":os.path.join(SRC,path),"doc_type":"决算","year":year,"scope":scope,
        "targets":targets,"family":family,"pages_viewed":pages,"quote":quote,
        "effective_screen_done":"是" if result in ("candidate","control","exclude") else "否",
        "candidate_or_exclusion":result,"reason":reason,
        "identification_difficulty":"无" if result!="candidate" else "需逐行复算/回原文确认",
        "budget_units":1})
    if result=="candidate":
        cand_target={"RA-009":"N02","RA-022":"T03,T07","RB-2024-D06":"N06","RA-016":"T07"}.get(sid,targets)
        cand_rows.append({"candidate_id":f"B1C-{len(cand_rows)+1:02d}","sample_id":sid,"targets":cand_target,
            "pdf_path":os.path.join(SRC,path),"sha256":h,"pages":pages,"quote":quote,"reason":reason,
            "annotation_path":f"annotations/{ {'RA-009':'B1-2025-U01','RA-022':'B1-2025-D01','RB-2024-D06':'B1-2024-D02','RA-016':'B1-2025-U02'}.get(sid,'') }/annotation.json",
            "review_status":"PENDING_REVIEW","pool":"D"})
# add non-target C-02 candidate
cand_rows.append({"candidate_id":f"B1C-{len(cand_rows)+1:02d}","sample_id":"RB-2023-D01","targets":"C-02(非目标)",
    "pdf_path":os.path.join(SRC,ROWS["RB-2023-D01"][0]),"sha256":sha(ROWS["RB-2023-D01"][0]),
    "pages":"9-11","quote":"21401父级286,236.11；项级之和286,297.57","reason":"真实层级差61.46；不属本轮13目标，供B2",
    "annotation_path":"annotations/B1-2023-D01/annotation.json","review_status":"PENDING_REVIEW","pool":"D"})

with open(os.path.join(OUT,"candidate_screen_log.csv"),"w",encoding="utf-8-sig",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(log_rows[0])); w.writeheader(); w.writerows(log_rows)
with open(os.path.join(OUT,"candidates.csv"),"w",encoding="utf-8-sig",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(cand_rows[0])); w.writeheader(); w.writerows(cand_rows)

# target status
TS = [
 ("T01","收支总表平衡","defect","INCOMPLETE",5,8,"","","筛选5份，未穷尽8份上限；未发现真实不平衡。"),
 ("T02","收入表行来源和","defect","NOT_FOUND",8,6,"","","用满6份上限；RA-021/RA-001等0.08/0.01为R1或提取伪差；RA-012/RA-007为收入表C-02层级，不属T02。"),
 ("T03","支出表合计=基本+项目","defect","PENDING_REVIEW",7,6,"B1-2025-D01","annotations/B1-2025-D01/annotation.json","RA-022差0.02，刚超2项R1包络0.015，待复核确认。"),
 ("T04","财政拨款总表平衡","defect","INCOMPLETE",2,6,"","","筛选2份，未穷尽6份上限；未发现不平衡。"),
 ("T05","一般公共预算支出表类=款=项","defect","NOT_FOUND",9,8,"","","用满8份上限；系统V33-005/120命中均为收入表层级或重复行；未发现T05真实缺陷。"),
 ("T06","基本支出表=人员+公用","defect","INCOMPLETE",2,6,"","","筛选4份基本支出表，未穷尽6份上限；表内等式均成立。"),
 ("T07","三公表合计=三项","defect","PENDING_REVIEW",8,6,"B1-2025-U02","annotations/B1-2025-U02/annotation.json","RA-016预算数7.40 vs 8.40差1.00；RA-022决算数808.46 vs 808.51差0.05为次要候选。"),
 ("T08","经济分类表类=款","defect","NOT_FOUND",4,4,"","","用满4份上限；未发现经济分类类=款真实缺陷。"),
 ("X01","总表vs收入/支出表","defect","INCOMPLETE",1,6,"","","筛选3份，未穷尽6份上限；合计一致。"),
 ("X02","基金/国资列","defect","INCOMPLETE",2,6,"","","筛选2份，未穷尽6份上限；未见列合计不平。"),
 ("N02","收入说明vs收入表","defect","PENDING_REVIEW",4,7,"B1-2025-U01","annotations/B1-2025-U01/annotation.json","RA-009财政拨款收入13,859.21 vs说明17,976.00，差4,116.79。"),
 ("N06","基本支出说明vs表","defect","PENDING_REVIEW",2,5,"B1-2024-D02","annotations/B1-2024-D02/annotation.json","RB-2024-D06人员+公用6,745.65 vs说明6,745.63，差0.02。"),
 ("N08","基金/国资说明vs表","defect","INCOMPLETE",1,6,"","","筛选2份，未穷尽6份上限；空表说明正确。"),
 ("C-R1","R1舍入包络内对照","control","PENDING_REVIEW",1,4,"","","RA-001收入合计10,367.43 vs分项10,367.44，差0.01在包络内；尚未做正式annotation。"),
 ("C-CROSS","跨页表对照","control","PENDING_REVIEW",1,4,"","","RA-021收入表跨页9-11，手工复算相等；系统0.08/0.09为伪差。"),
 ("C-EMPTY","空表正确说明对照","control","PENDING_REVIEW",1,4,"","","RA-001政府性基金/国资空表说明正确（p13-14）。"),
 ("C-ZERO","零三公正确说明对照","control","INCOMPLETE",0,4,"","","尚未找到零三公样本；未消耗预算。"),
]
with open(os.path.join(OUT,"target_status.csv"),"w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f); w.writerow(["target_id","target_name","target_type","status","effective_screened","budget_cap","candidate_sample","evidence_path","note"])
    w.writerows(TS)

# contract
contract = {
 "task":"GovBudgetChecker 金标补样 B1",
 "plan_commit":"d90ba45956613c13900d1d972a90557a77a1b6cf",
 "run_id":os.path.basename(OUT),
 "branch":"codex/gold-b1-20261103",
 "snapshot_commit":"48ee4e28441bd6ddbf34beee85bc6883d60db773",
 "runtime_profile":"AI off / OCR off / DATABASE_URL empty / dotenv off / GBC_TEMPLATE_SCOPE=0",
 "conventions":[
  "按 §3.7 逐目标预算执行，不采用 §3.5 早期“40份找不到两份就停整个B1”。",
  "FOUND=案例已经独立复核并冻结，不代表系统检出；DETECTED/MISSED才是检出。",
  "D池真实案例可FOUND但只作回归；I池资格由复核方确认。",
  "不因找不到某类缺陷无限扩池或自动追加下一轮。",
  "本轮无可用独立复核角色，所有初标记PENDING_REVIEW，不写reviewed_by/truth_frozen_at，不冻结。",
  "预算规范为草案，未批准；仅结构试标，不进入效果指标。"
 ],
 "scope":{"b2":False,"b3":False,"business_code":False,"rules":False,"new_collection":False},
 "budgets":{"table_pool":40,"narrative_pool":20,"control_pool":10,
            "target_caps":{"T01":8,"T02":6,"T03":6,"T04":6,"T05":8,"T06":6,"T07":6,"T08":4,"X01":6,"X02":6,"N02":7,"N06":5,"N08":6}},
 "status_definitions":{"FOUND":"案例冻结","NOT_FOUND":"用满目标上限且0合格","INCOMPLETE":"预算未用尽/未轮到","PENDING_REVIEW":"初标已交未独立复核/未冻结","N/A":"仅政策排除或用户移出"},
 "created_at":NOW,
}
json.dump(contract,open(os.path.join(OUT,"execution_contract.json"),"w",encoding="utf-8"),ensure_ascii=False,indent=2)
print("ledgers written",len(log_rows),len(cand_rows),len(TS))
