/**
 * 材料详情 API（WP2-B）的响应类型与纯函数适配层。
 *
 * 字段名与后端 `src/schemas/material_detail.py` 一一对应，**刻意不改写**：
 * 前端换一套命名，字段漂移就再也对不上。纯函数（时间轴分组、缺失态判定、
 * 覆盖 KPI 折算、运行分栏）放在这里而不是页面里，是为了能用项目既有的
 * jiti 直跑测试覆盖，不必引入 React Testing Library / jsdom。
 *
 * 三条口径贯穿全文件（与后端契约同源）
 * ------------------------------------
 * 1. **没有槽位 ≠ 缺失**：`exists=false` 只说明"库里没有这条材料"，
 *    应收基线未建立时推不出"应该有却没有"。
 * 2. **`null` ≠ 0**：`null` 是"当前算不出来"，`0` 是"算过且确实是零"。
 * 3. **未知不猜**：年度未知不兜底成具体年份；没有安全预览入口不自己拼 URL。
 */

// 相对路径而不是 `@/` 别名：本文件是纯逻辑层，要被 jiti 直跑的单测直接 import，
// 而 jiti 不解析 tsconfig 的 paths 别名（与 materialLedgerAdapters 同层做法一致）。
import {
  UNKNOWN_COUNT_TEXT,
  formatCount,
  formatRate,
  presentAnalysisConclusion,
  presentCoverageReason,
  presentCoverageStatus,
  presentRunStatus,
  type CoverageStatus,
} from "../../../lib/materialDetailPresentation";
import type { MaterialSlotSummary } from "./materialLedgerAdapters";

// ---- meta -------------------------------------------------------------------

export interface MaterialDetailMeta {
  data_basis: "existing_slots_only";
  expected_materials_ready: boolean;
  generated_at: string;
  linkage_basis?: string;
  legacy_unlinked_runs_excluded?: boolean;
}

// ---- 单位时间轴 -------------------------------------------------------------

export interface TimelineUnitRef {
  unit_id: string;
  unit_name: string;
  subject_kind: string;
  department_id: string | null;
  department_name: string | null;
  jurisdiction_id: string | null;
  jurisdiction_name: string | null;
}

export interface TimelineYearRow {
  fiscal_year: number;
  budget_slots: MaterialSlotSummary[];
  final_slots: MaterialSlotSummary[];
  unclassified_slots: MaterialSlotSummary[];
}

export interface UnitTimelineData {
  unit: TimelineUnitRef;
  years: TimelineYearRow[];
  unresolved_year_slots: MaterialSlotSummary[];
}

export interface UnitTimelineResponse {
  ok: true;
  data: UnitTimelineData;
  meta: MaterialDetailMeta;
}

// ---- 版本与来源 -------------------------------------------------------------

export interface MaterialSourceItem {
  source_id: string;
  source_kind: string;
  source_url: string | null;
  source_page_title: string | null;
  source_site: string | null;
  published_at: string | null;
  discovered_at: string | null;
  last_checked_at: string | null;
  source_page_hash: string | null;
  status: string;
}

export interface MaterialVersionItem {
  document_version_id: number;
  document_id: number;
  file_hash: string | null;
  original_filename: string | null;
  file_size_bytes: number | null;
  content_type: string | null;
  storage_backend: string | null;
  created_at: string | null;
  is_current: boolean;
  preview_url: string | null;
  download_url: string | null;
}

export interface SlotVersionListData {
  slot_id: string;
  items: MaterialVersionItem[];
  current_document_version_id: number | null;
}

export interface SlotVersionListResponse {
  ok: true;
  data: SlotVersionListData;
  meta: MaterialDetailMeta;
}

// ---- 处理记录 ---------------------------------------------------------------

