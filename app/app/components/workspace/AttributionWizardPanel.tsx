/**
 * AttributionWizardPanel：三步预算主体归属向导（Task 5.2），还原
 * `02-upload-center-v2-department-unit.png`。
 *
 * 步骤：
 * 1. 选择预算主管部门（含搜索）
 * 2. 选择文件层级：部门汇总文件 / 单位文件（二选一切换）
 * 3. 选择所属预算单位（含"本级单位"/"直属单位"徽章，仅当步骤 2 选择"单位文件"时必填）
 *
 * 底部归属路径面包屑与必填校验均复用 uploadCenterAdapters.ts 的纯函数
 * （formatAttributionBreadcrumb / validateAttribution），保证"面包屑怎么拼"与
 * "能不能提交"两处判断始终使用同一套规则，不会出现面包屑显示完整但校验说没选完
 * （或反之）的分裂。
 */
"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui";

import {
  formatAttributionBreadcrumb,
  formatUnitScopeHint,
  selectDepartmentOptions,
  selectUnitOptionsForDepartment,
  validateAttribution,
  type FileLevel,
  type OrganizationRecordLike,
} from "./uploadCenterAdapters";
import { isHeadUnit } from "../../../lib/unitMatch";

export interface AttributionSelection {
  departmentId: string;
  fileLevel: FileLevel | null;
  unitId: string;
}

export interface AttributionWizardPanelProps {
  value: AttributionSelection;
  onChange: (value: AttributionSelection) => void;
}

