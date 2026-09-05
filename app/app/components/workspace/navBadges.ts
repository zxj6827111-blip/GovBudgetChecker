import { normalizeUiTaskStatus } from "../../../lib/uiAdapters";
import type { JobSummaryRecord } from "../../../lib/uiAdapters";

import type { NavBadgeKey } from "./nav";

/**
 * 从真实任务列表计算导航角标数字。
 *
 * 关键约束（任务书"角标接真实计数，无数据显示空而非 0"）：
 * - 输入为 null/undefined（尚未拉到数据，例如请求中或请求失败）时，
 *   全部角标返回 undefined（空），而不是 0——0 意味着"已确认此刻数量为零"，
 *   未拉到数据不能被冒充为"已确认为零"。
 * - 输入为空数组（真实拉到数据，且确认当前没有任何任务）时，
 *   angular 返回 0，这是一个真实的统计结果，必须原样显示。
 */
export type NavBadgeCounts = Partial<Record<NavBadgeKey, number>>;

export function computeNavBadgeCounts(jobs: JobSummaryRecord[] | null | undefined): NavBadgeCounts {
  if (jobs === null || jobs === undefined) {
    return {};
  }

  let analyzing = 0;
  let reviewRequired = 0;
  for (const job of jobs) {
    const status = normalizeUiTaskStatus(job.status);
    if (status === "analyzing") {
      analyzing += 1;
    } else if (status === "review_required") {
      reviewRequired += 1;
    }
  }

  return {
    analyzing,
    review_required: reviewRequired,
  };
}
