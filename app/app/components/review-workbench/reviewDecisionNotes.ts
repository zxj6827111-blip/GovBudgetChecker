/**
 * 忽略理由与补充意见共用工作流的 note 字段。
 *
 * 后端 issue_workflow_store 只持久化 status + note，忽略理由不新增字段：
 * 写成 note 开头的固定两行（“忽略理由：…”“说明：…”），空一行后才是审核员
 * 自己写的补充意见。导出报告直接显示 note，这两行本身就是可读的处理记录。
 * 改为确认或恢复待处理时去掉理由块，只保留补充意见。
 */

export const IGNORE_REASON_OPTIONS = [
  { code: "rounding", label: "取整误差" },
  { code: "scope", label: "口径不同" },
  { code: "false_positive", label: "规则误报" },
  { code: "disclosed", label: "已在其他章节披露" },
  { code: "other", label: "其他" },
] as const;

export type IgnoreReasonCode = (typeof IGNORE_REASON_OPTIONS)[number]["code"];

export interface IgnoreReason {
  label: string;
  detail: string;
}

export interface ParsedDecisionNote {
  ignoreReason: IgnoreReason | null;
  /** 审核员自己写的补充意见（不含理由块）。 */
  userNote: string;
}

const REASON_PREFIX = "忽略理由：";
const DETAIL_PREFIX = "说明：";
export const IGNORE_DETAIL_MAX_LENGTH = 400;

export function ignoreReasonLabel(code: string): string | null {
  return IGNORE_REASON_OPTIONS.find((option) => option.code === code)?.label ?? null;
}

/** “其他”必须写说明，其余理由说明可选。 */
export function isIgnoreReasonComplete(code: string, detail: string): boolean {
  if (!ignoreReasonLabel(code)) return false;
  return code !== "other" || detail.trim().length > 0;
}

export function parseDecisionNote(note: string | null | undefined): ParsedDecisionNote {
  const text = String(note ?? "").replace(/\r\n/g, "\n");
  const lines = text.split("\n");
  if (!lines[0]?.startsWith(REASON_PREFIX)) {
    return { ignoreReason: null, userNote: text.trim() };
  }
  const label = lines[0].slice(REASON_PREFIX.length).trim();
  let rest = 1;
  let detail = "";
  if (lines[1]?.startsWith(DETAIL_PREFIX)) {
    detail = lines[1].slice(DETAIL_PREFIX.length).trim();
    rest = 2;
  }
  return {
    ignoreReason: label ? { label, detail } : null,
    userNote: lines.slice(rest).join("\n").trim(),
  };
}

export function composeDecisionNote(ignoreReason: IgnoreReason | null, userNote: string): string {
  const note = userNote.trim();
  if (!ignoreReason) return note;
  // 说明写在一行里：换行会把后面的文字误读成补充意见。
  const detail = ignoreReason.detail.replace(/\s*\n\s*/g, " ").trim().slice(0, IGNORE_DETAIL_MAX_LENGTH);
  const block = [`${REASON_PREFIX}${ignoreReason.label}`, ...(detail ? [`${DETAIL_PREFIX}${detail}`] : [])].join("\n");
  return note ? `${block}\n\n${note}` : block;
}

/** 状态离开“已忽略”时只留下补充意见。 */
export function stripIgnoreReason(note: string | null | undefined): string {
  return parseDecisionNote(note).userNote;
}
