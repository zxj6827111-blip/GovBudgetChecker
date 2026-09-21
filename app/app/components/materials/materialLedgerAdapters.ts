/**
 * 材料台账 API 的响应类型与纯函数适配层。
 *
 * 类型与后端 `src/schemas/material_ledger.py` 一一对应（字段名刻意不改写：
 * 前端换一套命名，字段漂移就再也对不上）。纯函数（KPI 折算、行映射、空态判定）
 * 放在这里而不是页面里，是为了能用项目既有的 jiti 直跑测试覆盖，
 * 不必为组件测试引入 React Testing Library / jsdom。
 */

// 相对路径而不是 `@/` 别名：本文件是纯逻辑层，要被 jiti 直跑的单测直接 import，
// 而 jiti 不解析 tsconfig 的 paths 别名（与 workbenchAdapters 等同层的做法一致）。
import type { MaterialStatus } from "../../../lib/materialStatusPresentation";

export type ReportKindFilter = "all" | "budget" | "final";

export interface MaterialStatusCounts {
  not_due: number;
  missing: number;
  uploaded: number;
  processing: number;
  review_required: number;
  reviewing: number;
  completed: number;
  not_applicable: number;
  mapping_required: number;
  failed: number;
}

/**
 * 应收基线相关字段。`expected_materials_ready=false` 时恒为 null，
 * 表示"当前无法计算"——与 0（已计算且为零）严格区分。
 */
export interface ExpectedCoverageFields {
  expected_total: number | null;
  expected_budget_total: number | null;
  expected_final_total: number | null;
  coverage_rate: number | null;
  budget_coverage_rate: number | null;
  final_coverage_rate: number | null;
  missing_expected_total: number | null;
}

export interface MaterialLedgerMeta {
  data_basis: "existing_slots_only";
  expected_materials_ready: boolean;
  generated_at: string;
}

export interface PaginationMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface MaterialListMeta extends MaterialLedgerMeta {
  pagination: PaginationMeta;
}

export interface MaterialSlotSummary {
  slot_id: string;
  slot_key: string;
  jurisdiction_id: string | null;
  jurisdiction_name: string | null;
  department_org_id: string | null;
  department_name: string | null;
  subject_org_id: string;
  subject_org_name: string;
  subject_kind: string;
  material_scope: string;
  fiscal_year: number | null;
  report_kind: string;
  caliber: string;
  caliber_conflict_candidate: string | null;
  status: MaterialStatus | string;
  status_reason: string | null;
  applicability_status: string;
  applicability_note: string | null;
  due_at: string | null;
  current_document_version_id: number | null;
  formal_issue_count: number | null;
  updated_at: string | null;
}

export interface MaterialCoverageSummary extends ExpectedCoverageFields {
  slot_total: number;
  status_counts: MaterialStatusCounts;
  budget_total: number;
  final_total: number;
  unknown_kind_total: number;
  /**
   * 截止时间未知（due_at 为空）的槽位数：独立的数据质量指标，与任意状态并存，
   * 不能用来与 not_due 相减反推"未到期"。
   */
  due_at_unknown: number;
  /**
   * 已确认尚未到截止时间的槽位数（status='not_due' 且 reason='due_not_reached'）。
   * 界面"未到期"只取这个字段；`status_counts.not_due` 是状态机事实
   * （含"截止时间未知"那种同样停在 not_due 的情况），两者并存、不互相覆盖。
   */
  not_due_confirmed: number;
  jurisdiction_unknown_total: number;
}

export interface DistrictCoverageItem extends ExpectedCoverageFields {
  district_id: string;
  district_name: string;
  slot_total: number;
  status_counts: MaterialStatusCounts;
  budget_total: number;
  final_total: number;
  unknown_kind_total: number;
  due_at_unknown: number;
  not_due_confirmed: number;
  updated_at: string | null;
}

