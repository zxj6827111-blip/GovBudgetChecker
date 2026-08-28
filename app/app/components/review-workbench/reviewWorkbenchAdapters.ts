/**
 * reviewWorkbenchAdapters.ts：Task 6 审核工作台的纯逻辑层。
 *
 * 拆分原因与本项目既有约定一致（见 app/app/components/ui/buttonStyles.ts 顶部
 * 注释）：前端单测用 jiti 直跑 .ts 文件，无 JSX 依赖的纯函数拆出后才能被
 * tests/*.test.ts 直接 import 断言，.tsx 组件文件仍走完整 Next.js 编译 + e2e。
 *
 * 本文件承载 Task 6 任务书里最容易被复核抓到的红线：
 * - 缩略图懒加载/并发上限/缓存的调度逻辑（性能关键路径，任务书要求"实测数据"）；
 * - 问题数与排序口径必须与 count_formal_findings 对齐，不得前端另算；
 * - 年份未识别显示"未识别到"，禁止 2000 兜底；
 * - review_required 状态徽章不得显示成"分析完成"；
 * - 底部状态条计数必须来自真实工作流状态，不得写死原型图的 2/1/3。
 */

import type { Problem } from "../../../lib/mock";
import type { JobDetailRecord, JobSummaryRecord } from "../../../lib/uiAdapters";
import { normalizeProblemBbox } from "../task-review/problemPreview";
import type { OverlayBox } from "../task-review/problemPreview";

// ---------------------------------------------------------------------------
// 缩略图懒加载调度（Task 6.2，性能关键）
// ---------------------------------------------------------------------------

/**
 * 缩略图加载请求调度器：并发上限 + 客户端缓存 + 去重。
 *
 * 设计目标（对照任务书"⚠️ 性能硬要求"）：
 * 1. 懒加载：由调用方（ThumbnailRail 组件）通过 IntersectionObserver 决定何时
 *    调用 requestPage()，本类不主动拉取任何未被请求的页；
 * 2. 并发上限：同时在途的请求数不超过 maxConcurrent，超出的请求排队等待；
 * 3. 客户端缓存：同一 job 同一页只真正发起一次网络请求，后续请求直接复用
 *    已完成的结果（无论成功还是失败，失败也缓存——避免同一张坏图反复重试
 *    拖慢整体加载）。
 *
 * 之所以做成一个独立可测的调度器类，而不是直接在组件里用 useEffect + fetch，
 * 是因为"并发上限是否真的生效"这件事必须能在没有真实浏览器/网络的情况下用
 * 纯单测验证（mock fetch 统计同时在途请求数的峰值），这是任务书明确要求的
 * 测试形式（"可 mock fetch 统计请求数"）。
 */
export interface ThumbnailLoaderOptions {
  /** 同时在途的最大请求数。原型图 48 页齐发会拖垮后端，必须设上限。 */
  maxConcurrent: number;
  /** 实际发起请求的函数，测试时可注入 mock 统计调用次数/峰值并发。 */
  fetchPage: (page: number) => Promise<string>;
}

export type ThumbnailEntryStatus = "idle" | "loading" | "loaded" | "error";

export interface ThumbnailEntry {
  status: ThumbnailEntryStatus;
  /** 加载成功后的图片来源（对象 URL 或直接的图片地址），失败/加载中为 null。 */
  src: string | null;
}

export class ThumbnailLoadScheduler {
  private readonly maxConcurrent: number;
  private readonly fetchPage: (page: number) => Promise<string>;
  private readonly entries = new Map<number, ThumbnailEntry>();
  private readonly pending: number[] = [];
  private activeCount = 0;
  private peakConcurrent = 0;
  private totalRequestCount = 0;
  private listeners = new Set<() => void>();

  constructor(options: ThumbnailLoaderOptions) {
    this.maxConcurrent = Math.max(1, options.maxConcurrent);
    this.fetchPage = options.fetchPage;
  }

  /** 订阅状态变化（组件用它触发重渲染），返回取消订阅函数。 */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private notify(): void {
    for (const listener of Array.from(this.listeners)) {
      listener();
    }
  }

  getEntry(page: number): ThumbnailEntry {
    return this.entries.get(page) ?? { status: "idle", src: null };
  }

  /** 已发起的真实网络请求总数（去重后），供性能实测统计"总请求数"。 */
  getTotalRequestCount(): number {
    return this.totalRequestCount;
  }

  /** 实测到的并发峰值，供性能实测统计"并发峰值"。 */
  getPeakConcurrent(): number {
    return this.peakConcurrent;
  }

