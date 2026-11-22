/**
 * 正文金额校正目标选择（R5-A）。
 *
 * 规则：先按唯一问题 ID 精确匹配；只有完全没有精确绑定时才回退同页
 * 同规则候选，且候选多于一条时不给默认选中项，必须由复核人选择。
 * 上一轮缺陷的根因是 `find` 的谓词同时接受“精确 ID 或同页”，
 * 第一条同页候选先命中，点第二笔却提交第一笔的 target_id。
 */
export interface TextCorrectionTargetLike {
  target_id: string;
  issue_ids?: string[];
  page?: number;
  rule_id?: string;
}

export interface CorrectionTargetSelection<T extends TextCorrectionTargetLike> {
  pool: T[];
  initialTargetId: string | null;
  ambiguous: boolean;
}

export function selectCorrectionTargets<T extends TextCorrectionTargetLike>(
  targets: T[],
  problem: { id: string; page?: number; ruleId: string },
): CorrectionTargetSelection<T> {
  const exact = targets.filter((item) =>
    Array.isArray(item.issue_ids) && item.issue_ids.map(String).includes(problem.id));
  const samePage = targets.filter((item) =>
    problem.page !== undefined
    && Number(item.page) === problem.page
    && (problem.ruleId === "CMM-011" || item.rule_id === problem.ruleId));
  const pool = exact.length ? exact : samePage;
  return {
    pool,
    initialTargetId: exact.length === 1
      ? exact[0].target_id
      : pool.length === 1 ? pool[0].target_id : null,
    ambiguous: pool.length > 1,
  };
}