export interface RunSummaryItem {
  job_uuid: string;
  document_version_id: number;
  is_current_document_version: boolean;
  status: string;
  mode: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  ai_findings_count: number | null;
  rule_findings_count: number | null;
  merged_findings_count: number | null;
  has_results: boolean;
  structured_ingest_status: string | null;
  elapsed_total_ms: number | null;
  error_summary: string | null;
  analysis_status: string | null;
  quality_status: string | null;
  analysis_conclusion: string | null;
  obligation_catalog_version: string | null;
  formal_issue_count: number | null;
  linkage_basis?: string;
}

export interface SlotRunListData {
  slot_id: string;
  items: RunSummaryItem[];
}

export interface SlotRunListResponse {
  ok: true;
  data: SlotRunListData;
  meta: MaterialDetailMeta;
}

// ---- 材料详情 ---------------------------------------------------------------

export interface AnalysisFindingItem {
  finding_id: string | null;
  title: string | null;
  message: string | null;
  severity: string | null;
  severity_bucket: string;
  source: string | null;
  rule_id: string | null;
  obligation_ids: string[];
  evidence_page: number | null;
  evidence_bbox: number[] | null;
  evidence_text: string | null;
  evidence_status: string | null;
  evidence_missing: string[];
  tags: string[];
  why_not: string | null;
}

export interface CoverageGroupItem {
  group_id: string;
  group_title: string | null;
  applicable: number;
  completed: number;
  not_applicable: number;
  unresolved: number;
}

export interface CoverageObligationItem {
  obligation_id: string;
  group_id: string | null;
  group_title: string | null;
  title: string | null;
  status: string;
  reason: string | null;
  reason_label: string | null;
  detail: string | null;
  blocks_gate: boolean;
  requires_ai: boolean;
  input_gaps: string[];
}

export interface CoverageSummaryBlock {
  catalog_version: string | null;
  catalog_fingerprint: string | null;
  applicable_total: number;
  completed_total: number;
  not_applicable_total: number;
  unresolved_total: number;
  blocking_total: number;
  coverage_rate: number | null;
  auto_completion_rate: number | null;
  by_reason: Record<string, number>;
  by_group: CoverageGroupItem[];
}

export interface CoverageBlock {
  available: boolean;
  reason: string | null;
  summary: CoverageSummaryBlock | null;
  items: CoverageObligationItem[];
}

export interface AnalysisBlock {
  available: boolean;
  reason: string | null;
  run: RunSummaryItem | null;
  formal_findings: AnalysisFindingItem[] | null;
  manual_review_items: AnalysisFindingItem[] | null;
  info_findings: AnalysisFindingItem[] | null;
  formal_issue_count: number | null;
  coverage: CoverageBlock | null;
}

export interface SlotDetailData {
  slot: MaterialSlotSummary;
  current_version: MaterialVersionItem | null;
  historical_version_count: number;
  version_total: number;
  sources: MaterialSourceItem[];
  current_analysis: AnalysisBlock;
}

export interface SlotDetailResponse {
  ok: true;
  data: SlotDetailData;
  meta: MaterialDetailMeta;
}

// ---- Tab 契约 ---------------------------------------------------------------

export type MaterialDetailTabKey = "overview" | "findings" | "coverage" | "versions" | "runs";

/** Tab 顺序与 key 稳定：以后全局搜索可以按 `?tab=` 直接落到某个 Tab。 */
export const MATERIAL_DETAIL_TABS: Array<{ key: MaterialDetailTabKey; label: string }> = [
  { key: "overview", label: "材料概览" },
  { key: "findings", label: "检查结果" },
  { key: "coverage", label: "检查覆盖" },
  { key: "versions", label: "版本与来源" },
  { key: "runs", label: "处理记录" },
];

/** 未知/缺失的 `?tab=` 一律回落到概览，而不是报错或显示空 Tab。 */
export function resolveDetailTab(value: unknown): MaterialDetailTabKey {
  const key = String(value ?? "").trim();
  const found = MATERIAL_DETAIL_TABS.find((item) => item.key === key);
  return found ? found.key : "overview";
}