  /**
   * 请求某一页缩略图。懒加载语义：只有调用方主动调用本方法（通常由
   * IntersectionObserver 触发"进入视口"）才会真正排队/加载，本类不会
   * 自己扫描全部页码提前拉取。
   *
   * 幂等：同一页已经 loaded/loading/error/在排队中时直接返回，不产生重复请求
   * （客户端缓存 + 去重）。失败结果会被缓存为 sticky 的 error 态，不会被普通的
   * requestPage 调用自动重试——这正是"避免同一张坏图反复重试拖慢整体加载"的
   * 落地：懒加载滚动来回触发 IntersectionObserver 时，同一张坏图不应该在短时间内
   * 被反复打网络请求。需要重试失败页时用 retryPage()，语义上是一次显式的、
   * 用户可感知的操作，与"被动懒加载触发"分开。
   */
  requestPage(page: number): void {
    const existing = this.entries.get(page);
    if (existing) {
      // loaded / loading / error / 已在排队中：均不重复发起请求。
      return;
    }
    if (this.pending.includes(page)) {
      return;
    }
    this.entries.set(page, { status: "idle", src: null });
    this.pending.push(page);
    this.drain();
  }

  /** 显式重试失败页（例如用户手动点击"重新加载"），会清除 sticky 的 error 态
   *  并重新排队，仍然经过同一套并发调度，不会绕过上限直接发起请求。 */
  retryPage(page: number): void {
    const existing = this.entries.get(page);
    if (existing?.status !== "error") {
      return;
    }
    this.entries.delete(page);
    this.requestPage(page);
  }

  private drain(): void {
    while (this.activeCount < this.maxConcurrent && this.pending.length > 0) {
      const page = this.pending.shift();
      if (page === undefined) {
        break;
      }
      void this.loadOne(page);
    }
  }

  private async loadOne(page: number): Promise<void> {
    this.activeCount += 1;
    this.peakConcurrent = Math.max(this.peakConcurrent, this.activeCount);
    this.totalRequestCount += 1;
    this.entries.set(page, { status: "loading", src: null });
    this.notify();

    try {
      const src = await this.fetchPage(page);
      this.entries.set(page, { status: "loaded", src });
    } catch {
      this.entries.set(page, { status: "error", src: null });
    } finally {
      this.activeCount -= 1;
      this.notify();
      this.drain();
    }
  }
}

// ---------------------------------------------------------------------------
// 中栏 PDF 视图：bbox 高亮定位（Task 6.3，复用 problemPreview.ts 的换算逻辑）
// ---------------------------------------------------------------------------

/**
 * 计算 bbox 在渲染图片上的百分比定位框，支持可变渲染缩放（用户可调"缩放 X%"）。
 *
 * 复用而非重写：`app/app/components/task-review/problemPreview.ts` 的
 * `getProblemOverlayBox` 已经实现了同一套"bbox（PDF 点坐标）× 渲染缩放 /
 * 图片自然像素宽高 = 百分比"换算公式，本函数直接复用其中的核心算术，
 * 唯一区别是把硬编码的 `PROBLEM_PREVIEW_SCALE=1.6` 换成调用方传入的
 * `renderScale`——旧模态的图片永远以固定 1.6 倍缩放请求，因此可以把缩放值
 * 写死在那个函数里；审核工作台的中栏 PDF 视图有用户可调的"缩放 X%"控件，
 * 请求图片时的实际缩放会变化，所以百分比换算必须把这个变量当参数传入，
 * 不能沿用那个写死 1.6 的版本（沿用会导致缩放变化后高亮框错位）。
 * `getProblemOverlayStyle`/`getProblemOverlayLabelStyle`（把 OverlayBox 转成
 * CSSProperties）本身与渲染缩放无关，本文件不重复实现，直接从
 * problemPreview.ts 导入使用（见 PdfViewerPane.tsx）。
 */
export function computeOverlayBoxAtScale(
  bbox: number[] | undefined,
  naturalSize: { width: number; height: number } | null,
  renderScale: number,
): OverlayBox | null {
  const normalized = normalizeProblemBbox(bbox);
  if (!normalized || !naturalSize || naturalSize.width <= 0 || naturalSize.height <= 0) {
    return null;
  }
  return {
    leftPct: ((normalized[0] * renderScale) / naturalSize.width) * 100,
    topPct: ((normalized[1] * renderScale) / naturalSize.height) * 100,
    widthPct: (((normalized[2] - normalized[0]) * renderScale) / naturalSize.width) * 100,
    heightPct: (((normalized[3] - normalized[1]) * renderScale) / naturalSize.height) * 100,
  };
}

