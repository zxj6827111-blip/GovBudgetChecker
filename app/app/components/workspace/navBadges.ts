import type { JobCountsView } from "../../../lib/jobListViews";
import { normalizeUiTaskStatus } from "../../../lib/uiAdapters";
import type { JobSummaryRecord } from "../../../lib/uiAdapters";

import type { NavBadgeKey } from "./nav";

/**
 * 处理角标来自分析运行；复核角标必须由清单服务的材料生命周期计数提供。
 *
 * 关键约束（任务书"角标接真实计数，无数据显示空而非 0"）：
 * - 输入为 null/undefined（尚未拉到数据，例如请求中或请求失败）时，
 *   全部角标返回 undefined（空），而不是 0——0 意味着"已确认此刻数量为零"，
 *   未拉到数据不能被冒充为"已确认为零"。
 * - 输入为空数组（真实拉到数据，且确认当前没有任何任务）时，
 *   angular 返回 0，这是一个真实的统计结果，必须原样显示。
 */
export type NavBadgeCounts = Partial<Record<NavBadgeKey, number>>;

export function computeNavBadgeCounts(jobs: JobSummaryRecord[] | null | undefined, reviewPendingCount?: number): NavBadgeCounts {
  const reviewCounts = typeof reviewPendingCount === "number" ? { review_required: reviewPendingCount } : {};
  if (jobs === null || jobs === undefined) {
    return reviewCounts;
  }

  let analyzing = 0;
  for (const job of jobs) {
    const status = normalizeUiTaskStatus(job.status);
    if (status === "analyzing") {
      analyzing += 1;
    }
  }

  return {
    analyzing,
    ...reviewCounts,
  };
}

/** 接口汇总版：counts 为 null（请求中或失败）时同样不渲染处理角标。 */
export function navBadgeCountsFromJobCounts(counts: JobCountsView | null | undefined): NavBadgeCounts {
  return counts ? { analyzing: counts.status_counts.analyzing } : {};
}