export interface MaterialCoverageData {
  summary: MaterialCoverageSummary;
  districts: DistrictCoverageItem[];
}

export interface MaterialCoverageResponse {
  ok: true;
  data: MaterialCoverageData;
  meta: MaterialLedgerMeta;
}

export interface DepartmentScopeStat {
  slot_total: number;
  status_counts: MaterialStatusCounts;
}

export interface DepartmentMatrixItem extends ExpectedCoverageFields {
  department_id: string | null;
  department_name: string | null;
  subject_count: number;
  slot_total: number;
  status_counts: MaterialStatusCounts;
  budget: DepartmentScopeStat;
  final: DepartmentScopeStat;
  unknown_kind_total: number;
  missing: number;
  /** 状态机事实：not_due 总数（含"截止时间未知"那部分），界面不直接展示。 */
  not_due: number;
  /** 界面"未到期"展示用的数字：已确认尚未到截止时间。 */
  not_due_confirmed: number;
  due_at_unknown: number;
  updated_at: string | null;
}

export interface DistrictDepartmentMatrixData {
  district: { district_id: string; district_name: string };
  items: DepartmentMatrixItem[];
}

export interface DistrictDepartmentMatrixResponse {
  ok: true;
  data: DistrictDepartmentMatrixData;
  meta: MaterialListMeta;
}

export interface SubjectSlotRef {
  exists: boolean;
  slot: MaterialSlotSummary | null;
}

export interface DepartmentSubjectRow {
  subject_org_id: string;
  subject_org_name: string;
  subject_kind: string;
  relationship: string;
  budget: SubjectSlotRef;
  final: SubjectSlotRef;
  unclassified: SubjectSlotRef;
}

export interface DepartmentGroupSet {
  department_summary: DepartmentSubjectRow[];
  head_unit: DepartmentSubjectRow[];
  subordinate_units: DepartmentSubjectRow[];
  relationship_unknown: DepartmentSubjectRow[];
}

export interface DepartmentMatrixData {
  department: {
    department_id: string;
    department_name: string;
    jurisdiction_id: string | null;
    jurisdiction_name: string | null;
  };
  fiscal_year: number;
  groups: DepartmentGroupSet;
}

export interface DepartmentMatrixResponse {
  ok: true;
  data: DepartmentMatrixData;
  meta: MaterialLedgerMeta;
}

// ---- KPI 折算 ---------------------------------------------------------------

export interface MaterialKpiDefinition {
  key: string;
  label: string;
  /**
   * 从 summary 折算展示值。
   *
   * "未到期"取 `summary.not_due_confirmed`（已确认尚未到截止时间），**不是**
   * `status_counts.not_due`：后者含"截止时间未知"的槽位，把未知当已知正是
   * 本轮要修掉的口径错误；两者也不能相减反推（due_at_unknown 与任意状态并存）。
   */
  fromSummary: (summary: MaterialCoverageSummary) => number;
  testId: string;
}

/**
 * 首页 KPI 的第一屏（§十九）。口径与后端字段一一对应，
 * 不在前端做任何"合并状态"的再解释以外的加工：
 * - 待复核 = review_required + reviewing（两者都是"等人处理"）；
 * - 未到期只统计 due_not_reached，截止时间未知单独展示；
 * - not_applicable 绝不计入缺失。
 */
