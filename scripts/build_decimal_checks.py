# -*- coding: utf-8 -*-
import csv, sys
from decimal import Decimal
OUT=sys.argv[1]
rows=[
 # sample, check, item, operands, expected, reported, diff, unit, r1_n
 ("B1-2025-U02","GS-T07","预算数合计","0.00,7.40,1.00","8.40","7.40","1.00","万元",3),
 ("B1-2025-U02","GS-T07","预算数车小计","0.00,6.40","6.40","7.40","1.00","万元",2),
 ("B1-2025-D01","GS-T07","决算数合计","457.69,328.11,22.71","808.51","808.46","0.05","万元",3),
 ("B1-2024-D02","GS-N06","人员+公用 vs说明","5350.25,1395.40","6745.65","6745.63","0.02","万元",0),
 ("B1-2025-U01","GS-N02","财政拨款收入表vs说明","13859.21","13859.21","17976.00","4116.79","万元",0),
 ("B1-2025-U01","GS-N02","说明分项合计vs本年收入","17976.00,1363.30,2753.34","22092.64","17975.85","4116.79","万元",3),
 ("B1-2025-U01","GS-N05","逐项年初预算合计","9577.58,3064.88,795.63,556.94","13995.03","13995.10","0.07","万元",4),
 ("B1-2025-U01","GS-N07","三公构成占比","0.00,97.11,0.00","97.11","100.00","2.89","%",3),
 ("B1-2025-D01","GS-N07","出国费表vs说明","457.66","457.66","457.69","0.03","万元",0),
 ("B1-2023-D01","GS-T02","2140101行政运行","29663.18,1451.96","31115.14","29663.18","1451.96","万元",2),
 ("B1-2023-D01","GS-T02","2140106公路养护","182493.28,871.01","183364.29","182493.28","871.01","万元",2),
 ("B1-2023-D01","GS-T02","2140123航道维护","30802.54,79.96","30882.50","30802.54","79.96","万元",2),
 ("B1-2023-D01","GS-T02","21462车辆通行费","60953.45,498.76","61452.21","60953.45","498.76","万元",2),
 ("B1-2023-D01","GS-T02","2080505养老缴费","7874.31,145.47,29.20","8048.98","8019.78","29.20","万元",3),
 ("B1-2023-D01","GS-T05","2210201合计=基本+项目","5697.24,5697.24","11394.48","5697.24","5697.24","万元",2),
 ("B1-2023-D01","GS-N05","第8项表vs说明","4573.66,0.20","4573.86","4573.66","0.20","万元",2),
]
out=[]
for sample,check,item,ops,exp,rep,diff,unit,n in rows:
    vals=[Decimal(x) for x in ops.split(',')]
    lhs=sum(vals,Decimal('0')); exp=Decimal(exp); rep=Decimal(rep); diff=Decimal(diff)
    ok_sum=(lhs==exp); ok_diff=(abs(exp-rep)==diff)
    env=None; over=None
    if n:
        env=(Decimal(n)+1)*Decimal('0.005'); over=(diff>env)
    out.append({"sample_id":sample,"check_id":check,"item":item,"operands":"+".join(ops.split(',')),
        "decimal_sum":str(lhs),"expected":str(exp),"reported":str(rep),"difference":str(diff),"unit":unit,
        "r1_n":n,"r1_envelope":str(env) if env is not None else "",
        "over_envelope":str(over) if over is not None else "",
        "sum_ok":ok_sum,"diff_ok":ok_diff,"result":"PASS" if ok_sum and ok_diff else "FAIL"})
with open(OUT+"/decimal_checks.csv","w",encoding="utf-8-sig",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
print("rows",len(out),"fails",[r for r in out if r["result"]!="PASS"])