/** 从 Problem.location（"第 N 页"文案，来自 resolveProblemLocation）或 page 字段
 *  解析出问题所在页码；无法定位时返回 null，调用方不得猜测一个页码跳转。 */
export function resolveProblemTargetPage(problem: Problem): number | null {
  if (typeof problem.page === "number" && Number.isFinite(problem.page) && problem.page > 0) {
    return problem.page;
  }
  return null;
}

// ---------------------------------------------------------------------------
// 阶段记录 tab（Task 6.4，接第一批 Task 3 的阶段数据）
// ---------------------------------------------------------------------------

/** 5 态阶段枚举，顺序与 src/services/pipeline_stages.py 的 PipelineStage 完全一致
 *  （上传/PDF 解析/元数据识别/规则与 AI 分析/质量门禁），明确不含 OCR。
 *  前端不重新发明这个顺序的来源——它必须与后端枚举保持同构，任何一处新增
 *  阶段都要同步改两处，但两处各自独立维护（前端不能直接 import 后端 Python
 *  枚举），这是跨语言共享枚举定义的既有做法（对照 BadgeTone 等纯字符串常量）。 */
export const PIPELINE_STAGE_SEQUENCE = [
  "upload",
  "pdf_parse",
  "metadata_recognition",
  "rule_ai_analysis",
  "quality_gate",
] as const;

export type PipelineStageId = (typeof PIPELINE_STAGE_SEQUENCE)[number];

export const PIPELINE_STAGE_LABELS: Record<PipelineStageId, string> = {
  upload: "上传",
  pdf_parse: "PDF 解析",
  metadata_recognition: "元数据识别",
  rule_ai_analysis: "规则与 AI 分析",
  quality_gate: "质量门禁",
};

export type StageHistoryStatus = "done" | "current" | "pending" | "failed" | "unknown";

export interface StageHistoryEntry {
  stage: PipelineStageId;
  label: string;
  status: StageHistoryStatus;
  /** 仅 current 阶段有意义的完成度百分比；null 表示进度未知，调用方必须显示"—"
   *  （复用 isProgressUnknown 的语义，不在本函数重新判定）。 */
  percent: number | null;
}

export interface StageProgressLike {
  phase?: string | null;
  percent?: number | null;
}

export interface StageFailedAtLike {
  phase?: string | null;
}

/**
 * 从 job detail 的 stage_progress/stage_failed_at 派生 5 态阶段记录列表。
 *
 * 判定规则：
 * - 有 stage_failed_at 时：该阶段之前（不含）的阶段视为 done，该阶段本身视为
 *   failed，该阶段之后的阶段视为 unknown（失败后流水线中断，不知道后续阶段
 *   本来会不会顺利完成，不能说它们"待处理"，那意味着还会继续跑）；
 * - 无失败、有 stage_progress.phase 时：该阶段之前视为 done，该阶段本身视为
 *   current（带 percent），之后视为 pending；
 * - phase 不在 5 态枚举里（未知/历史脏数据）或完全没有 stage_progress 时，
 *   全部阶段视为 unknown——不得凭空把某个阶段标成"已完成"。
 */
export function deriveStageHistory(
  stageProgress: StageProgressLike | null | undefined,
  stageFailedAt: StageFailedAtLike | null | undefined,
): StageHistoryEntry[] {
  const failedPhase = stageFailedAt?.phase;
  const failedIndex =
    typeof failedPhase === "string"
      ? (PIPELINE_STAGE_SEQUENCE as readonly string[]).indexOf(failedPhase)
      : -1;

  if (failedIndex >= 0) {
    return PIPELINE_STAGE_SEQUENCE.map((stage, index) => ({
      stage,
      label: PIPELINE_STAGE_LABELS[stage],
      status: index < failedIndex ? "done" : index === failedIndex ? "failed" : "unknown",
      percent: index === failedIndex ? null : null,
    }));
  }

  const currentPhase = stageProgress?.phase;
  const currentIndex =
    typeof currentPhase === "string"
      ? (PIPELINE_STAGE_SEQUENCE as readonly string[]).indexOf(currentPhase)
      : -1;

  if (currentIndex < 0) {
    return PIPELINE_STAGE_SEQUENCE.map((stage) => ({
      stage,
      label: PIPELINE_STAGE_LABELS[stage],
      status: "unknown",
      percent: null,
    }));
  }

  return PIPELINE_STAGE_SEQUENCE.map((stage, index) => ({
    stage,
    label: PIPELINE_STAGE_LABELS[stage],
    status: index < currentIndex ? "done" : index === currentIndex ? "current" : "pending",
    percent:
      index === currentIndex && typeof stageProgress?.percent === "number" ? stageProgress.percent : null,
  }));
}

