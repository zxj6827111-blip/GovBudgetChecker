// 单位归属辅助函数：识别“部门本级”与“直属单位”。
// 用于批量上传时区分“部门本级（即部门自身）”与“直属单位”，
// 保证单位下拉选项、归属确认的语义一致性。

export interface UnitOptionLike {
    name: string;
}

/** 归一化组织名称：去除空白与全半角括号，并去掉“本级”后缀标志，便于与部门名称对比。 */
function normalizeOrgName(value: string): string {
    return value.replace(/[\s（）()]/g, "").replace(/（本级）|本级/g, "");
}

/** 判断单位是否为部门本级（即名称与部门名称一致，或名称含“本级”标志）。 */
export function isHeadUnit(unit?: UnitOptionLike | null, department?: UnitOptionLike | null): boolean {
    if (!unit || !department) return false;
    return normalizeOrgName(unit.name) === normalizeOrgName(department.name) || unit.name.includes("本级");
}

/** 单位选项显示名：部门本级标注为“（部门本级）”，直属单位标注为“（直属单位）”。 */
export function formatUnitOption(unit: UnitOptionLike, department?: UnitOptionLike | null): string {
    return `${unit.name}（${isHeadUnit(unit, department) ? "部门本级" : "直属单位"}）`;
}
