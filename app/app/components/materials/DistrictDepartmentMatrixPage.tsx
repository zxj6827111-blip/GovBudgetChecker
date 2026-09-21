"use client";

import Link from "next/link";
import type { Route } from "next";
import { useCallback, useMemo, useState } from "react";

import { Card, SectionTitle, Td, Th } from "@/components/ui";
import { UNKNOWN_METRIC_TEXT, formatMaterialTimestamp } from "@/lib/materialStatusPresentation";

import { MaterialBreadcrumb } from "./MaterialBreadcrumb";
import { MaterialDataBasisNotice } from "./MaterialDataBasisNotice";
import { MaterialFilterBar } from "./MaterialFilterBar";
import {
  DEFAULT_MATERIAL_FILTERS,
  buildMaterialQuery,
  normalizeFiscalYearInput,
  readDistrictDepartmentsPayload,
  toDepartmentMatrixRows,
  type DistrictDepartmentMatrixResponse,
  type MaterialFilterState,
} from "./materialLedgerAdapters";
import { useMaterialResource } from "./useMaterialResource";

/**
 * DistrictDepartmentMatrixPage（页面 07）：区级主管部门矩阵。
 *
 * 列：主管部门 / 预算槽位 / 决算槽位 / 待复核 / 待确认归属 / 处理失败 / 逾期未上传 /
 * 未到期 / 完整率 / 更新时间（§三十五）。
 *
 * 两条口径
 * --------
 * 1. 没有应收基线时「完整率」显示 `—`，不按现有槽位反推比例；
 * 2. **部门矩阵需要财政年度**（后端接口年度必填，不得暗中使用当前自然年），
 *    因此未选年度时行内给出明确提示而不是给一个会 422 的链接。
 */
export interface DistrictDepartmentMatrixPageProps {
  districtId: string;
  initialFiscalYear?: string;
}