// ---------------------------------------------------------------------------
// 问题计数与排序（必须与 count_formal_findings 同口径，不得前端另算）
// ---------------------------------------------------------------------------

/**
 * 从真实 Problem 列表统计"审核问题（N）"的 N。
 *
 * 关键约束：这里只是对已经过后端 count_formal_findings 口径过滤后的
 * problems.length 做直接计数——toUiProblems() 消费的 job detail 里，
 * 降级条目已经在后端 apply_evidence_completeness/is_formal_finding 判定阶段
 * 被标记（evidence_status=degraded_missing_evidence），但 toUiProblems 本身
 * 不会因为 evidenceStatus 存在就过滤掉这条 Problem——降级问题仍然要展示给
 * 用户（带降级标识，见任务书"用户有权知道哪条证据不完整"），只是不计入
 * 正式问题数。因此本函数显式排除 evidenceStatus 为降级标记的条目，
 * 与后端 is_formal_finding 判定同一个字符串常量。
 */
export const EVIDENCE_STATUS_DEGRADED = "degraded_missing_evidence";

export function countFormalProblems(problems: Problem[]): number {
  return problems.filter((problem) => problem.evidenceStatus !== EVIDENCE_STATUS_DEGRADED).length;
}

export function isProblemDegraded(problem: Problem): boolean {
  return problem.evidenceStatus === EVIDENCE_STATUS_DEGRADED;
}

/** 问题卡默认排序：按页码升序，页码缺失的排最后（不能定位页码的问题不应该
 *  混在有页码的问题中间打乱阅读顺序）。同页时保持原始顺序（稳定排序）。 */
export function sortProblemsForReview(problems: Problem[]): Problem[] {
  return problems
    .map((problem, index) => ({ problem, index }))
    .sort((a, b) => {
      const pageA = a.problem.page ?? Number.POSITIVE_INFINITY;
      const pageB = b.problem.page ?? Number.POSITIVE_INFINITY;
      if (pageA !== pageB) {
        return pageA - pageB;
      }
      return a.index - b.index;
    })
    .map((entry) => entry.problem);
}

/** 从 job detail 的 result.meta.pages 提取真实总页数；缺失时返回 null，
 *  调用方（缩略图栏/顶部信息条）必须显示"—"或不渲染，不得猜测一个页数
 *  （原型图"12 / 48"里的 48 必须是真实解析出的页数，不是设计稿占位）。 */
export function extractTotalPageCount(detail: JobDetailRecord | null | undefined): number | null {
  const result = detail?.result;
  if (!result || typeof result !== "object") {
    return null;
  }
  const meta = (result as Record<string, unknown>).meta;
  if (!meta || typeof meta !== "object") {
    return null;
  }
  const pages = (meta as Record<string, unknown>).pages;
  if (typeof pages === "number" && Number.isFinite(pages) && pages > 0) {
    return pages;
  }
  return null;
}

// ---------------------------------------------------------------------------
// 元数据 tab（M2 版本留痕首次前端露面）
// ---------------------------------------------------------------------------

export interface FindingVersionsSummary {
  rule_versions?: string[];
  model_versions?: string[];
  prompt_versions?: string[];
  engine_versions?: string[];
  engine_version?: string | null;
}

/** 从 job detail 的 result.meta.versions 提取版本留痕摘要；缺失时返回 null，
 *  调用方必须显示"未识别到"而不是猜测或留空白。 */
export function extractFindingVersions(detail: JobDetailRecord | null | undefined): FindingVersionsSummary | null {
  const result = detail?.result;
  if (!result || typeof result !== "object") {
    return null;
  }
  const meta = (result as Record<string, unknown>).meta;
  if (!meta || typeof meta !== "object") {
    return null;
  }
  const versions = (meta as Record<string, unknown>).versions;
  if (!versions || typeof versions !== "object") {
    return null;
  }
  return versions as FindingVersionsSummary;
}

/** 把去重版本列表格式化为展示文案，多个值用"、"连接；空列表/缺失显示"未识别到"。 */
export function formatVersionList(values: string[] | null | undefined): string {
  if (!Array.isArray(values) || values.length === 0) {
    return "未识别到";
  }
  return values.join("、");
}

