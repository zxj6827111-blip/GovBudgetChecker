/**
 * 材料详情（WP2-B）的中文呈现契约。
 *
 * 为什么中文只在展示层
 * --------------------
 * 后端返回的都是**技术码**：obligation 状态、未完成原因、运行状态、严重度、
 * 来源类型、证据状态。中文映射集中在本文件一处，理由与 WP2-A 的状态映射相同：
 * 两端各写一份必然漂移，而"同一个状态在两个页面叫两个名字"会让用户
 * 再也无法判断哪个页面是对的。
 *
 * 三个刻意的设计
 * --------------
 * 1. **未登记的状态码不吞掉**：原样显示 `待确认：xxx`，让"后端多了一个新码"
 *    在界面上立刻可见，而不是显示成空白。
 * 2. **"算不出来"与"算了是零"分开**：`null` 一律显示 `—`，
 *    `0` 显示 `0`。两者含义完全不同（见 `formatCount`）。
 * 3. **不做业务结论**：本文件只把码翻成词，不判断"这份材料有没有问题"。
 */

import type { BadgeTone } from "@/components/ui/badgeStyles";

// ---- 检查义务状态（obligation ledger 的 status 取值） ------------------------

export type CoverageStatus =
  | "completed"
  | "not_applicable"
  | "not_implemented"
  | "not_executed"
  | "insufficient_data"
  | "parse_ambiguity"
  | "execution_error"
  | "profile_unresolved"
  | "kind_conflict"
  | "ai_not_run"
  | "ai_failed";

export interface CoverageStatusPresentation {
  /** 业务用户看到的主文案。 */
  label: string;
  /**
   * 说明"为什么没做完、下一步该做什么"。
   * `not_implemented` 与 `not_executed` 的差别就在这里：前者是本系统还没有
   * 这项自动检查，后者是本次没跑到 —— 对用户是两件事（一个只能人工核验，
   * 一个可以重跑）。
   */
  detail: string;
  /** 是否属于"阻塞：不得把材料标成检查完成"的类别。 */
  blocking: boolean;
  tone: BadgeTone;
}

const COVERAGE_STATUS_PRESENTATION: Record<CoverageStatus, CoverageStatusPresentation> = {
  completed: {
    label: "自动检查完成",
    detail: "该项检查已由系统执行并得出结果。",
    blocking: false,
    tone: "done",
  },
  not_applicable: {
    label: "不适用",
    detail: "本次材料不含这项检查对应的内容，不计入完成率分母。",
    blocking: false,
    tone: "neutral",
  },
  not_implemented: {
    label: "自动检查暂不可用，需要人工核验",
    detail: "系统尚未实现这项自动检查，只能由人工核验。",
    blocking: true,
    tone: "review",
  },
  not_executed: {
    label: "检查未执行",
    detail: "这项检查本次没有跑到，结论里不包含它。",
    blocking: true,
    tone: "failed",
  },
  insufficient_data: {
    label: "取数不足，需要人工核验",
    detail: "结构化数据里缺少这项检查需要的字段或表，无法自动判定。",
    blocking: true,
    tone: "review",
  },
  parse_ambiguity: {
    label: "解析存在歧义",
    detail: "文档解析结果不唯一（例如同一科目出现多处），需要人工确认口径。",
    blocking: true,
    tone: "review",
  },
  execution_error: {
    label: "检查执行异常",
    detail: "这项检查在运行时出错，没有得出任何结论。",
    blocking: true,
    tone: "failed",
  },
  profile_unresolved: {
    label: "材料画像待确认",
    detail: "这份材料是什么类型/口径还没确认，因此无法决定该跑哪些检查。",
    blocking: true,
    tone: "lowconf",
  },
  kind_conflict: {
    label: "文种冲突待确认",
    detail: "预算/决算的判定互相矛盾，未确认前不能按任一文种给出检查结论。",
    blocking: true,
    tone: "lowconf",
  },
  ai_not_run: {
    label: "语义检查未执行",
    detail: "这项检查依赖语义（AI）复核，本次未执行。",
    blocking: true,
    tone: "processing",
  },
  ai_failed: {
    label: "语义检查失败",
    detail: "语义复核调用失败，未得出候选结论。",
    blocking: true,
    tone: "failed",
  },
};