export function materialDetailTabHref(slotId: string, tab: MaterialDetailTabKey): string {
  return `/materials/slots/${encodeURIComponent(slotId)}?tab=${tab}`;
}

/** 面包屑：材料台账 > 区县 > 主管部门 > 单位 > 财政年度/文种。 */
export function buildDetailBreadcrumb(slot: MaterialSlotSummary): Array<{
  label: string;
  href: string | null;
}> {
  const entries: Array<{ label: string; href: string | null }> = [
    { label: "材料台账", href: "/materials" },
  ];
  if (slot.jurisdiction_id) {
    entries.push({
      label: slot.jurisdiction_name ?? slot.jurisdiction_id,
      href: `/materials/district/${encodeURIComponent(slot.jurisdiction_id)}`,
    });
  }
  if (slot.department_org_id) {
    const year = slot.fiscal_year ? `?year=${slot.fiscal_year}` : "";
    entries.push({
      label: slot.department_name ?? slot.department_org_id,
      href: `/materials/department/${encodeURIComponent(slot.department_org_id)}${year}`,
    });
  }
  if (slot.subject_org_id) {
    entries.push({
      label: slot.subject_org_name,
      href: `/materials/unit/${encodeURIComponent(slot.subject_org_id)}`,
    });
  }
  entries.push({
    label: `${slot.fiscal_year ?? "年度待确认"} / ${presentReportKindShort(slot.report_kind)}`,
    href: null,
  });
  return entries;
}

function presentReportKindShort(kind: unknown): string {
  return (
    { budget: "预算", final: "决算", unknown: "文种待确认" } as Record<string, string>
  )[String(kind ?? "")] ?? "文种待确认";
}

// ---- 时间轴展示 -------------------------------------------------------------

export interface TimelineSlotCell {
  /** 没有槽位时的文案：只能是「尚无已建立材料」，不能是"缺失"。 */
  emptyLabel: string;
  exists: boolean;
  /** 界面主卡片展示的那一条（多条时取排序第一条，后端已给定顺序）。 */
  primary: MaterialSlotSummary | null;
  /** 除主卡片外还有几条：> 0 时界面必须提示，不能隐藏。 */
  extraCount: number;
  total: number;
  /** 该单元格的全部槽位（含 primary）。界面据此逐条列出，不做静默截断。 */
  all: MaterialSlotSummary[];
}

export const TIMELINE_ABSENT_LABEL = "尚无已建立材料";

/**
 * 时间轴单元格的呈现。
 *
 * 与部门矩阵的 `presentSlotCell` 同一取向，但这里多一条：同一（年度 × 文种）
 * 下可能有多条槽位（正式槽位 + `mapping_required` 占位槽位）。用
 * `slot | null` 会让多出来的那条凭空消失，而它恰恰最需要人工确认，
 * 因此这里同时给出 `extraCount` 与完整列表 —— 界面既要提示
 * 「另有 N 个待确认槽位」，也要能逐条展示。
 */
export function presentTimelineCell(slots: MaterialSlotSummary[] | null | undefined): TimelineSlotCell {
  const list = Array.isArray(slots) ? slots : [];
  if (list.length === 0) {
    return {
      emptyLabel: TIMELINE_ABSENT_LABEL,
      exists: false,
      primary: null,
      extraCount: 0,
      total: 0,
      all: [],
    };
  }
  return {
    emptyLabel: TIMELINE_ABSENT_LABEL,
    exists: true,
    primary: list[0],
    extraCount: list.length - 1,
    total: list.length,
    all: list,
  };
}

export interface TimelineRowView {
  fiscalYear: number;
  budget: TimelineSlotCell;
  final: TimelineSlotCell;
  unclassified: TimelineSlotCell;
  /** 年度状态标签（保守推导，见 `presentYearStatus`）。 */
  yearStatus: string;
}

