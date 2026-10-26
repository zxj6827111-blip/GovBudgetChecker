/**
 * AI 检查范围展示逻辑（纯函数）。
 *
 * 只回答四个问题：
 * 1. 本次有没有启用大模型文字复核；
 * 2. 有效完成几个检查单元、失败几个、是否发生过补试；
 * 3. 哪些专项/页码没有完成，原因是什么；
 * 4. 本次结论只适用于哪些已完成检查。
 *
 * 历史结果缺少 ai_coverage 时显示“未记录/不可用”，绝不默认“全部完成”。
 */

export type AiScopeTone = "success" | "warning" | "danger" | "muted";

export interface AiScopeFailedUnit {
  specialty: string;
  specialtyLabel: string;
  status: string;
  statusLabel: string;
  pages: number[];
  pagesLabel: string;
  errorType: string | null;
  httpStatus: number | null;
  error: string | null;
  retryable: boolean | null;
  attempts: number | null;
}

export interface AiScopeView {
  recorded: boolean;
  missingCoverage: boolean;
  requested: boolean | null;
  state: string;
  stateLabel: string;
  tone: AiScopeTone;
  headline: string;
  summary: string;
  scopeLine: string;
  conclusionScope: string;
  conclusionScopeLabel: string;
  coverageStatus: string | null;
  coverageStatusLabel: string;
  planned: number | null;
  succeeded: number | null;
  failed: number | null;
  retried: number | null;
  failedUnits: AiScopeFailedUnit[];
  uncoveredPages: number[];
  note: string;
  needsRecheck: boolean;
}

const SPECIALTY_LABELS: Record<string, string> = {
  text_semantics: "文字语义",
  narrative_consistency: "说明一致性",
};

const STATE_LABELS: Record<string, string> = {
  succeeded: "已完成",
  degraded: "未完整完成",
  failed: "未成功",
  not_requested: "未启用",
  not_run: "未执行",
  running: "检查中",
  processing: "检查中",
  disabled: "未启用",
  empty_input: "无可检查文本",
};

const COVERAGE_STATUS_LABELS: Record<string, string> = {
  complete: "覆盖完整",
  partial: "部分覆盖",
  failed: "执行失败",
  not_run: "未执行",
  unknown: "覆盖未知",
};

const UNIT_STATUS_LABELS: Record<string, string> = {
  error: "调用失败",
  invalid: "响应不合法",
  truncated: "响应被截断",
  partial: "部分响应被拒",
  unknown: "状态未知",
};

const CONCLUSION_SCOPE_LABELS: Record<string, string> = {
  findings_within_covered_checks: "结论仅适用于本次已完成的检查范围",
  no_findings_within_covered_checks: "未发现问题仅限本次已完成的检查范围",
  incomplete_scope: "检查未完成，不能得出完整结论",
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asInt(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
    return Math.trunc(Number(value));
  }
  return null;
}

function asBool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function asText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function uniquePages(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  const pages = value
    .map((item) => asInt(item))
    .filter((item): item is number => item !== null && item > 0);
  return Array.from(new Set(pages)).sort((left, right) => left - right);
}

/** 页码压缩：13,14,15,20 → “第13-15、20页”；不把缺失页码猜成 1。 */
export function formatPageList(pages: number[]): string {
  const sorted = Array.from(new Set(pages.filter((page) => Number.isInteger(page) && page > 0)))
    .sort((left, right) => left - right);
  if (sorted.length === 0) return "页码未记录";
  const groups: string[] = [];
  let start = sorted[0];
  let previous = sorted[0];
  const flush = () => {
    groups.push(start === previous ? String(start) : `${start}-${previous}`);
  };
  for (const page of sorted.slice(1)) {
    if (page === previous + 1) {
      previous = page;
      continue;
    }
    flush();
    start = page;
    previous = page;
  }
  flush();
  return `第${groups.join("、")}页`;
}

