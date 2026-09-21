/**
 * 材料状态与原因的唯一呈现契约。
 *
 * 为什么必须只有一份
 * ------------------
 * 三个页面（首页 / 区级矩阵 / 部门矩阵）都要显示同一批状态。如果各自硬编码
 * 颜色和文案，就会出现"首页把 mapping_required 算作待复核、部门页算作待确认"
 * 这种同一份数据两种说法的情况——用户再也无法判断哪个页面是对的。
 * 因此状态码 → { 文案, 色调, 图标, 说明 } 的映射只在本文件里存在。
 *
 * 三个刻意的设计
 * --------------
 * 1. **状态码与文案分离**：后端只给出机器可读的 `status` / `status_reason`，
 *    中文在这里翻译。后端翻译会让同一原因在两端各写一份，必然漂移。
 * 2. **未知状态码不吞掉**：未登记的状态/原因原样显示（`待确认：xxx`），
 *    绝不退化成空字符串或"未知"这种查不出问题的显示——WP1 出现新增状态时，
 *    界面上要能立刻看见那个新码，而不是显示成一片空白。
 * 3. **null 与 0 语义分离**：完整率这类字段为 null 时显示 `—`（无法计算），
 *    与 0（已计算且为零）严格区分。
 */

import type { BadgeTone } from "@/components/ui/badgeStyles";

/** 与后端 `material_slots.status` 的 CHECK 约束逐字一致（WP1 唯一权威）。 */
export type MaterialStatus =
  | "not_due"
  | "missing"
  | "uploaded"
  | "processing"
  | "review_required"
  | "reviewing"
  | "completed"
  | "not_applicable"
  | "mapping_required"
  | "failed";

export const MATERIAL_STATUSES: MaterialStatus[] = [
  "not_due",
  "missing",
  "uploaded",
  "processing",
  "review_required",
  "reviewing",
  "completed",
  "not_applicable",
  "mapping_required",
  "failed",
];

/** 图标名（字符串），由组件层映射到具体图标组件：纯逻辑模块不引入 UI 依赖。 */
export type MaterialStatusIcon =
  | "clock"
  | "alert"
  | "check"
  | "loader"
  | "user-check"
  | "help"
  | "slash"
  | "x";

export interface MaterialStatusPresentation {
  label: string;
  /**
   * 色调表达的是**关注等级**，不是语义本身：
   * 红=需要立刻处理、橙=需要人工介入、蓝=处理中、绿=已闭环、灰=无需动作。
   * "逾期未上传"与"处理失败"同为最高关注等级，靠文案区分，不能靠颜色区分。
   */
  tone: BadgeTone;
  icon: MaterialStatusIcon;
  description: string;
}

const STATUS_PRESENTATION: Record<MaterialStatus, MaterialStatusPresentation> = {
  not_due: {
    label: "未到期",
    tone: "neutral",
    icon: "clock",
    description: "尚未到应收/公开截止时间，不计入缺失。",
  },
  missing: {
    label: "逾期未上传",
    tone: "failed",
    icon: "alert",
    description: "已过截止时间、确认应当有这份材料，但当前没有文件。",
  },
  uploaded: {
    label: "已上传",
    tone: "processing",
    icon: "check",
    description: "已绑定文件版本，分析/复核链路尚未把它推进到更高状态。",
  },
  processing: {
    label: "处理中",
    tone: "processing",
    icon: "loader",
    description: "分析进行中，尚未产生可复核的结论。",
  },
  review_required: {
    label: "待人工复核",
    tone: "review",
    icon: "user-check",
    description: "自动分析已完成，但仍有人工必须处理的问题或冲突。",
  },
  reviewing: {
    label: "复核中",
    tone: "review",
    icon: "user-check",
    description: "人工复核进行中，结论尚未落定。",
  },
  completed: {
    label: "已完成",
    tone: "done",
    icon: "check",
    description: "分析完成且人工复核结论已持久化。",
  },
  not_applicable: {
    label: "不适用",
    tone: "neutral",
    icon: "slash",
    description: "已人工确认本年度无此材料，不计入缺失。",
  },
  mapping_required: {
    label: "待确认归属",
    tone: "lowconf",
    icon: "help",
    description: "主体/年度/文种尚未确认或存在冲突，需人工确认后才能计入统计。",
  },
  failed: {
    label: "处理失败",
    tone: "failed",
    icon: "x",
    description: "材料存在但处理链路失败，需重试或人工处理（与「逾期未上传」不是一回事）。",
  },
};

/**
 * 状态成因的中文说明。
 *
 * 取值域来自 WP1 的两处：
 * - 状态机（`src/services/material_slot_status.py`）写入 `status_reason`；
 * - 归属判定（`src/services/material_slot_resolver.py`）与绑定（`material_slot_service`）
 *   的原因码 —— 这些码当前**没有落库**，但它们已在协议层登记，WP2-B/WP9
 *   把它们接进响应后，界面不需要再改一处文案。
 */