export const COVERAGE_STATUSES: CoverageStatus[] = [
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

export function isCoverageStatus(value: unknown): value is CoverageStatus {
  return typeof value === "string" && (COVERAGE_STATUSES as string[]).includes(value);
}

export function presentCoverageStatus(status: unknown): CoverageStatusPresentation {
  if (isCoverageStatus(status)) {
    return COVERAGE_STATUS_PRESENTATION[status];
  }
  const raw = typeof status === "string" ? status.trim() : "";
  return {
    label: raw ? `待确认：${raw}` : "状态未识别",
    detail: "后端返回了本前端尚未登记的检查义务状态码，请连同状态码一起排查。",
    blocking: true,
    tone: "neutral",
  };
}

/**
 * 未完成原因码的中文。
 *
 * 与 `materialStatusPresentation.REASON_LABELS` 刻意分开：那边的码来自
 * **槽位**状态机（材料到没到、身份定没定），这边的码来自**检查义务**账本
 * （这项检查为什么没做完）。混成一张表会出现"同一串码在两种语境下同义"的错觉。
 */
const COVERAGE_REASON_LABELS: Record<string, string> = {
  not_implemented: "自动检查暂不可用",
  not_executed: "检查未执行",
  insufficient_data: "取数不足",
  parse_error: "解析存在歧义",
  parse_ambiguity: "解析存在歧义",
  execution_error: "检查执行异常",
  profile_unresolved: "材料画像待确认",
  kind_conflict: "文种冲突待确认",
  ai_not_run: "语义检查未执行",
  ai_failed: "语义检查失败",
};

export function presentCoverageReason(reason: unknown): string | null {
  if (reason === null || reason === undefined || reason === "") {
    return null;
  }
  const code = String(reason).trim();
  if (!code) {
    return null;
  }
  return COVERAGE_REASON_LABELS[code] ?? `待确认：${code}`;
}

// ---- 严重度 -----------------------------------------------------------------

export interface SeverityPresentation {
  label: string;
  tone: BadgeTone;
}

/** 严重度归并桶 → 中文。与后端 `severity_bucket` 的三个桶一一对应。 */
const SEVERITY_BUCKETS: Record<string, SeverityPresentation> = {
  error: { label: "高", tone: "failed" },
  warn: { label: "中", tone: "review" },
  info: { label: "提示", tone: "neutral" },
};

export function presentSeverityBucket(bucket: unknown): SeverityPresentation {
  const key = String(bucket ?? "").trim();
  if (key in SEVERITY_BUCKETS) {
    return SEVERITY_BUCKETS[key];
  }
  return { label: "待确认", tone: "neutral" };
}

/** 原始 severity 码的中文（未登记时回退到归并桶的文案）。 */
const SEVERITY_CODES: Record<string, string> = {
  error: "高",
  err: "高",
  fatal: "高",
  critical: "高",
  high: "高",
  warn: "中",
  warning: "中",
  medium: "中",
  low: "中",
  manual_review: "待人工核验",
  info: "提示",
};

export function presentSeverityCode(severity: unknown): string {
  const code = String(severity ?? "").trim().toLowerCase();
  if (!code) {
    return "未标注";
  }
  return SEVERITY_CODES[code] ?? `待确认：${code}`;
}

// ---- 证据链状态 -------------------------------------------------------------

/** 与 `src/services/evidence_guard.py` 的三个常量逐字一致。 */
export const EVIDENCE_STATUS_DEGRADED = "degraded_missing_evidence";

export function presentEvidenceStatus(status: unknown): string | null {
  const code = String(status ?? "").trim();
  if (!code) {
    return null;
  }
  return (
    {
      complete: "证据完整",
      incomplete_rule_warning: "规则命中但证据不完整（已标注，不影响正式结论）",
      [EVIDENCE_STATUS_DEGRADED]: "缺证据，已降级为需人工核验",
    } as Record<string, string>
  )[code] ?? `待确认：${code}`;
}

// ---- 分析运行 ---------------------------------------------------------------

export interface RunStatusPresentation {
  label: string;
  tone: BadgeTone;
}

/**
 * 运行状态的中文。
 *
 * 取值域取自仓库既有的任务状态（`analysis_jobs.status`）。
 * ``done`` 之外一律不显示"完成"：``degraded`` / ``review_required`` 是
 * "跑完了但有保留"，与"跑成功"不是一回事。
 */
const RUN_STATUSES: Record<string, RunStatusPresentation> = {
  pending: { label: "排队中", tone: "neutral" },
  queued: { label: "排队中", tone: "neutral" },
  processing: { label: "分析中", tone: "processing" },
  running: { label: "分析中", tone: "processing" },
  done: { label: "已完成", tone: "done" },
  completed: { label: "已完成", tone: "done" },
  degraded: { label: "完成（部分能力降级）", tone: "review" },
  review_required: { label: "完成（需人工复核）", tone: "review" },
  error: { label: "处理失败", tone: "failed" },
  failed: { label: "处理失败", tone: "failed" },
  cancelled: { label: "已取消", tone: "neutral" },
};

export function presentRunStatus(status: unknown): RunStatusPresentation {
  const code = String(status ?? "").trim().toLowerCase();
  if (code in RUN_STATUSES) {
    return RUN_STATUSES[code];
  }
  return {
    label: code ? `待确认：${code}` : "状态未识别",
    tone: "neutral",
  };
}

/** 分析结论（quality gate 的 analysis_conclusion）。 */
export function presentAnalysisConclusion(conclusion: unknown): string | null {
  const code = String(conclusion ?? "").trim();
  if (!code) {
    return null;
  }
  return (
    {
      findings_detected: "发现问题",
      no_findings: "未发现问题",
      incomplete: "结论不完整（有检查未完成）",
    } as Record<string, string>
  )[code] ?? `待确认：${code}`;
}

// ---- 文件版本 ---------------------------------------------------------------

export function presentVersionBadge(isCurrent: boolean): { label: string; tone: BadgeTone } {
  return isCurrent
    ? { label: "当前版本", tone: "done" }
    : { label: "历史版本", tone: "neutral" };
}

/** 文件名的 hash 摘要：只显示前后各 4 位，避免一长串塞满表格。 */
export function formatFileHash(hash: string | null | undefined): string {
  const text = String(hash ?? "").trim();
  if (!text) {
    return "—";
  }
  if (text.length <= 12) {
    return text;
  }
  return `${text.slice(0, 4)}…${text.slice(-4)}`;
}

/** 文件大小：无法计算显示 `—`，不显示 0 B。 */
export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) {
    return "—";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${Math.round((bytes / 1024) * 10) / 10} KB`;
  }
  return `${Math.round((bytes / (1024 * 1024)) * 10) / 10} MB`;
}

// ---- 材料来源 ---------------------------------------------------------------

/**
 * 来源类型的中文。
 *
 * ``manual_upload`` 显示为「人工上传」而**不是**「来源缺失」（§二十六）：
 * 人工上传本来就没有 URL，"没有 URL"与"没有来源"是两件事。
 */
export function presentSourceKind(kind: unknown): string {
  const code = String(kind ?? "").trim();
  if (!code) {
    return "来源类型待确认";
  }
  return (
    {
      official_site: "官网公开来源",
      manual_upload: "人工上传",
      excel_import: "表格导入",
    } as Record<string, string>
  )[code] ?? `待确认：${code}`;
}

export function presentSourceStatus(status: unknown): string {
  const code = String(status ?? "").trim();
  if (!code) {
    return "状态未识别";
  }
  return (
    {
      active: "有效",
      unreachable: "暂时无法访问",
      superseded: "已被新来源取代",
      retired: "已退役",
    } as Record<string, string>
  )[code] ?? `待确认：${code}`;
}

// ---- 数值呈现 ---------------------------------------------------------------

/**
 * 计数呈现：`null` → `—`（当前无法计算），数字照原样显示（含 0）。
 *
 * 这是本模块最重要的一条纪律。`formal_issue_count` 为 `null` 表示
 * "还没算出可确认的结论"，为 `0` 表示"已经确认没有正式问题"。
 * 把 `null` 显示成 `0` 会让用户以为这份材料查过且没问题——正是本项目
 * 历史上最危险的假完成。
 */
export const UNKNOWN_COUNT_TEXT = "—";

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return UNKNOWN_COUNT_TEXT;
  }
  return String(value);
}

/** 比例呈现：`null` → `—`；0 分母由后端给 null，前端不再二次计算。 */
export function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return UNKNOWN_COUNT_TEXT;
  }
  return `${Math.round(value * 1000) / 10}%`;
}