export function AttributionWizardPanel({ value, onChange }: AttributionWizardPanelProps) {
  const [organizationsWithParent, setOrganizationsWithParent] = useState<OrganizationRecordLike[]>([]);
  const [departmentSearch, setDepartmentSearch] = useState("");

  // 只请求一次完整组织树并保留 parent_id：单位归属判定（selectUnitOptionsForDepartment /
  // isHeadUnit）需要真实的部门-单位父子关系，OrganizationFilterSelect.flattenOrganizationTree
  // 的拉平结果不带 parent_id（那个函数是给"筛选下拉"场景设计的，不需要层级关系），
  // 因此这里用自己的 flattenWithParent 而不是复用它。
  useEffect(() => {
    let cancelled = false;
    async function loadWithParent() {
      try {
        const response = await fetch("/api/organizations?stats=none", { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as { tree?: OrganizationTreeNode[] };
        const flat = flattenWithParent(payload.tree);
        if (!cancelled) {
          setOrganizationsWithParent(flat);
        }
      } catch {
        // 保持空列表，下拉显示"暂无可选部门"。
      }
    }
    void loadWithParent();
    return () => {
      cancelled = true;
    };
  }, []);

  const departmentOptions = useMemo(() => selectDepartmentOptions(organizationsWithParent), [organizationsWithParent]);
  const filteredDepartments = useMemo(() => {
    const keyword = departmentSearch.trim();
    if (!keyword) {
      return departmentOptions;
    }
    return departmentOptions.filter((department) => department.name.includes(keyword));
  }, [departmentOptions, departmentSearch]);

  const selectedDepartment = useMemo(
    () => organizationsWithParent.find((org) => org.id === value.departmentId) ?? null,
    [organizationsWithParent, value.departmentId],
  );
  const unitOptions = useMemo(
    () => selectUnitOptionsForDepartment(organizationsWithParent, value.departmentId),
    [organizationsWithParent, value.departmentId],
  );
  const selectedUnit = useMemo(
    () => unitOptions.find((unit) => unit.id === value.unitId) ?? null,
    [unitOptions, value.unitId],
  );

  const validation = validateAttribution(value);
  const breadcrumb = formatAttributionBreadcrumb(selectedDepartment, value.fileLevel, selectedUnit);

  return (
    <div className="rounded-card border border-border bg-white p-5" data-testid="gbc-attribution-wizard">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-slate-900">预算主体归属</div>
          <div className="mt-0.5 text-xs text-slate-500">使用层级和唯一组织 ID 区分同名组织与单位</div>
        </div>
        <div className="text-xs text-slate-400">步骤 1-3</div>
      </div>

      <div className="space-y-5">
        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium text-slate-900">1 选择预算主管部门</span>
          </div>
          <input
            value={departmentSearch}
            onChange={(event) => setDepartmentSearch(event.target.value)}
            placeholder="搜索部门"
            data-testid="gbc-attribution-department-search"
            className="mb-2 w-full rounded-md border border-border px-3 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
          />
          <select
            value={value.departmentId}
            onChange={(event) => onChange({ departmentId: event.target.value, fileLevel: value.fileLevel, unitId: "" })}
            data-testid="gbc-attribution-department-select"
            className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
          >
            <option value="">所属部门 *</option>
            {filteredDepartments.map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-slate-400">部门决定后续可选的本级单位和直属单位范围。</p>
        </div>

        <div>
          <span className="mb-2 block text-sm font-medium text-slate-900">2 选择文件层级</span>
          <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="文件层级">
            <button
              type="button"
              role="radio"
              aria-checked={value.fileLevel === "department_summary"}
              onClick={() => onChange({ ...value, fileLevel: "department_summary", unitId: "" })}
              data-testid="gbc-attribution-level-department"
              className={`rounded-md border px-3 py-2 text-sm font-medium transition-colors ${
                value.fileLevel === "department_summary"
                  ? "border-primary-400 bg-primary-100 text-primary-700"
                  : "border-border bg-white text-slate-600 hover:bg-surface-100"
              }`}
            >
              部门汇总文件
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={value.fileLevel === "unit"}
              onClick={() => onChange({ ...value, fileLevel: "unit" })}
              data-testid="gbc-attribution-level-unit"
              className={`rounded-md border px-3 py-2 text-sm font-medium transition-colors ${
                value.fileLevel === "unit"
                  ? "border-primary-400 bg-primary-100 text-primary-700"
                  : "border-border bg-white text-slate-600 hover:bg-surface-100"
              }`}
            >
              单位文件
            </button>
          </div>
          <p className="mt-1 text-xs text-slate-400">部门汇总预算不选单位；单位预算必须继续选择预算单位。</p>
        </div>

        {value.fileLevel === "unit" ? (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-slate-900">3 选择所属预算单位</span>
              <span className="text-xs text-slate-400">{formatUnitScopeHint(selectedDepartment?.name ?? "")}</span>
            </div>
            <select
              value={value.unitId}
              onChange={(event) => onChange({ ...value, unitId: event.target.value })}
              disabled={!value.departmentId}
              data-testid="gbc-attribution-unit-select"
              className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:bg-surface-100"
            >
              <option value="">预算单位 *</option>
              {unitOptions.map((unit) => (
                <option key={unit.id} value={unit.id}>
                  {unit.name}
                  {isHeadUnit(unit, selectedDepartment) ? "（本级单位）" : "（直属单位）"}
                </option>
              ))}
            </select>
            <div className="mt-2 flex flex-wrap gap-2">
              {unitOptions.slice(0, 3).map((unit) => (
                <button
                  key={unit.id}
                  type="button"
                  onClick={() => onChange({ ...value, unitId: unit.id })}
                  data-testid={`gbc-attribution-unit-quickpick-${unit.id}`}
                  className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-left text-xs transition-colors ${
                    value.unitId === unit.id
                      ? "border-primary-400 bg-primary-50"
                      : "border-border bg-white hover:bg-surface-100"
                  }`}
                >
                  <Badge tone={isHeadUnit(unit, selectedDepartment) ? "processing" : "neutral"}>
                    {isHeadUnit(unit, selectedDepartment) ? "本级单位" : "直属单位"}
                  </Badge>
                  <span className="text-slate-700">{unit.name}</span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div className="mt-5 rounded-md bg-surface-100 px-3 py-2 text-xs text-slate-500" data-testid="gbc-attribution-breadcrumb">
        当前归属路径
        <div className="mt-1 text-sm font-medium text-slate-900">{breadcrumb}</div>
      </div>

      <div className="mt-3 rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700" data-testid="gbc-attribution-same-name-notice">
        同名处理规则：部门和本级单位即使名称相同，也按&ldquo;部门 ID + 单位 ID + 层级类型&rdquo;分别保存，禁止仅按名称合并。
      </div>

      {!validation.isComplete ? (
        <div className="mt-2 text-xs text-danger-600" data-testid="gbc-attribution-validation-error">
          {validation.reason}
        </div>
      ) : null}
    </div>
  );
}

interface OrganizationTreeNode {
  id: string;
  name: string;
  level?: string;
  parent_id?: string | null;
  children?: OrganizationTreeNode[];
}

function flattenWithParent(nodes: OrganizationTreeNode[] | undefined): OrganizationRecordLike[] {
  if (!Array.isArray(nodes)) {
    return [];
  }
  const result: OrganizationRecordLike[] = [];
  // structuralParentId 是遍历时已知的"这个节点在树里挂在谁下面"，作为 item.parent_id
  // 缺失时的兜底——真实后端响应（storage.get_tree() 的 OrganizationTree.parent_id）
  // 总是带这个字段，这里的兜底只是防御性的，不依赖它也能从树形结构本身推出父子关系。
  const walk = (items: OrganizationTreeNode[], structuralParentId: string | null) => {
    for (const item of items) {
      if (!item?.id) {
        continue;
      }
      result.push({
        id: String(item.id),
        name: String(item.name ?? ""),
        level: String(item.level ?? "organization"),
        parent_id: item.parent_id ? String(item.parent_id) : structuralParentId,
      });
      if (Array.isArray(item.children) && item.children.length > 0) {
        walk(item.children, String(item.id));
      }
    }
  };
  walk(nodes, null);
  return result;
}

export default AttributionWizardPanel;
