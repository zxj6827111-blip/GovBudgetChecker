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

// ---------------------------------------------------------------------------
// 提交字段解析（修复 A1）：doc_type / fiscal_year 何时发送、何时不发送
// ---------------------------------------------------------------------------

/** 批量预设的结构（与 UploadBatchPresets 的 BatchPresetValues 保持结构兼容）。 */
export interface UploadPresetValuesLike {
  organizationId: string;
  year: string;
  docType: string;
}

export interface UploadFormFields {
  /** null 表示"该字段整个不发送"——空字符串进 FormData 会触发后端另一条校验路径。 */
  docType: string | null;
  fiscalYear: string | null;
}

/**
 * 计算某个文件最终提交给后端的 doc_type / fiscal_year。
 *
 * 优先级（与 applyManualConfirmationOverride 的覆盖语义一致）：
 * 1. 有效 preflight（含批量预设 + 单文件补齐）识别出的值；
 * 2. 识别不到时回落到批量预设值；
 * 3. 两者都为空 → 返回 null，调用方必须**整个不发送**该字段，
 *    让后端用封面识别结果（_resolve_upload_metadata 的 detected_* 分支）。
 *
 * 反例（核心断言）：预设 docType 默认值为空（不得预填 dept_budget），
 * 否则上传决算材料时前端会提交"预算"、后端封面识别为"决算"→ 必然 422
 * report_type_conflict——这正是实机"上传失败且无原因"的第一个根因。
 */
export function buildUploadFormFields(
  effective: PreflightResponseLike | null | undefined,
  presets: UploadPresetValuesLike,
): UploadFormFields {
  const detectedDocType = String(effective?.doc_type ?? "").trim();
  const docType = detectedDocType || String(presets.docType ?? "").trim() || null;

  const detectedYear = typeof effective?.report_year === "number" && effective.report_year > 0
    ? String(effective.report_year)
    : "";
  const fiscalYear = detectedYear || String(presets.year ?? "").trim() || null;

  return { docType, fiscalYear };
}

// ---------------------------------------------------------------------------
// 上传失败错误映射（修复 A2）：后端结构化错误 → 用户可读、可行动的中文
// ---------------------------------------------------------------------------

export interface UploadFailureMessage {
  /** 一句话结论，例如"文档类型与封面识别不一致"。 */
  title: string;
  /** 关键值对照（提交 X / 封面识别 Y、实际大小 / 系统限制 等）。 */
  detail?: string;
  /** 下一步怎么办（用户据此可行动）。 */
  suggestion?: string;
}

export interface DescribeUploadFailureInput {
  filename: string;
  status: number;
  /** 后端响应体（已 JSON.parse；解析失败为 null）。 */
  payload: unknown;
  /** 当前文件实际大小（字节），用于 413 文案展示真实值。 */
  fileSizeBytes?: number | null;
  /** 系统配置的大小上限（MB），来自 /api/config。 */
  maxUploadMb?: number | null;
}

/** doc_type 枚举 → 中文标签（用户可据此行动，raw 枚举值看不懂）。 */
function formatDocTypeLabel(value: unknown): string {
  const text = String(value ?? "").trim();
  if (text === "dept_budget") {
    return "部门预算（dept_budget）";
  }
  if (text === "dept_final") {
    return "部门决算（dept_final）";
  }
  return text || "未指定";
}

/** FastAPI 的 detail 既可能是字符串也可能是对象（结构化错误就是对象）。 */
function extractDetail(payload: unknown): { text: string; object: Record<string, unknown> | null } {
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    const record = payload as Record<string, unknown>;
    const detail = record.detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      return { text: String((detail as Record<string, unknown>).message ?? ""), object: detail as Record<string, unknown> };
    }
    if (typeof detail === "string") {
      return { text: detail, object: null };
    }
    return { text: String(record.error ?? record.message ?? ""), object: record as Record<string, unknown> };
  }
  return { text: "", object: null };
}