export const MATERIAL_KPIS: MaterialKpiDefinition[] = [
  {
    key: "slot_total",
    label: "已建材料",
    fromSummary: (summary) => summary.slot_total,
    testId: "gbc-material-kpi-slot-total",
  },
  {
    key: "uploaded",
    label: "已上传",
    fromSummary: (summary) => summary.status_counts.uploaded,
    testId: "gbc-material-kpi-uploaded",
  },
  {
    key: "review",
    label: "待复核",
    fromSummary: (summary) =>
      summary.status_counts.review_required + summary.status_counts.reviewing,
    testId: "gbc-material-kpi-review",
  },
  {
    key: "completed",
    label: "已完成",
    fromSummary: (summary) => summary.status_counts.completed,
    testId: "gbc-material-kpi-completed",
  },
  {
    key: "mapping_required",
    label: "待确认归属",
    fromSummary: (summary) => summary.status_counts.mapping_required,
    testId: "gbc-material-kpi-mapping",
  },
  {
    key: "failed",
    label: "处理失败",
    fromSummary: (summary) => summary.status_counts.failed,
    testId: "gbc-material-kpi-failed",
  },
  {
    key: "not_due",
    label: "未到期",
    fromSummary: (summary) => summary.not_due_confirmed,
    testId: "gbc-material-kpi-not-due",
  },
  {
    key: "missing",
    label: "逾期未上传",
    fromSummary: (summary) => summary.status_counts.missing,
    testId: "gbc-material-kpi-missing",
  },
];

export interface MaterialKpiValue {
  key: string;
  label: string;
  value: number;
  testId: string;
}

/** 把一份统计折算成首页 KPI 的展示值。`summary` 为 null 表示还没拿到数据。 */
export function buildMaterialKpis(summary: MaterialCoverageSummary | null): MaterialKpiValue[] {
  if (!summary) {
    return [];
  }
  return MATERIAL_KPIS.map((definition) => ({
    key: definition.key,
    label: definition.label,
    value: definition.fromSummary(summary),
    testId: definition.testId,
  }));
}

// ---- 行映射 ---------------------------------------------------------------

export interface DistrictCardRow {
  districtId: string;
  districtName: string;
  slotTotal: number;
  budgetTotal: number;
  finalTotal: number;
  reviewRequired: number;
  mappingRequired: number;
  failed: number;
  missing: number;
  /** "未到期"展示值：已确认尚未到截止时间（不含"截止时间未知"）。 */
  notDueConfirmed: number;
  /** 截止时间未知的槽位数：与 notDueConfirmed 并列展示，两者含义不同。 */
  dueAtUnknown: number;
  /** 完整率：无应收基线时为 null，界面显示 `—`。 */
  coverageRate: number | null;
  updatedAt: string | null;
}

export function toDistrictCardRows(districts: DistrictCoverageItem[]): DistrictCardRow[] {
  return districts.map((item) => ({
    districtId: item.district_id,
    districtName: item.district_name,
    slotTotal: item.slot_total,
    budgetTotal: item.budget_total,
    finalTotal: item.final_total,
    reviewRequired: item.status_counts.review_required + item.status_counts.reviewing,
    mappingRequired: item.status_counts.mapping_required,
    failed: item.status_counts.failed,
    missing: item.status_counts.missing,
    notDueConfirmed: item.not_due_confirmed,
    dueAtUnknown: item.due_at_unknown,
    coverageRate: item.coverage_rate,
    updatedAt: item.updated_at,
  }));
}

export interface DepartmentMatrixRow {
  departmentId: string | null;
  departmentName: string;
  subjectCount: number;
  budgetSlotTotal: number;
  finalSlotTotal: number;
  reviewRequired: number;
  mappingRequired: number;
  failed: number;
  missing: number;
  /** "未到期"展示值：已确认尚未到截止时间（不含"截止时间未知"）。 */
  notDueConfirmed: number;
  /** 截止时间未知的槽位数：与"未到期"是两个不同口径，必须都能看到。 */
  dueAtUnknown: number;
  coverageRate: number | null;
  updatedAt: string | null;
}

/** 未归入主管部门的行（区级政府本级材料）在前端有明确名称，不显示空白。 */
export const UNAPPOINTED_DEPARTMENT_LABEL = "未归入主管部门（区级政府本级材料）";

