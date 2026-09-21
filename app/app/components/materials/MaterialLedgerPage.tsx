"use client";

import Link from "next/link";
import type { Route } from "next";
import { useCallback, useMemo, useState } from "react";

import { Card, Metric, SectionTitle } from "@/components/ui";
import { UNKNOWN_METRIC_TEXT, formatMaterialTimestamp } from "@/lib/materialStatusPresentation";

import { MaterialDataBasisNotice } from "./MaterialDataBasisNotice";
import { MaterialFilterBar } from "./MaterialFilterBar";
import {
  DEFAULT_MATERIAL_FILTERS,
  buildMaterialKpis,
  buildMaterialQuery,
  normalizeFiscalYearInput,
  readCoveragePayload,
  toDistrictCardRows,
  type MaterialCoverageResponse,
  type MaterialFilterState,
} from "./materialLedgerAdapters";
import { useMaterialResource } from "./useMaterialResource";

/**
 * MaterialLedgerPage（页面 06）：材料台账首页。
 *
 * 内容：数据口径提示 → 筛选 → 状态 KPI → 区县卡片 → 进入区级矩阵。
 *
 * 三条不制造假结论的规则
 * ----------------------
 * 1. 顶部**始终**展示数据口径提示（本轮只有已建槽位，没有应收基线）；
 * 2. 区县卡片的"完整率"在没有应收基线时显示 `—`，不显示 0%、更不按现有槽位反推一个比例；
 * 3. `not_applicable`（人工确认不适用）不并入任何"缺失"类 KPI。
 */
