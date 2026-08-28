/**
 * 规则与版本页（Task 8.3）的纯逻辑层：版本信息格式化与条目展示派生。
 *
 * 红线（任务书明确要求）：
 * - 禁止硬编码任何版本号（原型图里的 "3.8.1" 是设计稿占位），
 *   本文件不存在任何版本常量——所有展示值来自 /api/rules/version 的
 *   真实响应，读不到显示"未识别到"；
 * - "未识别到"与真实值严格区分，不得用空字符串、0 或占位符冒充。
 */

export interface RulesVersionResponse {
  available?: boolean;
  unavailable_reason?: string | null;
  rules_file?: string | null;
  ruleset_version?: string | null;
  metadata_version?: string | null;
  rule_entry_count?: number | null;
  engine_version?: string | null;
}

export interface RulesEntry {
  rule_id: string;
  title: string;
  severity: string;
  doc_scope: string[];
}

export interface RulesEntriesResponse {
  available?: boolean;
  items?: RulesEntry[];
  total?: number | null;
  limit?: number;
  offset?: number;
}

/** 版本/路径等展示值：null/undefined/空串 → "未识别到"，否则原样返回。 */
export function formatRulesValueText(value: string | null | undefined): string {
  const text = typeof value === "string" ? value.trim() : "";
  return text || "未识别到";
}

/** 条目数展示：null（读不到）→ "未识别到"，数字 → 原样字符串。 */
export function formatRuleCountText(value: number | null | undefined): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return "未识别到";
}

/** 规则条目 severity 徽章的 tone 映射（high=危险/medium=警告/low=中性）。 */
export function resolveSeverityTone(severity: string): "danger" | "warning" | "neutral" {
  const normalized = severity.trim().toLowerCase();
  if (normalized === "high" || normalized === "critical") {
    return "danger";
  }
  if (normalized === "medium") {
    return "warning";
  }
  return "neutral";
}

/** doc_scope 列表展示文案；空列表显示"—"（不是"未识别到"——规则没有
 *  适用范围声明与"识别失败"是两回事，这里用中性占位即可）。 */
export function formatDocScopeText(docScope: string[]): string {
  if (!Array.isArray(docScope) || docScope.length === 0) {
    return "—";
  }
  return docScope.join("、");
}