export function toDepartmentMatrixRows(items: DepartmentMatrixItem[]): DepartmentMatrixRow[] {
  return items.map((item) => ({
    departmentId: item.department_id,
    departmentName: item.department_name ?? UNAPPOINTED_DEPARTMENT_LABEL,
    subjectCount: item.subject_count,
    budgetSlotTotal: item.budget.slot_total,
    finalSlotTotal: item.final.slot_total,
    reviewRequired: item.status_counts.review_required + item.status_counts.reviewing,
    mappingRequired: item.status_counts.mapping_required,
    failed: item.status_counts.failed,
    missing: item.missing,
    notDueConfirmed: item.not_due_confirmed,
    dueAtUnknown: item.due_at_unknown,
    coverageRate: item.coverage_rate,
    updatedAt: item.updated_at,
  }));
}

// ---- 部门矩阵分组 ---------------------------------------------------------------

export interface DepartmentGroupSection {
  key: keyof DepartmentGroupSet;
  title: string;
  rows: DepartmentSubjectRow[];
}

const GROUP_TITLES: Array<{ key: keyof DepartmentGroupSet; title: string }> = [
  { key: "department_summary", title: "部门汇总" },
  { key: "head_unit", title: "本部单位" },
  { key: "subordinate_units", title: "直属单位" },
  { key: "relationship_unknown", title: "关系待确认" },
];

/**
 * 分组展示顺序固定为"部门汇总 → 本部单位 → 直属单位"，
 * 「关系待确认」只在非空时出现（它是异常兜底组，常驻会让页面看起来总有坏数据）。
 */
export function buildDepartmentGroups(groups: DepartmentGroupSet | null): DepartmentGroupSection[] {
  if (!groups) {
    return [];
  }
  return GROUP_TITLES.filter(
    (entry) => entry.key !== "relationship_unknown" || groups[entry.key].length > 0,
  ).map((entry) => ({ ...entry, rows: groups[entry.key] }));
}

export function countDepartmentSubjects(groups: DepartmentGroupSet | null): number {
  if (!groups) {
    return 0;
  }
  return (
    groups.department_summary.length +
    groups.head_unit.length +
    groups.subordinate_units.length +
    groups.relationship_unknown.length
  );
}

// ---- 具体槽位的展示 ---------------------------------------------------------------

export interface SlotCellPresentation {
  /** 没有槽位时的文案：只能是"尚无已建立材料"，不能是"缺失"。 */
  emptyLabel: string;
  exists: boolean;
  slot: MaterialSlotSummary | null;
}

export const SLOT_ABSENT_LABEL = "尚无已建立材料";

/**
 * 槽位单元格的呈现。
 *
 * **关键口径**：`exists=false` 只表示"当前库里没有这条槽位"。
 * 应收基线未建立时，"没有槽位"没有任何依据被解释成"缺失/逾期/未上传"，
 * 因此这里给出的文案只有「尚无已建立材料」一种。
 */
export function presentSlotCell(ref: SubjectSlotRef | null | undefined): SlotCellPresentation {
  const exists = Boolean(ref?.exists && ref?.slot);
  return {
    exists,
    slot: exists ? (ref?.slot ?? null) : null,
    emptyLabel: SLOT_ABSENT_LABEL,
  };
}

// ---- 筛选参数 ---------------------------------------------------------------

export interface MaterialFilterState {
  fiscalYear: string;
  reportKind: ReportKindFilter;
  status: MaterialStatus | "all";
}

export const DEFAULT_MATERIAL_FILTERS: MaterialFilterState = {
  fiscalYear: "",
  reportKind: "all",
  status: "all",
};

