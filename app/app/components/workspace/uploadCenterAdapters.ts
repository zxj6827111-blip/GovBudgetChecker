/**
 * 上传中心（Task 5）的纯逻辑层：预检状态映射、上传限制校验、三步归属必填与
 * 联动约束、组织拉平与"部门/本级单位"同名区分。拆到无 JSX 依赖的 .ts 文件，
 * 理由与 app/components/workspace/workbenchAdapters.ts 一致。
 *
 * 复用而非重写：`isHeadUnit`/`formatUnitOption` 直接从 app/lib/unitMatch.ts
 * 导入——这是 app/app/components/BatchUploadModal.tsx 已经在用、且已有专属
 * 单测（tests/unitMatch.regression.test.ts）的既有实现，本文件不重新发明
 * "部门本级"判定逻辑，只在此基础上构建三步归属向导需要的新决策函数。
 */

import { isHeadUnit } from "../../../lib/unitMatch";

// ---------------------------------------------------------------------------
// 预检状态映射（/api/documents/preflight 响应 -> 三态徽章）
// ---------------------------------------------------------------------------

export type PreflightStatus = "passed" | "needs_confirmation" | "failed";

export interface PreflightMatchCandidate {
  organization_id: string;
  organization_name: string;
  level: string;
  confidence: number;
}

export interface PreflightResponseLike {
  report_year?: number | null;
  doc_type?: string | null;
  report_kind?: string | null;
  current?: PreflightMatchCandidate | null;
}

/** 与 current 是否存在同一个置信度门槛，来自 api/routes/upload.py `_inspect_document_preflight`
 *  的 `if index == 0 and float(confidence or 0.0) >= 0.6: current = serialized`——current 字段
 *  本身已经隐含了 0.6 门槛（低于该值 current 为 null）。本文件不重复发明另一个门槛数字，
 *  只是把"current 是否存在"这件事翻译成三态徽章语义，門槛的唯一来源仍是后端。 */
export function derivePreflightStatus(response: PreflightResponseLike | null | undefined): PreflightStatus {
  if (!response) {
    return "failed";
  }
  const hasReportYear = typeof response.report_year === "number" && response.report_year > 0;
  const hasDocType = Boolean(response.doc_type);
  const hasConfidentOrgMatch = Boolean(response.current?.organization_id);

  // "需要确认"：年度/文档类型未识别到，或组织匹配置信度不足（后端 current=null）。
  // 这三者都是真实的低置信度判定结果，不是随机或按文件名猜的——full 判据直接来自
  // preflight 响应体的三个真实字段，缺任何一个都不能视为"校验通过"。
  if (!hasReportYear || !hasDocType || !hasConfidentOrgMatch) {
    return "needs_confirmation";
  }
  return "passed";
}

export const PREFLIGHT_STATUS_LABELS: Record<PreflightStatus, string> = {
  passed: "校验通过",
  needs_confirmation: "需要确认",
  failed: "校验失败",
};

// ---------------------------------------------------------------------------
// 分析前确认闸门（前置修复 1）：needs_confirmation 的补齐与解除
// ---------------------------------------------------------------------------

/** needs_confirmation 的具体成因，供 UI 精确提示"缺什么"而不是笼统一句话。 */
export type PreflightConfirmationReason = "missing_report_year" | "missing_doc_type" | "low_confidence_org";

/**
 * 逐项列出 preflight 响应缺失/低置信度的字段。
 *
 * 与 derivePreflightStatus 判据完全一致（同样的三个真实字段），只是把"是否需要确认"
 * 这个布尔结果展开成"具体缺哪几项"的列表，用于 UI 精确提示与单文件编辑表单的
 * 初始值兜底（例如只缺年份时不应该连文档类型一起要求用户重新选）。
 */
export function listPreflightConfirmationReasons(
  response: PreflightResponseLike | null | undefined,
): PreflightConfirmationReason[] {
  if (!response) {
    return [];
  }
  const reasons: PreflightConfirmationReason[] = [];
  const hasReportYear = typeof response.report_year === "number" && response.report_year > 0;
  const hasDocType = Boolean(response.doc_type);
  const hasConfidentOrgMatch = Boolean(response.current?.organization_id);
  if (!hasReportYear) {
    reasons.push("missing_report_year");
  }
  if (!hasDocType) {
    reasons.push("missing_doc_type");
  }
  if (!hasConfidentOrgMatch) {
    reasons.push("low_confidence_org");
  }
  return reasons;
}

export const PREFLIGHT_CONFIRMATION_REASON_LABELS: Record<PreflightConfirmationReason, string> = {
  missing_report_year: "年份未识别到",
  missing_doc_type: "文档类型未识别到",
  low_confidence_org: "组织匹配置信度不足",
};

