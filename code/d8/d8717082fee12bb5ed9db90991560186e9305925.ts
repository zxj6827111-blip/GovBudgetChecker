import fs from "node:fs";
import path from "node:path";
import { toUiProblems } from "/Volumes/workSSD/software development/GovBudgetChecker/artifacts/comprehensive-review-closure-20261109/app/lib/uiAdapters";
import { issueCategoryId } from "/Volumes/workSSD/software development/GovBudgetChecker/artifacts/comprehensive-review-closure-20261109/app/lib/issueCategories";
const out="/Volumes/workSSD/software development/GovBudgetChecker/outputs/comprehensive_review_closure_20261109";
const entries=JSON.parse(fs.readFileSync(path.join(out,"release_final7_summary.json"),"utf8"));
const counts: Record<string,number>={};const unknown=new Map<string,{rule:string,title:string,count:number}>();
for(const e of entries){const s=JSON.parse(fs.readFileSync(path.join(out,"release_final7",e.job,"uploads",e.job,"status.json"),"utf8"));for(const p of toUiProblems(s as any,{includeEvidencePreview:false})){const c=issueCategoryId(p);counts[c]=(counts[c]||0)+1;if(c==="other"){const key=p.ruleId+"|"+p.title;const prior=unknown.get(key);unknown.set(key,{rule:p.ruleId,title:p.title,count:(prior?.count||0)+1});}}}
const result={counts,unknown:[...unknown.values()].sort((a,b)=>b.count-a.count)};
fs.writeFileSync(path.join(out,"category_census.json"),JSON.stringify(result,null,2));console.log(JSON.stringify({counts,unknown:result.unknown.slice(0,45)},null,2));
