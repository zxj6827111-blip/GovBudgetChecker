"use client";

import Link from "next/link";
import type { Route } from "next";
import { useMemo } from "react";

import { Badge, SectionTitle } from "@/components/ui";

import { MaterialBreadcrumb } from "./MaterialBreadcrumb";
import { MaterialDataBasisNotice } from "./MaterialDataBasisNotice";
import { MaterialDetailCoverageTab } from "./MaterialDetailCoverageTab";
import { MaterialDetailFindingsTab } from "./MaterialDetailFindingsTab";
import { MaterialDetailOverviewTab } from "./MaterialDetailOverviewTab";
import { MaterialDetailRunsTab } from "./MaterialDetailRunsTab";
import { MaterialDetailVersionsTab } from "./MaterialDetailVersionsTab";
import { MaterialStatusBadge } from "./MaterialStatusBadge";
import { MaterialStatusReason } from "./MaterialStatusReason";
import {
  MATERIAL_DETAIL_TABS,
  buildDetailBreadcrumb,
  materialDetailTabHref,
  presentSlotState,
  readSlotDetailPayload,
  resolveDetailTab,
  type MaterialDetailTabKey,
  type SlotDetailResponse,
} from "./materialDetailAdapters";
import { useMaterialResource } from "./useMaterialResource";

/**
 * MaterialDetailPage（页面 10-13）：材料明细。
 *
 * 五个 Tab 固定为：材料概览 / 检查结果 / 检查覆盖 / 版本与来源 / 处理记录。
 * Tab key 稳定且写进查询串（`?tab=coverage`），这样以后全局搜索可以直接
 * 落到某个 Tab，而不需要再发明一套路由。
 *
 * 三条不能让步的口径（本页是全系统最容易被误读的地方）
 * ----------------------------------------------------
 * 1. **当前分析只对当前文件版本**：版本被替换后，上一版的分析只出现在
 *    「版本与来源」与「处理记录」里并标"历史版本"，绝不能显示成本文件的结果。
 * 2. **算不出来就显示"—"**：`formal_issue_count` 为 null 与为 0 是两个结论，
 *    页面按 `formatCount` 分开渲染，绝不把 null 补成 0。
 * 3. **没有分析不等于没有问题**：文案固定为"当前文件版本暂无可确认的分析结果"，
 *    **禁止**"暂无问题"。
 */
export interface MaterialDetailPageProps {
  slotId: string;
  /** 来自查询串的 Tab；未识别时回落到概览。 */
  tab?: string;
}