const REASON_LABELS: Record<string, string> = {
  // 状态机
  due_not_reached: "尚未到公开截止时间",
  due_at_unknown: "截止时间未知",
  due_exceeded: "已过截止时间且未上传",
  applicability_unresolved: "是否应当有这份材料尚未确认",
  applicability_marked_not_applicable: "已人工确认本年度无此材料",
  identity_unresolved: "年度/文种/主体映射待确认",
  awaiting_analysis: "已上传，等待分析",
  analysis_running: "分析进行中",
  analysis_failed: "分析失败，需重试或人工处理",
  findings_pending: "存在待人工处理的问题",
  review_in_progress: "人工复核进行中",
  review_completed: "人工复核已完成",
  caliber_conflict: "材料口径存在冲突",
  // 归属判定（WP1 resolver）
  organization_missing: "缺少主体信息",
  organization_unresolved: "主体无法确认",
  organization_level_unknown: "主体层级无法确认",
  kind_conflict: "预算/决算文种存在冲突",
  year_conflict: "财政年度存在冲突",
  kind_unknown: "文种无法识别",
  year_unknown: "财政年度无法识别",
  // 绑定
  slot_binding_conflict: "文件版本已绑定其他材料",
  slot_version_missing: "文件版本不存在",
};

/** 未知原因码的显示前缀：保留原始 code，便于直接拿去排查。 */
export const UNKNOWN_REASON_PREFIX = "待确认：";

export function isMaterialStatus(value: unknown): value is MaterialStatus {
  return typeof value === "string" && (MATERIAL_STATUSES as string[]).includes(value);
}

/**
 * 状态 → 呈现。未登记的状态码不丢弃：返回中性徽章 + 原始码，
 * 让"数据库里多了一个新状态"这件事在界面上直接可见。
 */
export function presentMaterialStatus(status: unknown): MaterialStatusPresentation {
  if (isMaterialStatus(status)) {
    return STATUS_PRESENTATION[status];
  }
  const raw = typeof status === "string" ? status.trim() : "";
  return {
    label: raw ? `${UNKNOWN_REASON_PREFIX}${raw}` : "状态未识别",
    tone: "neutral",
    icon: "help",
    description: "后端返回了本前端尚未登记的状态码，请连同状态码一起排查。",
  };
}

export function presentStatusReason(reason: unknown): string | null {
  if (reason === null || reason === undefined || reason === "") {
    return null;
  }
  const code = String(reason).trim();
  if (!code) {
    return null;
  }
  return REASON_LABELS[code] ?? `${UNKNOWN_REASON_PREFIX}${code}`;
}

// ---- 其它维度 ---------------------------------------------------------------

export function presentSubjectKind(kind: unknown): string {
  return (
    {
      department: "部门",
      unit: "单位",
      government: "政府",
      unknown: "待确认",
    } as Record<string, string>
  )[String(kind ?? "")] ?? "待确认";
}

export function presentMaterialScope(scope: unknown): string {
  return (
    {
      department_summary: "部门汇总",
      unit_self: "单位本级",
      government: "政府",
      unknown: "待确认",
    } as Record<string, string>
  )[String(scope ?? "")] ?? "待确认";
}

export function presentReportKind(kind: unknown): string {
  return (
    {
      budget: "预算",
      final: "决算",
      unknown: "待确认",
    } as Record<string, string>
  )[String(kind ?? "")] ?? "待确认";
}

export function presentCaliber(caliber: unknown): string {
  return (
    {
      summary: "汇总口径",
      self: "本级口径",
      unknown: "口径待确认",
    } as Record<string, string>
  )[String(caliber ?? "")] ?? "口径待确认";
}

export function presentRelationship(relationship: unknown): string {
  return (
    {
      department_summary: "部门汇总",
      head_unit: "本部单位",
      subordinate_unit: "直属单位",
      relationship_unknown: "关系待确认",
    } as Record<string, string>
  )[String(relationship ?? "")] ?? "关系待确认";
}

export function presentApplicability(status: unknown): string {
  return (
    {
      applicable: "应纳入",
      not_applicable: "不适用",
      unresolved: "适用性待确认",
    } as Record<string, string>
  )[String(status ?? "")] ?? "适用性待确认";
}

// ---- 口径提示 ---------------------------------------------------------------

/**
 * 顶部数据口径提示。切换条件只有一个：后端 meta 的 `expected_materials_ready`。
 *
 * 前端不判断"应该有几个槽位"，因此也不能自行决定要不要提"完整率不可用"——
 * 只负责按后端给出的口径状态选文案。
 */
export function resolveDataBasisNotice(expectedMaterialsReady: boolean): {
  notice: string;
  emptyStateNotice: string;
} {
  if (expectedMaterialsReady) {
    return {
      notice: "数据口径：已建立应收材料基线，完整率与缺失数按基线计算。",
      emptyStateNotice: "当前筛选条件下没有需要处理的材料。",
    };
  }
  return {
    notice:
      "数据口径：当前仅统计已建立的材料槽位。应收材料基线尚未导入，完整率和真实缺失数可能暂不可计算。",
    emptyStateNotice: "这不代表该部门不存在应收材料。",
  };
}

/** 完整率等"无法计算"字段的显示值：null → `—`，绝不用 0 冒充。 */
export const UNKNOWN_METRIC_TEXT = "—";

export function formatCoverageRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) {
    return UNKNOWN_METRIC_TEXT;
  }
  return `${Math.round(rate * 1000) / 10}%`;
}

/** 时间统一由前端格式化（后端只给 ISO 8601 UTC）。解析失败显示 `—`，不猜时间。 */
export function formatMaterialTimestamp(value: string | null | undefined): string {
  if (!value) {
    return UNKNOWN_METRIC_TEXT;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return UNKNOWN_METRIC_TEXT;
  }
  const pad = (input: number) => String(input).padStart(2, "0");
  return (
    `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ` +
    `${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
  );
}