function failedUnitsFrom(raw: unknown): AiScopeFailedUnit[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    const unit = asRecord(item);
    if (!unit) return [];
    const specialty = asText(unit.specialty);
    const status = asText(unit.status) || "unknown";
    const pages = uniquePages(unit.pages);
    const attempts = asInt(unit.attempts);
    return [{
      specialty: specialty || "unknown",
      specialtyLabel: SPECIALTY_LABELS[specialty] || specialty || "未知专项",
      status,
      statusLabel: UNIT_STATUS_LABELS[status] || status,
      pages,
      pagesLabel: formatPageList(pages),
      errorType: asText(unit.error_type) || null,
      httpStatus: asInt(unit.http_status),
      error: asText(unit.error) || null,
      retryable: asBool(unit.retryable),
      attempts,
    }];
  });
}

function uncoveredPagesFrom(raw: unknown): number[] {
  if (!Array.isArray(raw)) return [];
  return uniquePages(
    raw.flatMap((item) => {
      const range = asRecord(item);
      return range && Array.isArray(range.pages) ? range.pages : [];
    }),
  );
}

export function deriveAiScope(
  meta: unknown,
  options: { requestedHint?: boolean | null; conclusionScopeHint?: string | null } = {},
): AiScopeView {
  const metaRecord = asRecord(meta);
  const execution = asRecord(metaRecord?.ai_execution);
  const coverage = asRecord(metaRecord?.ai_coverage);
  const qualityGate = asRecord(metaRecord?.quality_gate);

  const requested = asBool(execution?.requested) ?? options.requestedHint ?? null;
  const state = asText(execution?.state) || (coverage ? "unknown" : "");
  const stateLabel = STATE_LABELS[state] || "状态未知";
  const coverageStatus = asText(coverage?.status) || null;
  const coverageStatusLabel = coverageStatus
    ? COVERAGE_STATUS_LABELS[coverageStatus] || coverageStatus
    : "覆盖未记录";

  const planned = asInt(coverage?.planned_calls);
  const failed = asInt(coverage?.failed_calls);
  const finished = asInt(coverage?.finished_calls ?? coverage?.completed_calls);
  let succeeded = asInt(coverage?.succeeded_calls ?? coverage?.effective_completed_calls);
  // 旧记录只有 finished/completed（含失败）、failed、partial 与 unknown；
  // 有效完成只能反推，不能把“已结束”直接当“已完成”。
  if (succeeded === null) {
    const ok = asInt(coverage?.valid_finding_calls);
    const empty = asInt(coverage?.valid_empty_calls);
    if (ok !== null && empty !== null) {
      succeeded = ok + empty;
    } else if (finished !== null && failed !== null) {
      const partialCount = asInt(coverage?.partial_calls) ?? 0;
      const unknownCount = asInt(coverage?.unknown_calls) ?? 0;
      succeeded = Math.max(0, finished - failed - partialCount - unknownCount);
    }
  }
  const retried = asInt(coverage?.retried_calls);
  const failedUnits = failedUnitsFrom(coverage?.failed_units);
  const uncoveredPages = uncoveredPagesFrom(coverage?.uncovered_ranges);
  const conclusionScope = asText(metaRecord?.conclusion_scope)
    || asText(qualityGate?.conclusion_scope)
    || asText(options.conclusionScopeHint);
  const conclusionScopeLabel = CONCLUSION_SCOPE_LABELS[conclusionScope]
    || (conclusionScope === "旧版未记录" || !conclusionScope
      ? "历史结果未记录结论作用域"
      : conclusionScope);

  let tone: AiScopeTone = "muted";
  let headline = "";
  let summary = "";
  let scopeLine = "";
  let needsRecheck = false;

  if (requested === false) {
    headline = "大模型文字复核：本次未执行";
    summary = "以下仅有本地规则结果，不能据此认为文字语义已经检查。";
    scopeLine = "错别字、多字漏字、病句、说明理由是否充分等文字语义检查未执行；可在任务页使用大模型重新分析补检。";
    // 纯规则任务也要给出可执行的补检入口，而不是只写“未执行”。
    needsRecheck = true;
  } else if (!coverage && requested === null && !state) {
    headline = "大模型检查状态与覆盖：未记录";
    summary = "历史结果没有保存 AI 是否执行、检查了哪些范围。";
    scopeLine = "不能据此认为已完成或未完成文字语义检查；如需确认，请重新分析。";
    needsRecheck = true;
  } else if (!coverage) {
    tone = state === "succeeded" ? "warning" : state === "failed" ? "danger" : "warning";
    headline = state === "succeeded"
      ? "大模型文字复核：状态为已完成，但覆盖信息未记录"
      : `大模型文字复核：${stateLabel}，覆盖信息未记录`;
    summary = "没有检查单元计数，无法确认是否完整覆盖。";
    scopeLine = "历史结果未记录检查范围，不能据此认为全量完成；结论只适用于可确认的已完成部分。";
    needsRecheck = true;
  } else if (planned === null || succeeded === null) {
    tone = coverageStatus === "not_run" ? "muted" : "warning";
    headline = coverageStatus === "not_run"
      ? "大模型文字复核：本次未执行"
      : `大模型文字复核：${coverageStatusLabel}（计数未记录）`;
    summary = "覆盖记录存在，但缺少检查单元计数，无法计算有效完成比例。";
    scopeLine = "不能把“覆盖记录存在”当作“全部检查完成”。";
    needsRecheck = coverageStatus !== "not_run";
  } else if (planned === 0) {
    headline = "大模型文字复核：未执行（没有检查单元）";
    summary = "本次没有可审计的文本窗口。";
    scopeLine = "未执行文字语义检查，不能据此认为材料没有文字问题。";
  } else if (coverageStatus === "complete" && succeeded >= planned && failed === 0) {
    tone = "success";
    headline = `大模型文字复核：已完整完成（${succeeded}/${planned}）`;
    summary = retried && retried > 0
      ? `全部检查单元有效完成；其中 ${retried} 个单元首次失败后补试成功。`
      : "全部检查单元有效完成。";
    scopeLine = "结论适用于本次全部检查单元；已检查范围不等于保证无遗漏。";
  } else {
    const isTotalFailure = coverageStatus === "failed" || (succeeded === 0 && (failed ?? 0) > 0);
    tone = isTotalFailure ? "danger" : "warning";
    const failedText = failed && failed > 0 ? `，失败 ${failed}` : "";
    const retryText = retried && retried > 0 ? `，另有 ${retried} 个单元重试` : "";
    headline = isTotalFailure
      ? `大模型文字复核：执行失败（有效完成 ${succeeded}/${planned}）`
      : `大模型文字复核：有效完成 ${succeeded}/${planned}${failedText}${retryText}`;
    summary = "存在未完成的检查单元；已保留有效结果，但不能视为完整审核通过。";
    scopeLine = "结论仅适用于已完成检查；未完成范围需要补检后才能得出完整结论。";
    needsRecheck = true;
  }

  if (uncoveredPages.length > 0) {
    const pageText = `未覆盖页：${formatPageList(uncoveredPages)}。`;
    summary = summary ? `${summary} ${pageText}` : pageText;
    needsRecheck = true;
  }

  const note = asText(coverage?.coverage_note);

  return {
    recorded: Boolean(coverage),
    missingCoverage: !coverage,
    requested,
    state,
    stateLabel,
    tone,
    headline,
    summary,
    scopeLine,
    conclusionScope: conclusionScope || "未记录",
    conclusionScopeLabel,
    coverageStatus,
    coverageStatusLabel,
    planned,
    succeeded,
    failed,
    retried,
    failedUnits,
    uncoveredPages,
    note,
    needsRecheck,
  };
}
