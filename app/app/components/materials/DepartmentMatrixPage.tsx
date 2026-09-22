"use client";

import { useCallback, useMemo, useState } from "react";

import { Card, SectionTitle } from "@/components/ui";

import { MaterialBreadcrumb } from "./MaterialBreadcrumb";
import { MaterialDataBasisNotice } from "./MaterialDataBasisNotice";
import { MaterialSlotCard } from "./MaterialSlotCard";
import {
  FISCAL_YEAR_MAX,
  FISCAL_YEAR_MIN,
  buildDepartmentGroups,
  countDepartmentSubjects,
  normalizeFiscalYearInput,
  readDepartmentMatrixPayload,
  type DepartmentMatrixResponse,
  type DepartmentSubjectRow,
} from "./materialLedgerAdapters";
import { useMaterialResource } from "./useMaterialResource";

/**
 * DepartmentMatrixPage（页面 08）：主管部门材料矩阵。
 *
 * 三个分组固定存在：部门汇总 / 本部单位 / 直属单位；「关系待确认」只在真有
 * 这类材料时出现（它是异常兜底组，常驻会让页面上常挂着一块看起来像坏数据的区域）。
 *
 * 财政年度必填且由页面显式提供：不传年度时后端返回 422，前端不替用户猜年度，
 * 而是先请他选一个（"按今年查"正是本项目历史上把 2024 年度决算写成 2025 的成因）。
 */
export interface DepartmentMatrixPageProps {
  departmentId: string;
  fiscalYear?: string;
}

function SubjectRowCells({ row }: { row: DepartmentSubjectRow }) {
  return (
    <tr data-testid={`gbc-material-subject-${row.subject_org_id}`}>
      <td className="border-b border-border px-4 py-3 align-top">
        <div className="whitespace-nowrap text-sm text-slate-900">{row.subject_org_name}</div>
        <div className="mt-1 whitespace-nowrap text-xs text-slate-400">
          主体 id {row.subject_org_id}
        </div>
      </td>
      <td className="border-b border-border px-4 py-3 align-top">
        <MaterialSlotCard ref={row.budget} testId={`gbc-material-slot-${row.subject_org_id}-budget`} />
      </td>
      <td className="border-b border-border px-4 py-3 align-top">
        <MaterialSlotCard ref={row.final} testId={`gbc-material-slot-${row.subject_org_id}-final`} />
      </td>
      <td className="border-b border-border px-4 py-3 align-top">
        {row.unclassified.exists ? (
          <MaterialSlotCard
            ref={row.unclassified}
            testId={`gbc-material-slot-${row.subject_org_id}-unclassified`}
          />
        ) : (
          <span className="text-xs text-slate-300">—</span>
        )}
      </td>
    </tr>
  );
}