export function buildTimelineRows(data: UnitTimelineData | null): TimelineRowView[] {
  const years = data?.years ?? [];
  return years.map((row) => {
    const budget = presentTimelineCell(row.budget_slots);
    const final = presentTimelineCell(row.final_slots);
    const unclassified = presentTimelineCell(row.unclassified_slots);
    return {
      fiscalYear: row.fiscal_year,
      budget,
      final,
      unclassified,
      yearStatus: presentYearStatus([budget, final, unclassified]),
    };
  });
}

/**
 * 年度状态标签。
 *
 * **刻意没有「完整」这个取值**：判断"完整"需要应收材料基线（WP9），
 * 当前基线未建立，说某个年度"完整"就是把"我只知道这些"说成
 * "这些就是全部"。因此这里只按已存在的槽位给保守描述。
 */
export function presentYearStatus(cells: TimelineSlotCell[]): string {
  const slots = cells
    .filter((cell) => cell.exists && cell.primary)
    .flatMap((cell) => (cell.primary ? [cell.primary] : []));
  if (slots.length === 0) {
    return TIMELINE_ABSENT_LABEL;
  }
  const statuses = slots.map((slot) => String(slot.status ?? ""));
  if (statuses.includes("missing")) {
    return "有逾期未上传";
  }
  if (statuses.includes("mapping_required")) {
    return "有待确认项";
  }
  if (statuses.includes("failed")) {
    return "有处理失败";
  }
  if (statuses.every((status) => status === "completed")) {
    return "已复核完成";
  }
  return "处理中";
}

/** 年度未知的槽位在时间轴末尾单独成行，标题固定，不假装是某个年份。 */
export const UNRESOLVED_YEAR_LABEL = "年度待确认";

// ---- 缺失态与截止时间待确认 -------------------------------------------------

export interface SlotStateNotice {
  /** 是否处于"真实的缺失态"（只有 status='missing' 才算）。 */
  isMissing: boolean;
  /** 是否"截止时间未知"（不得显示成缺失）。 */
  isDueUnknown: boolean;
  /** 主文案。 */
  label: string;
  /** 补充说明（缺失态下用于区分"有没有历史版本"）。 */
  detail: string | null;
}

/**
 * 材料状态的主文案。
 *
 * 两条不能让步的规则：
 *
 * 1. **只有 `status='missing'` 才能进缺失态**（§五十七）；尤其
 *    `not_due + due_at_unknown` 必须显示"截止时间未知，当前无法判断是否逾期"，
 *    绝不能显示成"缺失"（§五十九）——把未知当已知是最典型的虚假结论。
 * 2. **missing 且存在历史版本时不能写"从未上传"**（§五十八）：历史版本仍在，
 *    只能写"当前无有效文件版本"。连"该槽位尚未关联任何 PDF 版本"这种
 *    关于槽位本身的描述，也要与"该单位从未上传过"区分开。
 */
export function presentSlotState(
  slot: { status?: unknown; status_reason?: unknown } | null | undefined,
  versionTotal: number,
): SlotStateNotice {
  const status = String(slot?.status ?? "");
  const reason = String(slot?.status_reason ?? "");
  if (status === "missing") {
    return {
      isMissing: true,
      isDueUnknown: false,
      label: "逾期未上传",
      // 两种形态都不出现"从未上传"这种断言：历史版本可能还在，文件也可能
      // 已上传但未绑定到这条材料。措辞甚至连否定句里也不放——一句"不能视为
      // 从未上传"在扫描关键字时与断言本身无法区分，反而增加误读空间。
      detail:
        versionTotal > 0
          ? "当前无有效文件版本；该材料槽位下存在历史版本，上一版文件仍保留在版本历史里。"
          : "该材料槽位尚未关联任何 PDF 版本；文件可能已上传但未绑定到这条材料。",
    };
  }
  if (status === "not_due" && reason === "due_at_unknown") {
    return {
      isMissing: false,
      isDueUnknown: true,
      label: "截止时间未知，当前无法判断是否逾期",
      detail: "该材料没有登记应收/公开截止时间，因此既不能判定逾期，也不能判定未到期。",
    };
  }
  return { isMissing: false, isDueUnknown: false, label: "", detail: null };
}

