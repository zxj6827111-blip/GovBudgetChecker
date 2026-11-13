import fs from "node:fs";
import assert from "node:assert/strict";
import {toUiProblems} from '/Volumes/workSSD/software development/GovBudgetChecker/artifacts/shiquan-source-coverage-20261109/app/lib/uiAdapters.ts';
import {buildCalculationGroups} from '/Volumes/workSSD/software development/GovBudgetChecker/artifacts/shiquan-source-coverage-20261109/app/app/components/review-workbench/reviewReadingAdapters.ts';
const path=process.argv[2];
const status=JSON.parse(fs.readFileSync(path,"utf8"));
const problems=toUiProblems(status);
const issue=problems.find(p=>p.ruleId==="V33-005"&&p.description.includes("basic_actual")&&p.description.includes("14206.00")&&p.description.includes("4610.99"));
assert.ok(issue,"必须保留主样本真实基本支出列差额");
const groups=buildCalculationGroups(issue);
assert.equal(groups.length,1,"同一原格算式只展示一遍");
assert.equal(groups[0].items.length,11,"11个类全部列入包括7个原件空白零");
assert.equal(groups[0].items.filter(x=>x.blankAsZero).length,7);
assert.equal(groups[0].itemsSum,"4610.99");
for(const code of ["201","204","205","206","207","211","224"])
 assert.ok(groups[0].items.some(x=>x.label.includes(code)&&x.blankAsZero&&x.valueText==="0.00"));
const receipt={oneCalculation:true,classRows:11,certifiedBlankZeros:7,displaySum:groups[0].itemsSum,difference:groups[0].difference,rows:groups[0].items};
fs.writeFileSync('/Volumes/workSSD/software development/GovBudgetChecker/outputs/shiquan_source_coverage_20261109/display_verification.json',JSON.stringify(receipt,null,2));
console.log(JSON.stringify({oneCalculation:true,classRows:11,certifiedBlankZeros:7,displaySum:groups[0].itemsSum,difference:groups[0].difference}));