/**
 * 把上传接口的失败响应映射为用户可读、可行动的中文消息。
 *
 * 覆盖 api/routes/upload.py 与 api/runtime.py 实际会返回的全部错误状态：
 * - 413：文件超大（File exceeds {N}MB limit）/ 页数超限（PDF页数超过限制：…）
 * - 422：report_type_conflict / report_year_conflict（显示提交值 vs 封面识别值）
 * - 409：重复上传；403：组织无权限；404：组织不存在；503：组织服务不可用
 * - 429：限流（沿用 normalizeBackendError 的既有文案口径）；401：未登录
 * - 400：非 PDF 签名 / PDF 无法解析
 *
 * 未知状态码不得把结构化 detail 原样 dump（可能含内部路径），只显示状态码。
 */
export function describeUploadFailure(input: DescribeUploadFailureInput): UploadFailureMessage {
  const { filename, status } = input;
  const { text, object } = extractDetail(input.payload);

  if (status === 413) {
    // 后端有两种 413：体积超限（英文文案）与页数超限（中文文案，自带实际值与上限）。
    if (/页数|pages?/i.test(text)) {
      return {
        title: `文件 ${filename} 页数超过系统限制`,
        detail: text || "PDF 页数超过限制",
        suggestion: "请拆分材料后分批上传，或联系管理员调整页数上限。",
      };
    }
    const sizeDetail =
      typeof input.fileSizeBytes === "number" && typeof input.maxUploadMb === "number"
        ? `文件实际大小 ${formatFileSizeMb(input.fileSizeBytes)}，系统限制 ${input.maxUploadMb} MB`
        : text || "文件大小超过系统限制";
    return {
      title: `文件 ${filename} 大小超过系统限制`,
      detail: sizeDetail,
      suggestion: "请压缩或拆分 PDF 后重新上传。",
    };
  }

  if (status === 422 && object) {
    const errorCode = String(object.error ?? "");
    if (errorCode === "report_type_conflict") {
      return {
        title: `文件 ${filename} 的文档类型与封面识别不一致`,
        detail: `提交类型：${formatDocTypeLabel(object.submitted_doc_type)}；封面识别：${formatDocTypeLabel(object.detected_doc_type)}`,
        suggestion: "请把文档类型改为与封面一致（例如封面识别为部门决算，就把类型改为部门决算），或清空类型让系统自动判定后重新上传。",
      };
    }
    if (errorCode === "report_year_conflict") {
      return {
        title: `文件 ${filename} 的年份与封面识别不一致`,
        detail: `提交年份：${String(object.submitted_year ?? "未指定")}；封面识别：${String(object.detected_year ?? "未指定")}`,
        suggestion: "请核对材料封面上的年度后修改预设年份，或清空年份让系统自动判定后重新上传。",
      };
    }
    return {
      title: `文件 ${filename} 上传参数校验未通过`,
      detail: "提交的元数据与 PDF 封面识别结果冲突。",
      suggestion: "请核对年份、文档类型后重试，或清空预设让系统按封面自动判定。",
    };
  }

  if (status === 409) {
    return {
      title: `文件 ${filename} 与历史任务重复`,
      detail: text || "同一单位、同一年度、同一类型已存在相同内容的材料。",
      suggestion: "请在处理队列中查看既有任务；确需重新上传时，先删除或让管理员处理原任务。",
    };
  }

  if (status === 403) {
    return {
      title: `没有权限向该组织上传 ${filename}`,
      detail: "当前账号不归属或未被授权访问所选组织。",
      suggestion: "请确认组织选择无误；如需权限请联系管理员分配。",
    };
  }

  if (status === 404) {
    return {
      title: "所选组织不存在",
      detail: text || "organization not found",
      suggestion: "该组织可能已被删除，请重新选择组织后上传。",
    };
  }

  if (status === 503) {
    return {
      title: "组织服务暂不可用",
      detail: text || "organization service unavailable",
      suggestion: "请稍后重试；若持续出现请联系管理员检查组织服务。",
    };
  }

  if (status === 429 || /too many requests/i.test(text)) {
    return {
      title: "上传请求过于频繁",
      detail: "后端暂时限流。",
      suggestion: "请稍等一分钟后重试。",
    };
  }

  if (status === 401) {
    return {
      title: "登录状态已过期",
      detail: "上传前需要重新登录。",
      suggestion: "请重新登录后再上传。",
    };
  }

  if (status === 400) {
    if (/PDF/i.test(text)) {
      return {
        title: `文件 ${filename} 不是有效的 PDF`,
        detail: text,
        suggestion: "请检查文件是否损坏、是否为扫描生成的伪 PDF 后重新上传。",
      };
    }
    return {
      title: `文件 ${filename} 上传请求无效`,
      detail: text || "请求格式或参数不被接受。",
      suggestion: "请确认文件为 PDF 后重试；若持续出现请联系管理员。",
    };
  }

  // 未知状态码：只给状态码与通用建议，不原样 dump detail（防内部信息泄漏）。
  return {
    title: `文件 ${filename} 上传失败（HTTP ${status}）`,
    suggestion: "请稍后重试；若持续出现请联系管理员并附上任务时间。",
  };
}