// ---- 当前分析 ---------------------------------------------------------------

/**
 * 当前分析不可用时的文案。
 *
 * **禁止显示"暂无问题"**（§五十三）：那会把"没有可确认的结论"读成
 * "查过且没问题"。这里按成因分开给话，让用户知道下一步该做什么。
 */
export function presentAnalysisAvailability(reason: unknown): string {
  const code = String(reason ?? "").trim();
  if (code === "no_current_document_version") {
    return "该材料当前没有有效文件版本，因此没有可确认的分析结果。";
  }
  if (code === "no_persisted_analysis_for_current_version") {
    return "当前文件版本暂无可确认的分析结果（该版本尚未完成分析，或分析结果尚未落库）。";
  }
  return "当前文件版本暂无可确认的分析结果。";
}

export const ANALYSIS_UNAVAILABLE_LABEL = "当前文件版本暂无可确认的分析结果";

/** 检查覆盖不可用时的文案（§三十七）。 */
export function presentCoverageAvailability(reason: unknown): string {
  const code = String(reason ?? "").trim();
  if (code === "no_coverage_for_current_document_version") {
    return "当前文件版本暂无可确认的检查覆盖记录。";
  }
  return "当前文件版本暂无可确认的检查覆盖记录。";
}

export const COVERAGE_UNAVAILABLE_LABEL = "当前文件版本暂无可确认的检查覆盖记录";

export interface CoverageKpi {
  key: string;
  label: string;
  value: string;
  /** 副说明，解释这个数字的边界。 */
  hint: string;
}

/**
 * 检查覆盖的 KPI。
 *
 * 不可用时**一个数字都不给**（返回空列表），页面整体显示"覆盖暂不可用"——
 * 不显示 `0 / 0`、`0%`、`100%` 或"全部通过"（§三十七）：
 * "没有覆盖记录"与"覆盖完整"在数字上不能长得一样。
 */
export function buildCoverageKpis(coverage: CoverageBlock | null | undefined): CoverageKpi[] {
  if (!coverage?.available || !coverage.summary) {
    return [];
  }
  const summary = coverage.summary;
  return [
    {
      key: "applicable",
      label: "应检查义务",
      value: formatCount(summary.applicable_total),
      hint: "按当前材料画像展开的检查事项总数",
    },
    {
      key: "completed",
      label: "自动检查完成",
      value: formatCount(summary.completed_total),
      hint: "已由系统执行并得出结论的事项",
    },
    {
      key: "not_applicable",
      label: "不适用",
      value: formatCount(summary.not_applicable_total),
      hint: "本次材料不含对应内容，不计入完成率分母",
    },
    {
      key: "pending_review",
      label: "待人工核验",
      value: formatCount(summary.unresolved_total - summary.blocking_total),
      hint: "非阻塞的未完成事项",
    },
    {
      key: "blocking",
      label: "阻塞未完成",
      value: formatCount(summary.blocking_total),
      hint: "存在这些事项时不得把材料标成「检查完成」",
    },
    {
      key: "coverage_rate",
      label: "覆盖完成率",
      value: formatRate(summary.coverage_rate),
      hint: "分母为 0 时显示 —，不按 100% 计算",
    },
  ];
}

export interface CoverageGroupRow {
  groupId: string;
  title: string;
  applicable: number;
  completed: number;
  /** 形如 `6 / 7`，供表格直接显示。 */
  progress: string;
  /** 未完成事项的中文摘要（逐条列出，不合并成"有问题"）。 */
  unresolvedNotes: Array<{ code: string; label: string; count: number; blocking: boolean }>;
}

/**
 * 义务分组的逐行呈现。
 *
 * 分组标题优先用后端透出的 `group_title`（引擎产出的中文，已落库）；
 * 缺失时回退到 `group_id`，**不**在前端再维护一份分组名表 —— 那会变成
 * 第三套中文，与引擎漂移后没人能发现。
 */