export function DistrictDepartmentMatrixPage({
  districtId,
  initialFiscalYear = "",
}: DistrictDepartmentMatrixPageProps) {
  const [filters, setFilters] = useState<MaterialFilterState>({
    ...DEFAULT_MATERIAL_FILTERS,
    fiscalYear: initialFiscalYear,
  });
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");

  const year = normalizeFiscalYearInput(filters.fiscalYear);
  const effectiveFilters = useMemo<MaterialFilterState>(
    () => ({ ...filters, fiscalYear: year.value }),
    [filters, year.value],
  );

  const url = useMemo(
    () =>
      `/api/materials/districts/${encodeURIComponent(districtId)}/departments${buildMaterialQuery(
        effectiveFilters,
        { page: String(page), page_size: "20", q: keyword.trim() },
      )}`,
    [districtId, effectiveFilters, page, keyword],
  );

  const response = useMaterialResource<DistrictDepartmentMatrixResponse>(
    url,
    readDistrictDepartmentsPayload,
  );
  const rows = useMemo(
    () => toDepartmentMatrixRows(response.data?.data?.items ?? []),
    [response.data],
  );
  const pagination = response.data?.meta?.pagination ?? null;
  const districtName = response.data?.data?.district?.district_name ?? districtId;

  const updateFilters = useCallback((patch: Partial<MaterialFilterState>) => {
    setFilters((previous) => ({ ...previous, ...patch }));
    setPage(1);
  }, []);

  return (
    <div className="mx-auto max-w-[1400px] px-6 py-6" data-testid="gbc-material-district-page">
      <MaterialBreadcrumb
        entries={[{ label: "材料台账", href: "/materials" }, { label: districtName }]}
      />

      <div className="mt-3">
        <SectionTitle
          title={`${districtName} · 主管部门材料矩阵`}
          desc="按主管部门汇总的材料槽位与处理状态。"
        />
      </div>

      <div className="mt-4">
        <MaterialDataBasisNotice
          expectedMaterialsReady={response.data?.meta?.expected_materials_ready ?? false}
        />
      </div>

      <div className="mt-5 flex flex-wrap items-start gap-3">
        <MaterialFilterBar
          filters={filters}
          onChange={updateFilters}
          testIdPrefix="gbc-material-district"
          action={
            <button
              type="button"
              onClick={response.reload}
              data-testid="gbc-material-district-refresh"
              className="rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-600 transition-colors hover:bg-slate-50"
            >
              刷新
            </button>
          }
        />
        <input
          type="search"
          value={keyword}
          onChange={(event) => {
            setKeyword(event.target.value);
            setPage(1);
          }}
          placeholder="搜索主管部门"
          aria-label="搜索主管部门"
          data-testid="gbc-material-district-search"
          className="min-w-[200px] rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        />
      </div>

      {response.loading ? (
        <div
          className="mt-5 rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
          data-testid="gbc-material-district-loading"
        >
          正在加载主管部门矩阵…
        </div>
      ) : null}

      {response.error ? (
        <div
          className="mt-5 rounded-card border border-danger-200 bg-danger-50 p-6 text-sm text-danger-700"
          data-testid="gbc-material-district-error"
        >
          <p>主管部门矩阵加载失败：{response.error}</p>
          <button
            type="button"
            onClick={response.reload}
            className="mt-3 rounded-md border border-border bg-white px-3 py-1.5 text-sm text-slate-600"
            data-testid="gbc-material-district-retry"
          >
            重试
          </button>
        </div>
      ) : null}

      {!response.loading && !response.error && response.data ? (
        <div className="mt-5">
          <Card
            title="主管部门"
            desc={
              year.value
                ? `${year.value} 年度 · 共 ${pagination?.total ?? 0} 个主管部门`
                : `未限定年度 · 共 ${pagination?.total ?? 0} 个主管部门`
            }
            data-testid="gbc-material-district-table"
          >
            {rows.length === 0 ? (
              <div className="py-8 text-center text-sm text-slate-500" data-testid="gbc-material-district-empty">
                <p>当前筛选条件下暂无已建立的材料槽位。</p>
                {response.data.meta?.expected_materials_ready ? null : (
                  <p className="mt-1 text-xs text-slate-400">这不代表该区不存在应收材料。</p>
                )}
              </div>
            ) : (
              <div className="overflow-x-auto">
                {/* min-w：11 列都需要 nowrap，交给外层 overflow-x-auto 横向滚动；
                    不设 min-w 时浏览器会把首列压到 3 个字一行，"查看矩阵"也会被裁掉。 */}
                <table className="w-full min-w-[900px] border-collapse">
                  <thead>
                    {/* 表头统一 nowrap：列名换行（"预算槽
位"）会让整张表看起来像排版事故，
                        也会把行高撑成两行。窄屏靠外层 overflow-x-auto 横向滚动，不靠折行。 */}
                    {/* 列名用 §三十五 的短表头（宽表在窄内容区会横向滚动，长表头只是把
                        更新时间/操作挤出视口）；完整含义放 title，悬停可见。 */}
                    <tr>
                      <Th className="w-[180px] whitespace-nowrap">主管部门</Th>
                      <Th className="whitespace-nowrap">预算槽位</Th>
                      <Th className="whitespace-nowrap">决算槽位</Th>
                      <Th className="whitespace-nowrap">待复核</Th>
                      <Th className="whitespace-nowrap" title="待确认归属（mapping_required）">
                        待映射
                      </Th>
                      <Th className="whitespace-nowrap" title="处理失败（failed）">
                        失败
                      </Th>
                      <Th className="whitespace-nowrap" title="逾期未上传（missing）">
                        缺失
                      </Th>
                      <Th className="whitespace-nowrap" title="未到期（not_due，含截止时间未知）">
                        未到期
                      </Th>
                      <Th className="whitespace-nowrap" title="应收材料基线尚未建立时不可计算，显示 —">
                        完整率
                      </Th>
                      <Th className="whitespace-nowrap">更新时间</Th>
                      <Th className="whitespace-nowrap">操作</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const testId = `gbc-material-district-row-${row.departmentId ?? "unappointed"}`;
                      const departmentId = row.departmentId;
                      const canOpen = Boolean(departmentId && year.value);
                      return (
                        <tr key={testId} data-testid={testId}>
                          <Td>
                            <div className="whitespace-nowrap text-sm text-slate-900">
                              {row.departmentName}
                            </div>
                            <div className="whitespace-nowrap text-xs text-slate-400">
                              覆盖主体 {row.subjectCount} 个
                            </div>
                          </Td>
                          <Td className="whitespace-nowrap">{row.budgetSlotTotal}</Td>
                          <Td className="whitespace-nowrap">{row.finalSlotTotal}</Td>
                          <Td className="whitespace-nowrap">{row.reviewRequired}</Td>
                          <Td className="whitespace-nowrap">{row.mappingRequired}</Td>
                          <Td className="whitespace-nowrap">{row.failed}</Td>
                          <Td className="whitespace-nowrap">{row.missing}</Td>
                          <Td className="whitespace-nowrap">{row.notDue}</Td>
                          <Td className="whitespace-nowrap">
                            <span
                              title={
                                row.coverageRate === null
                                  ? "应收材料基线尚未建立，完整率暂不可计算"
                                  : undefined
                              }
                            >
                              {row.coverageRate === null ? UNKNOWN_METRIC_TEXT : row.coverageRate}
                            </span>
                          </Td>
                          <Td className="whitespace-nowrap">{formatMaterialTimestamp(row.updatedAt)}</Td>
                          <Td className="whitespace-nowrap">
                            {canOpen ? (
                              <Link
                                href={
                                  `/materials/department/${departmentId}?year=${year.value}` as Route
                                }
                                data-testid={`${testId}-open`}
                                className="whitespace-nowrap text-sm text-primary-700 hover:underline"
                              >
                                查看矩阵
                              </Link>
                            ) : (
                              <span
                                className="text-xs text-slate-400"
                                data-testid={`${testId}-need-year`}
                              >
                                {departmentId ? "请先选择财政年度" : "无主管部门，无法下钻"}
                              </span>
                            )}
                          </Td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {pagination && pagination.total_pages > 1 ? (
            <div className="mt-4 flex items-center justify-end gap-2" data-testid="gbc-material-district-pagination">
              <button
                type="button"
                onClick={() => setPage((value) => Math.max(1, value - 1))}
                disabled={pagination.page <= 1}
                data-testid="gbc-material-district-prev"
                className="rounded-md border border-border bg-white px-3 py-1.5 text-sm text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                上一页
              </button>
              <span className="text-xs text-slate-500">
                第 {pagination.page} / {pagination.total_pages} 页
              </span>
              <button
                type="button"
                onClick={() => setPage((value) => Math.min(pagination.total_pages, value + 1))}
                disabled={pagination.page >= pagination.total_pages}
                data-testid="gbc-material-district-next"
                className="rounded-md border border-border bg-white px-3 py-1.5 text-sm text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                下一页
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default DistrictDepartmentMatrixPage;