/** 把结构化失败消息拼成一段可直接展示的文本（换行分隔，whitespace-pre-line 渲染）。 */
export function formatUploadFailureText(message: UploadFailureMessage): string {
  const lines = [message.title];
  if (message.detail) {
    lines.push(message.detail);
  }
  if (message.suggestion) {
    lines.push(`建议：${message.suggestion}`);
  }
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// 分析启动（修复 2）：上传成功后触发首次分析；失败与"上传失败"是两个状态
// ---------------------------------------------------------------------------

/**
 * 首次分析触发参数（与既有 UI 入口完全同参）。
 *
 * 接口选择依据（不要凭猜调用）：
 * - POST /api/analyze/{job_id}：调 runtime.start_analysis——上传端点只落
 *   status=uploaded、从不入队（api/runtime.py 上传分支实测），start_analysis 是
 *   唯一的"把任务排入分析"路径，因此"首次分析"走它。
 * - POST /api/jobs/{job_id}/reanalyze：语义是"重置既有分析并重跑"（会清理
 *   旧产物、409 拒绝进行中的任务），用于已分析过一次的任务，不是首次分析。
 * - 参数与旧 UI（gbc-ui-demo）及工作台/队列/审核台的 reanalyze 全部一致：
 *   mode="dual"（双轨：本地规则 + AI 合并，analyze_dual 模式；start_analysis
 *   的默认值是 legacy，不传会退回单轨，与系统其他入口不一致），
 *   use_local_rules=true，use_ai_assist=true。
 */
export const ANALYZE_START_REQUEST_BODY = {
  mode: "dual",
  use_local_rules: true,
  use_ai_assist: true,
} as const;

/**
 * 把"分析启动"接口的失败响应映射为用户可读、可行动的中文。
 *
 * 与 describeUploadFailure 的关键区别（任务书修复 2 硬性要求）：此刻文件已经
 * 上传成功、任务已存在于处理队列（状态=待分析），必须如实告知"上传成功但分析
 * 启动失败"，不能与"上传失败"混为一谈。
 */
export function describeAnalyzeStartFailure(input: {
  filename: string;
  /** HTTP 状态码；网络异常（请求根本没发出去）传 0。 */
  status: number;
  /** 后端响应体（已 JSON.parse；解析失败为 null）。 */
  payload: unknown;
}): UploadFailureMessage {
  const { filename, status } = input;
  const { text } = extractDetail(input.payload);

  if (status === 409) {
    return {
      title: `文件 ${filename} 上传成功，但该任务已在分析中，无需重复启动`,
      suggestion: "请在处理队列查看该任务的分析进度。",
    };
  }
  if (status === 404) {
    return {
      title: `文件 ${filename} 上传成功，但启动分析时任务不存在（可能已被删除）`,
      suggestion: "请在处理队列确认任务状态；确认不存在时请重新上传。",
    };
  }
  if (status === 401) {
    return {
      title: `文件 ${filename} 上传成功，但登录状态已过期，分析未能启动`,
      suggestion: "请重新登录后，在处理队列对该任务重试分析。",
    };
  }
  if (status === 429 || /too many requests/i.test(text)) {
    return {
      title: `文件 ${filename} 上传成功，但分析启动请求过于频繁`,
      detail: "后端暂时限流。",
      suggestion: "请稍等一分钟后在处理队列对该任务重试分析。",
    };
  }
  if (status === 0) {
    return {
      title: `文件 ${filename} 上传成功，但分析启动请求发送失败`,
      detail: "网络异常或服务暂不可用。",
      suggestion: "任务已在处理队列中（状态为待分析），可稍后重试分析。",
    };
  }
  return {
    title: `文件 ${filename} 上传成功，但分析启动失败（HTTP ${status}）`,
    detail: text || undefined,
    suggestion: "任务已在处理队列中（状态为待分析），可稍后重试分析；若持续出现请联系管理员。",
  };
}

// ---------------------------------------------------------------------------
// 批量提交结果汇总（修复 2）：逐文件隔离 + 如实区分两类失败
// ---------------------------------------------------------------------------

/** 单个文件的提交结果（上传 → 触发分析 的完整链路）。 */
export interface SubmitFileOutcome {
  /** 待上传列表条目 id（用于回填列表状态，同名文件也不混淆）。 */
  entryId: string;
  filename: string;
  /** 上传是否成功（任务是否已创建）。 */
  uploadOk: boolean;
  /** 分析是否成功启动（仅在 uploadOk 为 true 时才可能为 true）。 */
  analysisStarted: boolean;
  /** 上传成功时后端返回的任务 id。 */
  jobId: string | null;
  /** 失败时的完整用户可读文案（formatUploadFailureText 的输出）；成功为 null。 */
  failureText: string | null;
}

export interface SubmitOutcomeSummary {
  /** 是否所有文件都"上传成功且分析已启动"。 */
  allSucceeded: boolean;
  /** 上传成功的任务 id（无论分析是否启动——任务都已存在于队列）。 */
  uploadedJobIds: string[];
  /** 上传失败、仍保留在待上传列表的条目 id（可修正后重试）。 */
  failedEntryIds: string[];
  /** 有失败时的汇总文案；全部成功时为 null（调用方不需要展示任何错误）。 */
  summaryText: string | null;
}

/**
 * 汇总一批逐文件提交结果。
 *
 * 反例（核心断言，任务书修复 2）：
 * - "上传成功但分析启动失败"与"上传失败"必须是可区分的两段文案，不得合并成
 *   一句笼统的"失败"；
 * - 全部成功时 summaryText 必须为 null——不得在成功路径上渲染任何错误容器；
 * - 某个文件失败不影响其他文件：每个 outcome 独立携带结果，本函数只做汇总，
 *   不做"一个失败就否定整批"的归并。
 */
export function summarizeSubmitOutcome(outcomes: SubmitFileOutcome[]): SubmitOutcomeSummary {
  const uploadedJobIds = outcomes
    .filter((outcome) => outcome.uploadOk && outcome.jobId)
    .map((outcome) => outcome.jobId as string);
  const uploadFailed = outcomes.filter((outcome) => !outcome.uploadOk);
  const analyzeStartFailed = outcomes.filter(
    (outcome) => outcome.uploadOk && !outcome.analysisStarted,
  );
  const failedEntryIds = uploadFailed.map((outcome) => outcome.entryId);

  if (uploadFailed.length === 0 && analyzeStartFailed.length === 0) {
    return { allSucceeded: true, uploadedJobIds, failedEntryIds: [], summaryText: null };
  }

  const lines: string[] = [];
  if (analyzeStartFailed.length > 0) {
    lines.push(
      `上传成功但分析启动失败 ${analyzeStartFailed.length} 个文件（任务已进入处理队列，状态为待分析，可在队列中重试分析）：`,
    );
    for (const outcome of analyzeStartFailed) {
      lines.push(`· ${outcome.failureText ?? ""}`);
    }
  }
  if (uploadFailed.length > 0) {
    lines.push(`上传失败 ${uploadFailed.length} 个文件（未创建任务，已保留在待上传列表，可修正后重试）：`);
    for (const outcome of uploadFailed) {
      lines.push(`· ${outcome.failureText ?? ""}`);
    }
  }
  return {
    allSucceeded: false,
    uploadedJobIds,
    failedEntryIds,
    summaryText: lines.join("\n"),
  };
}