export function MaterialDetailPage({ slotId, tab = "overview" }: MaterialDetailPageProps) {
  const activeTab: MaterialDetailTabKey = resolveDetailTab(tab);
  const url = useMemo(() => `/api/materials/slots/${encodeURIComponent(slotId)}`, [slotId]);
  const response = useMaterialResource<SlotDetailResponse>(url, readSlotDetailPayload);
  const data = response.data?.data ?? null;
  const slot = data?.slot ?? null;
  const state = useMemo(
    () => presentSlotState(slot, data?.version_total ?? 0),
    [slot, data?.version_total],
  );
  const breadcrumb = useMemo(
    () =>
      slot
        ? buildDetailBreadcrumb(slot)
        : [{ label: "材料台账", href: "/materials" }, { label: slotId, href: null }],
    [slot, slotId],
  );

  return (
    <div className="mx-auto max-w-[1600px] px-6 py-6" data-testid="gbc-material-detail-page">
      <MaterialBreadcrumb
        entries={breadcrumb.map((entry) => ({
          label: entry.label,
          href: entry.href ?? undefined,
        }))}
        testId="gbc-material-detail-breadcrumb"
      />

      <div className="mt-3">
        <SectionTitle
          title="材料详情"
          desc="这条材料是什么状态、检查结论是什么、用了哪个文件版本、谁在什么时候处理过它。"
        />
      </div>

      <div className="mt-4">
        <MaterialDataBasisNotice
          expectedMaterialsReady={response.data?.meta?.expected_materials_ready ?? false}
        />
      </div>

      {response.loading ? (
        <div
          className="mt-5 rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
          data-testid="gbc-material-detail-loading"
        >
          正在加载材料详情…
        </div>
      ) : null}

      {response.error ? (
        <div
          className="mt-5 rounded-card border border-danger-200 bg-danger-50 p-6 text-sm text-danger-700"
          data-testid="gbc-material-detail-error"
        >
          <p>材料详情加载失败：{response.error}</p>
          <button
            type="button"
            onClick={response.reload}
            className="mt-3 rounded-md border border-border bg-white px-3 py-1.5 text-sm text-slate-600"
            data-testid="gbc-material-detail-retry"
          >
            重试
          </button>
        </div>
      ) : null}

      {!response.loading && !response.error && data && slot ? (
        <>
          <div
            className="mt-5 rounded-card border border-border bg-white px-5 py-4 shadow-soft"
            data-testid="gbc-material-detail-header"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2
                  className="text-lg font-semibold text-slate-900"
                  data-testid="gbc-material-detail-title"
                >
                  {slot.subject_org_name}
                  {slot.fiscal_year ? ` ${slot.fiscal_year} 年度` : " 年度待确认"}
                  {slot.report_kind === "budget"
                    ? "预算"
                    : slot.report_kind === "final"
                      ? "决算"
                      : "（文种待确认）"}
                </h2>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Badge tone="processing">
                    {slot.fiscal_year ? `财政年度 ${slot.fiscal_year}` : "财政年度待确认"}
                  </Badge>
                  <Badge tone="neutral">
                    {slot.report_kind === "budget"
                      ? "预算"
                      : slot.report_kind === "final"
                        ? "决算"
                        : "文种待确认"}
                  </Badge>
                  <MaterialStatusBadge status={slot.status} testId="gbc-material-detail-status" />
                  <MaterialStatusReason
                    reason={slot.status_reason}
                    className="text-xs text-slate-500"
                    testId="gbc-material-detail-reason"
                  />
                </div>
              </div>

              <dl className="text-right text-xs text-slate-500">
                <div data-testid="gbc-material-detail-due">
                  <dt className="inline">应收/公开截止：</dt>
                  <dd className="inline text-slate-700">
                    {slot.due_at ? slot.due_at.slice(0, 10) : "未登记"}
                  </dd>
                </div>
                <div className="mt-1" data-testid="gbc-material-detail-current-version">
                  <dt className="inline">当前文件版本：</dt>
                  <dd className="inline text-slate-700">
                    {data.current_version
                      ? `v${data.current_version.document_version_id}`
                      : "无"}
                  </dd>
                </div>
                <div className="mt-1" data-testid="gbc-material-detail-updated">
                  <dt className="inline">最后更新：</dt>
                  <dd className="inline text-slate-700">{slot.updated_at ?? "—"}</dd>
                </div>
              </dl>
            </div>

            {slot.caliber_conflict_candidate ? (
              <p
                className="mt-3 rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700"
                data-testid="gbc-material-detail-caliber-conflict"
              >
                口径冲突待确认：当前识别口径为「{slot.caliber}」，另一次识别为「
                {slot.caliber_conflict_candidate}」，需人工裁决后才能判断两笔数字能否相加。
              </p>
            ) : null}

            {state.isDueUnknown ? (
              <p
                className="mt-3 rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700"
                data-testid="gbc-material-detail-due-unknown"
              >
                {state.label}
              </p>
            ) : null}
          </div>

          <nav
            className="mt-5 flex flex-wrap gap-1 border-b border-border"
            aria-label="材料详情分区"
            data-testid="gbc-material-detail-tabs"
          >
            {MATERIAL_DETAIL_TABS.map((item) => {
              const isActive = item.key === activeTab;
              return (
                <Link
                  key={item.key}
                  href={materialDetailTabHref(slotId, item.key) as Route}
                  scroll={false}
                  aria-current={isActive ? "page" : undefined}
                  data-testid={`gbc-material-detail-tab-${item.key}`}
                  data-tab-active={isActive ? "true" : "false"}
                  className={
                    isActive
                      ? "-mb-px border-b-2 border-primary-600 px-3 py-2 text-sm font-medium text-primary-700"
                      : "-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-slate-500 hover:text-slate-700"
                  }
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="mt-5" data-testid={`gbc-material-detail-panel-${activeTab}`}>
            {activeTab === "overview" ? (
              <MaterialDetailOverviewTab data={data} state={state} />
            ) : null}
            {activeTab === "findings" ? <MaterialDetailFindingsTab analysis={data.current_analysis} /> : null}
            {activeTab === "coverage" ? (
              <MaterialDetailCoverageTab coverage={data.current_analysis?.coverage ?? null} />
            ) : null}
            {activeTab === "versions" ? (
              <MaterialDetailVersionsTab slotId={slotId} detail={data} />
            ) : null}
            {activeTab === "runs" ? <MaterialDetailRunsTab slotId={slotId} /> : null}
          </div>
        </>
      ) : null}
    </div>
  );
}

export default MaterialDetailPage;