export function buildCoverageGroups(coverage: CoverageBlock | null | undefined): CoverageGroupRow[] {
  if (!coverage?.available || !coverage.summary) {
    return [];
  }
  const reasonsByGroup = new Map<string, Map<string, number>>();
  for (const item of coverage.items) {
    if (!item.blocks_gate) {
      continue;
    }
    const key = String(item.group_id ?? "");
    const bucket = reasonsByGroup.get(key) ?? new Map<string, number>();
    const code = String(item.reason ?? item.status ?? "");
    bucket.set(code, (bucket.get(code) ?? 0) + 1);
    reasonsByGroup.set(key, bucket);
  }

  return coverage.summary.by_group.map((group) => {
    const notes = Array.from(reasonsByGroup.get(group.group_id)?.entries() ?? []).map(
      ([code, count]) => ({
        code,
        label: presentCoverageReason(code) ?? code,
        count,
        blocking: true,
      }),
    );
    return {
      groupId: group.group_id,
      title: group.group_title ?? group.group_id,
      applicable: group.applicable,
      completed: group.completed,
      progress: `${group.completed} / ${group.applicable}`,
      unresolvedNotes: notes,
    };
  });
}

/** 单条义务的展开明细（技术编号放这里，不进主文案）。 */
export function presentObligationItem(item: CoverageObligationItem): {
  title: string;
  businessLabel: string;
  technicalLabel: string;
} {
  const presentation = presentCoverageStatus(item.status);
  return {
    title: item.title ?? item.obligation_id,
    businessLabel: presentation.label,
    technicalLabel: [item.obligation_id, item.reason ?? item.status].filter(Boolean).join(" · "),
  };
}

// ---- 处理记录 ---------------------------------------------------------------

export interface RunRowView {
  jobUuid: string;
  statusLabel: string;
  isCurrentVersion: boolean;
  versionBadge: string;
  documentVersionId: number;
  mode: string | null;
  startedAt: string | null;
  completedAt: string | null;
  findingsSummary: string;
  elapsedLabel: string;
  analysisStatusLabel: string | null;
  conclusionLabel: string | null;
  catalogVersion: string | null;
  errorSummary: string | null;
}

/**
 * 运行的 finding 摘要。
 *
 * 只给已落库的三类计数；`has_results=false` 时三者都显示 `—` ——
 * 没有落库结果时显示 0 会把"还没出结论"读成"跑了且没问题"。
 */
export function presentRunFindings(run: RunSummaryItem): string {
  if (!run.has_results) {
    return UNKNOWN_COUNT_TEXT;
  }
  return `${formatCount(run.ai_findings_count)} AI / ${formatCount(run.rule_findings_count)} 规则 / ${formatCount(run.merged_findings_count)} 合并`;
}

