import { isHeadUnit, formatUnitOption, type UnitOptionLike } from "../lib/unitMatch";

const department = { id: "dept-1", name: "上海市普陀区人民政府办公室", level: "department" };

const headUnitParen = { id: "unit-1", name: "上海市普陀区人民政府办公室（本级）", level: "unit", parent_id: "dept-1" };
const headUnitPlain = { id: "unit-2", name: "民政局本级", level: "unit", parent_id: "dept-2" };
const directUnit = { id: "unit-3", name: "上海市普陀区信息化服务中心", level: "unit", parent_id: "dept-1" };

const results: string[] = [];

function assert(condition: boolean, label: string): void {
    if (!condition) {
        throw new Error(`ASSERT FAILED: ${label}`);
    }
    results.push(`PASS: ${label}`);
}

// isHeadUnit
assert(isHeadUnit(headUnitParen, department) === true, "isHeadUnit: 部门本级（带全角括号）判定为真");
assert(isHeadUnit(headUnitPlain, { id: "dept-2", name: "民政局", level: "department" } as UnitOptionLike) === true, "isHeadUnit: 部门本级（无括号）判定为真");
assert(isHeadUnit(directUnit, department) === false, "isHeadUnit: 直属单位判定为假");
assert(isHeadUnit(undefined, department) === false, "isHeadUnit: 单位缺失返回 false");
assert(isHeadUnit(headUnitParen, undefined) === false, "isHeadUnit: 部门缺失返回 false");

// formatUnitOption
assert(formatUnitOption(headUnitParen, department) === "上海市普陀区人民政府办公室（本级）（部门本级）", "formatUnitOption: 部门本级标注");
assert(formatUnitOption(directUnit, department) === "上海市普陀区信息化服务中心（直属单位）", "formatUnitOption: 直属单位标注");
assert(formatUnitOption(headUnitParen) === "上海市普陀区人民政府办公室（本级）（直属单位）", "formatUnitOption: 无部门上下文时按直属单位标注");

// normalize 内部行为：归一化后名称一致
const normalize = (value: string) => value.replace(/[\s（）()]/g, "").replace(/（本级）|本级/g, "");
assert(normalize(headUnitParen.name) === normalize(department.name), "normalize: 去除括号与本级后与部门名一致");

console.log(results.join("\n"));
console.log(`\n${results.length} assertions passed`);