/** 把筛选状态转成查询串。空值一律不进查询串（不猜默认年度/默认状态）。 */
export function buildMaterialQuery(filters: MaterialFilterState, extra?: Record<string, string>): string {
  const params = new URLSearchParams();
  if (filters.fiscalYear.trim()) {
    params.set("fiscal_year", filters.fiscalYear.trim());
  }
  if (filters.reportKind !== "all") {
    params.set("report_kind", filters.reportKind);
  }
  if (filters.status !== "all") {
    params.set("status", filters.status);
  }
  for (const [key, value] of Object.entries(extra ?? {})) {
    if (value !== "") {
      params.set(key, value);
    }
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** 预算/决算筛选的可选值。 */
export const REPORT_KIND_FILTER_OPTIONS: Array<{ value: ReportKindFilter; label: string }> = [
  { value: "all", label: "全部文种" },
  { value: "budget", label: "预算" },
  { value: "final", label: "决算" },
];

// ---- 财政年度输入 ---------------------------------------------------------------

/** 与后端 `Query(ge=2000, le=2099)` 同源：前端先挡一次，避免把 422 当交互反馈。 */
export const FISCAL_YEAR_MIN = 2000;
export const FISCAL_YEAR_MAX = 2099;

export interface FiscalYearInput {
  /** 输入框里保留用户原始输入（不做静默改写）。 */
  raw: string;
  /** 可直接用于查询的年度；空串表示"不按年度筛选"。 */
  value: string;
  valid: boolean;
}

/**
 * 校验财政年度输入。
 *
 * - 空 → 合法（不筛选任何年度，而不是"默认今年"）；
 * - 4 位且落在 [2000, 2099] → 合法；
 * - 其余 → 非法：**不参与查询**，也不做任何兜底取值（把 "25" 猜成 2025
 *   会让用户以为自己在看 2025 年的数据）。
 */
export function normalizeFiscalYearInput(raw: string): FiscalYearInput {
  const text = String(raw ?? "").trim();
  if (!text) {
    return { raw: text, value: "", valid: true };
  }
  if (!/^\d{4}$/.test(text)) {
    return { raw: text, value: "", valid: false };
  }
  const year = Number(text);
  if (year < FISCAL_YEAR_MIN || year > FISCAL_YEAR_MAX) {
    return { raw: text, value: "", valid: false };
  }
  return { raw: text, value: text, valid: true };
}

// ---- 响应解析（契约校验） ---------------------------------------------------

/**
 * 把未知的响应体收敛成契约内的对象；**形状不符就返回 null**。
 *
 * 为什么不能让页面直接读 `payload.data.summary`：代理层或后端一旦返回
 * `{}` / HTML 错误页，页面会在读取嵌套属性时抛错并白屏。更糟的是把
 * "响应无法解析"显示成"没有材料"——那是把故障伪装成业务结论。
 * 因此这里显式区分"契约内的空数据"（合法）与"不符合契约的响应"（错误）。
 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function hasLedgerEnvelope(payload: unknown): payload is { ok: true; data: Record<string, unknown>; meta: Record<string, unknown> } {
  if (!isRecord(payload) || payload.ok !== true) {
    return false;
  }
  return isRecord(payload.data) && isRecord(payload.meta);
}

export function readCoveragePayload(payload: unknown): MaterialCoverageResponse | null {
  if (!hasLedgerEnvelope(payload)) {
    return null;
  }
  const summary = payload.data.summary;
  const districts = payload.data.districts;
  if (!isRecord(summary) || !Array.isArray(districts)) {
    return null;
  }
  return payload as unknown as MaterialCoverageResponse;
}

export function readDistrictDepartmentsPayload(
  payload: unknown,
): DistrictDepartmentMatrixResponse | null {
  if (!hasLedgerEnvelope(payload)) {
    return null;
  }
  const data = payload.data;
  if (!isRecord(data.district) || !Array.isArray(data.items)) {
    return null;
  }
  return payload as unknown as DistrictDepartmentMatrixResponse;
}

export function readDepartmentMatrixPayload(payload: unknown): DepartmentMatrixResponse | null {
  if (!hasLedgerEnvelope(payload)) {
    return null;
  }
  const data = payload.data;
  if (!isRecord(data.department) || !isRecord(data.groups)) {
    return null;
  }
  return payload as unknown as DepartmentMatrixResponse;
}
