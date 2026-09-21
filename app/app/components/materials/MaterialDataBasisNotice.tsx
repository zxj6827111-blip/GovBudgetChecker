"use client";

import { resolveDataBasisNotice } from "@/lib/materialStatusPresentation";

/**
 * MaterialDataBasisNotice：材料台账的数据口径提示（页面顶部固定展示）。
 *
 * 存在的理由
 * ----------
 * 本轮只统计"已经存在于 material_slots 里的槽位"，**还没有**应收材料基线。
 * 不把这句说清楚，用户一定会把"库里只有 4 条"读成"只应有 4 条"，
 * 进而把"完整率"当真实覆盖率看。宁可页面上多一行说明，
 * 也不能让一个不可计算的数字被当成结论。
 *
 * 文案由 `expected_materials_ready` 决定（后端 meta 给出的口径状态），
 * 前端只负责选文案，不自行判断"应该有几个"。
 */
export interface MaterialDataBasisNoticeProps {
  expectedMaterialsReady: boolean;
  className?: string;
  testId?: string;
}

export function MaterialDataBasisNotice({
  expectedMaterialsReady,
  className,
  testId = "gbc-material-data-basis-notice",
}: MaterialDataBasisNoticeProps) {
  const { notice } = resolveDataBasisNotice(expectedMaterialsReady);

  return (
    <div
      className={
        className ??
        "rounded-md border border-border bg-surface-100 px-4 py-3 text-xs text-slate-600"
      }
      data-testid={testId}
      data-expected-materials-ready={expectedMaterialsReady ? "true" : "false"}
    >
      {notice}
    </div>
  );
}

export default MaterialDataBasisNotice;