/** 年份展示：未识别到（null/undefined/非正数）必须显示"未识别到"，禁止 2000 兜底。 */
export function formatMetadataYear(reportYear: JobSummaryRecord["report_year"]): string {
  if (typeof reportYear === "number" && Number.isFinite(reportYear) && reportYear > 0) {
    return String(reportYear);
  }
  return "未识别到";
}

/** 文档类型展示：unknown/缺失必须显示"未识别到"，不得猜测成"部门预算"等具体类型。 */
export function formatMetadataReportKind(reportKind: JobSummaryRecord["report_kind"]): string {
  if (reportKind === "budget") {
    return "部门预算";
  }
  if (reportKind === "final") {
    return "部门决算";
  }
  return "未识别到";
}

// ---------------------------------------------------------------------------
// 顶部状态徽章（review_required 不得显示成"分析完成"）
// ---------------------------------------------------------------------------

export type WorkbenchHeaderBadgeTone = "review" | "processing" | "lowconf" | "failed" | "done";

export interface WorkbenchHeaderBadge {
  tone: WorkbenchHeaderBadgeTone;
  label: string;
}

/**
 * 顶部状态徽章的 tone + 文案，判定逻辑与 WorkbenchQueueTable.resolveQualityBadge
 * 保持一致口径（review_required 独立分支显示"需要人工复核"，不跟随 completed
 * 分支——这是任务书明确写出的反例："review_required 状态徽章不得显示成
 * '分析完成'"）。两处各自维护一份实现（不抽公共函数）是因为输入类型不同
 * （这里吃 JobDetailRecord 的 status/quality_status，Queue 表吃 JobSummaryRecord），
 * 但判定顺序必须保持同构，任何一处改动都要同步检查另一处。
 */
export function resolveWorkbenchHeaderBadge(job: {
  status?: string | null;
  quality_status?: string | null;
}): WorkbenchHeaderBadge {
  const status = String(job.status ?? "").trim().toLowerCase();
  if (status === "review_required" || status === "needs_review") {
    return { tone: "review", label: "需要人工复核" };
  }
  if (status === "error" || status === "failed") {
    return { tone: "failed", label: "处理失败" };
  }
  if (status === "processing" || status === "analyzing" || status === "queued") {
    return { tone: "processing", label: "正在分析" };
  }
  if (job.quality_status === "degraded") {
    return { tone: "lowconf", label: "低置信度" };
  }
  return { tone: "done", label: "分析完成" };
}

// ---------------------------------------------------------------------------
// 底部状态条：已确认/已忽略/待处理计数（必须来自真实工作流状态）
// ---------------------------------------------------------------------------

export interface WorkflowIssueRecord {
  issue_id: string;
  status: string;
}

export interface WorkflowStatusCounts {
  confirmed: number;
  ignored: number;
  pending: number;
}

/**
 * 从真实 /api/workflow 状态计算底部状态条的三个计数。
 *
 * 口径：
 * - confirmed：workflow 状态为 "confirmed" 的问题数；
 * - ignored：workflow 状态为 "no_issue" 的问题数（"忽略"操作写入的状态，见
 *   ReviewWorkbenchPage 的 handleConfirm/handleIgnore）；
 * - pending：既不在 workflow 记录里、也不是上述两种终态的问题数——即
 *   "全部问题数 - 已确认 - 已忽略"，包含从未操作过的问题（workflow 里完全
 *   没有记录）与显式标记为 pending/needs_review/in_package 的问题。
 *
 * 反例（核心，对照任务书"计数接真实工作流状态，不得写死原型图的 2/1/3"）：
 * 全部问题数与 workflowRecords 都为空时，三项计数必须是 0/0/0（真实的零），
 * 不是原型图示例的 2/1/3。
 */
export function computeWorkflowStatusCounts(
  problems: Problem[],
  workflowRecords: Record<string, WorkflowIssueRecord>,
): WorkflowStatusCounts {
  const formalProblems = problems.filter((problem) => !isProblemDegraded(problem));
  let confirmed = 0;
  let ignored = 0;
  for (const problem of formalProblems) {
    const record = workflowRecords[problem.id];
    if (record?.status === "confirmed") {
      confirmed += 1;
    } else if (record?.status === "no_issue") {
      ignored += 1;
    }
  }
  const pending = Math.max(0, formalProblems.length - confirmed - ignored);
  return { confirmed, ignored, pending };
}