/** 耗时：未知显示 `—`，不显示 0 ms。 */
export function formatElapsed(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) {
    return UNKNOWN_COUNT_TEXT;
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  const seconds = ms / 1000;
  if (seconds < 60) {
    return `${Math.round(seconds * 10) / 10} s`;
  }
  return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} s`;
}

export function buildRunRows(runs: RunSummaryItem[]): RunRowView[] {
  return runs.map((run) => ({
    jobUuid: run.job_uuid,
    statusLabel: presentRunStatus(run.status).label,
    isCurrentVersion: run.is_current_document_version,
    versionBadge: run.is_current_document_version ? "当前文件版本" : "历史文件版本",
    documentVersionId: run.document_version_id,
    mode: run.mode,
    startedAt: run.started_at,
    completedAt: run.completed_at,
    findingsSummary: presentRunFindings(run),
    elapsedLabel: formatElapsed(run.elapsed_total_ms),
    analysisStatusLabel: run.analysis_status
      ? presentRunStatus(run.analysis_status).label
      : null,
    conclusionLabel: run.analysis_conclusion
      ? presentAnalysisConclusion(run.analysis_conclusion)
      : null,
    catalogVersion: run.obligation_catalog_version,
    errorSummary: run.error_summary,
  }));
}

/**
 * 运行列表按当前/历史文件版本分栏（§四十八）。
 *
 * 不分成一列显示：混在一张表里会让用户以为上一版文件的运行结论
 * 就是这一版文件的结果。
 */
export function partitionRunRows(rows: RunRowView[]): {
  current: RunRowView[];
  history: RunRowView[];
} {
  return {
    current: rows.filter((row) => row.isCurrentVersion),
    history: rows.filter((row) => !row.isCurrentVersion),
  };
}

// ---- 响应解析（契约校验） ---------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function hasDetailEnvelope(
  payload: unknown,
): payload is { ok: true; data: Record<string, unknown>; meta: Record<string, unknown> } {
  if (!isRecord(payload) || payload.ok !== true) {
    return false;
  }
  return isRecord(payload.data) && isRecord(payload.meta);
}

export function readUnitTimelinePayload(payload: unknown): UnitTimelineResponse | null {
  if (!hasDetailEnvelope(payload)) {
    return null;
  }
  const data = payload.data;
  if (!isRecord(data.unit) || !Array.isArray(data.years) || !Array.isArray(data.unresolved_year_slots)) {
    return null;
  }
  return payload as unknown as UnitTimelineResponse;
}

export function readSlotDetailPayload(payload: unknown): SlotDetailResponse | null {
  if (!hasDetailEnvelope(payload)) {
    return null;
  }
  const data = payload.data;
  if (!isRecord(data.slot) || !isRecord(data.current_analysis) || !Array.isArray(data.sources)) {
    return null;
  }
  return payload as unknown as SlotDetailResponse;
}

export function readSlotVersionsPayload(payload: unknown): SlotVersionListResponse | null {
  if (!hasDetailEnvelope(payload)) {
    return null;
  }
  if (!Array.isArray(payload.data.items)) {
    return null;
  }
  return payload as unknown as SlotVersionListResponse;
}

export function readSlotRunsPayload(payload: unknown): SlotRunListResponse | null {
  if (!hasDetailEnvelope(payload)) {
    return null;
  }
  if (!Array.isArray(payload.data.items)) {
    return null;
  }
  return payload as unknown as SlotRunListResponse;
}

// ---- 检查结果分栏 -----------------------------------------------------------

export interface FindingsSection {
  key: "formal" | "manual_review" | "info";
  title: string;
  items: AnalysisFindingItem[];
  /** 该栏的说明：明确它是不是"正式问题"。 */
  desc: string;
}

/**
 * 检查结果的三栏。
 *
 * 「正式问题」必须来自后端通过正式门禁（`is_formal_finding`）的那一批；
 * 前端**禁止**把全部 finding 都显示成正式问题。三栏之和等于该版本分析
 * 结论里的全部条目，不会漏掉也不会重复。
 */
export function buildFindingsSections(analysis: AnalysisBlock | null | undefined): FindingsSection[] {
  if (!analysis?.available) {
    return [];
  }
  return [
    {
      key: "formal",
      title: "正式问题",
      items: analysis.formal_findings ?? [],
      desc: "证据完整、可作为正式结论的问题。",
    },
    {
      key: "manual_review",
      title: "需人工核验",
      items: analysis.manual_review_items ?? [],
      desc: "证据不足被降级的候选问题：它们不是已确认的问题，需要人工判断。",
    },
    {
      key: "info",
      title: "信息提示",
      items: analysis.info_findings ?? [],
      desc: "通过正式门禁、但不构成需要处置的问题。",
    },
  ];
}

export const COVERAGE_STATUS_ORDER: CoverageStatus[] = [
  "completed",
  "not_applicable",
  "not_implemented",
  "not_executed",
  "insufficient_data",
  "parse_ambiguity",
  "execution_error",
  "profile_unresolved",
  "kind_conflict",
  "ai_not_run",
  "ai_failed",
];