export function MaterialLedgerPage() {
  const [filters, setFilters] = useState<MaterialFilterState>(DEFAULT_MATERIAL_FILTERS);
  const year = normalizeFiscalYearInput(filters.fiscalYear);

  const effectiveFilters = useMemo<MaterialFilterState>(
    () => ({ ...filters, fiscalYear: year.value }),
    [filters, year.value],
  );
  const url = useMemo(
    () => `/api/materials/coverage${buildMaterialQuery(effectiveFilters)}`,
    [effectiveFilters],
  );

  const response = useMaterialResource<MaterialCoverageResponse>(url, readCoveragePayload);
  const body = response.data;
  const summary = body?.data?.summary ?? null;
  const kpis = useMemo(() => buildMaterialKpis(summary), [summary]);
  const districtRows = useMemo(
    () => toDistrictCardRows(body?.data?.districts ?? []),
    [body],
  );

  const updateFilters = useCallback((patch: Partial<MaterialFilterState>) => {
    setFilters((previous) => ({ ...previous, ...patch }));
  }, []);

  const hasData = Boolean(summary && summary.slot_total > 0);

  return (
    <div className="mx-auto max-w-[1400px] px-6 py-6" data-testid="gbc-material-ledger-page">
      <SectionTitle
        title="材料台账"
        desc="按地区、主管部门与主体查看应收材料的到件与处理状态。"
      />

      <div className="mt-4">
        <MaterialDataBasisNotice expectedMaterialsReady={body?.meta?.expected_materials_ready ?? false} />
      </div>

      <div className="mt-5">
        <MaterialFilterBar
          filters={filters}
          onChange={updateFilters}
          testIdPrefix="gbc-material-ledger"
          action={
            <button
              type="button"
              onClick={response.reload}
              data-testid="gbc-material-ledger-refresh"
              className="rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-600 transition-colors hover:bg-slate-50"
            >
              刷新
            </button>
          }
        />
      </div>

      {response.loading ? (
        <div
          className="mt-5 rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
          data-testid="gbc-material-ledger-loading"
        >
          正在加载材料台账…
        </div>
      ) : null}

      {response.error ? (
        <div
          className="mt-5 rounded-card border border-danger-200 bg-danger-50 p-6 text-sm text-danger-700"
          data-testid="gbc-material-ledger-error"
        >
          <p>材料台账加载失败：{response.error}</p>
          <button
            type="button"
            onClick={response.reload}
            className="mt-3 rounded-md border border-border bg-white px-3 py-1.5 text-sm text-slate-600"
            data-testid="gbc-material-ledger-retry"
          >
            重试
          </button>
        </div>
      ) : null}

      {!response.loading && !response.error && summary ? (
        <>
          <div className="mt-5 grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-4">
            {kpis.map((kpi) => (
              <Metric key={kpi.key} label={kpi.label} value={kpi.value} data-testid={kpi.testId} />
            ))}
          </div>

          <div
            className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-500"
            data-testid="gbc-material-ledger-summary-extra"
          >
            <span data-testid="gbc-material-ledger-budget-total">预算槽位 {summary.budget_total}</span>
            <span data-testid="gbc-material-ledger-final-total">决算槽位 {summary.final_total}</span>
            <span data-testid="gbc-material-ledger-unknown-kind-total">
              文种待确认 {summary.unknown_kind_total}
            </span>
            <span
              data-testid="gbc-material-ledger-due-unknown"
              title="due_at 尚未建立，无法判断是否到期；与「未到期」不是一回事"
            >
              截止时间未知 {summary.due_at_unknown}
            </span>
            {summary.jurisdiction_unknown_total > 0 ? (
              <span data-testid="gbc-material-ledger-jurisdiction-unknown">
                未归入行政区划 {summary.jurisdiction_unknown_total}
              </span>
            ) : null}
          </div>

          <div className="mt-6">
            <Card
              title="区县材料台账"
              desc="点击区县查看该区各主管部门的材料矩阵。"
              data-testid="gbc-material-district-cards"
            >
              {districtRows.length === 0 ? (
                <div className="py-6 text-center text-sm text-slate-500" data-testid="gbc-material-ledger-empty">
                  <p>当前筛选条件下暂无已建立的材料槽位。</p>
                  {body?.meta.expected_materials_ready ? null : (
                    <p className="mt-1 text-xs text-slate-400">这不代表系统中不存在应收材料。</p>
                  )}
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {districtRows.map((row) => (
                    <Link
                      key={row.districtId}
                      href={
                        `/materials/district/${row.districtId}${
                          effectiveFilters.fiscalYear ? `?year=${effectiveFilters.fiscalYear}` : ""
                        }` as Route
                      }
                      data-testid={`gbc-material-district-card-${row.districtId}`}
                      className="rounded-card border border-border bg-white p-4 transition-colors hover:border-primary-400 hover:bg-surface-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold text-slate-900">{row.districtName}</span>
                        <span className="text-xs text-slate-400">共 {row.slotTotal} 个槽位</span>
                      </div>
                      <dl className="mt-3 grid grid-cols-3 gap-x-3 gap-y-2 text-xs text-slate-500">
                        <div>
                          <dt>预算槽位</dt>
                          <dd className="text-sm text-slate-800">{row.budgetTotal}</dd>
                        </div>
                        <div>
                          <dt>决算槽位</dt>
                          <dd className="text-sm text-slate-800">{row.finalTotal}</dd>
                        </div>
                        <div>
                          <dt title="应收材料基线尚未建立">完整率</dt>
                          <dd className="text-sm text-slate-800">
                            {row.coverageRate === null ? UNKNOWN_METRIC_TEXT : row.coverageRate}
                          </dd>
                        </div>
                        <div>
                          <dt>待复核</dt>
                          <dd className="text-sm text-slate-800">{row.reviewRequired}</dd>
                        </div>
                        <div>
                          <dt>待确认归属</dt>
                          <dd className="text-sm text-slate-800">{row.mappingRequired}</dd>
                        </div>
                        <div>
                          <dt>逾期未上传</dt>
                          <dd className="text-sm text-slate-800">{row.missing}</dd>
                        </div>
                        <div>
                          <dt title="已明确 due_at 且尚未到截止时间">未到期</dt>
                          <dd className="text-sm text-slate-800">{row.notDueConfirmed}</dd>
                        </div>
                        <div>
                          <dt title="due_at 尚未建立，无法判断是否到期">截止时间未知</dt>
                          <dd className="text-sm text-slate-800">{row.dueAtUnknown}</dd>
                        </div>
                      </dl>
                      <p className="mt-3 text-xs text-slate-400">
                        更新于 {formatMaterialTimestamp(row.updatedAt)}
                      </p>
                    </Link>
                  ))}
                </div>
              )}
            </Card>
          </div>

          {hasData ? null : (
            <p className="mt-4 text-xs text-slate-400" data-testid="gbc-material-ledger-no-slots-hint">
              当前筛选年度内没有已建立的材料槽位。
            </p>
          )}
        </>
      ) : null}
    </div>
  );
}

export default MaterialLedgerPage;
