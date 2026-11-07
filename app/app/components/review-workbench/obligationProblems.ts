/**
 * 补核义务并入「必须处理」：把复核上下文里的每条补核义务转成问题队列里的一条，
 * 与正式问题同列、同样逐条处理。
 *
 * 只改展示与操作入口，不改判定：
 * - 结论仍写入补核接口（PUT /api/reviews/{slot}/obligations/{id}），完成门禁、
 *   乐观锁、依据纪律都由服务端照旧判定；
 * - 条目 id 带 ``obligation:`` 前缀，与问题工作流（/api/workflow）的 issue_id
 *   不会撞号，也不会被误送进问题工作流。
 */

import type { Problem, ProblemEvidenceRef } from "@/lib/mock";

import { obligationGuidance } from "./obligationGuidance";
import {
  isObligationHandled,
  type ObligationReviewBlockRecord,
  type ObligationReviewItemRecord,
} from "./reviewLifecycleAdapters";

export const OBLIGATION_PROBLEM_SOURCE = "obligation";
const OBLIGATION_PROBLEM_PREFIX = "obligation:";
/** 与后端 ObligationDecisionRequest.evidence_reference 的上限一致。 */
const EVIDENCE_REFERENCE_MAX_LENGTH = 200;

export type ObligationDecision = "verified_ok" | "verified_issue" | "not_applicable" | "pending";

/** 按钮与处理记录上的说法；取值域与后端四态逐字一致。 */
export const OBLIGATION_DECISION_LABELS: Record<Exclude<ObligationDecision, "pending">, string> = {
  verified_ok: "核对无误",
  verified_issue: "确认有问题",
  not_applicable: "不适用",
};

export function obligationProblemId(obligationId: string): string {
  return `${OBLIGATION_PROBLEM_PREFIX}${obligationId}`;
}

export function isObligationProblem(problem: Pick<Problem, "source"> | null | undefined): boolean {
  return problem?.source === OBLIGATION_PROBLEM_SOURCE;
}

/**
 * 引擎卡点原文里的机器字段名换成人话，只改展示：如
 * “人员经费合计的total_actual/决算操作数缺失或不唯一” → “人员经费合计的决算数值没取到（或取到不止一个）”。
 * 引擎原文不改（下游仍按原句式解析），审计记录仍是原文。
 */
export function readableObligationReason(text: string | null | undefined): string {
  return String(text ?? "")
    .replace(/[A-Za-z]+_[A-Za-z0-9_]*\//g, "")
    .replace(/(原始)?操作数缺失或不唯一/g, "数值没取到（或取到不止一个）")
    .trim();
}

function locationText(page: number, label: string | null): string {
  return label ? `第 ${page} 页 · ${label}` : `第 ${page} 页`;
}

/** 补核位置转成证据引用：有框的可直接高亮，没有框的只翻页。 */
function locationRefs(item: ObligationReviewItemRecord, id: string): ProblemEvidenceRef[] {
  return (item.locations ?? [])
    .filter((location) => Number.isInteger(location.page) && location.page > 0)
    .map((location, index) => {
      const bbox = Array.isArray(location.bbox) && location.bbox.length === 4 ? location.bbox : undefined;
      // R2.2 §7：补核位置链的 bbox（表框/finding 证据框）不得标 exact_span/verified——
      // 它是结构/证据框定位，不是对缺数位置的已验证精确跨度。
      return {
        key: `${id}|location|${index}`,
        role: "原文位置",
        page: location.page,
        locationText: locationText(location.page, location.label),
        bbox,
        locationLevel: bbox ? "search_region" : "page",
        verificationStatus: "unverified",
      };
    });
}

/** 一条补核义务在问题队列里的样子：标题是检查项名称，“未能自动核对”由分组与徽标说明。 */
export function toObligationProblem(
  item: ObligationReviewItemRecord,
  allItems: ObligationReviewItemRecord[],
  jobId?: string,
): Problem {
  const id = obligationProblemId(item.obligation_id);
  const evidenceRefs = locationRefs(item, id);
  const first = evidenceRefs[0];
  const checkName = item.title?.trim() || item.obligation_id;
  const reason = [item.reason_label || item.reason, readableObligationReason(item.detail)].filter(Boolean).join("：");
  return {
    id,
    ruleId: item.obligation_id,
    title: checkName,
    severity: "manual_review",
    category: "未能自动核对",
    page: first?.page,
    location: first?.locationText,
    description: reason || "系统没能自动完成这项检查，需要人工核对原文。",
    suggestion: obligationGuidance(item, allItems).howToDecide,
    snippet: "",
    evidenceImage: "",
    status: isObligationHandled(item) ? "resolved" : "pending",
    source: OBLIGATION_PROBLEM_SOURCE,
    bbox: first?.bbox,
    evidenceRefs,
    check: checkName,
    jobId,
  };
}

/** 复核上下文里全部补核义务（含已处理，便于撤销）转成问题队列条目。 */
export function toObligationProblems(
  block: ObligationReviewBlockRecord | null | undefined,
  jobId?: string,
): Problem[] {
  if (!block?.available) return [];
  const items = block.items ?? [];
  return items.map((item) => toObligationProblem(item, items, jobId));
}

/** 问题 id → 补核义务，供卡片与提交时取回原记录（含乐观锁 revision）。 */
export function obligationItemsByProblemId(
  block: ObligationReviewBlockRecord | null | undefined,
): Record<string, ObligationReviewItemRecord> {
  if (!block?.available) return {};
  return Object.fromEntries((block.items ?? []).map((item) => [obligationProblemId(item.obligation_id), item]));
}

/** 已处理的补核写进处理状态表（值为决定码）；待处理的不写，与问题工作流同一约定。 */
export function obligationStatusEntries(
  block: ObligationReviewBlockRecord | null | undefined,
): Record<string, string> {
  if (!block?.available) return {};
  return Object.fromEntries(
    (block.items ?? [])
      .filter((item) => isObligationHandled(item))
      .map((item) => [obligationProblemId(item.obligation_id), String(item.decision)]),
  );
}

/**
 * 「核对无误」一键提交时的证据位置：取系统定位到的页码与表名，并注明是按系统定位
 * 核对的，审计时能区分“人工另找的依据”和“按系统指引看过的位置”。
 * 没有可用位置时返回 null，由调用方要求人工写一句核对了哪里。
 */
export function obligationAutoEvidence(item: ObligationReviewItemRecord): string | null {
  const places = (item.locations ?? [])
    .filter((location) => Number.isInteger(location.page) && location.page > 0)
    .map((location) => locationText(location.page, location.label));
  if (places.length === 0) return null;
  const suffix = "（按系统定位核对）";
  let text = "";
  for (const place of places) {
    const next = text ? `${text}；${place}` : place;
    if (next.length + suffix.length > EVIDENCE_REFERENCE_MAX_LENGTH) break;
    text = next;
  }
  return `${text || places[0].slice(0, EVIDENCE_REFERENCE_MAX_LENGTH - suffix.length)}${suffix}`;
}
