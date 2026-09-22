"use client";

import { useMemo } from "react";

import { Card, SectionTitle } from "@/components/ui";

import { MaterialBreadcrumb } from "./MaterialBreadcrumb";
import { MaterialDataBasisNotice } from "./MaterialDataBasisNotice";
import { MaterialSlotCard } from "./MaterialSlotCard";
import {
  TIMELINE_ABSENT_LABEL,
  UNRESOLVED_YEAR_LABEL,
  buildTimelineRows,
  readUnitTimelinePayload,
  type TimelineSlotCell,
  type UnitTimelineResponse,
} from "./materialDetailAdapters";
import { useMaterialResource } from "./useMaterialResource";

/**
 * UnitTimelinePage（页面 09）：一个单位的多年度材料时间轴。
 *
 * 存在的理由
 * ----------
 * 部门矩阵回答"这个部门下有哪些主体"，但看不出"这个单位历年到底交了没有"。
 * 时间轴按**财政年度**排列，是唯一能回答"2024 年决算到底有没有"的视图。
 *
 * 四条不能让步的口径
 * ------------------
 * 1. **按财政年度排，不按发布时间排**：2024 年度决算在 2025 年 8 月发布是常态，
 *    按发布时间排会把它挪到 2025 那一行 —— 使用者据此做的每一个判断都会错位。
 * 2. **没有槽位只写「尚无已建立材料」**，绝不写"缺失/逾期/未上传"：
 *    应收基线（WP9）未建立时，"库里没有"推不出"应该有却没有"。
 * 3. **年度未知单独成行**，不兜底成任何具体年份。
 * 4. **同一年度的多条槽位一条都不隐藏**：除主卡外逐条列出（兜底显示"另有 N 条"）。
 */
export interface UnitTimelinePageProps {
  unitId: string;
}