/**
 * 单文件的人工补齐值。三个字段均为可选——用户可能只需要补一项（例如只缺年份），
 * 不应强迫用户重新填写已经识别正确的其它字段。
 *
 * organizationId/organizationName 成对出现：id 是提交时真正写入后端的值，
 * name 只用于 UI 展示（面包屑/下拉当前值），避免组件为了显示名称反过来查一次组织树。
 */
export interface ManualConfirmationOverride {
  reportYear?: string;
  docType?: string;
  organizationId?: string;
  organizationName?: string;
}

/**
 * 把批量预设或单文件人工补齐值叠加到某个文件的 preflight 响应上，得到"解除确认后
 * 的有效 preflight 结果"。
 *
 * 关键约束（对照任务书"补齐后状态转换必须真实生效"）：
 * - 只在原字段缺失/低置信度时才用覆盖值顶替，已经识别正确的字段不被覆盖值污染
 *   （例如批量预设的"部门预算"不应该把某个文件已经正确识别出的"部门决算"改掉）；
 * - 返回的新对象可以直接喂给 derivePreflightStatus 计算出新状态，也可以直接用于
 *   拼装最终提交给后端的表单字段——两处消费同一份"有效值"，不会出现"UI 认为已解除
 *   确认，但提交时用的仍是原始空值"的分裂。
 */
export function applyManualConfirmationOverride(
  response: PreflightResponseLike | null | undefined,
  override: ManualConfirmationOverride | null | undefined,
): PreflightResponseLike {
  const base: PreflightResponseLike = response ? { ...response } : {};
  if (!override) {
    return base;
  }

  const hasReportYear = typeof base.report_year === "number" && base.report_year > 0;
  if (!hasReportYear && override.reportYear) {
    const parsedYear = Number.parseInt(override.reportYear, 10);
    if (Number.isFinite(parsedYear) && parsedYear > 0) {
      base.report_year = parsedYear;
    }
  }

  if (!base.doc_type && override.docType) {
    base.doc_type = override.docType;
  }

  const hasConfidentOrgMatch = Boolean(base.current?.organization_id);
  if (!hasConfidentOrgMatch && override.organizationId) {
    base.current = {
      organization_id: override.organizationId,
      organization_name: override.organizationName || "",
      level: base.current?.level || "",
      // 人工选择视为完全确认，置信度记为 1——这与手动关联组织（associate 接口）
      // 的既有语义一致：人工选择不再是"匹配出来的猜测"，是已确认事实。
      confidence: 1,
    };
  }

  return base;
}

// ---------------------------------------------------------------------------
// 上传限制校验（真实值来自 /api/config，绝不可硬编码原型图的 200MB）
// ---------------------------------------------------------------------------

export interface UploadLimitViolation {
  type: "size" | "pages";
  message: string;
}

/**
 * 校验单个文件是否超过真实系统限制。
 *
 * 反例（核心断言）：maxUploadMb/maxUploadPages 必须由调用方从 /api/config 传入，
 * 本函数不内置任何默认值（不写 200，也不写 30）——如果调用方真的传入了 200，
 * 这个函数不会替它纠正，纠正的责任在"从哪里读取这两个数字"这一步，
 * 不在这个纯校验函数里，两个职责必须分开才能被独立测试。
 */
export function checkUploadLimit(
  fileSizeBytes: number,
  maxUploadMb: number,
  pageCount: number | null | undefined,
  maxUploadPages: number,
): UploadLimitViolation | null {
  const maxBytes = maxUploadMb * 1024 * 1024;
  if (fileSizeBytes > maxBytes) {
    return {
      type: "size",
      message: `文件大小 ${(fileSizeBytes / 1024 / 1024).toFixed(1)} MB 超过系统限制 ${maxUploadMb} MB`,
    };
  }
  if (typeof pageCount === "number" && pageCount > maxUploadPages) {
    return {
      type: "pages",
      message: `文件页数 ${pageCount} 页超过系统限制 ${maxUploadPages} 页`,
    };
  }
  return null;
}

// ---------------------------------------------------------------------------
// 三步归属向导：部门 -> 文件层级 -> 预算单位
// ---------------------------------------------------------------------------

export type FileLevel = "department_summary" | "unit";

export interface AttributionState {
  departmentId: string;
  fileLevel: FileLevel | null;
  unitId: string;
}

export interface AttributionValidationResult {
  isComplete: boolean;
  missingStep: 1 | 2 | 3 | null;
  reason: string;
}

/**
 * 三步归属的必填与联动校验。
 *
 * 反例（核心断言，对照任务书 5.2）：
 * - 未选完必填项不得允许提交；
 * - 部门汇总文件（department_summary）不应要求选单位；
 * - 单位文件（unit）必须选单位。
 */