export function DepartmentMatrixPage({ departmentId, fiscalYear = "" }: DepartmentMatrixPageProps) {
  const [yearInput, setYearInput] = useState(fiscalYear);
  const year = normalizeFiscalYearInput(yearInput);
  const effectiveYear = year.value;

  const url = useMemo(
    () =>
      effectiveYear
        ? `/api/materials/departments/${encodeURIComponent(departmentId)}/matrix?fiscal_year=${effectiveYear}`
        : null,
    [departmentId, effectiveYear],
  );

  const response = useMaterialResource<DepartmentMatrixResponse>(url, readDepartmentMatrixPayload);
  const matrix = response.data?.data ?? null;
  const groups = useMemo(() => buildDepartmentGroups(matrix?.groups ?? null), [matrix]);
  const subjectTotal = useMemo(() => countDepartmentSubjects(matrix?.groups ?? null), [matrix]);

  const submitYear = useCallback((value: string) => setYearInput(value), []);

  return (
    <div className="mx-auto max-w-[1600px] px-6 py-6" data-testid="gbc-material-department-page">
      <MaterialBreadcrumb
        entries={[
          { label: "材料台账", href: "/materials" },
          ...(matrix?.department.jurisdiction_id
            ? [
                {
                  label: matrix.department.jurisdiction_name ?? matrix.department.jurisdiction_id,
                  href: `/materials/district/${matrix.department.jurisdiction_id}${
                    effectiveYear ? `?year=${effectiveYear}` : ""
                  }`,
                },
              ]
            : []),
          { label: matrix?.department.department_name ?? departmentId },
        ]}
      />

      <div className="mt-3">
        <SectionTitle
          title={`${matrix?.department.department_name ?? departmentId} · 材料矩阵`}
          desc="按主体层级查看该主管部门的预算与决算材料。"
        />
      </div>

      <div className="mt-4">
        <MaterialDataBasisNotice
          expectedMaterialsReady={response.data?.meta?.expected_materials_ready ?? false}
        />
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <label className="text-sm text-slate-600" htmlFor="gbc-material-department-year">
          财政年度（必填）
        </label>
        <input
          id="gbc-material-department-year"
          type="text"
          inputMode="numeric"
          value={yearInput}
          onChange={(event) => submitYear(event.target.value)}
          placeholder={`如 ${FISCAL_YEAR_MIN + 25}`}
          data-testid="gbc-material-department-year-filter"
          className="w-[160px] rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        />
        {yearInput && !year.valid ? (
          <span className="text-xs text-warning-700" data-testid="gbc-material-department-year-hint">
            年度需为 {FISCAL_YEAR_MIN}–{FISCAL_YEAR_MAX} 的 4 位年份。
          </span>
        ) : null}
        <button
          type="button"
          onClick={response.reload}
          disabled={!effectiveYear}
          data-testid="gbc-material-department-refresh"
          className="ml-auto rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
        >
          刷新
        </button>
      </div>

      {!effectiveYear ? (
        <div
          className="mt-5 rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
          data-testid="gbc-material-department-need-year"
        >
          请先选择财政年度：材料按财政年度建立槽位，未指定年度时无法确定要看哪一年。
        </div>
      ) : null}

      {response.loading ? (
        <div
          className="mt-5 rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
          data-testid="gbc-material-department-loading"
        >
          正在加载部门材料矩阵…
        </div>
      ) : null}

      {response.error ? (
        <div
          className="mt-5 rounded-card border border-danger-200 bg-danger-50 p-6 text-sm text-danger-700"
          data-testid="gbc-material-department-error"
        >
          <p>部门材料矩阵加载失败：{response.error}</p>
          <button
            type="button"
            onClick={response.reload}
            className="mt-3 rounded-md border border-border bg-white px-3 py-1.5 text-sm text-slate-600"
            data-testid="gbc-material-department-retry"
          >
            重试
          </button>
        </div>
      ) : null}

      {!response.loading && !response.error && matrix ? (
        subjectTotal === 0 ? (
          <div
            className="mt-5 rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
            data-testid="gbc-material-department-empty"
          >
            <p>当前筛选年度暂无已建立的材料槽位。</p>
            {response.data?.meta?.expected_materials_ready ? null : (
              <p className="mt-1 text-xs text-slate-400">这不代表该部门不存在应收材料。</p>
            )}
          </div>
        ) : (
          <>
            <div
              className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-500"
              data-testid="gbc-material-department-summary"
            >
              <span data-testid="gbc-material-department-year">财政年度 {matrix.fiscal_year}</span>
              <span data-testid="gbc-material-department-subject-count">
                覆盖主体 {subjectTotal} 个
              </span>
            </div>

            {groups.map((group) => (
              <div key={group.key} className="mt-4">
                <Card
                  title={group.title}
                  desc={`${group.rows.length} 个主体`}
                  data-testid={`gbc-material-group-${group.key}`}
                >
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[1100px] border-collapse">
                      <thead>
                        <tr>
                          <th className="w-[280px] whitespace-nowrap border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                            主体
                          </th>
                          <th className="whitespace-nowrap border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                            预算
                          </th>
                          <th className="whitespace-nowrap border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                            决算
                          </th>
                          <th className="w-[240px] whitespace-nowrap border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                            文种待确认
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {group.rows.map((row) => (
                          <SubjectRowCells key={row.subject_org_id} row={row} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              </div>
            ))}
          </>
        )
      ) : null}
    </div>
  );
}

export default DepartmentMatrixPage;