function TimelineCellView({ cell, testId }: { cell: TimelineSlotCell; testId: string }) {
  if (!cell.exists || !cell.primary) {
    return (
      <div
        className="rounded-md border border-dashed border-border px-3 py-2 text-xs text-slate-400"
        data-testid={testId}
        data-slot-exists="false"
      >
        {cell.emptyLabel}
      </div>
    );
  }

  const primary = cell.primary;
  const extras = cell.extraCount > 0 ? cell.total - 1 : 0;

  return (
    <div data-testid={testId} data-slot-exists="true" data-slot-total={cell.total}>
      <MaterialSlotCard
        ref={{ exists: true, slot: primary }}
        testId={`${testId}-primary`}
        href={`/materials/slots/${encodeURIComponent(primary.slot_id)}`}
      />
      {extras > 0 ? (
        <details className="mt-1" data-testid={`${testId}-extra`}>
          <summary
            className="cursor-pointer text-xs text-warning-700"
            data-testid={`${testId}-extra-summary`}
          >
            另有 {extras} 个待确认槽位
          </summary>
          <div className="mt-1 space-y-1">
            {/* 逐条列出而不是只给一句提示：占位槽位恰恰最需要人工确认，
                只显示数量会让它们永远没人处理。 */}
            {(cell.all ?? []).slice(1).map((slot) => (
              <MaterialSlotCard
                key={slot.slot_id}
                ref={{ exists: true, slot }}
                testId={`${testId}-extra-${slot.slot_id}`}
                href={`/materials/slots/${encodeURIComponent(slot.slot_id)}`}
              />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

export function UnitTimelinePage({ unitId }: UnitTimelinePageProps) {
  const url = useMemo(
    () => `/api/materials/units/${encodeURIComponent(unitId)}/timeline`,
    [unitId],
  );
  const response = useMaterialResource<UnitTimelineResponse>(url, readUnitTimelinePayload);
  const data = response.data?.data ?? null;
  const rows = useMemo(() => buildTimelineRows(data), [data]);
  const unresolved = data?.unresolved_year_slots ?? [];

  return (
    <div className="mx-auto max-w-[1600px] px-6 py-6" data-testid="gbc-material-unit-page">
      <MaterialBreadcrumb
        entries={[
          { label: "材料台账", href: "/materials" },
          ...(data?.unit.jurisdiction_id
            ? [
                {
                  label: data.unit.jurisdiction_name ?? data.unit.jurisdiction_id,
                  href: `/materials/district/${encodeURIComponent(data.unit.jurisdiction_id)}`,
                },
              ]
            : []),
          ...(data?.unit.department_id
            ? [
                {
                  label: data.unit.department_name ?? data.unit.department_id,
                  href: `/materials/department/${encodeURIComponent(data.unit.department_id)}`,
                },
              ]
            : []),
          { label: data?.unit.unit_name ?? unitId },
        ]}
      />

      <div className="mt-3">
        <SectionTitle
          title={`${data?.unit.unit_name ?? unitId} · 单位年度材料时间轴`}
          desc="一个单位按财政年度查看预算和决算；发布日期与财政年度分开记录。"
        />
      </div>

      <div className="mt-4">
        <MaterialDataBasisNotice
          expectedMaterialsReady={response.data?.meta?.expected_materials_ready ?? false}
        />
      </div>

      <div
        className="mt-4 rounded-card border border-warning-200 bg-warning-50 px-4 py-3 text-xs text-warning-700"
        data-testid="gbc-material-timeline-caveat"
      >
        <p className="font-medium">不要用「上半年 = 预算、下半年 = 决算」来归类。</p>
        <p className="mt-1">
          例如 2024 年度单位预算发布于 2024-02-27，而 2024 年度单位决算发布于 2025-08-20，
          两者都属于「财政年度 2024」。本页因此只按财政年度排列，并把网页发布日期
          单独展示在材料来源里。
        </p>
      </div>

      {response.loading ? (
        <div
          className="mt-5 rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
          data-testid="gbc-material-unit-loading"
        >
          正在加载单位材料时间轴…
        </div>
      ) : null}

      {response.error ? (
        <div
          className="mt-5 rounded-card border border-danger-200 bg-danger-50 p-6 text-sm text-danger-700"
          data-testid="gbc-material-unit-error"
        >
          <p>单位材料时间轴加载失败：{response.error}</p>
          <button
            type="button"
            onClick={response.reload}
            className="mt-3 rounded-md border border-border bg-white px-3 py-1.5 text-sm text-slate-600"
            data-testid="gbc-material-unit-retry"
          >
            重试
          </button>
        </div>
      ) : null}

      {!response.loading && !response.error && data ? (
        rows.length === 0 && unresolved.length === 0 ? (
          <div
            className="mt-5 rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
            data-testid="gbc-material-unit-empty"
          >
            <p>该单位当前没有任何已建立的材料槽位。</p>
            {data.unit.subject_kind === "unknown" ? (
              <p className="mt-1 text-xs text-slate-400" data-testid="gbc-material-unit-kind-unknown">
                主体类型待确认：这条时间轴的身份来自材料识别结果，尚未与组织目录对齐。
              </p>
            ) : null}
          </div>
        ) : (
          <>
            <div className="mt-4">
              <Card title="财政年度材料" desc="按财政年度降序，与网页发布日期无关">
                <div className="overflow-x-auto" data-testid="gbc-material-timeline-table">
                  <table className="w-full min-w-[1100px] border-collapse">
                    <thead>
                      <tr>
                        <th className="w-[140px] whitespace-nowrap border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                          财政年度
                        </th>
                        <th className="border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                          预算材料
                        </th>
                        <th className="border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                          决算材料
                        </th>
                        <th className="w-[260px] border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                          文种待确认
                        </th>
                        <th className="w-[140px] whitespace-nowrap border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                          年度情况
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row) => (
                        <tr key={row.fiscalYear} data-testid={`gbc-material-timeline-year-${row.fiscalYear}`}>
                          <td className="border-b border-border px-4 py-3 align-top">
                            <div
                              className="text-lg font-semibold text-slate-900"
                              data-testid={`gbc-material-timeline-year-label-${row.fiscalYear}`}
                            >
                              {row.fiscalYear}
                            </div>
                            <div className="text-xs text-slate-400">财政年度</div>
                          </td>
                          <td className="border-b border-border px-4 py-3 align-top">
                            <TimelineCellView
                              cell={row.budget}
                              testId={`gbc-material-timeline-budget-${row.fiscalYear}`}
                            />
                          </td>
                          <td className="border-b border-border px-4 py-3 align-top">
                            <TimelineCellView
                              cell={row.final}
                              testId={`gbc-material-timeline-final-${row.fiscalYear}`}
                            />
                          </td>
                          <td className="border-b border-border px-4 py-3 align-top">
                            <TimelineCellView
                              cell={row.unclassified}
                              testId={`gbc-material-timeline-unclassified-${row.fiscalYear}`}
                            />
                          </td>
                          <td className="border-b border-border px-4 py-3 align-top text-sm text-slate-600">
                            <span data-testid={`gbc-material-timeline-year-status-${row.fiscalYear}`}>
                              {row.yearStatus}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>

            {unresolved.length > 0 ? (
              <div className="mt-4">
                <Card
                  title={UNRESOLVED_YEAR_LABEL}
                  desc="识别不到财政年度的材料：它们不属于任何一个具体年度，因此单列"
                  data-testid="gbc-material-timeline-unresolved"
                >
                  <div className="space-y-2">
                    {unresolved.map((slot) => (
                      <MaterialSlotCard
                        key={slot.slot_id}
                        ref={{ exists: true, slot }}
                        testId={`gbc-material-timeline-unresolved-${slot.slot_id}`}
                        href={`/materials/slots/${encodeURIComponent(slot.slot_id)}`}
                      />
                    ))}
                  </div>
                </Card>
              </div>
            ) : null}

            <div
              className="mt-4 rounded-card border border-border bg-surface-100 px-4 py-3 text-xs text-slate-600"
              data-testid="gbc-material-timeline-status-note"
            >
              <p className="font-medium">状态要分清：未到期 / 已上传待分析 / 待人工复核 / 已完成 / 不适用 / 逾期未上传。</p>
              <p className="mt-1">
                没有槽位的格子只写「{TIMELINE_ABSENT_LABEL}」：应收材料基线尚未导入，
                「库里没有」推不出「应该有却没有」。同理，本页也不显示「年度完整」——
                判断完整需要应收基线，否则就是把「我只知道这些」说成「这些就是全部」。
              </p>
            </div>
          </>
        )
      ) : null}
    </div>
  );
}

export default UnitTimelinePage;