export function validateAttribution(state: AttributionState): AttributionValidationResult {
  if (!state.departmentId) {
    return { isComplete: false, missingStep: 1, reason: "请先选择预算主管部门" };
  }
  if (state.fileLevel === null) {
    return { isComplete: false, missingStep: 2, reason: "请选择文件层级（部门汇总文件或单位文件）" };
  }
  if (state.fileLevel === "unit" && !state.unitId) {
    return { isComplete: false, missingStep: 3, reason: "单位文件必须选择所属预算单位" };
  }
  // department_summary 分支不检查 unitId——即便调用方误传了一个 unitId 进来，
  // 语义上"部门汇总文件"就是不需要单位，isComplete 仍然为 true。
  return { isComplete: true, missingStep: null, reason: "" };
}

// ---------------------------------------------------------------------------
// 归属路径面包屑（原型图："上海市普陀区财政局（部门）› 上海市普陀区财政局（本级单位）"）
// ---------------------------------------------------------------------------

export interface AttributionOrganizationLike {
  id: string;
  name: string;
}

/**
 * 生成归属路径面包屑文案。同名场景（部门与本级单位显示名相同）时，
 * 通过括注"（部门）"/"（本级单位）"/"（直属单位）"区分，而不是把两者合并成一段——
 * 这与后端 Organization.generate_id(name, level, parent_id) 的行为一致：
 * 即使 name 相同，department 与其 unit 子节点的 id 也因 level/parent_id 不同而不同，
 * 是两个独立的组织记录，前端面包屑必须如实反映"这是两个不同的组织"而非"同一个名字"。
 */
export function formatAttributionBreadcrumb(
  department: AttributionOrganizationLike | null,
  fileLevel: FileLevel | null,
  unit: AttributionOrganizationLike | null,
): string {
  if (!department) {
    return "尚未选择归属";
  }
  const departmentSegment = `${department.name}（部门）`;
  if (fileLevel === "department_summary" || !unit) {
    return departmentSegment;
  }
  const unitLabel = isHeadUnit(unit, department) ? "本级单位" : "直属单位";
  return `${departmentSegment} › ${unit.name}（${unitLabel}）`;
}

// ---------------------------------------------------------------------------
// 组织拉平（用于部门下拉/单位下拉），复用 OrganizationFilterSelect 的拉平约定
// ---------------------------------------------------------------------------

export interface OrganizationRecordLike {
  id: string;
  name: string;
  level: string;
  parent_id: string | null;
}

/** 从拉平后的组织列表中筛出部门（level=department），按名称排序。 */
export function selectDepartmentOptions(organizations: OrganizationRecordLike[]): OrganizationRecordLike[] {
  return organizations
    .filter((org) => org.level === "department")
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
}

/**
 * 筛出某部门下的单位（level=unit 且 parent_id 匹配），"本级单位"排在最前，
 * 其余按名称排序——对照原型图"本级单位"徽章总是出现在下拉第一项。
 */
export function selectUnitOptionsForDepartment(
  organizations: OrganizationRecordLike[],
  departmentId: string,
): OrganizationRecordLike[] {
  const department = organizations.find((org) => org.id === departmentId) ?? null;
  const units = organizations.filter((org) => org.level === "unit" && org.parent_id === departmentId);
  return units.slice().sort((a, b) => {
    const aIsHead = isHeadUnit(a, department);
    const bIsHead = isHeadUnit(b, department);
    if (aIsHead !== bIsHead) {
      return aIsHead ? -1 : 1;
    }
    return a.name.localeCompare(b.name, "zh-CN");
  });
}

/**
 * "仅显示 X 所属单位" 提示文案：原型图单位下拉旁边的说明文字（"仅显示财政局所属单位"），
 * 用于确认当前单位下拉已经按 selectUnitOptionsForDepartment 完成部门级过滤——
 * 这是一句描述性文案而非独立的二次过滤器（单位一旦按部门 parent_id 筛选，
 * 语义上已经是"仅显示该部门所属单位"，没有第二套过滤规则）。
 */
export function formatUnitScopeHint(departmentName: string): string {
  const trimmed = departmentName.trim();
  return trimmed ? `仅显示${trimmed}所属单位` : "请先选择预算主管部门";
}

/** 文件大小的展示文案，统一 MB 一位小数（与原型图"18.4 MB"格式一致）。 */
export function formatFileSizeMb(sizeBytes: number): string {
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 页数展示文案：未知（尚未从 preflight 拿到）显示 em dash，不猜测具体数字。 */
export function formatPageCountText(pageCount: number | null | undefined): string {
  if (typeof pageCount !== "number" || !Number.isFinite(pageCount)) {
    return "—";
  }
  return `${pageCount} 页`;
}
